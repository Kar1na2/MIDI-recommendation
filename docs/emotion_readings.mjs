// Presentation rules requested for the existing section consensus scores.
import { finite, emotionValue, timedSection } from './emotion.mjs';

export const readingChannels = ['words_valence_z', 'background_arousal_z', 'voice_arousal', 'voice_valence'];
export const lyricModels = ['T1', 'T3', 'L1'];
export const togetherLabel = 'Lyrics + music together: a model-based reading of two channels, not a measured feeling.';

export function percentile(value, reference) {
  if (!finite(value) || !reference.length) return null;
  const below = reference.filter(score => score < value).length;
  const equal = reference.filter(score => score === value).length;
  return 100 * (below + equal / 2) / reference.length;
}

export function percentileBand(value) {
  if (!finite(value)) return null;
  if (value < 10) return 'very_low';
  if (value < 30) return 'low';
  if (value < 70) return 'middle';
  if (value <= 90) return 'high';
  return 'very_high';
}

function quantile(sorted, fraction) {
  if (!sorted.length) return null;
  const index = (sorted.length - 1) * fraction, lower = Math.floor(index);
  return sorted[lower] + (sorted[Math.ceil(index)] - sorted[lower]) * (index - lower);
}

export function modelScores(section) {
  return Object.fromEntries(lyricModels.map(model => [model, finite(section?.model_z?.[`${model}_valence`]) ? section.model_z[`${model}_valence`] : null]));
}

function spread(scores) {
  const available = Object.values(scores).filter(finite);
  return available.length >= 2 ? Math.max(...available) - Math.min(...available) : null;
}

export function makeCalibration(entries) {
  const primary = entries.filter(entry => entry.data.cohort === 'random').flatMap(entry => entry.data.sections.filter(s => s.set === 'primary'));
  const distributions = Object.fromEntries(readingChannels.map(channel => [channel, primary.map(s => emotionValue(s, channel)).filter(finite).sort((a, b) => a - b)]));
  const spreads = entries.filter(entry => entry.data.cohort === 'random').flatMap(entry => entry.sections.filter(s => s.set === 'primary').map(s => spread(modelScores(s)))).filter(finite).sort((a, b) => a - b);
  return {
    reference: { cohort: 'random', songs: entries.filter(entry => entry.data.cohort === 'random').length, sectionSet: 'primary', sections: primary.length },
    percentileMethod: 'Empirical midrank: 100 × (count below + half the count equal) / count scored. Nulls excluded.',
    bandBoundaries: 'Very low covers percentiles below 10. Low covers 10 to under 30. Middle covers 30 to under 70. High covers 70 through 90. Very high covers values over 90.',
    channels: Object.fromEntries(readingChannels.map(channel => [channel, {
      count: distributions[channel].length,
      cutoffs: Object.fromEntries([10, 30, 70, 90].map(p => [`p${p}`, quantile(distributions[channel], p / 100)]))
    }])),
    wordsAgreement: { count: spreads.length, firstTercile: quantile(spreads, 1 / 3), secondTercile: quantile(spreads, 2 / 3), signMagnitude: 0.3 },
    distributions
  };
}

export function wordsAgreement(scores, calibration) {
  const available = Object.values(scores).filter(finite), width = spread(scores);
  const reference = calibration.wordsAgreement;
  const oppositeSigns = available.some(value => value < 0) && available.some(value => value > 0) && available.some(value => Math.abs(value) > reference.signMagnitude);
  if (!finite(width) || !finite(reference.firstTercile) || !finite(reference.secondTercile)) return { label: 'Agreement unavailable', level: null, spread: width, spreadTercile: null, oppositeSigns, models: available.length };
  const tercile = width <= reference.firstTercile ? 1 : width <= reference.secondTercile ? 2 : 3;
  const disagree = oppositeSigns || tercile === 3;
  return { label: disagree ? 'Models disagree' : 'Models agree', level: disagree ? 'low' : tercile === 1 ? 'high' : 'moderate', spread: width, spreadTercile: tercile, oppositeSigns, models: available.length };
}

export function musicTrend(data, section) {
  if (!timedSection(section)) return { word: null, change: null };
  const third = (section.end_s - section.start_s) / 3;
  const points = data.curves_1hz.filter(p => p.section_id === section.section_id && p.start_s >= section.start_s && p.start_s < section.end_s && finite(p.background_arousal_z));
  const first = points.filter(p => p.start_s < section.start_s + third);
  const last = points.filter(p => p.start_s >= section.end_s - third);
  if (!first.length || !last.length) return { word: null, change: null };
  const mean = rows => rows.reduce((sum, p) => sum + p.background_arousal_z, 0) / rows.length;
  const change = mean(last) - mean(first);
  return { word: change >= 0.5 ? 'builds' : change <= -0.5 ? 'drops' : '', change };
}

export function buildSongReadings(data, sourceSections, calibration, display = {}) {
  const sources = new Map(sourceSections.map(s => [s.section_id, s]));
  const probabilityRows = new Map((display.probabilities?.sections || []).map(s => [s.section_id, s]));
  const l1Rows = new Map((display.l1?.sections || []).map(s => [s.section_id, s]));
  const descriptions = new Map((display.descriptions?.sections || []).map(s => [s.section_id, s]));
  const ordered = data.sections.filter(timedSection).sort((a, b) => a.start_s - b.start_s || a.end_s - b.end_s || a.section_id.localeCompare(b.section_id));
  const previous = new Map(ordered.map((s, index) => [s.section_id, ordered[index - 1] || null]));
  const songValues = Object.fromEntries(readingChannels.map(channel => [channel, data.sections.map(s => emotionValue(s, channel)).filter(finite)]));
  const repeated = new Map(), repeats = new Map();
  for (const section of ordered) {
    const hash = sources.get(section.section_id)?.text_sha256;
    if (typeof hash !== 'string' || !hash.length) continue;
    if (repeated.has(hash)) repeats.set(section.section_id, repeated.get(hash));
    else repeated.set(hash, { section_id: section.section_id, label: section.label });
  }
  return { track_id: data.track_id, sections: Object.fromEntries(data.sections.map(section => {
    const models = modelScores(sources.get(section.section_id));
    const probabilities = probabilityRows.get(section.section_id);
    const l1 = l1Rows.get(section.section_id);
    const description = descriptions.get(section.section_id);
    return [section.section_id, {
      channels: Object.fromEntries(readingChannels.map(channel => {
        const value = emotionValue(section, channel), prior = previous.get(section.section_id), priorValue = prior ? emotionValue(prior, channel) : null;
        const position = percentile(value, calibration.distributions[channel]);
        return [channel, { percentile: position, band: percentileBand(position),
          rank: finite(value) ? 1 + songValues[channel].filter(other => other > value).length : null,
          rankedSections: songValues[channel].length,
          previousSection: prior ? { section_id: prior.section_id, label: prior.label } : null,
          changeFromPrevious: finite(value) && finite(priorValue) ? value - priorValue : null }];
      })),
      wordsModels: models,
      wordsModelDetails: Object.fromEntries(lyricModels.map(model => {
        const value = models[model];
        const channel = probabilities?.[model];
        const position = percentile(value, calibration.distributions.words_valence_z);
        const row = { score: value, percentile: position, band: percentileBand(position) };
        if (model !== 'L1') {
          row.status = typeof channel?.status === 'string' ? channel.status : null;
          row.probabilities = Object.fromEntries(['negative', 'neutral', 'positive'].map(name => [name, finite(channel?.probabilities?.[name]) ? channel.probabilities[name] : null]));
        } else {
          row.energy = finite(l1?.arousal) ? l1.arousal : null;
          row.uncertain = typeof l1?.uncertain === 'boolean' ? l1.uncertain : null;
          row.status = typeof l1?.status === 'string' ? l1.status : null;
          row.description = description?.status === 'ok' && typeof description.reading === 'string' && description.reading.trim() ? description.reading : null;
        }
        return [model, row];
      })),
      wordsAgreement: wordsAgreement(models, calibration),
      musicTrend: musicTrend(data, section),
      repeatedLyrics: repeats.get(section.section_id) || null
    }];
  })) };
}

const lyricsWords = {
  very_low: 'Very dark wording: sad, bitter or angry',
  low: 'Leaning negative: downbeat or troubled wording',
  high: 'Positive: hopeful, happy-leaning wording',
  very_high: 'Very positive: joyful, loving or triumphant wording'
};
export function lyricMood(band) {
  return band === 'middle' ? 'Neutral: plain, matter-of-fact wording' : lyricsWords[band] || null;
}
const musicWords = { very_low: 'Very calm, stripped back', low: 'Relaxed, laid back', middle: 'Moderate, steady', high: 'Energetic, driving', very_high: 'Intense, full force' };
const voiceWords = { very_low: 'Soft, low-key delivery', low: 'Gentle, relaxed delivery', middle: 'Moderate, steady delivery', high: 'Strong, animated delivery', very_high: 'Intense, high, bright delivery' };
const instrumentalWords = { very_low: 'Very calm instrumental', low: 'Relaxed instrumental flow', middle: 'Steady groove', high: 'Driving instrumental push', very_high: 'Intense instrumental push' };

export function readingWords(reading) {
  const lyricsBand = reading?.channels?.words_valence_z?.band, musicBand = reading?.channels?.background_arousal_z?.band;
  const agreement = reading?.wordsAgreement;
  let lyrics = lyricsWords[lyricsBand] || null;
  if (lyricsBand === 'middle') lyrics = 'Neutral: plain, matter-of-fact wording';
  let together = null;
  if (lyricsBand && musicBand) {
    const energy = ['very_low', 'low'].includes(musicBand) ? 'low' : musicBand === 'middle' ? 'middle' : 'high';
    if (lyricsBand === 'middle') together = instrumentalWords[musicBand];
    else if (['high', 'very_high'].includes(lyricsBand)) together = { high: 'Upbeat, joyful', middle: 'Warm, upbeat-leaning', low: 'Tender, reflective' }[energy];
    else together = { high: 'Tense, defiant or angry', middle: 'Uneasy, brooding', low: 'Sad, melancholic' }[energy];
  }
  return { lyrics, music: musicWords[musicBand] || null, voice: voiceWords[reading?.channels?.voice_arousal?.band] || null, together };
}
