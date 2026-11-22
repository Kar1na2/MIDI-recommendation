import { finite, timedSection } from './emotion.mjs';
import { percentile, percentileBand } from './emotion_readings.mjs';

export const musicModels = ['M1', 'M2', 'M3'];
export const measuredLabel = 'measured in this section, not the reason for the rating';
export const instrumentNotice = 'Instrument names come from automatic transcription and may be wrong.';
export const energyWords = { very_low: 'Very calm, stripped back', low: 'Relaxed, laid back', middle: 'Moderate, steady', high: 'Energetic, driving', very_high: 'Intense, full force' };
export const featureLabels = {
  instrumental_rms: ['Instrumental loudness', 'quiet', 'moderate', 'loud'],
  instrumental_brightness_hz: ['Instrumental tone', 'dark, muffled tone', 'balanced', 'bright, sharp tone'],
  onset_density_total: ['Total onset density', 'sparse', 'moderate', 'busy'],
  off_strong_beat_share: ['Off-beat share', 'on the beat', 'mixed', 'heavily syncopated'],
  median_register_midi: ['Note register', 'low', 'middle', 'high'],
  key_strength: ['Key strength', 'ambiguous key', null, 'clear key'],
  pattern_repetition_rate: ['Pattern repetition', 'varied', 'moderate', 'repetitive'],
  chord_layer_onset_rate: ['Chord change rate', 'slow', 'moderate', 'fast chord changes']
};
export const isMusicFeature = name => name !== 'tempo_bpm' && !name.startsWith('voice_') && name !== 'onset_density__voice' && name !== 'family_voice';

function distribution(values) {
  const sorted = values.filter(finite).sort((a, b) => a - b);
  const mean = sorted.length ? sorted.reduce((sum, value) => sum + value, 0) / sorted.length : null;
  const deviation = sorted.length ? Math.sqrt(sorted.reduce((sum, value) => sum + (value - mean) ** 2, 0) / sorted.length) : null;
  const quantile = p => {
    if (!sorted.length) return null;
    const position = (sorted.length - 1) * p / 100;
    const lower = Math.floor(position), upper = Math.ceil(position);
    return sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower);
  };
  return { count: sorted.length, mean, deviation, cutoffs: Object.fromEntries([10, 15, 30, 50, 70, 85, 90].map(p => [`p${p}`, quantile(p)])), firstTercile: quantile(100 / 3), secondTercile: quantile(200 / 3), values: sorted };
}

export function arousalSpread(section) {
  const scores = musicModels.map(model => section.model_z?.[`${model}_arousal`]).filter(finite);
  return scores.length >= 2 ? Math.max(...scores) - Math.min(...scores) : null;
}

export function musicCalibration(entries) {
  const primary = entries.filter(entry => entry.data.cohort === 'random').flatMap(entry => entry.sections.filter(s => s.set === 'primary'));
  const names = [...new Set(entries.flatMap(entry => entry.sections.flatMap(s => Object.keys(s.features || {}).filter(isMusicFeature))))].sort();
  return {
    reference: { songs: entries.filter(entry => entry.data.cohort === 'random').length, sections: primary.length, cohort: 'random', sectionSet: 'primary' },
    models: Object.fromEntries(musicModels.map(model => [model, distribution(primary.map(s => s.model_z?.[`${model}_arousal`]))])),
    consensus: distribution(primary.map(s => s.consensus?.background_arousal)),
    spread: distribution(primary.map(arousalSpread)),
    features: Object.fromEntries(names.map(name => [name, distribution(primary.map(s => s.features?.[name]))]))
  };
}

export function calibrationSummary(calibration) {
  const omitValues = value => Object.fromEntries(Object.entries(value).filter(([key]) => key !== 'values'));
  return {
    ...calibration,
    models: Object.fromEntries(Object.entries(calibration.models).map(([name, values]) => [name, omitValues(values)])),
    consensus: omitValues(calibration.consensus), spread: omitValues(calibration.spread),
    features: Object.fromEntries(Object.entries(calibration.features).map(([name, values]) => [name, omitValues(values)])),
    featureZMethod: 'Population mean and standard deviation of available random-300 primary-section values. A zero-variance or missing reference produces null z.'
  };
}

export function featureReading(name, raw, reference) {
  const value = finite(raw) ? raw : null;
  const position = percentile(value, reference?.values || []);
  const band = !finite(position) ? null : position < 15 ? 'low' : position > 85 ? 'high' : 'middle';
  let words = band ? featureLabels[name]?.[{ low: 1, middle: 2, high: 3 }[band]] || null : null;
  if (name === 'major_mode_score' && finite(value)) words = value < 0.5 ? 'minor-leaning' : 'major-leaning';
  return { raw: value, z: finite(value) && reference?.deviation > 0 ? (value - reference.mean) / reference.deviation : null, percentile: position, band, words };
}

export function movement(section, points) {
  const scored = points.filter(p => finite(p.start_s) && finite(p.background_arousal_consensus_z) && p.start_s >= section.start_s && p.start_s < section.end_s).sort((a, b) => a.start_s - b.start_s);
  const duration = section.end_s - section.start_s;
  const first = scored.filter(p => p.start_s < section.start_s + duration / 3);
  const last = scored.filter(p => p.start_s >= section.end_s - duration / 3);
  const mean = rows => rows.reduce((sum, p) => sum + p.background_arousal_consensus_z, 0) / rows.length;
  const change = first.length && last.length ? mean(last) - mean(first) : null;
  const peak = scored.reduce((best, p) => !best || p.background_arousal_consensus_z > best.background_arousal_consensus_z ? p : best, null);
  return { change, word: !finite(change) ? null : change >= 0.5 ? 'builds' : change <= -0.5 ? 'drops' : 'steady', peak_s: peak?.start_s ?? null };
}

export function musicAgreement(models, spread, reference) {
  const available = musicModels.filter(model => finite(models[model]?.z));
  const oppositeSides = available.some(model => models[model].z < 0) && available.some(model => models[model].z > 0);
  const wide = finite(spread) && finite(reference.secondTercile) && spread > reference.secondTercile;
  if (available.length < 2) return { spread, oppositeSides, wide, text: 'Model agreement is unavailable.' };
  if (!wide && !oppositeSides) return { spread, oppositeSides, wide, text: `The ${available.length === 3 ? 'three' : 'available'} models give similar energy ratings.` };
  const ordered = [...available].sort((a, b) => models[a].z - models[b].z);
  const low = models[ordered[0]].z, high = models[ordered.at(-1)].z;
  let text;
  if (ordered.length === 3) {
    const lowerGap = models[ordered[1]].z - low, upperGap = high - models[ordered[1]].z;
    text = upperGap >= lowerGap
      ? `${ordered[2]} rates it as more energetic than ${ordered[0]} and ${ordered[1]}.`
      : `${ordered[0]} rates it as calmer than ${ordered[1]} and ${ordered[2]}.`;
  } else text = `${ordered[1]} rates it as more energetic than ${ordered[0]}.`;
  return { spread, oppositeSides, wide, text };
}

export function buildMusicSections(entry, calibration, windows = { status: 'unavailable', sections: {} }) {
  const instruments = [...new Set(entry.sections.flatMap(s => Object.keys(s.features || {}).filter(name => name.startsWith('onset_density__') && isMusicFeature(name))))].sort();
  const tempoValues = [...new Set(entry.sections.map(s => s.features?.tempo_bpm).filter(finite))];
  return {
    tempo_bpm: tempoValues.length === 1 ? tempoValues[0] : null,
    tempoStatus: tempoValues.length > 1 ? 'inconsistent section values' : tempoValues.length === 1 ? 'song-level' : 'not measured',
    windowStatus: windows.status,
    sections: Object.fromEntries(entry.sections.map(section => {
      const points = timedSection(section) ? (windows.sections?.[section.section_id] || []).filter(p => finite(p.start_s) && p.start_s >= section.start_s && p.start_s < section.end_s) : [];
      const models = Object.fromEntries(musicModels.map(model => {
        const z = finite(section.model_z?.[`${model}_arousal`]) ? section.model_z[`${model}_arousal`] : null;
        const position = percentile(z, calibration.models[model].values);
        return [model, { raw: finite(section.raw?.[`${model}_arousal`]) ? section.raw[`${model}_arousal`] : null, z, percentile: position, band: percentileBand(position),
          valence: { raw: finite(section.raw?.[`${model}_valence`]) ? section.raw[`${model}_valence`] : null, z: finite(section.model_z?.[`${model}_valence`]) ? section.model_z[`${model}_valence`] : null },
          windows: points.map(p => ({ start_s: p.start_s, end_s: p.end_s, raw: p[`${model}_arousal`] ?? null, z: p[`${model}_arousal_z`] ?? null, valenceRaw: p[`${model}_valence`] ?? null, valenceZ: p[`${model}_valence_z`] ?? null })) }];
      }));
      const z = finite(section.consensus?.background_arousal) ? section.consensus.background_arousal : null;
      const position = percentile(z, calibration.consensus.values);
      const featureNames = [...new Set([...Object.keys(featureLabels), 'major_mode_score', ...Object.keys(section.features || {}).filter(isMusicFeature), ...instruments])];
      const features = Object.fromEntries(featureNames.map(name => [name, featureReading(name, section.features?.[name], calibration.features[name])]));
      const topInstruments = instruments.filter(name => finite(features[name]?.raw)).sort((a, b) => features[b].raw - features[a].raw).slice(0, 3);
      const dropouts = instruments.filter(name => features[name]?.band === 'low');
      return [section.section_id, { models, consensus: { z, percentile: position, band: percentileBand(position) }, features, topInstruments, dropouts,
        agreement: musicAgreement(models, arousalSpread(section), calibration.spread),
        movement: movement(section, points),
        consensusWindows: points.map(p => ({ start_s: p.start_s, end_s: p.end_s, z: p.background_arousal_consensus_z ?? null })) }];
    }))
  };
}

export const instrumentName = name => name.replace(/^onset_density__/, '').replaceAll('_', ' ');

export function musicSummary(reading) {
  if (!reading || !reading.consensus.band) return 'Energy is not scored for this section.';
  const eligible = Object.keys(featureLabels).filter(name => name !== 'key_strength');
  const extremes = eligible.filter(name => ['low', 'high'].includes(reading.features[name]?.band) && reading.features[name]?.words)
    .sort((a, b) => Math.abs(reading.features[b].percentile - 50) - Math.abs(reading.features[a].percentile - 50)).slice(0, 4);
  const phrases = extremes.map(name => {
    const words = reading.features[name].words;
    if (name === 'instrumental_rms') return `${words} backing`;
    if (name === 'instrumental_brightness_hz') return `a ${words}`;
    if (name === 'pattern_repetition_rate') return `${words} patterns`;
    if (name === 'off_strong_beat_share') return `${words} note onsets`;
    if (name === 'median_register_midi') return `a ${words} register`;
    if (name === 'onset_density_total') return `${words} note onsets`;
    if (name === 'chord_layer_onset_rate') return words.includes('chord changes') ? words : `${words} chord changes`;
    return `${featureLabels[name][0].toLowerCase()} ${words}`;
  });
  for (const name of reading.dropouts) phrases.push(`sparse ${instrumentName(name)} that nearly drop out`);
  if (reading.features.major_mode_score?.words) phrases.push(`a ${reading.features.major_mode_score.words} mode`);
  let agreement = reading.agreement.text;
  const positions = musicModels.map(model => reading.models[model].percentile);
  if (positions.every(p => finite(p) && p < 10)) {
    const upper = Math.ceil(Math.max(...positions));
    agreement = `All three models rate it among the calmest ${upper}% of sections.`;
  }
  return `${energyWords[reading.consensus.band].split(',')[0]}${phrases.length ? ' with ' + phrases.join(', ') : ''} · ${agreement}`;
}
