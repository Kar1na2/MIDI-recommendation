// Display and temporal selection only. Scores and boundaries stay as supplied.
export const finite = value => typeof value === 'number' && Number.isFinite(value);
export const emotionValue = (section, channel) => channel.endsWith('_z') ? section[channel] : section.experimental?.[channel];
export const timedSection = section => finite(section.start_s) && finite(section.end_s) && section.end_s > section.start_s;
export const sectionWindow = section => ({ id: `emotion-${section.section_id}`, label: section.label, start: section.start_s, end: section.end_s });
export const overlaps = (a, b) => a.start < b.end && b.start < a.end;
export const selectedEmotionSections = (data, window) => window ? (data?.sections || []).filter(s => timedSection(s) && overlaps(sectionWindow(s), window)) : [];

export function describeValue(value, mood = false) {
  if (!finite(value)) return 'not scored';
  if (value === 0) return mood ? 'mood at the 300-song average' : 'energy at the 300-song average';
  if (mood) return `${value > 0 ? 'more positive' : 'more negative'} mood than typical`;
  return `${value > 0 ? 'higher' : 'lower'} energy than typical`;
}

export function missingLabel(song, section, channel) {
  if (channel === 'words_valence_z' && (song?.report?.ring1_source === 'instrumental' || section?.section_id?.startsWith('span-'))) return 'no lyric text';
  if (channel.startsWith('voice_') && (song?.report?.ring1_source === 'instrumental' || section?.available_models?.[channel] === 0)) return 'no singing detected';
  return 'not scored';
}

// Separate paths across nulls, missing seconds and source section boundaries.
export function curvePaths(data, channel, duration, width, height, extent) {
  const sections = new Map(data.sections.filter(timedSection).map(s => [s.section_id, s]));
  const points = [...data.curves_1hz].sort((a, b) => a.start_s - b.start_s);
  const paths = [];
  let part = [], previous = null;
  for (const point of points) {
    const section = sections.get(point.section_id), value = emotionValue(point, channel);
    const usable = section && finite(value) && finite(point.start_s) && point.start_s >= section.start_s && point.start_s < section.end_s && point.start_s <= duration;
    if (!usable || previous && (previous.section_id !== point.section_id || point.start_s - previous.start_s > 1.001)) {
      if (part.length) paths.push(part.join(' '));
      part = [];
    }
    if (usable) part.push(`${part.length ? 'L' : 'M'}${point.start_s / duration * width},${height / 2 - value / extent * (height / 2 - 7)}`);
    previous = usable ? point : null;
  }
  if (part.length) paths.push(part.join(' '));
  return paths;
}

export function corpusCallout(corpus) {
  const h1 = corpus?.h1;
  if (!h1 || !finite(h1.share_above_zero) || !finite(h1.median) || !finite(h1.n)) return 'Corpus development check unavailable.';
  return `Choruses had higher instrumental energy than verses in ${Math.round(h1.share_above_zero * 100)}% of ${h1.n} songs (median ${h1.median >= 0 ? '+' : ''}${h1.median.toFixed(2)} z) · development check on these same songs, not confirmed.`;
}
