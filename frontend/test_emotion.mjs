import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { buildEmotionData, localCatalog, sourceFolder, dataFolder } from './build_emotion_data.mjs';
import { sectionWindow, selectedEmotionSections, overlaps, describeValue, missingLabel, curvePaths, corpusCallout } from './emotion.mjs';
import { percentile, percentileBand, makeCalibration, buildSongReadings, wordsAgreement, musicTrend, readingWords, togetherLabel } from './emotion_readings.mjs';
import { renderEmotion, updateEmotionTime, appendEmotionDetails } from './emotion_view.mjs';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const section = (overrides = {}) => ({ section_id: 'lyric-0000', label: 'Verse 1', start_s: 2.25, end_s: 7.75, set: 'primary', words_valence_z: null, background_arousal_z: 0,
  experimental: { voice_valence: null, voice_arousal: null, background_valence: null }, available_models: { words_valence: 0, background_arousal: 3, voice_valence: 0, voice_arousal: 0 }, confidence: null, reasons: [], relation: null, ...overrides });

test('build keys copies by track_id and preserves every null, score and source boundary', async () => {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), 'emotion-build-'));
  try {
    const source = path.join(folder, 'source'), destination = path.join(folder, 'destination');
    await fs.mkdir(source);
    const data = { kind: 'song', track_id: 'trackA', sections: [section(), section({ section_id: 'lyric-0001', start_s: null, end_s: null })], curves_1hz: [] };
    const bytes = JSON.stringify(data, null, 2) + '\n';
    await fs.writeFile(path.join(source, 'differentFilename.json'), bytes);
    await fs.writeFile(path.join(source, 'corpus.json'), '{"kind":"corpus"}\n');
    const manifest = await buildEmotionData({ source, destination, catalog: [{ id: 'trackA' }, { id: 'missing' }], metadataRoot: folder });
    assert.equal(await fs.readFile(path.join(destination, 'trackA.json'), 'utf8'), bytes);
    const copy = JSON.parse(await fs.readFile(path.join(destination, 'trackA.json'), 'utf8'));
    assert.equal(copy.sections[0].words_valence_z, null);
    assert.equal(copy.sections[0].background_arousal_z, 0);
    assert.deepEqual(copy.sections.map(s => [s.start_s, s.end_s]), [[2.25, 7.75], [null, null]]);
    assert.deepEqual(manifest.coverage, { frontendSongs: 2, withEmotionFile: 1, withoutEmotionFile: 1, withScoredSections: 1, withoutScoredSections: 1, instrumentalsWithNoLyricSections: 0 });
    assert.deepEqual(manifest.withoutEmotionTrackIds, ['missing']);
    assert.equal(await fs.readFile(path.join(destination, 'corpus.json'), 'utf8'), '{"kind":"corpus"}\n');
  } finally { await fs.rm(folder, { recursive: true, force: true }); }
});

test('generated files exactly match all digest JSONs, including nulls and timings', async () => {
  const files = (await fs.readdir(sourceFolder)).filter(name => name.endsWith('.json') && !['schema.json', 'corpus.json'].includes(name));
  assert.equal(files.length, 325);
  for (const name of [...files, 'corpus.json']) {
    const original = await fs.readFile(path.join(sourceFolder, name));
    const data = JSON.parse(original);
    const copy = await fs.readFile(path.join(dataFolder, data.kind === 'song' ? `${data.track_id}.json` : name));
    assert.ok(original.equals(copy), name);
  }
});

test('coverage agrees with the actual frontend catalog, scores and instrumental reports', async () => {
  const catalog = await localCatalog();
  const manifest = JSON.parse(await fs.readFile(path.join(dataFolder, 'manifest.json'), 'utf8'));
  let withFile = 0, withScores = 0, instrumentals = 0;
  for (const song of catalog) {
    let data;
    try { data = JSON.parse(await fs.readFile(path.join(sourceFolder, `${song.id}.json`), 'utf8')); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (data) withFile++;
    if (data?.sections.some(s => [s.words_valence_z, s.background_arousal_z, s.experimental.voice_arousal, s.experimental.voice_valence].some(Number.isFinite))) withScores++;
    const folder = song.summary.cohort === 'pilot' ? 'mvp_325/songs' : 'outputs';
    try {
      const report = JSON.parse(await fs.readFile(path.join(root, folder, song.id, 'report.json'), 'utf8'));
      if (report.ring1_source === 'instrumental' && !report.lyric_sections?.length) instrumentals++;
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  assert.equal(catalog.length, 323);
  assert.deepEqual(manifest.coverage, { frontendSongs: 323, withEmotionFile: withFile, withoutEmotionFile: 323 - withFile, withScoredSections: withScores, withoutScoredSections: 323 - withScores, instrumentalsWithNoLyricSections: instrumentals });
  assert.equal(manifest.instrumentalTrackIds.length, instrumentals);
});

test('Genius markers are copied only from existing supplied categories', async () => {
  const manifest = JSON.parse(await fs.readFile(path.join(dataFolder, 'manifest.json'), 'utf8'));
  const id = '01prGWjCTxKBxJc400zwvQ';
  const source = JSON.parse(await fs.readFile(path.join(root, 'emotion_r2/songs', id, 'sections.json'), 'utf8'));
  assert.deepEqual(manifest.tracks[id].geniusSections, Object.fromEntries(source.sections.filter(s => ['chorus', 'verse'].includes(s.genius_category)).map(s => [s.section_id, s.genius_category])));
  assert.equal(manifest.tracks[id].geniusSections['lyric-0001'], undefined);
});

test('lane and ring selections share the exact window and half-open overlap rules', () => {
  const raw = section();
  const selected = sectionWindow(raw);
  const data = { sections: [raw, section({ section_id: 'next', start_s: 7.75, end_s: 10 }), section({ section_id: 'untimed', start_s: null })] };
  assert.deepEqual(selectedEmotionSections(data, selected), [raw]);
  const rings = [{ start: 0, end: 2.25 }, { start: 2.25, end: 5 }, { start: 5, end: 10 }];
  assert.deepEqual(rings.map(ring => overlaps(ring, selected)), [false, true, true]);
  assert.deepEqual(selectedEmotionSections(data, rings[2]).map(s => s.section_id), ['lyric-0000', 'next']);
  assert.deepEqual(selectedEmotionSections(data, null), []);
});

test('missing values have explicit labels and zero is a real average score', () => {
  assert.equal(describeValue(null), 'not scored');
  assert.equal(describeValue(0), 'energy at the 300-song average');
  assert.equal(describeValue(0.01), 'higher energy than typical');
  assert.equal(describeValue(-0.01, true), 'more negative mood than typical');
  assert.equal(missingLabel({ report: { ring1_source: 'instrumental' } }, null, 'words_valence_z'), 'no lyric text');
  assert.equal(missingLabel({}, section(), 'voice_arousal'), 'no singing detected');
  assert.equal(missingLabel({}, section(), 'words_valence_z'), 'not scored');
});

test('background curves keep gaps at nulls, skipped seconds and section boundaries', () => {
  const data = { sections: [section({ start_s: 0, end_s: 10 }), section({ section_id: 'second', start_s: 10, end_s: 20 })], curves_1hz: [
    { section_id: 'lyric-0000', start_s: 0, background_arousal_z: 1 },
    { section_id: 'lyric-0000', start_s: 1, background_arousal_z: null },
    { section_id: 'lyric-0000', start_s: 2, background_arousal_z: 0 },
    { section_id: 'lyric-0000', start_s: 3, background_arousal_z: -1 },
    { section_id: 'lyric-0000', start_s: 5, background_arousal_z: 1 },
    { section_id: 'second', start_s: 10, background_arousal_z: 1 }
  ] };
  const paths = curvePaths(data, 'background_arousal_z', 20, 200, 80, 2);
  assert.equal(paths.length, 4);
  assert.ok(paths[1].startsWith('M20,40 L30,'));
  assert.equal(paths.filter(p => p.includes('L')).length, 1);
});

test('corpus callout uses the supplied development result and caveat', async () => {
  const corpus = JSON.parse(await fs.readFile(path.join(dataFolder, 'corpus.json'), 'utf8'));
  assert.equal(corpusCallout(corpus), 'Choruses had higher instrumental energy than verses in 72% of 146 songs (median +0.26 z) · development check on these same songs, not confirmed.');
  assert.equal(corpusCallout(null), 'Corpus development check unavailable.');
});

const attitudeId = '6QIY4JAyzPH6UuFsyndaPs';
async function attitude() {
  const [data, readings] = await Promise.all([`${attitudeId}.json`, `${attitudeId}.readings.json`].map(name => fs.readFile(path.join(dataFolder, name), 'utf8').then(JSON.parse)));
  const byLabel = label => readings.sections[data.sections.find(s => s.label === label).section_id];
  const models = JSON.parse(await fs.readFile(path.join(dataFolder, 'models.json'), 'utf8'));
  return { data, readings, byLabel, models };
}

test('percentiles preserve nulls, handle ties and follow the requested band edges', () => {
  assert.equal(percentile(null, [0, 1]), null);
  assert.equal(percentile(0, []), null);
  assert.equal(percentile(0, [-1, 0, 0, 1]), 50);
  assert.deepEqual([null, 9.99, 10, 29.99, 30, 69.99, 70, 90, 90.01].map(percentileBand), [null, 'very_low', 'low', 'low', 'middle', 'middle', 'high', 'high', 'very_high']);
});

test('calibration uses only scored random-300 primary sections and model spreads', () => {
  const primary = section({ words_valence_z: 0, background_arousal_z: 0 });
  const metadata = { ...primary, model_z: { T1_valence: -1, T3_valence: 0, L1_valence: 1 } };
  const calibration = makeCalibration([
    { data: { cohort: 'random', sections: [primary, section({ set: 'secondary', words_valence_z: 99 })] }, sections: [metadata] },
    { data: { cohort: 'pilot', sections: [section({ words_valence_z: 99 })] }, sections: [{ ...metadata, model_z: { T1_valence: 99 } }] }
  ]);
  assert.deepEqual(calibration.distributions.words_valence_z, [0]);
  assert.equal(calibration.channels.words_valence_z.count, 1);
  assert.equal(calibration.wordsAgreement.firstTercile, 2);
  assert.equal(calibration.wordsAgreement.secondTercile, 2);
});

test('word agreement uses corpus terciles, the strict 0.3 sign rule and available models', () => {
  const calibration = { wordsAgreement: { firstTercile: 0.7, secondTercile: 1.3, signMagnitude: 0.3 } };
  assert.equal(wordsAgreement({ T1: 0.5, T3: 0.7, L1: 0.9 }, calibration).level, 'high');
  assert.equal(wordsAgreement({ T1: 0, T3: 0.5, L1: 1 }, calibration).level, 'moderate');
  assert.equal(wordsAgreement({ T1: 0.1, T3: 2, L1: 1 }, calibration).level, 'low');
  assert.equal(wordsAgreement({ T1: -0.3, T3: 0.3, L1: null }, calibration).oppositeSigns, false);
  assert.equal(wordsAgreement({ T1: -0.01, T3: 0.31, L1: null }, calibration).level, 'low');
  assert.equal(wordsAgreement({ T1: 0.4, T3: null, L1: null }, calibration).level, null);
});

test('music builds and drops compare time thirds at the requested 0.5 z boundary', () => {
  const s = section({ start_s: 0, end_s: 9 });
  const data = values => ({ curves_1hz: values.map(([start_s, background_arousal_z]) => ({ section_id: s.section_id, start_s, background_arousal_z })) });
  assert.equal(musicTrend(data([[0, 0], [6, 0.5]]), s).word, 'builds');
  assert.equal(musicTrend(data([[0, 0], [6, -0.5]]), s).word, 'drops');
  assert.equal(musicTrend(data([[0, 0], [6, 0.49]]), s).word, '');
  assert.equal(musicTrend(data([[0, null], [6, 0.7]]), s).change, null);
  assert.equal(musicTrend(data([[3, 0], [6, 0.7]]), s).change, null);
});

test('ATTITUDE Verse 2 has per-model neutral readings, moderate music and no mixed label', async () => {
  const { byLabel } = await attitude();
  const reading = byLabel('Verse 2'), words = readingWords(reading);
  assert.equal(words.lyrics, 'Neutral: plain, matter-of-fact wording');
  assert.doesNotMatch(words.lyrics, /mixed/i);
  assert.equal(words.music, 'Moderate, steady');
  assert.equal(words.together, 'Steady groove');
  assert.equal(reading.wordsAgreement.oppositeSigns, true);
});

test('ATTITUDE Pre-Chorus 2 has positive wording, agreement and a warm combined reading', async () => {
  const { byLabel } = await attitude();
  const reading = byLabel('Pre-Chorus 2'), words = readingWords(reading);
  assert.equal(words.lyrics, 'Positive: hopeful, happy-leaning wording');
  assert.equal(reading.wordsAgreement.label, 'Models agree');
  assert.equal(words.together, 'Warm, upbeat-leaning');
});

test('ATTITUDE Bridge 1 is very calm and tender, while Outro energy stays moderate', async () => {
  const { data, byLabel } = await attitude();
  assert.equal(readingWords(byLabel('Bridge 1')).music, 'Very calm, stripped back');
  assert.equal(readingWords(byLabel('Bridge 1')).together, 'Tender, reflective');
  assert.ok(Math.abs(data.sections.find(s => s.label === 'Outro 1').background_arousal_z + 0.02) < 0.003);
  assert.equal(readingWords(byLabel('Outro 1')).music, 'Moderate, steady');
});

test('derived ranks, immediate previous changes and repeated-lyrics references preserve missingness', () => {
  const a = section({ section_id: 'a', start_s: 0, end_s: 2, words_valence_z: 1 });
  const b = section({ section_id: 'b', start_s: 2, end_s: 4, words_valence_z: null });
  const c = section({ section_id: 'c', start_s: 4, end_s: 6, words_valence_z: 1 });
  const data = { track_id: 'test', cohort: 'random', sections: [c, a, b], curves_1hz: [] };
  const metadata = [a, b, c].map(s => ({ ...s, text_sha256: s === b ? null : 'same-fingerprint', model_z: {} }));
  const readings = buildSongReadings(data, metadata, makeCalibration([{ data, sections: metadata }])).sections;
  assert.equal(readings.c.channels.words_valence_z.rank, 1);
  assert.equal(readings.c.channels.words_valence_z.rankedSections, 2);
  assert.equal(readings.c.channels.words_valence_z.changeFromPrevious, null);
  assert.equal(readings.c.channels.words_valence_z.previousSection.section_id, 'b');
  assert.equal(readings.c.repeatedLyrics.section_id, 'a');
  assert.equal(readings.a.repeatedLyrics, null);
});

test('combined reading excludes voice and untrusted background valence across all bands', () => {
  const reading = (lyrics, music, level = 'high') => ({ channels: { words_valence_z: { band: lyrics }, background_arousal_z: { band: music }, voice_arousal: { band: 'very_high' }, background_valence: { band: 'very_low' } }, wordsAgreement: { level } });
  for (const band of ['high', 'very_high']) {
    assert.equal(readingWords(reading(band, 'very_high')).together, 'Upbeat, joyful');
    assert.equal(readingWords(reading(band, 'middle')).together, 'Warm, upbeat-leaning');
    assert.equal(readingWords(reading(band, 'very_low')).together, 'Tender, reflective');
  }
  for (const band of ['low', 'very_low']) {
    assert.equal(readingWords(reading(band, 'high')).together, 'Tense, defiant or angry');
    assert.equal(readingWords(reading(band, 'middle')).together, 'Uneasy, brooding');
    assert.equal(readingWords(reading(band, 'low')).together, 'Sad, melancholic');
  }
  assert.equal(readingWords(reading('middle', 'middle')).together, 'Steady groove');
  assert.equal(readingWords(reading('high', 'middle', 'low')).lyrics, 'Positive: hopeful, happy-leaning wording');
  assert.equal(readingWords(reading(null, 'middle')).together, null);
  assert.equal(readingWords(reading('high', null)).together, null);
  assert.equal(readingWords(reading('high', 'middle')).voice, 'Intense, high, bright delivery');
});

// A small DOM stand-in exercises rendering and events without a browser dependency.
class TestElement {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.attributes = {}; this.listeners = {};
    this.dataset = new Proxy({}, { set: (target, key, value) => { target[key] = String(value); this.attributes[`data-${key.replace(/[A-Z]/g, c => '-' + c.toLowerCase())}`] = String(value); return true; } });
    this.classList = { toggle: (name, active) => { const names = new Set((this.className || '').split(' ').filter(Boolean)); if (active) names.add(name); else names.delete(name); this.className = [...names].join(' '); } };
  }
  set className(value) { this.attributes.class = value; }
  get className() { return this.attributes.class || ''; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || '') + this.children.map(child => child.textContent).join(' '); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text = ''; this.children = children; }
  setAttribute(name, value) { this.attributes[name] = String(value); if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  querySelectorAll(selector) {
    const matches = item => selector.startsWith('.') ? item.className.split(' ').includes(selector.slice(1)) : Object.hasOwn(item.attributes, selector.slice(1, -1));
    return this.children.flatMap(child => [...(matches(child) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function withTestDocument(callback) {
  const original = globalThis.document;
  globalThis.document = { createElement: tag => new TestElement(tag), createElementNS: (_, tag) => new TestElement(tag) };
  try { callback(); } finally { if (original === undefined) delete globalThis.document; else globalThis.document = original; }
}

test('lane selection updates all model rows, highlights and the combined reading', async () => {
  const { data, readings, models } = await attitude();
  withTestDocument(() => {
    const host = new TestElement('section');
    const song = { emotion: data, emotionReadings: readings, emotionModels: models, duration: 220 };
    renderEmotion(host, song, null, (_, window) => updateEmotionTime(host, { window, duration: 220, position: window.start }));
    const verse = data.sections.find(s => s.label === 'Verse 2');
    const band = host.querySelectorAll('[data-emotion-id]').find(e => e.dataset.emotionId === verse.section_id);
    band.listeners.click();
    const lyrics = host.querySelectorAll('[data-reading-lane]')[0];
    const rows = lyrics.querySelectorAll('.words-model-row');
    assert.equal(rows.length, 3);
    assert.ok(rows[0].textContent.includes('T1 -0.38 z'));
    assert.ok(rows[1].textContent.includes('T3 +0.22 z'));
    assert.ok(rows[2].textContent.includes('L1 +0.61 z'));
    assert.ok(rows[2].textContent.includes("the model's own description, written after scoring"));
    assert.equal(rows.some(row => /^Mixed:/m.test(row.textContent)), false);
    assert.equal(lyrics.querySelector('.words-average').textContent, 'Average · +0.15 z');
    assert.equal(host.querySelectorAll('[data-reading-lane]')[1].querySelectorAll('.music-model-row').length, 3);
    assert.ok(host.querySelectorAll('[data-reading-lane]')[1].textContent.includes('Moderate, steady'));
    assert.ok(host.querySelector('.reading-combined').textContent.includes('Steady groove'));
    assert.equal(host.querySelectorAll('[data-emotion-id]').filter(e => e.dataset.emotionId === verse.section_id && e.getAttribute('aria-pressed') === 'true').length, 3);
    assert.equal(host.querySelectorAll('.emotion-playhead').every(e => Number(e.getAttribute('x1')) === verse.start_s / 220 * 720), true);
    updateEmotionTime(host, { window: null, duration: 220, position: verse.start_s });
    assert.ok(host.querySelectorAll('[data-reading-lane]')[0].textContent.includes('T1'));
    const preChorus = data.sections.find(s => s.label === 'Pre-Chorus 2');
    updateEmotionTime(host, { window: null, duration: 220, position: preChorus.start_s });
    assert.ok(host.querySelectorAll('[data-reading-lane]')[0].textContent.includes('Positive: hopeful, happy-leaning wording'));
    assert.ok(host.querySelector('.reading-combined').textContent.includes('Warm, upbeat-leaning'));
  });
});

test('null lyrics render an explicit music-only note and omit the Together line', () => {
  const raw = section({ start_s: 0, end_s: 9, section_id: 'span-0', words_valence_z: null });
  const data = { track_id: 'emptyWords', cohort: 'random', sections: [raw], curves_1hz: [] };
  const readings = buildSongReadings(data, [], makeCalibration([{ data, sections: [] }]));
  assert.equal(readingWords(readings.sections['span-0']).together, null);
  withTestDocument(() => {
    const panel = new TestElement('aside');
    appendEmotionDetails(panel, { emotion: data, emotionReadings: readings }, sectionWindow(raw));
    assert.ok(panel.textContent.includes('Lyrics unavailable'));
    assert.ok(panel.textContent.includes('Music only — Moderate, steady'));
    assert.equal(panel.textContent.includes(togetherLabel), false);
  });
});

test('ATTITUDE Verse 2 displays three shared-scale rows and all model outputs', async () => {
  const { data, byLabel, models } = await attitude();
  const section = data.sections.find(s => s.label === 'Verse 2');
  const details = byLabel('Verse 2').wordsModelDetails;
  assert.deepEqual(Object.keys(details), ['T1', 'T3', 'L1']);
  assert.equal(details.T1.score, -0.37720004519454864);
  assert.equal(details.T3.score, 0.2183465343941608);
  assert.equal(details.L1.score, 0.6072099357250312);
  assert.deepEqual(details.T1.probabilities, { negative: 0.2524271607398987, neutral: 0.4024697542190552, positive: 0.34510308504104614 });
  assert.deepEqual(details.T3.probabilities, { negative: 0.1696285754442215, neutral: 0.39781251549720764, positive: 0.43255889415740967 });
  assert.equal(details.L1.energy, 0.8);
  assert.equal(details.L1.uncertain, false);
  assert.ok(details.L1.description);
  assert.equal(models.models.filter(model => ['T1', 'T3', 'L1'].includes(model.role)).length, 3);
  assert.equal(Object.hasOwn(section, 'text'), false);
});

test('words lane keeps its Trusted badge above all three model rows', async () => {
  const { data, readings, models } = await attitude();
  withTestDocument(() => {
    const host = new TestElement('section');
    renderEmotion(host, { emotion: data, emotionReadings: readings, emotionModels: models, duration: 220 }, null, () => {});
    const firstLane = host.querySelector('.emotion-lane');
    const badge = firstLane.querySelector('.trust-badge');
    assert.ok(badge.textContent.startsWith('Trusted'));
    assert.equal(firstLane.querySelectorAll('.words-model-row').length, 3);
  });
});

test('model outputs preserve nulls and Part B absence remains explicit', () => {
  const source = section({ section_id: 'null-row', words_valence_z: null, experimental: { voice_arousal: null } });
  const calibration = makeCalibration([{ data: { cohort: 'random', sections: [source] }, sections: [] }]);
  const result = buildSongReadings({ track_id: 'nulls', cohort: 'random', sections: [source], curves_1hz: [] }, [], calibration, {
    probabilities: { sections: [{ section_id: 'null-row', T1: { status: 'missing_text', probabilities: { negative: null, neutral: null, positive: null } }, T3: { status: 'missing_text', probabilities: { negative: null, neutral: null, positive: null } } }] },
    l1: { sections: [{ section_id: 'null-row', status: 'missing_text', valence: null, arousal: null, uncertain: null }] }
  });
  const details = result.sections['null-row'].wordsModelDetails;
  assert.equal(details.T1.score, null); assert.equal(details.T1.probabilities.positive, null);
  assert.equal(details.T3.score, null); assert.equal(details.T3.probabilities.negative, null);
  assert.equal(details.L1.score, null); assert.equal(details.L1.energy, null); assert.equal(details.L1.uncertain, null); assert.equal(details.L1.description, null);
});

test('generated display data never embeds a source section-text string', async () => {
  const files = await fs.readdir(dataFolder);
  const ids = files.filter(name => /^[A-Za-z0-9]+\.json$/.test(name) && !['corpus.json', 'manifest.json', 'calibration.json', 'models.json', 'musicCalibration.json'].includes(name)).map(name => name.slice(0, -5));
  assert.equal(ids.length, 325);
  for (const id of ids) {
    let privateData;
    try { privateData = JSON.parse(await fs.readFile(path.join(root, 'emotion_r2/private', `${id}.json`), 'utf8')); }
    catch (error) { if (error.code === 'ENOENT') continue; throw error; }
    const display = await Promise.all([`${id}.json`, `${id}.readings.json`].map(name => fs.readFile(path.join(dataFolder, name), 'utf8').then(JSON.parse)));
    const strings = value => typeof value === 'string' ? [value] : value && typeof value === 'object' ? Object.values(value).flatMap(strings) : [];
    const visibleStrings = display.flatMap(strings);
    for (const section of privateData.sections || []) {
      if (typeof section.text === 'string' && section.text.trim()) assert.equal(visibleStrings.some(value => value.includes(section.text)), false, `${id} display asset contains source section text`);
    }
  }
});

test('Music lane selection exposes model curves, measured descriptions and missing feature states', async () => {
  const { data, readings, models } = await attitude();
  withTestDocument(() => {
    const host = new TestElement('section');
    let selected;
    const song = { emotion: data, emotionReadings: readings, emotionModels: models, duration: 220 };
    renderEmotion(host, song, null, (_, window) => { selected = window; updateEmotionTime(host, { window, position: window.start, duration: 220 }); });
    const music = host.querySelectorAll('.emotion-lane')[1];
    const bridge = data.sections.find(s => s.label === 'Bridge 1');
    music.querySelectorAll('[data-emotion-id]').find(band => band.dataset.emotionId === bridge.section_id).listeners.click();
    assert.equal(selected.start, bridge.start_s);
    assert.equal(selected.end, bridge.end_s);
    assert.equal(music.querySelectorAll('.music-model-row').length, 3);
    assert.equal(music.querySelectorAll('.music-sparkline').length, 3);
    const summary = music.querySelector('.music-summary').textContent;
    assert.ok(summary.includes('quiet'));
    assert.ok(summary.includes('dark'));
    assert.ok(summary.includes('sparse drums'));
    assert.ok(music.querySelector('.music-measurements').textContent.includes('not measured'));
    assert.doesNotMatch(music.textContent, /tempo|BPM/);
    assert.equal(music.querySelectorAll('.music-model-row').filter(row => row.textContent.includes('MediaEval training-data lineage')).length, 2);
    assert.ok(music.querySelector('.music-movement').textContent.includes('peaks at'));
  });
});

test('all Part B missing-text sections retain null scores, energy, uncertainty and descriptions', async () => {
  const files = (await fs.readdir(sourceFolder)).filter(name => /^[A-Za-z0-9]+\.json$/.test(name) && !['schema.json', 'corpus.json'].includes(name));
  let missingText = 0;
  for (const name of files) {
    const id = name.slice(0, -5);
    const [emotion, readings, registered] = await Promise.all([
      fs.readFile(path.join(dataFolder, name), 'utf8').then(JSON.parse),
      fs.readFile(path.join(dataFolder, `${id}.readings.json`), 'utf8').then(JSON.parse),
      fs.readFile(path.join(root, 'emotion_r2/display/l1_readings', `${id}.json`), 'utf8').then(JSON.parse)
    ]);
    const displayById = new Map(registered.sections.map(row => [row.section_id, row]));
    for (const source of emotion.sections) {
      const l1Source = displayById.get(source.section_id);
      const result = readings.sections[source.section_id].wordsModelDetails.L1;
      if (l1Source.status === 'missing_text') {
        missingText++;
        assert.equal(result.status, 'missing_text');
        assert.equal(result.score, null);
        assert.equal(result.energy, null);
        assert.equal(result.uncertain, null);
        assert.equal(result.description, null);
      } else if (l1Source.status === 'ok') {
        assert.equal(result.description, l1Source.reading);
      }
    }
  }
  assert.equal(files.length, 325);
  assert.equal(missingText, 1519);
});
