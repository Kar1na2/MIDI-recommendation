import { formatBoundary, formatTime } from './model.mjs';
import { finite, emotionValue, timedSection, sectionWindow, overlaps, selectedEmotionSections, missingLabel, curvePaths, corpusCallout } from './emotion.mjs';
import { readingWords, togetherLabel, lyricModels } from './emotion_readings.mjs';

const element = (tag, text, className) => {
  const result = document.createElement(tag);
  if (text != null) result.textContent = text;
  if (className) result.className = className;
  return result;
};
const svg = (tag, attributes) => {
  const result = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [name, value] of Object.entries(attributes)) result.setAttribute(name, value);
  return result;
};
const trusted = 'Models agreed sufficiently in the development checks. Human-validated accuracy has not been established.';
const provisional = 'Models agreed, but shared responses to pitch or brightness and missed singing sections limit interpretation. Human-validated accuracy has not been established.';
const displayedWindows = new WeakMap();
const readingContexts = new WeakMap();
const lanes = [
  { name: 'Lyrics', key: 'lyrics', subtitle: 'Positive versus negative wording', channels: ['words_valence_z'], models: ['words_valence'] },
  { name: 'Music', key: 'music', subtitle: 'Instrumental energy · calm to intense', channels: ['background_arousal_z'], models: ['background_arousal'] },
  { name: 'Voice', key: 'voice', subtitle: 'Experimental · vocal delivery', channels: ['voice_arousal'], models: ['voice_arousal'], experimental: true }
];

const sectionReading = (song, section) => song.emotionReadings?.sections?.[section?.section_id];
const signed = value => finite(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(2)}` : null;

function laneReading(song, section, lane) {
  const reading = sectionReading(song, section), words = readingWords(reading);
  const label = words[lane.key];
  if (!label) return finite(emotionValue(section, lane.channels[0])) ? 'Reading unavailable' : missingLabel(song, section, lane.channels[0]);
  return lane.key === 'music' && reading.musicTrend?.word ? `${label} · ${reading.musicTrend.word}` : label;
}

function appendTogether(host, song, section) {
  host.replaceChildren();
  const words = readingWords(sectionReading(song, section));
  if (words.together) host.append(element('p', togetherLabel, 'together-label'), element('p', words.together, 'together-words'));
  else if (!finite(section.words_valence_z)) host.append(element('p', `Lyrics unavailable for this section · ${missingLabel(song, section, 'words_valence_z')}. ${finite(section.background_arousal_z) ? `Music only — ${words.music || 'reading unavailable'}.` : 'Music is not scored.'}`, 'emotion-empty'));
  else if (!finite(section.background_arousal_z)) host.append(element('p', 'Music is not scored for this section.', 'emotion-empty'));
  else host.append(element('p', 'The combined reading is unavailable for this section.', 'emotion-empty'));
}

function updateReadingSummary(host, window, position = 0) {
  const context = readingContexts.get(host);
  if (!context) return;
  const { song } = context;
  const timed = (song.emotion?.sections || []).filter(timedSection).sort((a, b) => a.start_s - b.start_s || a.end_s - b.end_s);
  const selected = selectedEmotionSections(song.emotion, window).sort((a, b) => a.start_s - b.start_s || a.end_s - b.end_s);
  const exact = selected.find(s => window?.id === `emotion-${s.section_id}`);
  const atTime = timed.filter(s => s.start_s <= position && position < s.end_s);
  const section = exact || (window ? selected[0] : atTime.find(s => s.set === 'primary') || atTime[0]);
  const key = `${section?.section_id}|${window?.id}|${window?.start}|${window?.end}|${selected.length}`;
  if (context.key === key) return;
  context.key = key;
  const caption = host.querySelector('.emotion-reading-context');
  caption.textContent = section ? `${section.label} · ${formatTime(section.start_s)}–${formatTime(section.end_s)}${!exact && selected.length > 1 ? ` · ${selected.length} source sections overlap this window, with each reading in the detail panel` : ''}` : 'This time window has no scored emotion section.';
  for (const target of host.querySelectorAll('[data-reading-lane]')) {
    const lane = lanes.find(l => l.key === target.dataset.readingLane);
    target.textContent = section ? laneReading(song, section, lane) : missingLabel(song, null, lane.channels[0]);
  }
  const combined = host.querySelector('.reading-combined');
  combined.replaceChildren();
  if (section) appendTogether(combined, song, section);
}

function badge(experimental) {
  const result = element('span', experimental ? 'PROVISIONAL' : 'Trusted', 'trust-badge');
  result.tabIndex = 0;
  result.title = experimental ? provisional : trusted;
  result.setAttribute('aria-label', `${result.textContent}. ${result.title}`);
  result.append(element('span', result.title, 'trust-tooltip'));
  return result;
}

export function renderEmotion(host, song, corpus, choose) {
  host.replaceChildren();
  displayedWindows.delete(host);
  readingContexts.set(host, { song });
  host.append(element('h2', 'Emotion timeline'));
  host.append(element('p', null, 'emotion-reading-context'));
  const data = song.emotion;
  if (song.emotionError) {
    const message = element('p', song.emotionError, 'emotion-empty');
    message.setAttribute('role', 'status');
    host.append(message);
  }
  const timed = (data?.sections || []).filter(timedSection);
  const markers = song.emotionMeta?.geniusSections || {};
  for (const lane of lanes) {
    const article = element('section', null, `emotion-lane${lane.experimental ? ' experimental-voice' : ''}`);
    const heading = element('div', null, 'lane-heading');
    heading.append(element('h3', lane.name), badge(lane.experimental));
    article.append(heading, element('p', lane.subtitle, 'lane-subtitle'));
    const reading = element('p', null, 'lane-reading'); reading.dataset.readingLane = lane.key; article.append(reading);
    if (lane.experimental) article.append(element('p', 'Models agree, but may track pitch/brightness; the singing detector misses many sections.', 'voice-reason'));
    const chart = svg('svg', { viewBox: '0 0 720 84', preserveAspectRatio: 'none', class: 'emotion-chart', role: 'group', 'aria-label': `${lane.name} on the full song timeline` });
    chart.append(svg('line', { x1: 0, x2: 720, y1: 42, y2: 42, class: 'emotion-average' }));
    const values = timed.flatMap(s => lane.channels.map(ch => emotionValue(s, ch))).concat((data?.curves_1hz || []).flatMap(p => lane.channels.map(ch => emotionValue(p, ch)))).filter(finite);
    const extent = Math.max(1, ...values.map(Math.abs));
    for (const section of timed) {
      const window = sectionWindow(section);
      const start = Math.max(0, window.start), end = Math.min(song.duration, window.end);
      if (end <= start) continue;
      const category = markers[section.section_id];
      const band = svg('rect', { x: start / song.duration * 720, y: 0, width: (end - start) / song.duration * 720, height: 84,
        class: `emotion-band ${category || ''}${section.set === 'secondary' ? ' secondary' : ''}`, tabindex: 0, role: 'button',
        'data-emotion-start': window.start, 'data-emotion-end': window.end, 'data-emotion-id': section.section_id,
        'aria-label': `${lane.name}, ${section.label}, ${formatBoundary(window.start)} to ${formatBoundary(window.end)}${category ? ', Genius ' + category + ' label' : ''}. ${laneReading(song, section, lane)}` });
      const title = svg('title', {}); title.textContent = band.getAttribute('aria-label'); band.append(title);
      const select = () => choose({ key: 'emotion', name: 'Emotion section' }, window);
      band.addEventListener('click', select);
      band.addEventListener('keydown', event => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); select(); } });
      chart.append(band);
    }
    lane.channels.forEach((channel, index) => {
      // Background uses the supplied 1 Hz curve. Words and voice use section scores.
      if (channel === 'background_arousal_z' && data) {
        for (const d of curvePaths(data, channel, song.duration, 720, 84, extent)) {
          if (d.includes('L')) chart.append(svg('path', { d, class: 'emotion-line' }));
          else {
            const [cx, cy] = d.slice(1).split(',');
            chart.append(svg('circle', { cx, cy, r: 2, class: 'emotion-point' }));
          }
        }
      } else {
        for (const section of timed) {
          const value = emotionValue(section, channel);
          if (!finite(value)) continue;
          const y = 42 - value / extent * 35;
          chart.append(svg('line', { x1: Math.max(0, section.start_s) / song.duration * 720, x2: Math.min(song.duration, section.end_s) / song.duration * 720, y1: y, y2: y, class: `emotion-line${index ? ' mood-line' : ''}` }));
        }
      }
    });
    chart.append(svg('rect', { x: 0, y: 0, width: 0, height: 84, class: 'emotion-window', 'aria-hidden': 'true' }), svg('line', { x1: 0, x2: 0, y1: 0, y2: 84, class: 'emotion-playhead', 'aria-hidden': 'true' }));
    article.append(chart);
    const axis = element('div', null, 'emotion-axis'); axis.append(element('span', '0:00'), element('span', '300-song average · dotted line'), element('span', formatTime(song.duration))); article.append(axis);
    if (!values.length) article.append(element('p', lane.channels.map(ch => missingLabel(song, null, ch)).filter((label, i, labels) => labels.indexOf(label) === i).join(' · '), 'emotion-empty'));
    else article.append(element('p', lane.experimental ? 'The line shows vocal energy. Delivery descriptions are provisional.' : 'Select a section to read its wording, numbers and model agreement.', 'lane-help'));
    host.append(article);
  }
  host.append(element('div', null, 'reading-combined'));
  const labelled = timed.filter(s => markers[s.section_id]);
  if (labelled.length) {
    const nav = element('div', null, 'emotion-markers'); nav.setAttribute('aria-label', 'Genius section labels');
    nav.append(element('span', 'Genius labels'));
    for (const section of labelled) {
      const button = element('button', `${section.label} · ${formatTime(section.start_s)}`, markers[section.section_id]);
      button.addEventListener('click', () => choose({ key: 'emotion', name: 'Emotion section · Genius label' }, sectionWindow(section)));
      nav.append(button);
    }
    host.append(nav);
  }
  const untimed = (data?.sections || []).filter(s => !timedSection(s));
  if (untimed.length) host.append(element('p', `${untimed.length} source sections have no usable timing and are unavailable on the timeline.`, 'lane-help'));
  host.append(element('p', corpusCallout(corpus), 'corpus-callout'));
  const moodGuide = element('section', null, 'mood-reading-guide');
  moodGuide.append(element('h3', 'How to read the mood words'), element('p', 'Lyrics describe positive versus negative wording. Music describes calm versus intense instrumental energy. Together uses the standard valence × energy mood map. All are model readings, not listener feelings. Voice describes provisional delivery and is separate from the combined reading.'));
  host.append(moodGuide);
  const trust = element('details', null, 'emotion-trust');
  trust.append(element('summary', 'How to read this'));
  trust.append(element('p', 'These are existing model ratings of words, instrumental background and voice. Higher or lower means relative to primary sections in the random 300-song sample. The dotted line marks that average. Each lane scales to its supplied values.'));
  trust.append(element('p', 'Trusted describes model agreement in development checks. Human-validated accuracy has not been established. Voice is always provisional and experimental.'));
  trust.append(element('p', 'Gaps mean no available score. Instrumentals can have no lyric sections and no scored timeline. Secondary sections retain their supplied ratings and timing, with dashed section outlines. The background line contains supplied primary-section 1 Hz points only.'));
  trust.append(element('p', 'Selecting any lane or ring uses one playback window and highlights all overlapping source sections. Emotion and rhythm boundaries can differ. The detail panel lists the supplied emotion sections separately. Genius Chorus and Verse labels are marked in gold and green.'));
  trust.append(element('p', 'Mood is the model’s positive/negative rating of the material. H2 was not run.'));
  trust.append(element('p', 'The five wording and energy bands use primary-section consensus percentiles from the random 300 songs. Ties use the midpoint of their percentile range. Song ranks put higher scores first and share ranks for ties. Changes compare the immediately preceding timed section, with missing values retained.'));
  trust.append(element('p', 'Lyric agreement describes the spread of available T1, T3 and L1 scores. The widest corpus tercile is low agreement. Opposite signs with any model beyond 0.3 z also mean disagreement. At least two models are required. These display labels leave the saved channel trust verdicts intact.'));
  trust.append(element('p', 'Music builds or drops when the average supplied 1 Hz energy in the last third differs from the first third by at least 0.5 z. Both thirds need scored points.'));
  trust.append(element('span', 'Read the case study · coming later', 'case-study-placeholder'));
  host.append(trust);
  updateReadingSummary(host, null);
}

export function updateEmotionTime(host, state) {
  const x = state.duration ? state.position / state.duration * 720 : 0;
  for (const playhead of host.querySelectorAll('.emotion-playhead')) { playhead.setAttribute('x1', x); playhead.setAttribute('x2', x); }
  updateReadingSummary(host, state.window, state.position);
  if (displayedWindows.has(host) && displayedWindows.get(host) === state.window) return;
  displayedWindows.set(host, state.window);
  for (const overlay of host.querySelectorAll('.emotion-window')) {
    overlay.setAttribute('x', state.window && state.duration ? state.window.start / state.duration * 720 : 0);
    overlay.setAttribute('width', state.window && state.duration ? (state.window.end - state.window.start) / state.duration * 720 : 0);
  }
  for (const band of host.querySelectorAll('[data-emotion-id]')) {
    const active = Boolean(state.window && overlaps(state.window, { start: Number(band.dataset.emotionStart), end: Number(band.dataset.emotionEnd) }));
    band.classList.toggle('selected', active); band.setAttribute('aria-pressed', String(active));
  }
}

export function appendEmotionDetails(panel, song, window) {
  const matches = selectedEmotionSections(song.emotion, window);
  const group = element('section', null, 'emotion-details');
  group.append(element('h2', 'Emotion ratings'));
  if (!matches.length) group.append(element('p', song.emotionError || (song.report?.ring1_source === 'instrumental' ? 'No lyric text · no scored emotion section in this window.' : 'Not scored in this window.'), 'emotion-empty'));
  for (const section of matches) {
    const item = element('article', null, 'emotion-section-detail');
    item.append(element('h3', section.label), element('p', `${formatBoundary(section.start_s)} — ${formatBoundary(section.end_s)} · ${section.set} section`, 'window-time'));
    const category = song.emotionMeta?.geniusSections?.[section.section_id];
    if (category) item.append(element('p', `Genius ${category} label`, 'lane-help'));
    const reading = sectionReading(song, section);
    for (const lane of lanes) {
      const heading = element('div', null, 'lane-heading'); heading.append(element('h4', lane.name), badge(lane.experimental)); item.append(heading);
      if (lane.experimental) item.append(element('p', 'Experimental · models agree, but may track pitch/brightness; the singing detector misses many sections.', 'voice-reason'));
      item.append(element('p', laneReading(song, section, lane), 'emotion-reading'));
    }
    const together = element('div', null, 'reading-combined'); appendTogether(together, song, section); item.append(together);
    if (reading?.repeatedLyrics) item.append(element('p', `Repeated lyrics · same text fingerprint as ${reading.repeatedLyrics.label}.`, 'repeat-note'));
    const numbers = element('details', null, 'data-disclosure reading-numbers'); numbers.append(element('summary', 'Numbers and provenance'));
    const metrics = [
      ['Lyrics wording', 'words_valence_z', 'words_valence'], ['Music energy', 'background_arousal_z', 'background_arousal'],
      ['Voice delivery energy · PROVISIONAL', 'voice_arousal', 'voice_arousal'], ['Voice model valence score · PROVISIONAL', 'voice_valence', 'voice_valence']
    ];
    for (const [name, channel, modelKey] of metrics) {
      const value = emotionValue(section, channel), metric = reading?.channels?.[channel], count = section.available_models?.[modelKey];
      numbers.append(element('h4', name));
      numbers.append(element('p', finite(value) ? `${finite(metric?.percentile) ? `Percentile ${metric.percentile.toFixed(1)} · ` : ''}${signed(value)} z · ${Number.isInteger(count) ? count : 'unknown number of'} models` : missingLabel(song, section, channel)));
      if (metric?.rank) numbers.append(element('p', `Rank ${metric.rank} of ${metric.rankedSections} scored sections within this song.`));
      numbers.append(element('p', finite(metric?.changeFromPrevious) ? `${signed(metric.changeFromPrevious)} z since ${metric.previousSection.label}.` : 'Change from the previous section is unavailable.'));
    }
    numbers.append(element('h4', 'Lyric-model scores and agreement'));
    numbers.append(element('p', lyricModels.map(model => finite(reading?.wordsModels?.[model]) ? `${model} ${signed(reading.wordsModels[model])} z` : `${model} not scored`).join(' · ')));
    const agreement = reading?.wordsAgreement;
    numbers.append(element('p', agreement?.level ? `${agreement.label} · ${agreement.level} agreement · spread ${agreement.spread.toFixed(2)} z · corpus spread tercile ${agreement.spreadTercile} of 3 · ${agreement.models} of 3 models available.${agreement.oppositeSigns ? ' Opposite-sign rule applies.' : ''}` : 'Agreement unavailable.'));
    if (finite(reading?.musicTrend?.change)) numbers.append(element('p', `Music first-to-last-third change ${signed(reading.musicTrend.change)} z.`));
    else numbers.append(element('p', 'Music first-to-last-third change is unavailable.'));
    item.append(numbers);
    const experimental = element('details', null, 'data-disclosure'); experimental.append(element('summary', 'Experimental · background mood · untrusted'));
    const value = section.experimental?.background_valence, models = section.available_models?.background_valence;
    experimental.append(element('p', finite(value) ? `${signed(value)} z · ${Number.isInteger(models) ? models : 'unknown number of'} models. Background mood did not pass the supplied trust checks.` : 'Not scored.'));
    item.append(experimental); group.append(item);
  }
  panel.append(group);
}
