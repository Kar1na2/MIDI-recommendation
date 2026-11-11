// Presentation adapters only. These functions never estimate musical boundaries.
export function parseCSV(text) {
  const rows = []; let row = [], field = '', quoted = false;
  text = text.replace(/^\uFEFF/, '');
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') { if (quoted && text[i + 1] === '"') { field += '"'; i++; } else quoted = !quoted; }
    else if (c === ',' && !quoted) { row.push(field); field = ''; }
    else if ((c === '\n' || c === '\r') && !quoted) {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(field); if (row.some(x => x !== '')) rows.push(row); row = []; field = '';
    } else field += c;
  }
  if (quoted) throw new Error('The CSV has an unterminated quoted field.');
  if (field || row.length) { row.push(field); rows.push(row); }
  const headings = rows.shift();
  if (!headings?.length) throw new Error('The CSV has no column headings.');
  return rows.map((values, i) => {
    if (values.length !== headings.length) throw new Error(`CSV row ${i + 2} has ${values.length} fields; expected ${headings.length}.`);
    return Object.fromEntries(headings.map((key, j) => [key, values[j]]));
  });
}

export function buildCatalog(sampleText, summaryText, expectedCount = 300) {
  const sample = parseCSV(sampleText), summaries = parseCSV(summaryText);
  if (sample.length !== expectedCount) throw new Error(`mvp/sample.csv has ${sample.length} songs; expected ${expectedCount}.`);
  const byId = new Map(summaries.map(row => [row.track_id, row]));
  if (summaries.length !== sample.length || byId.size !== sample.length || new Set(sample.map(s => s.id)).size !== sample.length) throw new Error('The MVP song list and summary membership do not match.');
  return sample.map(row => {
    const summary = byId.get(row.id);
    if (!summary) throw new Error(`No mvp/summary.csv row exists for ${row.id}.`);
    const duration = Number(row.duration_s);
    if (!(duration > 0)) throw new Error(`No valid original-recording duration exists for ${row.id}.`);
    const parsed = {};
    for (const field of ['grid_steps_per_instrument', 'instrument_pattern_metrics']) {
      // Empty is explicitly unavailable, not an invented empty measurement.
      try { parsed[field] = summary[field] === '' ? null : JSON.parse(summary[field]); }
      catch { throw new Error(`Invalid ${field} JSON in the summary for ${row.id}.`); }
    }
    return { ...row, duration, summary: { ...summary, ...parsed } };
  });
}

export const formatTime = seconds => {
  const n = Math.max(0, Math.floor(seconds));
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
};
export const formatBoundary = seconds => {
  const ticks = Math.max(0, Math.round(Number(seconds) * 100));
  const whole = Math.floor(ticks / 100);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}.${String(ticks % 100).padStart(2, '0')}`;
};
export const voiceInstrument = name => name.split('__')[0] === 'voice';
const displayVocalLabel = value => {
  const match = String(value ?? '').match(/^Unlabeled\s+(\d+)$/i);
  return match ? `Vocal segment ${match[1]}` : value;
};
export const segmentBounds = (duration, section) => section ? [section.start, section.end] : [0, duration];
export const seekInWindow = (time, duration, section) => { const [a, b] = segmentBounds(duration, section); return Math.max(a, Math.min(b, time)); };
export const songAngle = (time, duration) => Math.max(0, Math.min(duration, time)) / duration * 360;

const finite = x => typeof x === 'number' && Number.isFinite(x);
const stageData = (name, files) => {
  const file = files[name];
  if (!file) throw new Error(`${name}.json was not supplied.`);
  if (file.error) throw new Error(file.error);
  if (file.status === 'failed') throw new Error(`${name}.json records a failed stage: ${file.reason || 'no reason supplied'}.`);
  return name === 'report' ? file : file.data;
};

function timedRing(ring, rows, duration, timing) {
  const rejected = []; const segments = [];
  rows.forEach((raw, index) => {
    const [start, end] = timing(raw);
    if (!finite(start) || !finite(end) || end <= start) { rejected.push(`Source row ${index + 1} has no usable start/end timing.`); return; }
    const a = Math.max(0, start), b = Math.min(duration, end);
    if (b <= a) { rejected.push(`Source row ${index + 1} is entirely outside the original recording.`); return; }
    segments.push({ ...raw, id: `${ring.key}-${index}`, start: a, end: b, sourceStart: start, sourceEnd: end,
      clipped: a !== start || b !== end, sourceIndex: index });
  });
  if (!segments.length) throw new Error(`No timed ${ring.name.toLowerCase()} are available. ${rejected.join(' ')}`);
  // Uncovered time is displayed as unassigned. It is never called vocal silence.
  const sorted = [...segments].sort((a, b) => a.start - b.start); let cursor = 0, gaps = 0;
  for (const section of sorted) {
    if (section.start - cursor > 0.001) segments.push({ id: `${ring.key}-gap-${gaps++}`, start: cursor, end: section.start, label: 'Unassigned timing', unassigned: true });
    cursor = Math.max(cursor, section.end);
  }
  if (duration - cursor > 0.001) segments.push({ id: `${ring.key}-gap-${gaps}`, start: cursor, end: duration, label: 'Unassigned timing', unassigned: true });
  return { ...ring, segments: segments.sort((a, b) => a.start - b.start), warnings: rejected };
}

export function buildSong(song, files) {
  const duration = song.duration, rings = [];
  let report, beats, patterns, lyrics, layers;
  const fileErrors = Object.entries(files).filter(([, v]) => v.error).map(([, v]) => v.error);
  try { report = stageData('report', files); } catch { /* Recorded explicitly in fileErrors/ring errors below. */ }
  try { beats = stageData('beats', files); } catch { /* Each dependent ring reports its missing source. */ }
  try { patterns = stageData('patterns', files); } catch { /* Each dependent ring reports its missing source. */ }
  try { layers = stageData('layers', files); } catch { /* The summary reports this missing source. */ }
  try { lyrics = stageData('lyrics', files); } catch { /* Report headings are the primary lyric source. */ }
  const hasVoice = Boolean(report?.patterns?.some(p => voiceInstrument(p.instrument)));
  const definitions = new Map((patterns?.patterns || []).map(p => [p.pattern_id, p]));
  function make(ring, fn) {
    try { rings.push(fn(ring)); }
    catch (error) { rings.push({ ...ring, segments: [], warnings: [], error: error.message }); }
  }
  const requireRaw = () => {
    if (!report) throw new Error(files.report?.error || 'The per-song report is unavailable.');
    if (!beats) throw new Error(files.beats?.error || 'The measured beat/bar data is unavailable.');
    if (beats.chosen_grid !== 'raw') throw new Error(`The supplied beat grid is ${String(beats.chosen_grid)}, not the required v1 raw grid.`);
    if (report.schema_version !== 'core-mvp/1') throw new Error(`Unsupported report schema: ${report.schema_version}.`);
  };
  make({ key: 'vocals', name: 'Vocal sections', radius: [208, 264], source: 'report.json → lyric_sections (raw times)' }, ring => {
    if (!report) throw new Error(files.report?.error || 'The lyric-section report is unavailable.');
    const source = report.ring1_source;
    if (source === 'instrumental') return { ...ring, source, warnings: [], segments: [{ id: 'instrumental', start: 0, end: duration, label: 'Instrumental', instrumental: true }] };
    if (source === 'vocal_activity') {
      if (!lyrics?.ring1_segments?.length) throw new Error(files.lyrics?.error || 'No vocal-activity intervals are available.');
      return timedRing({ ...ring, source: 'lyrics.json → ring1_segments', activityOnly: true }, lyrics.ring1_segments.map(s => ({ ...s, label: s.label, activityOnly: true })), duration, s => [s.start, s.end]);
    }
    if (!report.lyric_sections?.length) throw new Error('report.json has no timed lyric sections.');
    return timedRing(ring, report.lyric_sections.map(s => ({ ...s, label: displayVocalLabel(s.display_label || s.label),
      labelUnverified: s.labels === 'unverified', colorKey: s.group || s.label })), duration, s => [s.raw_start_s, s.raw_end_s]);
  });
  make({ key: 'major', name: 'Major rhythmic sections', radius: [146, 202], source: 'report.json → rhythmic_sections · raw grid', hasVoice }, ring => {
    requireRaw();
    if (!report.rhythmic_sections?.length) throw new Error('report.json has no rhythmic sections.');
    return timedRing(ring, report.rhythmic_sections.map(s => ({ ...s, label: `Rhythmic section ${s.rhythmic_section_id}`, colorKey: s.active_pattern_ids?.join('|') })), duration, s => [s.start_s, s.end_s]);
  });
  make({ key: 'detail', name: 'Instrumental pattern detail', radius: [84, 140], source: 'patterns.json → bars; beats.json → bars · raw grid' }, ring => {
    requireRaw();
    if (!patterns) throw new Error(files.patterns?.error || 'The per-instrument pattern data is unavailable.');
    if (!Array.isArray(patterns.patterns) || !Array.isArray(patterns.runs)) throw new Error('patterns.json does not supply both pattern definitions and runs. Detailed pattern segmentation is unavailable.');
    if (!patterns.patterns.length || !patterns.runs.length) throw new Error(`The backend supplied ${patterns.patterns.length} pattern definitions and ${patterns.runs.length} runs. ${Array.isArray(patterns.irregular_bars) ? patterns.irregular_bars.length + ' bars are marked irregular' : 'No irregular-bar count is supplied'}; no detailed pattern segmentation can be shown.`);
    if (!beats.bars?.length || !patterns.bars?.length) throw new Error('Measured bar times or per-bar pattern assignments are missing.');
    if (!report.rhythmic_sections?.length) throw new Error('The rhythmic sections are unavailable.');
    return timedRing(ring, report.rhythmic_sections.map(s => ({ ...s, label: `Rhythmic section ${s.rhythmic_section_id}`, colorKey: s.active_pattern_ids?.join('|') })), duration, s => [s.start_s, s.end_s]);
  });
  return { ...song, rings, report, beats, patterns, lyrics, layers, definitions, fileErrors };
}
