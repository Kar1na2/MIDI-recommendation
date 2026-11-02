const SVG = 'http://www.w3.org/2000/svg';
const PITCH_NAME = ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B'];
const PLOT = { width: 1080, height: 260, left: 54, right: 22, top: 18, bottom: 28 };

const fmt = (seconds) => {
  const centiseconds = Math.round(seconds * 100);
  const whole = Math.floor(centiseconds / 100);
  const hundredths = centiseconds % 100;
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}${hundredths ? `.${String(hundredths).padStart(2, '0')}` : ''}`;
};
const secRange = (start, end) => `${Number(start).toFixed(2)}–${Number(end).toFixed(2)}s`;
const noteName = (pitch) => `${PITCH_NAME[pitch % 12]}${Math.floor(pitch / 12) - 1}`;
const element = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

const playback = { stop: () => {} };
function claimPlayback(stop) {
  playback.stop();
  playback.stop = stop;
}
function renderRankedList(clips, original) {
  const list = document.getElementById('rankedList');
  const selected = document.getElementById('selectedCandidate');
  const buttons = [];
  const choose = (clip, button) => {
    playback.stop();
    buttons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    selected.replaceChildren();
    const summary = element('div', 'candidate-summary');
    summary.append(element('span', 'section-index', `Rank ${String(clip.rank).padStart(2, '0')} of 15`), element('h3', '', clip.title));
    summary.append(element('p', '', `${clip.source} · source MIDI ${secRange(clip.passage_start, clip.passage_end)}`));
    const peaks = element('div', 'peak-sequence');
    clip.apex.forEach((pitch, index) => {
      if (index) peaks.append(element('span', 'peak-arrow', index === 1 ? '↗' : '↘'));
      const peak = element('span', `peak peak-${index + 1}`, noteName(pitch));
      peak.append(element('small', '', ['first', 'higher', 'middle'][index]));
      peaks.append(peak);
    });
    summary.append(peaks, element('p', 'count-description', `${clip.counts.join(' · ')} notes per rise · ${clip.counts.every(count => count === clip.counts[0]) ? 'equal counts' : 'unequal counts'} · no skipped attack group in the screened line`));
    selected.append(summary, renderMidiCard(clip));
    const compare = element('div', 'quick-compare');
    compare.append(element('h4', '', 'Listen back to the original'));
    renderLocalAudio(original, compare);
    selected.append(compare);
    const sources = element('div', 'candidate-sources');
    for (const [label, url] of [['Score and source ↗', clip.source_url], ['Source MIDI ↗', clip.midi_url]]) {
      const link = element('a', '', label); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; sources.append(link);
    }
    sources.append(element('span', '', `Mutopia edition: ${clip.maintainer} · ${clip.license}. Extracted and time-shifted excerpt data follows the same license.`));
    selected.append(sources);
  };
  for (const clip of clips) {
    const button = element('button', 'candidate-button');
    button.type = 'button'; button.setAttribute('aria-pressed', 'false');
    const copy = element('span', 'candidate-copy');
    copy.append(element('span', 'candidate-title', clip.title), element('small', '', `${clip.counts.join(' · ')} notes · ${clip.source.split(' · ')[0]}`));
    button.append(element('span', 'rank-number', String(clip.rank).padStart(2, '0')), copy);
    button.addEventListener('click', () => choose(clip, button));
    buttons.push(button); list.append(button);
  }
  choose(clips[0], buttons[0]);
}

function svgNode(name, attrs = {}, text = '') {
  const node = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text) node.textContent = text;
  return node;
}

function createRoll(clip) {
  const svg = svgNode('svg', {
    viewBox: `0 0 ${PLOT.width} ${PLOT.height}`,
    class: 'piano-roll',
    role: 'img',
    'aria-label': `MIDI piano roll for ${clip.title}, excerpt ${fmt(clip.clip_start)} to ${fmt(clip.clip_end)}`,
    preserveAspectRatio: 'none',
  });
  const { width, height, left, right, top, bottom } = PLOT;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const minPitch = Math.floor(Math.min(...clip.notes.map((note) => note.pitch)) / 12) * 12;
  const maxPitch = (Math.floor(Math.max(...clip.notes.map((note) => note.pitch)) / 12) + 1) * 12;
  const pitchSpan = Math.max(maxPitch - minPitch, 12);
  const x = (seconds) => left + (seconds / clip.duration) * plotWidth;
  const y = (pitch) => top + ((maxPitch - pitch) / pitchSpan) * plotHeight;

  svg.append(svgNode('rect', {
    x: x(clip.passage_start_relative), y: top,
    width: x(clip.passage_end_relative) - x(clip.passage_start_relative),
    height: plotHeight, class: 'excerpt-highlight',
  }));
  for (const edge of [clip.passage_start_relative, clip.passage_end_relative]) {
    svg.append(svgNode('line', { x1: x(edge), x2: x(edge), y1: top, y2: height - bottom, class: 'excerpt-edge' }));
  }

  for (let pitch = minPitch; pitch <= maxPitch; pitch += 1) {
    const lineClass = pitch % 12 === 0 ? 'octave-line' : 'grid-line';
    svg.append(svgNode('line', { x1: left, x2: width - right, y1: y(pitch), y2: y(pitch), class: lineClass }));
    if (pitch % 12 === 0) {
      svg.append(svgNode('text', { x: left - 10, y: y(pitch) + 3, 'text-anchor': 'end' }, noteName(pitch)));
    }
  }

  const secStep = clip.duration > 10 ? 2 : 1;
  for (let sec = 0; sec <= clip.duration + 0.001; sec += secStep) {
    const px = x(sec);
    svg.append(svgNode('line', { x1: px, x2: px, y1: top, y2: height - bottom, class: 'grid-line' }));
    svg.append(svgNode('text', { x: px, y: height - 8, 'text-anchor': 'middle' }, `${sec}s`));
  }

  for (const note of clip.notes) {
    const px = x(note.start);
    const py = y(note.pitch) - Math.max(3.3, plotHeight / pitchSpan * 0.34);
    const noteWidth = Math.max(2.1, x(note.end) - px);
    const noteHeight = Math.max(3.3, plotHeight / pitchSpan * 0.68);
    const shape = svgNode('rect', { x: px, y: py, width: noteWidth, height: noteHeight, rx: 2, class: 'note' });
    shape.append(svgNode('title', {}, `${noteName(note.pitch)} · ${fmt(clip.clip_start + note.start - (clip.leading_pad || 0))}–${fmt(clip.clip_start + note.end - (clip.leading_pad || 0))}`));
    if (clip.kind === 'reference') shape.setAttribute('class', 'note context-note');
    svg.append(shape);
  }
  for (const note of clip.motif_notes || []) {
    svg.append(svgNode('rect', { x: x(note.start), y: y(note.pitch) - 3, width: Math.max(3, x(note.end) - x(note.start)), height: 6, rx: 2, class: `motif-note run-${note.run}` }));
  }
  const rollHead = svgNode('line', {x1: x(clip.passage_start_relative), x2: x(clip.passage_start_relative), y1: top, y2: height-bottom, class: 'roll-playhead', visibility: 'hidden'});
  svg.append(rollHead);
  svg.updatePosition = (position) => { rollHead.setAttribute('visibility', position === null ? 'hidden' : 'visible'); if (position !== null) { rollHead.setAttribute('x1', x(position)); rollHead.setAttribute('x2', x(position)); } };
  svg.append(svgNode('line', { x1: left, x2: width - right, y1: height - bottom, y2: height - bottom, class: 'axis-line' }));
  return svg;
}

function renderMidiCard(clip) {
  const card = element('article', 'clip-card');
  card.dataset.kind = clip.kind;
  const head = element('div', 'clip-card-head');
  const titleWrap = element('div', 'clip-title-wrap');
  titleWrap.append(element('span', 'kind-tag', clip.label));
  titleWrap.append(element('h3', '', clip.title));
  titleWrap.append(element('p', '', clip.source));
  const time = element('span', 'time-chip', `View ${secRange(clip.clip_start, clip.clip_end)} · excerpt ${secRange(clip.passage_start, clip.passage_end)}`);
  head.append(titleWrap, time);
  const roll = createRoll(clip);
  card.append(head, roll);
  const legend = element('div', 'clip-legend');
  legend.append(element('span', 'legend-swatch'), document.createTextNode('MIDI note'),
    element('span', 'legend-focus'), document.createTextNode('excerpt'),
    element('span', 'count-pill', `${clip.note_count} note attacks in view`));
  card.append(legend);
  const player = element('div', 'midi-preview-wrap');
  renderMidiAudio(clip, player, roll);
  card.append(player);
  return card;
}

function createScrub(duration) {
  const scrub = element('div', 'scrub');
  scrub.tabIndex = 0;
  scrub.setAttribute('role', 'slider');
  scrub.setAttribute('aria-label', 'Excerpt playback position');
  scrub.setAttribute('aria-valuemin', '0');
  scrub.setAttribute('aria-valuemax', String(Math.round(duration)));
  scrub.setAttribute('aria-valuenow', '0');
  const playhead = element('span', 'playhead');
  playhead.style.left = '0%';
  scrub.append(playhead);
  return { scrub, playhead };
}

function renderLocalAudio(clip, body) {
  const audio = element('audio', 'clip-audio');
  audio.src = clip.audio.src;
  audio.preload = 'metadata';
  audio.setAttribute('aria-label', `${clip.title}, playback from ${fmt(clip.audio.play_start)} to ${fmt(clip.audio.play_end)}`);
  const row = element('div', 'player-row');
  const play = element('button', 'play-button', '▶');
  play.type = 'button';
  play.setAttribute('aria-label', `Play ${clip.title} excerpt`);
  const label = element('span', 'play-label', 'Play excerpt');
  const playStart = clip.audio.play_start;
  const playEnd = clip.audio.play_end;
  const playDuration = playEnd - playStart;
  const timeline = createScrub(playDuration);
  const time = element('span', 'time-readout', `0:00 / ${fmt(playDuration)}`);
  row.append(play, label, timeline.scrub, time);
  const update = () => {
    const elapsed = Math.max(0, Math.min(playDuration, audio.currentTime - playStart));
    timeline.playhead.style.left = `${elapsed / playDuration * 100}%`;
    time.textContent = `${fmt(elapsed)} / ${fmt(playDuration)}`;
    timeline.scrub.setAttribute('aria-valuenow', String(Math.floor(elapsed)));
  };
  play.addEventListener('click', async () => {
    if (!audio.paused) {
      audio.pause();
      return;
    }
    if (audio.currentTime < playStart || audio.currentTime >= playEnd) audio.currentTime = playStart;
    claimPlayback(() => audio.pause());
    try { await audio.play(); } catch { label.textContent = 'Playback unavailable'; }
  });
  audio.addEventListener('play', () => { play.textContent = 'Ⅱ'; label.textContent = 'Pause excerpt'; });
  audio.addEventListener('pause', () => { play.textContent = '▶'; label.textContent = 'Play excerpt'; });
  audio.addEventListener('timeupdate', () => {
    update();
    if (audio.currentTime >= playEnd) {
      audio.pause();
      audio.currentTime = playEnd;
    }
  });
  audio.addEventListener('loadedmetadata', update);
  timeline.scrub.addEventListener('click', (event) => {
    const rect = timeline.scrub.getBoundingClientRect();
    const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    audio.currentTime = playStart + fraction * (playEnd - playStart);
    update();
  });
  timeline.scrub.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'Home') audio.currentTime = playStart;
    else if (event.key === 'End') audio.currentTime = playEnd;
    else audio.currentTime = Math.max(playStart, Math.min(playEnd, audio.currentTime + (event.key === 'ArrowRight' ? 1 : -1)));
    update();
  });
  body.append(audio, row, element('span', 'audio-window-label', `Local audio · starts at excerpt ${fmt(playStart)}, stops after its 3 s tail at ${fmt(playEnd)}`));
}

function renderMidiAudio(clip, body, roll) {
  const controls = element('div', 'preview-options');
  const mode = element('select', 'preview-mode');
  mode.setAttribute('aria-label', 'Preview voice');
  for (const [value, label] of [['score', 'Full score'], ['motif', 'Rising line only']]) {
    if (value === 'motif' && !clip.motif_notes?.length) continue;
    const option = element('option', '', label); option.value = value; mode.append(option);
  }
  controls.append(mode);
  const row = element('div', 'player-row midi-player');
  const play = element('button', 'play-button', '▶'); play.type = 'button';
  play.setAttribute('aria-label', `Play score excerpt for ${clip.title}`);
  const label = element('span', 'play-label', 'Play excerpt');
  const duration = clip.duration - clip.passage_start_relative;
  const timeline = createScrub(duration);
  timeline.scrub.setAttribute('role', 'progressbar'); timeline.scrub.removeAttribute('tabindex');
  const time = element('span', 'time-readout', `0:00 / ${fmt(duration)}`);
  row.append(play, label, timeline.scrub, time);
  let context, frame, master, active = false, started = 0, tones = [];
  const stop = () => {
    active = false; cancelAnimationFrame(frame);
    tones.forEach(tone => { try { tone.stop(); } catch {} }); tones = [];
    master?.disconnect();
    play.textContent = '▶'; label.textContent = 'Play excerpt';
    roll?.updatePosition(null);
  };
  mode.addEventListener('change', stop);
  play.addEventListener('click', async () => {
    if (active) { stop(); return; }
    claimPlayback(stop);
    try {
      context ??= new (window.AudioContext || window.webkitAudioContext)();
      await context.resume(); active = true;
      const start = context.currentTime + .06; started = start;
      master = context.createGain(); master.gain.value = .32; master.connect(context.destination);
      const notes = mode.value === 'motif' ? clip.motif_notes : clip.notes;
      for (const note of notes) {
        if (note.start < clip.passage_start_relative - .001 || note.start >= clip.duration) continue;
        const at = start + note.start - clip.passage_start_relative;
        const end = Math.min(start + duration, at + Math.max(.08, note.end - note.start) + .15);
        const envelope = context.createGain(); envelope.connect(master);
        const velocity = Math.max(.015, Math.min(.07, (note.velocity || 80) / 1600));
        envelope.gain.setValueAtTime(.0001, at);
        envelope.gain.exponentialRampToValueAtTime(velocity, at + .008);
        envelope.gain.exponentialRampToValueAtTime(.0001, Math.max(at + .04, end));
        const tone = context.createOscillator(); tone.type = 'triangle';
        tone.frequency.value = 440 * 2 ** ((note.pitch - 69) / 12);
        tone.connect(envelope); tone.start(at); tone.stop(end); tones.push(tone);
      }
      play.textContent = 'Ⅱ'; label.textContent = 'Stop excerpt';
      const tick = () => {
        if (!active) return;
        const elapsed = Math.max(0, Math.min(duration, context.currentTime - started));
        timeline.playhead.style.left = `${elapsed / duration * 100}%`;
        time.textContent = `${fmt(elapsed)} / ${fmt(duration)}`;
        timeline.scrub.setAttribute('aria-valuenow', String(Math.floor(elapsed)));
        roll?.updatePosition(clip.passage_start_relative + elapsed);
        if (elapsed >= duration) stop(); else frame = requestAnimationFrame(tick);
      };
      tick();
    } catch { stop(); label.textContent = 'Audio unavailable'; }
  });
  body.append(controls, row, element('span', 'audio-window-label', 'Score MIDI synthesis · excerpt start → three-second tail · Play restarts the excerpt'));
}

async function init() {
  try {
    const response = await fetch('data/experiment/arpeggio_clips.json?v=classical2');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const clips = await response.json();
    const original = clips.find(clip => clip.kind === 'target');
    renderLocalAudio(original, document.getElementById('originalAudio'));
    document.getElementById('originalMidi').append(renderMidiCard(original));
    renderRankedList(clips.filter(clip => clip.kind === 'reference'), original);
  } catch (error) {
    document.getElementById('selectedCandidate').append(element('p', 'load-error', `The comparison data could not load. ${error.message}`));
  }
}
init();
