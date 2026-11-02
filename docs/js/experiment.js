const SVG = 'http://www.w3.org/2000/svg';
const PITCH_NAME = ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B'];
const PLOT = { width: 1080, height: 260, left: 54, right: 22, top: 18, bottom: 28 };

const fmt = (seconds) => {
  const whole = Math.floor(seconds);
  const hundredths = Math.round((seconds - whole) * 100);
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
    shape.append(svgNode('title', {}, `${noteName(note.pitch)} · ${fmt(clip.clip_start + note.start)}–${fmt(clip.clip_start + note.end)}`));
    svg.append(shape);
  }
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
  card.append(head, createRoll(clip));
  const legend = element('div', 'clip-legend');
  legend.append(element('span', 'legend-swatch'), document.createTextNode('MIDI note'),
    element('span', 'legend-focus'), document.createTextNode('excerpt'),
    element('span', 'count-pill', `${clip.note_count} note attacks in view`));
  card.append(legend);
  const player = element('div', 'midi-preview-wrap');
  renderMidiAudio(clip, player);
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

function renderMidiAudio(clip, body) {
  const row = element('div', 'player-row midi-player');
  const play = element('button', 'play-button', '▶');
  play.type = 'button';
  play.setAttribute('aria-label', `Play MIDI preview for ${clip.title}`);
  const label = element('span', 'play-label', 'Play MIDI preview');
  const playDuration = clip.duration - clip.passage_start_relative;
  const timeline = createScrub(playDuration);
  timeline.scrub.setAttribute('role', 'progressbar');
  timeline.scrub.removeAttribute('tabindex');
  const time = element('span', 'time-readout', `0:00 / ${fmt(playDuration)}`);
  row.append(play, label, timeline.scrub, time);
  let context;
  let timer;
  let startedAt = 0;
  let activeNodes = [];
  const stop = () => {
    clearTimeout(timer);
    for (const node of activeNodes) {
      try { node.stop(); } catch { /* already stopped */ }
    }
    activeNodes = [];
    play.textContent = '▶';
    label.textContent = 'Play MIDI preview';
  };
  const draw = (position) => {
    timeline.playhead.style.left = `${Math.max(0, Math.min(1, position / playDuration)) * 100}%`;
    time.textContent = `${fmt(Math.min(position, playDuration))} / ${fmt(playDuration)}`;
    timeline.scrub.setAttribute('aria-valuenow', String(Math.floor(position)));
  };
  play.addEventListener('click', async () => {
    if (activeNodes.length) { stop(); return; }
    context ??= new AudioContext();
    await context.resume();
    const excerptStart = clip.passage_start_relative;
    const startAt = context.currentTime + 0.06;
    startedAt = performance.now();
    const master = context.createGain();
    master.gain.value = 0.48;
    master.connect(context.destination);
    for (const note of clip.notes) {
      if (note.end <= excerptStart) continue;
      const onset = startAt + Math.max(0, note.start - excerptStart);
      const offset = startAt + Math.max(0.07, note.end - excerptStart);
      const envelope = context.createGain();
      envelope.gain.setValueAtTime(0.0001, onset);
      envelope.gain.linearRampToValueAtTime(0.042, onset + 0.012);
      envelope.gain.setTargetAtTime(0.017, onset + 0.012, 0.08);
      envelope.gain.setTargetAtTime(0.0001, offset, 0.11);
      envelope.connect(master);
      const fundamental = 440 * (2 ** ((note.pitch - 69) / 12));
      const tone = context.createOscillator();
      tone.type = 'triangle';
      tone.frequency.value = fundamental;
      tone.connect(envelope);
      tone.start(onset);
      tone.stop(offset + 0.42);
      activeNodes.push(tone);
    }
    play.textContent = 'Ⅱ';
    label.textContent = 'Pause preview';
    const tick = () => {
      const elapsed = Math.min(playDuration, (performance.now() - startedAt) / 1000);
      draw(elapsed);
      if (elapsed >= playDuration) stop();
      else timer = setTimeout(tick, 80);
    };
    tick();
  });
  body.append(row, element('span', 'audio-window-label', `Synthesized from MIDI · starts at excerpt and plays through 3 s of tail`));
}

function renderYoutubeAudio(clip, body) {
  const start = Math.round(clip.audio.play_start);
  const end = Math.round(clip.audio.play_end);
  const frame = document.createElement('iframe');
  frame.className = 'youtube-frame';
  frame.title = `${clip.title} performance audio excerpt`;
  frame.loading = 'lazy';
  frame.referrerPolicy = 'strict-origin-when-cross-origin';
  frame.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share';
  frame.allowFullscreen = true;
  frame.src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(clip.audio.video_id)}?start=${start}&end=${end}&controls=1&rel=0&playsinline=1`;
  const link = element('a', 'audio-window-label', `Open performance audio · excerpt ${secRange(clip.audio.play_start, clip.audio.play_end)} (performance timing may differ)`);
  link.href = `https://www.youtube.com/watch?v=${encodeURIComponent(clip.audio.video_id)}&t=${start}s`;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  body.append(frame, link);
}

function renderAudioCard(clip) {
  const card = element('article', 'audio-card');
  const head = element('div', 'audio-card-head');
  head.append(element('span', 'kind-tag', clip.kind === 'target' ? 'Original audio' : clip.kind === 'reference' ? 'Score reference audio' : 'Unconfirmed candidate audio'));
  head.append(element('h3', '', clip.title));
  const audioWindow = clip.audio.type === 'youtube'
    ? `MIDI passage ${secRange(clip.passage_start, clip.passage_end)} · video excerpt ${secRange(clip.audio.play_start, clip.audio.play_end)}`
    : `MIDI passage ${secRange(clip.passage_start, clip.passage_end)} · audio stops at ${fmt(clip.audio.play_end)}`;
  head.append(element('p', '', audioWindow));
  const body = element('div', 'audio-card-body');
  if (clip.audio.type === 'local') renderLocalAudio(clip, body);
  else renderYoutubeAudio(clip, body);
  card.append(head, body);
  return card;
}

async function init() {
  const cards = document.getElementById('midiCards');
  const audioCards = document.getElementById('audioCards');
  try {
    const response = await fetch('data/experiment/arpeggio_clips.json');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const clips = await response.json();
    for (const clip of clips) {
      cards.append(renderMidiCard(clip));
      audioCards.append(renderAudioCard(clip));
    }
  } catch (error) {
    cards.append(element('p', 'load-error', `The excerpt data could not be loaded. Open this page through a local static server. (${error.message})`));
  }
}

init();
