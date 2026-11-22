import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { musicCalibration, buildMusicSections, featureReading, musicAgreement, movement, musicSummary, isMusicFeature, energyWords } from './music_readings.mjs';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const folder = path.join(root, 'frontend/data/emotion');
const id = '6QIY4JAyzPH6UuFsyndaPs';
const read = async filename => JSON.parse(await fs.readFile(filename, 'utf8'));
const attitude = () => read(path.join(folder, `${id}.readings.json`));

test('ATTITUDE Bridge 1 preserves all three model scores, a calm consensus and source windows', async () => {
  const data = await attitude(), bridge = data.music.sections['lyric-0007'];
  assert.deepEqual(['M1', 'M2', 'M3'].map(role => bridge.models[role].z.toFixed(2)), ['-1.35', '-1.32', '-1.46']);
  assert.equal(Math.round(bridge.consensus.percentile), 5);
  assert.equal(bridge.consensus.band, 'very_low');
  assert.equal(energyWords[bridge.consensus.band], 'Very calm, stripped back');
  assert.ok(bridge.consensusWindows.length > 0);
  for (const role of ['M1', 'M2', 'M3']) {
    assert.equal(bridge.models[role].windows.length, bridge.consensusWindows.length);
    assert.ok(bridge.models[role].windows.every(p => p.start_s >= 128.02 && p.start_s < 140.42000000000002));
  }
  const source = await read(path.join(root, 'emotion_r2/songs', id, 'sections.json'));
  const row = source.sections.find(s => s.section_id === 'lyric-0007');
  for (const role of ['M1', 'M2', 'M3']) {
    assert.equal(bridge.models[role].raw, row.raw[`${role}_arousal`]);
    assert.equal(bridge.models[role].z, row.model_z[`${role}_arousal`]);
    assert.equal(bridge.models[role].valence.z, row.model_z[`${role}_valence`]);
  }
});

test('ATTITUDE Bridge summary uses measured quietness, dark tone, sparse drums, repetition and minor mode', async () => {
  const bridge = (await attitude()).music.sections['lyric-0007'];
  const summary = musicSummary(bridge);
  for (const word of ['quiet', 'dark', 'sparse drums', 'repetitive', 'minor']) assert.ok(summary.includes(word), word);
  assert.ok(bridge.features.onset_density__drums.percentile < 15);
  assert.equal(bridge.features.instrumental_rms.words, 'quiet');
  assert.equal(bridge.features.instrumental_brightness_hz.words, 'dark, muffled tone');
  assert.equal(bridge.features.pattern_repetition_rate.words, 'repetitive');
  assert.doesNotMatch(summary, /tempo|BPM|150/);
  assert.equal(Object.keys(bridge.features).some(name => name.startsWith('voice_') || name === 'tempo_bpm' || name === 'onset_density__voice'), false);
});

test('ATTITUDE Outro stays moderate and tempo is held at song level', async () => {
  const data = await attitude(), outro = data.music.sections['lyric-0010'];
  assert.equal(outro.consensus.z.toFixed(2), '-0.02');
  assert.equal(energyWords[outro.consensus.band], 'Moderate, steady');
  assert.ok(Math.abs(data.music.tempo_bpm - 150) < 1e-8);
  assert.ok(Object.values(data.music.sections).every(s => !JSON.stringify(s).includes('tempo')));
});

test('missing feature values remain null and cannot receive a measured description', () => {
  const result = featureReading('instrumental_rms', null, { values: [0, 1], mean: 0.5, deviation: 0.5 });
  assert.deepEqual(result, { raw: null, z: null, percentile: null, band: null, words: null });
  assert.equal(featureReading('major_mode_score', 0, { values: [0, 1], mean: 0.5, deviation: 0.5 }).words, 'minor-leaning');
  assert.equal(featureReading('major_mode_score', 0.5, { values: [0, 1], mean: 0.5, deviation: 0.5 }).words, 'major-leaning');
});

test('music calibration excludes pilot, secondary, missing and vocal feature values', () => {
  const s = { section_id: 'a', start_s: 0, end_s: 9, set: 'primary', model_z: { M1_arousal: 0, M2_arousal: 1, M3_arousal: 2 }, consensus: { background_arousal: 1 }, features: { instrumental_rms: 0, voice_rms_mean: 99, onset_density__voice: 99, tempo_bpm: 150 } };
  const entries = [{ data: { cohort: 'random' }, sections: [s, { ...s, set: 'secondary', features: { instrumental_rms: 99 } }] }, { data: { cohort: 'pilot' }, sections: [{ ...s, features: { instrumental_rms: 99 } }] }];
  const c = musicCalibration(entries);
  assert.deepEqual(c.features.instrumental_rms.values, [0]);
  assert.deepEqual(c.models.M1.values, [0]);
  assert.deepEqual(c.spread.values, [2]);
  assert.equal(c.features.voice_rms_mean, undefined);
  assert.equal(c.features.tempo_bpm, undefined);
  assert.equal(isMusicFeature('onset_density__voice'), false);
  const result = buildMusicSections({ sections: [{ ...s, features: {} }] }, c);
  assert.equal(result.sections.a.features.instrumental_rms.raw, null);
  assert.deepEqual(result.sections.a.models.M1.windows, []);
  assert.equal(result.sections.a.movement.change, null);
  const fallback = buildMusicSections({ sections: [s] }, c, { status: 'unavailable', sections: {} });
  assert.equal(fallback.sections.a.models.M1.z, 0);
  assert.equal(fallback.sections.a.features.instrumental_rms.raw, 0);
  assert.deepEqual(fallback.sections.a.models.M1.windows, []);
});

test('movement uses valid first and last time thirds, preserves gaps and reports the peak time', () => {
  const s = { start_s: 100, end_s: 109 };
  const values = [[100, 0], [101, null], [106, 0.5], [107, 0.7]].map(([start_s, value]) => ({ start_s, background_arousal_consensus_z: value }));
  assert.deepEqual(movement(s, values), { change: 0.6, word: 'builds', peak_s: 107 });
  assert.equal(movement(s, [{ start_s: 100, background_arousal_consensus_z: null }, ...values.slice(2)]).change, null);
  assert.equal(movement(s, [{ start_s: 100, background_arousal_consensus_z: 1 }, { start_s: 106, background_arousal_consensus_z: 0.5 }]).word, 'drops');
  assert.equal(movement(s, [{ start_s: 100, background_arousal_consensus_z: 0 }, { start_s: 106, background_arousal_consensus_z: 0.49 }]).word, 'steady');
});

test('model disagreement identifies the differing model without using a mood mixture label', () => {
  const result = musicAgreement({ M1: { z: -0.1 }, M2: { z: 2 }, M3: { z: -0.2 } }, 2.2, { secondTercile: 1 });
  assert.ok(result.text.startsWith('M2 rates it as more energetic'));
  assert.equal(result.oppositeSides, true);
  assert.doesNotMatch(result.text, /mixed/i);
});

test('model notes, explanation values and feature references come from supplied files', async () => {
  const models = await read(path.join(folder, 'models.json'));
  const source = await read(path.join(root, 'emotion_r2/models_amended.json'));
  for (const role of ['M1', 'M2', 'M3']) {
    const target = models.models.find(m => m.role === role), original = source.models.find(m => m.role === role);
    assert.equal(target.id, original.id); assert.equal(target.card, original.card);
    assert.equal(target.trainingData, original.training_data || null);
  }
  const checks = (await read(path.join(root, 'emotion_r2/validation/explanation_checks.json'))).background_arousal;
  for (const feature of models.musicContext.leadingFeatures) {
    const original = checks.full.permutation_importance.find(f => f.feature === feature.feature);
    assert.equal(feature.meanR2Drop, original.mean_r2_drop);
    assert.equal(feature.positiveFolds, original.positive_folds);
    assert.ok(isMusicFeature(feature.feature));
  }
  const calibration = await read(path.join(folder, 'musicCalibration.json'));
  assert.equal(calibration.reference.songs, 300);
  assert.equal(calibration.reference.sections, 1877);
  assert.ok(calibration.features.instrumental_rms.cutoffs.p15 < calibration.features.instrumental_rms.cutoffs.p85);
});
