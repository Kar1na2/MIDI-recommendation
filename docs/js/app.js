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
const TOP_MATCHES_SHOWN = 3;
const MAX_BULLET_CHORDS = 3; // cap how many chord names a bullet spells out before "…"
const SKIP_SECONDS = 5;
// Amber/gold, not green - green is already spoken for by the accent color
// and the background, and wouldn't read as a distinct "selected" state.
const REGION_COLOR = 'rgba(255, 176, 59, 0.28)';

// Curated forte_class -> display-quality table, ported from
// scripts/webexport/chord_display_names.py (see that file for the "why" -
// short version: music21's own commonName is inconsistent per-segment for
// the same quality, so this is keyed by canonical forte_class instead).
// Covers exactly the 9 forte classes present in this corpus.
const CHORD_QUALITY_BY_FORTE = {
  '3-11B': 'major', '3-11A': 'minor', '3-10': 'diminished', '3-12': 'augmented',
  '4-20': 'major7', '4-26': 'minor7', '4-27B': 'dominant7', '4-27A': 'half-diminished7',
  '4-22A': 'add9',
};
const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];

function chordDisplayName(forte, rootPc) {
  const quality = CHORD_QUALITY_BY_FORTE[forte];
  const root = NOTE_NAMES[((rootPc % 12) + 12) % 12];
  return quality ? `${root} ${quality}` : `${root} [${forte}]`;
}

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
  backBtn: document.getElementById('backBtn'),
  playPauseBtn: document.getElementById('playPauseBtn'),
  fwdBtn: document.getElementById('fwdBtn'),
  selectionHint: document.getElementById('selectionHint'),
  analysisPanel: document.getElementById('analysisPanel'),
  closePanelBtn: document.getElementById('closePanelBtn'),
  matchesHeading: document.getElementById('matchesHeading'),
  matches: document.getElementById('matches'),
  matchDetail: document.getElementById('matchDetail'),
  backToMatchesBtn: document.getElementById('backToMatchesBtn'),
  matchDetailTitle: document.getElementById('matchDetailTitle'),
  jumpToMatchBtn: document.getElementById('jumpToMatchBtn'),
  chordAlignRows: document.getElementById('chordAlignRows'),
  chordAlignCaption: document.getElementById('chordAlignCaption'),
  noteAlignRows: document.getElementById('noteAlignRows'),
  volumeTrack: document.getElementById('volumeTrack'),
  volumeFill: document.getElementById('volumeFill'),
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
  volume: 1,                // persists across song switches (each new WaveSurfer instance re-applies it)
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
    height: 220,
    waveColor: '#4d4d4d',
    progressColor: '#7a7a7a',
    cursorColor: '#1DB954',
    barWidth: 2,
    barGap: 1,
    barRadius: 2,
    url: song.audio_url,
    // Precomputed RMS-energy peaks (scripts/webexport/compute_rms_peaks.mjs),
    // not wavesurfer's default raw max-abs-per-bucket amplitude - on a
    // loudness-normalized/heavily-compressed master, peak amplitude stays
    // close to flat across the whole track no matter how it's drawn, while
    // RMS energy still varies a lot. `duration` alongside `peaks` also lets
    // wavesurfer skip its own client-side decode for rendering entirely -
    // actual playback is unaffected, still served by `url` above. Falls
    // back to wavesurfer's own extraction (undefined) if a song is somehow
    // missing this field rather than crashing.
    peaks: song.waveform_peaks ? [song.waveform_peaks] : undefined,
    duration: song.duration,
  });
  state.ws = ws;
  ws.setVolume(state.volume);
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
// Volume - a utility control separate from the transport buttons; persists
// across song switches (each fresh WaveSurfer instance re-applies
// state.volume on creation - see loadSong()).
// ---------------------------------------------------------------------------
function setVolume(vol) {
  vol = Math.max(0, Math.min(1, vol));
  state.volume = vol;
  if (state.ws) state.ws.setVolume(vol);
  el.volumeFill.style.width = Math.round(vol * 100) + '%';
  el.volumeTrack.setAttribute('aria-valuenow', String(Math.round(vol * 100)));
}

function volumeFromClientX(clientX) {
  const rect = el.volumeTrack.getBoundingClientRect();
  return (clientX - rect.left) / rect.width;
}

el.volumeTrack.addEventListener('mousedown', (e) => {
  setVolume(volumeFromClientX(e.clientX));
  const onMove = (ev) => setVolume(volumeFromClientX(ev.clientX));
  const onUp = () => {
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
  };
  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
});
el.volumeTrack.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowRight' || e.key === 'ArrowUp') { e.preventDefault(); setVolume(state.volume + 0.05); }
  else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') { e.preventDefault(); setVolume(state.volume - 0.05); }
});
setVolume(state.volume); // paint the fill at boot, before any song is loaded

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

  // -- chord-progression query (sequences.json's compact form: id + root) --
  const chordQuery = seq.chords
    .filter((c) => c.end > start && c.start < end)
    .map((c) => ({ canonical_chord_id: c.canonical_chord_id, root_pc: c.root_pc }));

  // -- melodic-interval query (drop the null "first note of song" marker) --
  const noteQuery = seq.notes
    .filter((n) => n.onset >= start && n.onset < end && n.interval_from_prev !== null)
    .map((n) => n.interval_from_prev);

  if (!chordQuery.length && !noteQuery.length) {
    renderCombinedMatches([], 'No chords or notes in this selection.');
    return;
  }

  const chordResults = chordQuery.length
    ? searchChordQueryAgainstCorpus(chordQuery, state.sequences, { excludeSongId: songId })
    : [];
  const noteResults = noteQuery.length
    ? searchNoteQueryAgainstCorpus(noteQuery, state.sequences, { excludeSongId: songId })
    : [];

  renderCombinedMatches(combineMatches(chordResults, noteResults), 'No meaningful matches found.');
}

// ---------------------------------------------------------------------------
// Combine the two independent alignment signals (chord + melodic) into one
// ranking. First pass, expected to be re-tuned once seen against real
// selections - see the brief. Each result list is its own scale (the two
// scoring functions aren't calibrated against each other), so each is
// min-max'd against its own positive scores before combining; a
// non-positive score means "not a meaningful match" per the alignment
// engine's own convention and is treated as *absent* rather than 0, so a
// song strong on only one signal
// isn't dragged down by lacking the other. A song's combined score is the
// average of whichever signal(s) it actually has.
// ---------------------------------------------------------------------------
function combineMatches(chordResults, noteResults) {
  const maxChord = Math.max(0, ...chordResults.map((r) => r.score));
  const maxNote = Math.max(0, ...noteResults.map((r) => r.score));
  const chordBySong = new Map(chordResults.filter((r) => r.score > 0).map((r) => [r.song_id, r]));
  const noteBySong = new Map(noteResults.filter((r) => r.score > 0).map((r) => [r.song_id, r]));

  const combined = [];
  for (const songId of new Set([...chordBySong.keys(), ...noteBySong.keys()])) {
    const chord = chordBySong.get(songId) ?? null;
    const note = noteBySong.get(songId) ?? null;
    const chordNorm = chord && maxChord > 0 ? chord.score / maxChord : null;
    const noteNorm = note && maxNote > 0 ? note.score / maxNote : null;
    const norms = [chordNorm, noteNorm].filter((x) => x !== null);
    if (!norms.length) continue;
    const combinedScore = norms.reduce((a, b) => a + b, 0) / norms.length;
    combined.push({ song_id: songId, combinedScore, chordNorm, noteNorm, chord, note });
  }
  combined.sort((a, b) => b.combinedScore - a.combinedScore);
  return combined.slice(0, TOP_MATCHES_SHOWN);
}

// -- short, factual bullet text for whichever signal(s) actually matched --
// (never invented for a signal that didn't contribute to a song's inclusion)

function chordMatchBullet(songId, chordResult) {
  const targetChords = state.sequences[songId].chords;
  const [startIdx, endIdx] = chordResult.span;
  const matched = targetChords.slice(startIdx, endIdx);
  if (!matched.length) return null;
  const names = matched.slice(0, MAX_BULLET_CHORDS).map((c) => chordDisplayName(c.canonical_chord_id, c.root_pc));
  const more = matched.length > MAX_BULLET_CHORDS ? '…' : '';
  return `Shares ${names.join(' → ')}${more} around ${fmtTime(matched[0].start)}`;
}

function noteMatchBullet(songId, noteResult) {
  const rawNotes = state.sequences[songId].notes;
  // searchNoteQueryAgainstCorpus aligns against notes with the null-interval
  // "first note of the song" entry filtered out - span indices are into
  // that filtered array, so shift by 1 to land back on seq.notes.
  const offset = rawNotes.length && rawNotes[0].interval_from_prev === null ? 1 : 0;
  const note = rawNotes[noteResult.span[0] + offset];
  if (!note) return null;
  return `Similar melodic phrase near ${fmtTime(note.onset)}`;
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

function renderCombinedMatches(combined, emptyMessage) {
  showMatchList(); // a fresh analysis always starts back at the top-3 list, never mid-detail
  el.matches.innerHTML = '';
  if (combined.length === 0) {
    const li = document.createElement('li');
    li.className = 'emptyNote';
    li.textContent = emptyMessage;
    el.matches.appendChild(li);
    return;
  }
  combined.forEach((entry) => {
    const li = document.createElement('li');
    li.className = 'matchItem';

    const header = document.createElement('div');
    header.className = 'matchHeader';
    const titleSpan = document.createElement('span');
    titleSpan.className = 'matchTitle';
    titleSpan.textContent = state.manifestById.get(entry.song_id)?.title ?? entry.song_id;
    const scoreSpan = document.createElement('span');
    scoreSpan.className = 'matchScore';
    scoreSpan.textContent = Math.round(entry.combinedScore * 100) + '%';
    header.appendChild(titleSpan);
    header.appendChild(scoreSpan);
    li.appendChild(header);

    const bullets = document.createElement('ul');
    bullets.className = 'matchBullets';
    const bulletTexts = [
      entry.chord ? chordMatchBullet(entry.song_id, entry.chord) : null,
      entry.note ? noteMatchBullet(entry.song_id, entry.note) : null,
    ].filter(Boolean);
    bulletTexts.forEach((text) => {
      const b = document.createElement('li');
      b.textContent = text;
      bullets.appendChild(b);
    });
    li.appendChild(bullets);

    li.title = 'See a detailed comparison';
    li.addEventListener('click', () => renderMatchDetail(entry));
    el.matches.appendChild(li);
  });
}

// ---------------------------------------------------------------------------
// Match detail view (point 11): a pairwise chord/melody alignment, one
// level deeper than the top-3 list, in the same panel. Escape/click-outside
// still collapses the whole analysis flow via wireGlobalKeysAndClicks - that
// logic doesn't care which sub-view of #analysisPanel is showing, it just
// clears the region and hides the panel outright, so no separate dismissal
// path is needed here; "Back" only steps up one level, back to the list.
// ---------------------------------------------------------------------------
function primaryMode(entry) {
  return (entry.chordNorm ?? -1) >= (entry.noteNorm ?? -1) ? 'chord' : 'note';
}

function showMatchList() {
  el.matchDetail.hidden = true;
  el.matchesHeading.hidden = false;
  el.matches.hidden = false;
}

function renderMatchDetail(entry) {
  el.matchesHeading.hidden = true;
  el.matches.hidden = true;
  el.matchDetail.hidden = false;

  el.matchDetailTitle.textContent = state.manifestById.get(entry.song_id)?.title ?? entry.song_id;

  const mode = primaryMode(entry);
  const span = mode === 'chord' ? entry.chord.span : entry.note.span;
  el.jumpToMatchBtn.onclick = () => jumpToMatch(entry.song_id, mode, span);

  renderAlignTrack(el.chordAlignRows, entry.chord,
    (sym) => (sym === null ? '—' : chordDisplayName(sym.forte, sym.root)),
    (a, b) => a.forte === b.forte && a.root === b.root);

  if (entry.chord) {
    const shift = entry.chord.transposition > 6 ? entry.chord.transposition - 12 : entry.chord.transposition;
    el.chordAlignCaption.hidden = shift === 0;
    el.chordAlignCaption.textContent = `Target song's chords shown transposed ${shift > 0 ? '+' : ''}${shift} semitones so matching keys line up.`;
  } else {
    el.chordAlignCaption.hidden = true;
  }

  renderAlignTrack(el.noteAlignRows, entry.note,
    (iv) => (iv === null ? '—' : (iv > 0 ? `+${iv}` : String(iv))),
    (a, b) => a === b);
}

/** resultObj is the raw {alignedQuery, alignedTarget} entry from
 * alignment.js (or null if that signal didn't contribute to this song's
 * inclusion - see point 6/11's "don't invent a match" rule). labelFn/equalFn
 * are shared by both chord and note tracks; only how a symbol is displayed
 * and compared differs between them. */
function renderAlignTrack(rowsEl, resultObj, labelFn, equalFn) {
  rowsEl.innerHTML = '';
  if (!resultObj) {
    const p = document.createElement('p');
    p.className = 'alignEmpty';
    p.textContent = 'No match on this signal for this song.';
    rowsEl.appendChild(p);
    return;
  }
  const a = resultObj.alignedQuery, b = resultObj.alignedTarget;
  const queryRow = document.createElement('div');
  queryRow.className = 'alignRow';
  const targetRow = document.createElement('div');
  targetRow.className = 'alignRow';
  for (let i = 0; i < a.length; i++) {
    const isGap = a[i] === null || b[i] === null;
    const cls = isGap ? 'gap' : (equalFn(a[i], b[i]) ? 'match' : 'sub');
    queryRow.appendChild(makeAlignBlock(labelFn(a[i]), cls));
    targetRow.appendChild(makeAlignBlock(labelFn(b[i]), cls));
  }
  rowsEl.appendChild(queryRow);
  rowsEl.appendChild(targetRow);
}

function makeAlignBlock(text, cls) {
  const d = document.createElement('div');
  d.className = 'alignBlock ' + cls;
  d.textContent = text;
  return d;
}

el.backToMatchesBtn.addEventListener('click', showMatchList);

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
