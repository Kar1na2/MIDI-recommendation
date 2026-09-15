/**
 * bepop - frontend app logic (Phase 5). Plain ES module, no build step.
 * Wires docs/data/{manifest,sequences,songs/<id>}.json + wavesurfer.js
 * (loaded globally via CDN script tags in index.html) + docs/js/alignment.js
 * (Phase 4's validated matching engine) into the UI described in the
 * Phase 5 brief. See docs/README.md for the overall structure.
 */
import { searchChordQueryAgainstCorpus, searchNoteQueryAgainstCorpus } from './alignment.js';

const MIN_SELECTION_SECONDS = 0.15; // "non-trivial range" threshold for finalizing a drag-selection
const MAX_SUGGESTIONS = 6;
const MAX_MATCHES_SHOWN = 8;
const SKIP_SECONDS = 5;
const REGION_COLOR = 'rgba(29, 185, 84, 0.25)';

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------
const el = {
  searchInput: document.getElementById('searchInput'),
  searchBtn: document.getElementById('searchBtn'),
  suggestions: document.getElementById('suggestions'),
  noMatches: document.getElementById('noMatches'),
  homeScreen: document.getElementById('homeScreen'),
  songView: document.getElementById('songView'),
  songTitle: document.getElementById('songTitle'),
  loadingNote: document.getElementById('loadingNote'),
  stage: document.getElementById('stage'),
  waveformBox: document.getElementById('waveformBox'),
  waveform: document.getElementById('waveform'),
  pitchOverlay: document.getElementById('pitchOverlay'),
  backBtn: document.getElementById('backBtn'),
  playPauseBtn: document.getElementById('playPauseBtn'),
  fwdBtn: document.getElementById('fwdBtn'),
  selectionHint: document.getElementById('selectionHint'),
  analysisPanel: document.getElementById('analysisPanel'),
  closePanelBtn: document.getElementById('closePanelBtn'),
  selectionRange: document.getElementById('selectionRange'),
  chordSequence: document.getElementById('chordSequence'),
  chordMatches: document.getElementById('chordMatches'),
  noteMatches: document.getElementById('noteMatches'),
};

// ---------------------------------------------------------------------------
// Global app state
// ---------------------------------------------------------------------------
const state = {
  manifest: [],           // [{song_id, title, duration}]
  manifestById: new Map(),
  sequences: {},           // full sequences.json, keyed by song_id
  ws: null,                 // current WaveSurfer instance
  regions: null,            // current Regions plugin instance
  activeRegion: null,
  currentSong: null,        // current songs/<id>.json
  highlightedIndex: -1,
};

// ---------------------------------------------------------------------------
// Boot: load manifest.json + sequences.json once, up front (per Phase 4's
// DATA_LAYER.md - sequences.json is "the only file the matching engine
// needs loaded", meant to be resident for instant search).
// ---------------------------------------------------------------------------
async function boot() {
  const [manifest, sequences] = await Promise.all([
    fetch('data/manifest.json').then((r) => r.json()),
    fetch('data/sequences.json').then((r) => r.json()),
  ]);
  state.manifest = manifest;
  state.sequences = sequences;
  for (const entry of manifest) state.manifestById.set(entry.song_id, entry);
  wireSearch();
  wireGlobalKeysAndClicks();
  window.__bepopReady = true; // signal for tests/debugging only
}
boot();

// ---------------------------------------------------------------------------
// Search (home screen suggestions + song switching - one persistent bar)
// ---------------------------------------------------------------------------
function currentMatches() {
  const q = el.searchInput.value.trim().toLowerCase();
  if (!q) return [];
  return state.manifest
    .filter((s) => s.title.toLowerCase().includes(q))
    .slice(0, MAX_SUGGESTIONS);
}

function renderSuggestions() {
  const q = el.searchInput.value.trim();
  const matches = currentMatches();
  state.highlightedIndex = matches.length ? 0 : -1;

  el.suggestions.innerHTML = '';
  if (!q) {
    el.suggestions.hidden = true;
    el.noMatches.hidden = true;
    return;
  }
  if (matches.length === 0) {
    el.suggestions.hidden = true;
    el.noMatches.hidden = false;
    return;
  }
  el.noMatches.hidden = true;
  matches.forEach((m, i) => {
    const li = document.createElement('li');
    li.textContent = m.title;
    li.dataset.songId = m.song_id;
    if (i === state.highlightedIndex) li.classList.add('active');
    li.addEventListener('mousedown', (e) => {
      // mousedown (not click) so this fires before the input's blur hides the list
      e.preventDefault();
      selectSong(m.song_id);
    });
    el.suggestions.appendChild(li);
  });
  el.suggestions.hidden = false;
}

function moveHighlight(delta) {
  const items = [...el.suggestions.querySelectorAll('li')];
  if (!items.length) return;
  state.highlightedIndex = (state.highlightedIndex + delta + items.length) % items.length;
  items.forEach((li, i) => li.classList.toggle('active', i === state.highlightedIndex));
}

function submitSearch() {
  const items = [...el.suggestions.querySelectorAll('li')];
  if (state.highlightedIndex >= 0 && items[state.highlightedIndex]) {
    selectSong(items[state.highlightedIndex].dataset.songId);
    return;
  }
  const matches = currentMatches();
  if (matches.length > 0) {
    selectSong(matches[0].song_id);
  } else if (el.searchInput.value.trim()) {
    el.suggestions.hidden = true;
    el.noMatches.hidden = false;
  }
}

function wireSearch() {
  el.searchInput.addEventListener('input', renderSuggestions);
  el.searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); moveHighlight(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); moveHighlight(-1); }
    else if (e.key === 'Enter') { e.preventDefault(); submitSearch(); }
    else if (e.key === 'Escape') { el.suggestions.hidden = true; el.noMatches.hidden = true; el.searchInput.blur(); }
  });
  el.searchBtn.addEventListener('click', submitSearch);
  el.searchInput.addEventListener('blur', () => {
    // small delay so a suggestion's mousedown can still register first
    setTimeout(() => { el.suggestions.hidden = true; el.noMatches.hidden = true; }, 120);
  });
  el.searchInput.addEventListener('focus', renderSuggestions);
}

async function selectSong(songId) {
  el.searchInput.value = '';
  el.suggestions.hidden = true;
  el.noMatches.hidden = true;
  el.searchInput.blur();
  await loadSong(songId);
}

// ---------------------------------------------------------------------------
// Song view: teardown + fresh load (switching songs never carries state over)
// ---------------------------------------------------------------------------
function teardownSong() {
  clearSelection();
  if (state.ws) {
    state.ws.destroy();
    state.ws = null;
  }
  state.regions = null;
  state.currentSong = null;
  const ctx = el.pitchOverlay.getContext('2d');
  ctx.clearRect(0, 0, el.pitchOverlay.width, el.pitchOverlay.height);
}

async function loadSong(songId, { seekTo = null } = {}) {
  teardownSong();

  el.homeScreen.hidden = true;
  el.songView.hidden = false;
  el.songTitle.textContent = state.manifestById.get(songId)?.title ?? songId;
  el.loadingNote.hidden = false;

  const song = await fetch(`data/songs/${songId}.json`).then((r) => r.json());
  state.currentSong = song;
  el.songTitle.textContent = song.title;

  const ws = WaveSurfer.create({
    container: el.waveform,
    height: 140,
    waveColor: '#4d4d4d',
    progressColor: '#7a7a7a',
    cursorColor: '#1DB954',
    barWidth: 2,
    barGap: 1,
    barRadius: 2,
    url: song.audio_url,
  });
  state.ws = ws;
  const regions = ws.registerPlugin(WaveSurfer.Regions.create());
  state.regions = regions;
  // exposed for debugging/tests only - not read by any app logic
  window.__bepopWs = ws;
  window.__bepopRegions = regions;

  const decodeStart = performance.now();
  ws.on('ready', () => {
    el.loadingNote.hidden = true;
    const decodeMs = performance.now() - decodeStart;
    console.log(`[bepop] "${song.title}" decoded client-side in ${decodeMs.toFixed(0)}ms`);
    drawPitchOverlay();
    if (seekTo != null) ws.setTime(Math.max(0, Math.min(song.duration, seekTo)));
  });
  ws.on('play', () => { el.playPauseBtn.innerHTML = '&#10074;&#10074;'; });
  ws.on('pause', () => { el.playPauseBtn.innerHTML = '&#9658;'; });
  ws.on('finish', () => { el.playPauseBtn.innerHTML = '&#9658;'; });

  regions.enableDragSelection({ color: REGION_COLOR, minLength: 0.05 }, 3);
  regions.on('region-created', (region) => onRegionFinalized(region));
  regions.on('region-updated', (region) => {
    if (region === state.activeRegion) runAnalysis(region.start, region.end);
  });

  // draw the pitch overlay once more on container resize (responsive layout,
  // and the flex width change when the analysis panel opens/closes)
  window.addEventListener('resize', drawPitchOverlay);
  new ResizeObserver(() => drawPitchOverlay()).observe(el.waveformBox);
}

function onRegionFinalized(region) {
  if (state.activeRegion && state.activeRegion !== region) {
    state.activeRegion.remove();
  }
  const duration = region.end - region.start;
  if (duration < MIN_SELECTION_SECONDS) {
    region.remove();
    return;
  }
  state.activeRegion = region;
  runAnalysis(region.start, region.end);
  openPanel();
}

// ---------------------------------------------------------------------------
// Transport controls
// ---------------------------------------------------------------------------
el.playPauseBtn.addEventListener('click', () => state.ws && state.ws.playPause());
el.backBtn.addEventListener('click', () => state.ws && state.ws.skip(-SKIP_SECONDS));
el.fwdBtn.addEventListener('click', () => state.ws && state.ws.skip(SKIP_SECONDS));

// ---------------------------------------------------------------------------
// Pitch overlay (client-side, drawn from the song's note list - no
// precomputed peak/pitch data per Phase 4)
// ---------------------------------------------------------------------------
function drawPitchOverlay() {
  const song = state.currentSong;
  if (!song) return;
  const canvas = el.pitchOverlay;
  const box = el.waveformBox;
  const dpr = window.devicePixelRatio || 1;
  const cssWidth = el.waveform.clientWidth || box.clientWidth - 32;
  const cssHeight = 140;
  canvas.width = Math.max(1, Math.round(cssWidth * dpr));
  canvas.height = Math.max(1, Math.round(cssHeight * dpr));
  canvas.style.width = cssWidth + 'px';
  canvas.style.height = cssHeight + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssWidth, cssHeight);

  const notes = song.notes;
  if (!notes.length) return;
  let minPitch = Infinity, maxPitch = -Infinity;
  for (const n of notes) { if (n.pitch < minPitch) minPitch = n.pitch; if (n.pitch > maxPitch) maxPitch = n.pitch; }
  const range = Math.max(1, maxPitch - minPitch);
  const marginTop = cssHeight * 0.08, marginBottom = cssHeight * 0.08;
  const usable = cssHeight - marginTop - marginBottom;
  const x = (t) => (t / song.duration) * cssWidth;
  const y = (pitch) => cssHeight - marginBottom - ((pitch - minPitch) / range) * usable;

  ctx.strokeStyle = 'rgba(29, 185, 84, 0.55)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  notes.forEach((n, i) => {
    const px = x(n.onset), py = y(n.pitch);
    if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
  });
  ctx.stroke();

  ctx.fillStyle = '#1DB954';
  for (const n of notes) {
    ctx.beginPath();
    ctx.arc(x(n.onset), y(n.pitch), 1.6, 0, Math.PI * 2);
    ctx.fill();
  }
}

// ---------------------------------------------------------------------------
// Selection & analysis panel
// ---------------------------------------------------------------------------
function openPanel() {
  el.stage.classList.add('panelOpen');
  el.analysisPanel.hidden = false;
  el.selectionHint.hidden = true;
}

function closePanel() {
  el.stage.classList.remove('panelOpen');
  el.analysisPanel.hidden = true;
  el.selectionHint.hidden = false;
}

function clearSelection() {
  if (state.activeRegion) {
    state.activeRegion.remove();
    state.activeRegion = null;
  }
  closePanel();
}

el.closePanelBtn.addEventListener('click', clearSelection);

function fmtTime(t) {
  const m = Math.floor(t / 60);
  const s = (t - m * 60).toFixed(1);
  return `${m}:${s.padStart(4, '0')}`;
}

function runAnalysis(start, end) {
  const song = state.currentSong;
  const songId = song.song_id;
  const seq = state.sequences[songId];

  el.selectionRange.textContent = `${fmtTime(start)} – ${fmtTime(end)}`;

  // -- human-readable chord sequence for the selection (songs/<id>.json
  //    already carries chord_name; no need to re-derive it client-side) --
  const chordsInRange = song.chords.filter((c) => c.end > start && c.start < end);
  el.chordSequence.textContent = chordsInRange.length
    ? chordsInRange.map((c) => c.chord_name).join(' → ')
    : '(no chord data in this range)';

  // -- chord-progression query (sequences.json's compact form: id + root) --
  const chordQuery = seq.chords
    .filter((c) => c.end > start && c.start < end)
    .map((c) => ({ canonical_chord_id: c.canonical_chord_id, root_pc: c.root_pc }));

  // -- melodic-interval query (drop the null "first note of song" marker) --
  const noteQuery = seq.notes
    .filter((n) => n.onset >= start && n.onset < end && n.interval_from_prev !== null)
    .map((n) => n.interval_from_prev);

  const chordResults = chordQuery.length
    ? searchChordQueryAgainstCorpus(chordQuery, state.sequences, { excludeSongId: songId })
    : null;
  const noteResults = noteQuery.length
    ? searchNoteQueryAgainstCorpus(noteQuery, state.sequences, { excludeSongId: songId })
    : null;

  renderMatchList(el.chordMatches, chordResults, 'chord');
  renderMatchList(el.noteMatches, noteResults, 'note');
}

function jumpToMatch(songId, mode, span) {
  const seq = state.sequences[songId];
  const startIdx = span[0];
  let t = 0;
  if (mode === 'chord') {
    const arr = seq.chords;
    t = arr[Math.min(startIdx, arr.length - 1)]?.start ?? 0;
  } else {
    const arr = seq.notes;
    t = arr[Math.min(startIdx, arr.length - 1)]?.onset ?? 0;
  }
  selectSong(songId).then(() => {
    // selectSong -> loadSong doesn't accept opts; re-seek once ready instead
    if (state.ws) state.ws.once('ready', () => state.ws.setTime(t));
  });
}

function renderMatchList(listEl, results, mode) {
  listEl.innerHTML = '';
  if (results === null) {
    const li = document.createElement('li');
    li.className = 'emptyNote';
    li.textContent = 'No ' + (mode === 'chord' ? 'chords' : 'notes') + ' in this selection.';
    listEl.appendChild(li);
    return;
  }
  const shown = results.slice(0, MAX_MATCHES_SHOWN).filter((r) => r.score > 0);
  if (shown.length === 0) {
    const li = document.createElement('li');
    li.className = 'emptyNote';
    li.textContent = 'No meaningful matches found.';
    listEl.appendChild(li);
    return;
  }
  shown.forEach((r) => {
    const li = document.createElement('li');
    const title = state.manifestById.get(r.song_id)?.title ?? r.song_id;
    const titleSpan = document.createElement('span');
    titleSpan.className = 'matchTitle';
    titleSpan.textContent = title;
    const scoreSpan = document.createElement('span');
    scoreSpan.className = 'matchScore';
    scoreSpan.textContent = r.score.toFixed(2);
    li.appendChild(titleSpan);
    li.appendChild(scoreSpan);
    li.title = 'Jump to the matched moment';
    li.addEventListener('click', () => jumpToMatch(r.song_id, mode, r.span));
    listEl.appendChild(li);
  });
}

// ---------------------------------------------------------------------------
// Escape / click-outside clears the selection + collapses the panel
// ---------------------------------------------------------------------------
function wireGlobalKeysAndClicks() {
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && state.activeRegion) clearSelection();
  });
  document.addEventListener('click', (e) => {
    if (!state.activeRegion) return;
    const insidePanel = el.analysisPanel.contains(e.target);
    const insideRegion = state.activeRegion.element && state.activeRegion.element.contains(e.target);
    if (!insidePanel && !insideRegion) clearSelection();
  });
}
