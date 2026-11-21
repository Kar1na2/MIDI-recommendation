import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { buildEmotionData, localCatalog, sourceFolder, dataFolder } from './build_emotion_data.mjs';
import { sectionWindow, selectedEmotionSections, overlaps, describeValue, missingLabel, curvePaths, corpusCallout } from './emotion.mjs';

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
