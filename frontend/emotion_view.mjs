import { formatBoundary, formatTime } from './model.mjs';
import { finite, emotionValue, timedSection, sectionWindow, overlaps, selectedEmotionSections, describeValue, missingLabel, curvePaths, corpusCallout } from './emotion.mjs';

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
const lanes = [
  { name: 'Words', subtitle: 'Lyric mood', channels: ['words_valence_z'], models: ['words_valence'], mood: [true] },
  { name: 'Background energy', subtitle: 'Instrumental energy', channels: ['background_arousal_z'], models: ['background_arousal'], mood: [false] },
  { name: 'Voice', subtitle: 'Experimental · energy and mood', channels: ['voice_arousal', 'voice_valence'], models: ['voice_arousal', 'voice_valence'], mood: [false, true], experimental: true }
];

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
  host.append(element('h2', 'Emotion timeline'));
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
        'aria-label': `${lane.name}, ${section.label}, ${formatBoundary(window.start)} to ${formatBoundary(window.end)}${category ? ', Genius ' + category + ' label' : ''}. ${lane.channels.map((ch, i) => finite(emotionValue(section, ch)) ? describeValue(emotionValue(section, ch), lane.mood[i]) : missingLabel(song, section, ch)).join('. ')}` });
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
    else article.append(element('p', lane.experimental ? 'Energy — solid · Mood — dashed. Select a section for its ratings.' : 'Select a section for its rating and model count.', 'lane-help'));
    host.append(article);
  }
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
  const trust = element('details', null, 'emotion-trust');
  trust.append(element('summary', 'How to read this'));
  trust.append(element('p', 'These are existing model ratings of words, instrumental background and voice. Higher or lower means relative to primary sections in the random 300-song sample. The dotted line marks that average. Each lane scales to its supplied values.'));
  trust.append(element('p', 'Trusted describes model agreement in development checks. Human-validated accuracy has not been established. Voice is always provisional and experimental.'));
  trust.append(element('p', 'Gaps mean no available score. Instrumentals can have no lyric sections and no scored timeline. Secondary sections retain their supplied ratings and timing, with dashed section outlines. The background line contains supplied primary-section 1 Hz points only.'));
  trust.append(element('p', 'Selecting any lane or ring uses one playback window and highlights all overlapping source sections. Emotion and rhythm boundaries can differ. The detail panel lists the supplied emotion sections separately. Genius Chorus and Verse labels are marked in gold and green.'));
  trust.append(element('p', 'Mood is the model’s positive/negative rating of the material. H2 was not run.'));
  trust.append(element('span', 'Read the case study · coming later', 'case-study-placeholder'));
  host.append(trust);
}

export function updateEmotionTime(host, state) {
  const x = state.duration ? state.position / state.duration * 720 : 0;
  for (const playhead of host.querySelectorAll('.emotion-playhead')) { playhead.setAttribute('x1', x); playhead.setAttribute('x2', x); }
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
    for (const lane of lanes) {
      const heading = element('div', null, 'lane-heading'); heading.append(element('h4', lane.name), badge(lane.experimental)); item.append(heading);
      if (lane.experimental) item.append(element('p', 'Experimental · models agree, but may track pitch/brightness; the singing detector misses many sections.', 'voice-reason'));
      lane.channels.forEach((channel, index) => {
        const value = emotionValue(section, channel), models = section.available_models?.[lane.models[index]];
        const rating = finite(value) ? `${describeValue(value, lane.mood[index])} (${value >= 0 ? '+' : ''}${value.toFixed(2)} z)` : missingLabel(song, section, channel);
        item.append(element('p', `${lane.mood[index] ? 'Mood' : 'Energy'} · ${rating} · ${Number.isInteger(models) ? models : 'unknown number of'} models`, 'emotion-rating'));
      });
    }
    const experimental = element('details', null, 'data-disclosure'); experimental.append(element('summary', 'Experimental · background mood · untrusted'));
    const value = section.experimental?.background_valence, models = section.available_models?.background_valence;
    experimental.append(element('p', finite(value) ? `${describeValue(value, true)} (${value >= 0 ? '+' : ''}${value.toFixed(2)} z) · ${Number.isInteger(models) ? models : 'unknown number of'} models. Background mood did not pass the supplied trust checks.` : 'Not scored.'));
    item.append(experimental); group.append(item);
  }
  panel.append(group);
}
