const manifestUrl = 'data/instrumentals/manifest.json';
const stemNames = ['bass', 'drums', 'other'];
const palette = ['#cf5c3d', '#d3a844', '#587b83', '#76936d', '#b76c7c', '#ae825d', '#7787a2', '#c68549'];
const songSearch = document.querySelector('#songSearch');
const songSelect = document.querySelector('#songSelect');
const catalogCount = document.querySelector('#catalogCount');
const heading = document.querySelector('#songHeading');
const trackMeta = document.querySelector('#trackMeta');
const trackStatus = document.querySelector('#trackStatus');
const onion = document.querySelector('#onion');
const layerKey = document.querySelector('#layerKey');
const miniRecords = document.querySelector('#miniRecords');
const detailTitle = document.querySelector('#detailTitle');
const detailText = document.querySelector('#detailText');
const facts = document.querySelector('#facts');
const listenButton = document.querySelector('#listenWindow');
const playerStatus = document.querySelector('#playerStatus');
const audio = document.querySelector('#windowAudio');
const recordLabel = document.querySelector('#recordLabel');
const recordDuration = document.querySelector('#recordDuration');
const returnMain = document.querySelector('#returnMain');

let manifest;
let activeSong;
let songData;
let currentView = 'instrumental';
let selectedDepth = 1;
let selectedIndex = 0;
let currentSegment;
let currentAudioUrl;
let playbackFrame = 0;
let playbackToken = 0;

const fmt = seconds => {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
};
const currentLayers = () => songData?.views?.[currentView]?.layers || [];
const selectedLayer = () => currentLayers().find(layer => layer.depth === selectedDepth) || currentLayers()[0];
const selectedSegment = () => selectedLayer()?.segments?.find(segment => segment.index === selectedIndex) || selectedLayer()?.segments?.[0];

function stopPlayback(message = '') {
  playbackToken++;
  if (playbackFrame) cancelAnimationFrame(playbackFrame);
  playbackFrame = 0;
  audio.pause();
  if (message) setPlayerStatus(message);
}

function setPlayerStatus(message, isError = false) {
  playerStatus.textContent = message;
  playerStatus.classList.toggle('error', isError);
}

function installPlayerSource() {
  stopPlayback();
  const source = songData?.audio?.[currentView];
  currentAudioUrl = source?.url || null;
  if (currentAudioUrl && audio.src !== new URL(currentAudioUrl, document.baseURI).href) {
    audio.src = currentAudioUrl;
    audio.load();
  }
  listenButton.disabled = !currentAudioUrl || !currentSegment;
  if (!currentAudioUrl) {
    setPlayerStatus(source?.status === 'awaiting_host'
      ? 'Audio hosting is prepared separately; this preview will be available once its files are published.'
      : 'No playable audio file is attached to this view.');
  } else {
    const type = source.mime || (currentView === 'instrumental' ? 'MP3' : 'WAV');
    setPlayerStatus(`${type} preview · only the selected time window will play.`);
  }
}

async function playWindow() {
  if (!currentAudioUrl || !currentSegment) return;
  if (!audio.paused) {
    stopPlayback('Preview paused.');
    listenButton.querySelector('span').textContent = '▶';
    return;
  }
  const token = ++playbackToken;
  if (playbackFrame) cancelAnimationFrame(playbackFrame);
  listenButton.disabled = true;
  setPlayerStatus('Loading the selected passage…');
  try {
    if (audio.src !== new URL(currentAudioUrl, document.baseURI).href) {
      audio.src = currentAudioUrl;
      audio.load();
    }
    if (audio.readyState < 1) {
      await new Promise((resolve, reject) => {
        const loaded = () => { cleanup(); resolve(); };
        const failed = () => { cleanup(); reject(new Error('The audio host could not serve this file.')); };
        const cleanup = () => {
          audio.removeEventListener('loadedmetadata', loaded);
          audio.removeEventListener('error', failed);
        };
        audio.addEventListener('loadedmetadata', loaded, { once: true });
        audio.addEventListener('error', failed, { once: true });
      });
    }
    if (token !== playbackToken) return;
    const start = Math.max(0, Number(currentSegment.start) || 0);
    const end = Math.min(Number(currentSegment.end) || audio.duration, audio.duration);
    if (!Number.isFinite(end) || end - start < 0.15) throw new Error('This time window is shorter than the audio player can seek.');
    audio.currentTime = Math.min(start, Math.max(0, audio.duration - 0.05));
    await audio.play();
    if (token !== playbackToken) return;
    listenButton.querySelector('span').textContent = 'Ⅱ';
    setPlayerStatus(`Playing ${fmt(start)}–${fmt(end)} · ${currentView === 'instrumental' ? 'instrumental MP3' : `${currentView} WAV`}.`);
    const checkEnd = () => {
      if (token !== playbackToken) return;
      if (audio.currentTime >= end || audio.ended) {
        stopPlayback('Passage finished.');
        listenButton.querySelector('span').textContent = '▶';
      } else playbackFrame = requestAnimationFrame(checkEnd);
    };
    playbackFrame = requestAnimationFrame(checkEnd);
  } catch (error) {
    if (token !== playbackToken) return;
    stopPlayback(error.message || 'Could not play this preview. Check the audio host and try again.');
    setPlayerStatus(error.message || 'Could not play this preview. Check the audio host and try again.', true);
  } finally {
    if (token === playbackToken) listenButton.disabled = !currentAudioUrl;
  }
}

function annularPath(cx, cy, outer, inner, startAngle, endAngle) {
  const point = (radius, angle) => {
    const rad = (angle - 90) * Math.PI / 180;
    return [cx + radius * Math.cos(rad), cy + radius * Math.sin(rad)];
  };
  const [x1, y1] = point(outer, startAngle);
  const [x2, y2] = point(outer, endAngle);
  const [x3, y3] = point(inner, endAngle);
  const [x4, y4] = point(inner, startAngle);
  const large = endAngle - startAngle > 180 ? 1 : 0;
  return `M ${x1} ${y1} A ${outer} ${outer} 0 ${large} 1 ${x2} ${y2} L ${x3} ${y3} A ${inner} ${inner} 0 ${large} 0 ${x4} ${y4} Z`;
}

function chooseSegment(depth, index) {
  stopPlayback();
  selectedDepth = depth;
  selectedIndex = index;
  currentSegment = selectedSegment();
  renderOnion();
  renderDetail();
  installPlayerSource();
}

function renderOnion() {
  const layers = currentLayers();
  onion.innerHTML = '';
  if (!activeSong || !layers.length) {
    onion.innerHTML = '<text x="220" y="204" text-anchor="middle" fill="#eee4d6" font-family="DM Mono, monospace" font-size="11">NO PATTERN MAP</text><text x="220" y="224" text-anchor="middle" fill="#c8bcaa" font-family="DM Sans, sans-serif" font-size="12">This song is still being prepared.</text>';
    return;
  }
  const duration = Math.max(1, Number(songData.duration) || activeSong.duration || 1);
  const geometry = layers.length === 3
    ? [{ outer: 216, inner: 180 }, { outer: 174, inner: 137 }, { outer: 131, inner: 94 }]
    : [{ outer: 207, inner: 166 }, { outer: 155, inner: 111 }];
  layers.forEach((layer, layerOffset) => {
    const g = geometry[layerOffset] || { outer: 216 - layerOffset * 43, inner: 180 - layerOffset * 43 };
    for (const segment of layer.segments || []) {
      const start = Math.max(0, Number(segment.start) || 0);
      const end = Math.min(duration, Number(segment.end) || duration);
      const from = start / duration * 360;
      const to = end / duration * 360;
      const pad = Math.min(0.9, Math.max(0.08, (to - from) * 0.035));
      const isActive = selectedDepth === layer.depth && selectedIndex === segment.index;
      const shape = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      shape.setAttribute('d', annularPath(220, 220, g.outer, g.inner, from + pad, Math.max(from + pad + 0.2, to - pad)));
      shape.setAttribute('class', `ring-segment${isActive ? ' is-selected' : ''}`);
      shape.setAttribute('fill', palette[(segment.index + layerOffset * 2) % palette.length]);
      shape.setAttribute('tabindex', '0');
      shape.setAttribute('role', 'button');
      shape.setAttribute('aria-label', `${layer.name}: ${segment.label}, ${fmt(start)} to ${fmt(end)}`);
      shape.dataset.depth = layer.depth;
      shape.dataset.index = segment.index;
      const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
      title.textContent = `${layer.name} · ${segment.label} · ${fmt(start)}–${fmt(end)}`;
      shape.append(title);
      shape.addEventListener('click', () => chooseSegment(layer.depth, segment.index));
      shape.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); chooseSegment(layer.depth, segment.index); }
      });
      onion.append(shape);
    }
    const outline = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    outline.setAttribute('class', 'ring-outline');
    outline.setAttribute('cx', '220'); outline.setAttribute('cy', '220');
    outline.setAttribute('r', String((g.outer + g.inner) / 2));
    onion.append(outline);
  });
}

function renderLayerKey() {
  layerKey.innerHTML = '';
  for (const layer of currentLayers()) {
    const button = document.createElement('button');
    button.type = 'button';
    button.classList.toggle('active', selectedDepth === layer.depth);
    const count = layer.new_boundaries || 0;
    button.innerHTML = `<strong>${layer.name}</strong>${layer.segments.length} window${layer.segments.length === 1 ? '' : 's'} · ${count} supported change${count === 1 ? '' : 's'}`;
    button.addEventListener('click', () => chooseSegment(layer.depth, layer.segments[0]?.index ?? 0));
    layerKey.append(button);
  }
}

function renderDetail() {
  const layer = selectedLayer();
  currentSegment = selectedSegment();
  renderLayerKey();
  if (!currentSegment || !layer) {
    detailTitle.textContent = activeSong ? 'Pattern analysis is still in progress.' : 'Move through the arrangement.';
    detailText.textContent = activeSong?.reason || 'Choose a song to explore its patterns.';
    facts.innerHTML = '';
    listenButton.disabled = true;
    return;
  }
  const start = Number(currentSegment.start) || 0;
  const end = Number(currentSegment.end) || songData.duration;
  detailTitle.textContent = currentSegment.label;
  const recurrence = (songData.views[currentView].recurrences || []).filter(r =>
    (r.start >= start && r.start < end) || (r.repeat_start >= start && r.repeat_start < end));
  detailText.textContent = `${currentSegment.description}${currentSegment.boundary_supported || selectedDepth === 1 ? '' : ' This window carries through from the next outer view without a new split.'}`;
  const items = [
    ['Time', `${fmt(start)}–${fmt(end)} · ${Math.max(0, end - start).toFixed(1)} sec`],
    ['Zoom', layer.name],
    ['View', currentView === 'instrumental' ? 'Three stems together · instrumental MP3' : `${currentView} stem · isolated WAV`],
  ];
  if (recurrence.length) {
    const pair = recurrence[0];
    items.push(['Repeat', `Similar texture at ${fmt(pair.start)} and ${fmt(pair.repeat_start)} · ${Math.round(pair.similarity * 100)}% feature similarity`]);
  }
  facts.innerHTML = items.map(([label, value]) => `<div class="fact"><dt>${label}</dt><dd>${value}</dd></div>`).join('');
  if (songData.source?.url) {
    const row = document.createElement('div'); row.className = 'fact';
    const term = document.createElement('dt'); term.textContent = 'Source';
    const definition = document.createElement('dd');
    const link = document.createElement('a'); link.href = songData.source.url;
    link.target = '_blank'; link.rel = 'noopener noreferrer';
    link.textContent = songData.source.title || 'Instrumental recording';
    definition.append(link, document.createElement('br'), document.createTextNode(songData.source.kind === 'user_provided_instrumental' ? 'Source supplied by you' : 'Matched from source metadata'));
    row.append(term, definition); facts.append(row);
  }
}

function renderMiniRecords() {
  miniRecords.innerHTML = '';
  returnMain.hidden = currentView === 'instrumental';
  for (const stem of stemNames) {
    const view = songData?.views?.[stem];
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `mini-record${currentView === stem ? ' active' : ''}`;
    button.setAttribute('aria-pressed', String(currentView === stem));
    const layers = view?.layers || [];
    const changeCount = layers.reduce((sum, layer) => sum + (layer.new_boundaries || 0), 0);
    button.innerHTML = `<span class="mini-disc" aria-hidden="true"></span><span class="mini-copy"><strong>${stem[0].toUpperCase() + stem.slice(1)}</strong><span>${layers.length ? `${changeCount} supported change${changeCount === 1 ? '' : 's'} · up to 2 rings` : 'Analysis in progress'}</span></span>`;
    button.disabled = !view;
    button.addEventListener('click', () => {
      stopPlayback();
      currentView = stem;
      selectedDepth = 1;
      selectedIndex = 0;
      recordLabel.textContent = stem.toUpperCase();
      renderMiniRecords();
      renderOnion();
      renderDetail();
      installPlayerSource();
    });
    miniRecords.append(button);
  }
}

function clearCurrentTrack(message = 'Choose a song to explore its patterns.') {
  stopPlayback();
  activeSong = null; songData = null; currentSegment = null;
  heading.textContent = 'A listening map for every instrumental.';
  trackMeta.textContent = message;
  trackStatus.textContent = 'SELECT A RECORD';
  recordLabel.textContent = 'INSTRUMENTAL';
  recordDuration.textContent = 'PATTERN MAP';
  returnMain.hidden = true;
  onion.innerHTML = '<text x="220" y="216" text-anchor="middle" fill="#eee4d6" font-family="DM Sans, sans-serif" font-size="13">Choose a song to begin.</text>';
  layerKey.innerHTML = '';
  detailTitle.textContent = 'Move through the arrangement.';
  detailText.textContent = message;
  facts.innerHTML = '';
  listenButton.disabled = true;
  setPlayerStatus('Select a song and a window.');
  miniRecords.innerHTML = '';
}

async function loadSong(track) {
  stopPlayback();
  activeSong = track;
  songData = null;
  currentView = 'instrumental'; selectedDepth = 1; selectedIndex = 0;
  heading.textContent = `${track.artist} — ${track.title}`;
  trackStatus.textContent = track.status.replaceAll('_', ' ').toUpperCase();
  trackMeta.textContent = track.reason || `${fmt(track.duration)} · individual instrumental pattern map`;
  recordLabel.textContent = 'INSTRUMENTAL';
  recordDuration.textContent = fmt(track.duration);
  onion.innerHTML = '<text x="220" y="216" text-anchor="middle" fill="#eee4d6" font-family="DM Mono, monospace" font-size="11">OPENING THE RECORD…</text>';
  layerKey.innerHTML = '';
  detailTitle.textContent = track.status === 'not_found' ? 'No verified instrumental was found.' : 'Pattern analysis is still in progress.';
  detailText.textContent = track.reason || 'This song has not finished its instrumental search, stem separation, and analysis.';
  facts.innerHTML = '';
  listenButton.disabled = true;
  setPlayerStatus('Audio preview will be attached only when its source is verified.');
  renderMiniRecords();
  if (!track.analysis_url) return;
  try {
    const response = await fetch(track.analysis_url);
    if (!response.ok) throw new Error(`Pattern file returned ${response.status}.`);
    const data = await response.json();
    if (activeSong?.track_id !== track.track_id) return;
    songData = data;
    recordDuration.textContent = fmt(data.duration);
    renderOnion(); renderDetail(); renderMiniRecords(); installPlayerSource();
  } catch (error) {
    detailText.textContent = `This song's pattern record could not be opened. ${error.message}`;
  }
}

function populateSelect(preferredId = null) {
  const query = songSearch.value.trim().toLocaleLowerCase();
  const matches = manifest.tracks.filter(track => `${track.artist} ${track.title}`.toLocaleLowerCase().includes(query)).slice(0, 150);
  songSelect.innerHTML = '';
  for (const track of matches) {
    const option = document.createElement('option');
    option.value = track.track_id;
    option.textContent = `${track.artist} — ${track.title}${track.status === 'ready' ? '' : ` · ${track.status.replaceAll('_', ' ')}`}`;
    songSelect.append(option);
  }
  catalogCount.textContent = `${manifest.total_tracks.toLocaleString()} saved songs · ${manifest.tracks.filter(x => x.analysis_url).length.toLocaleString()} pattern maps ready · ${manifest.tracks.filter(x => x.audio_ready).length.toLocaleString()} hosted previews`;
  const chosen = matches.find(track => track.track_id === preferredId) || matches[0];
  if (!chosen) { clearCurrentTrack('No songs match that title or artist.'); return; }
  songSelect.value = chosen.track_id;
  loadSong(chosen);
}

listenButton.addEventListener('click', playWindow);
returnMain.addEventListener('click', () => {
  stopPlayback(); currentView = 'instrumental'; selectedDepth = 1; selectedIndex = 0;
  recordLabel.textContent = 'INSTRUMENTAL';
  renderMiniRecords(); renderOnion(); renderDetail(); installPlayerSource();
});
songSelect.addEventListener('change', () => {
  const track = manifest?.tracks.find(item => item.track_id === songSelect.value);
  if (track) loadSong(track);
});
songSearch.addEventListener('input', () => populateSelect(songSelect.value));

fetch(manifestUrl)
  .then(response => { if (!response.ok) throw new Error(`Catalog returned ${response.status}.`); return response.json(); })
  .then(data => {
    manifest = data;
    const first = data.tracks.find(track => track.analysis_url) || data.tracks[0];
    populateSelect(first?.track_id);
  })
  .catch(error => {
    clearCurrentTrack(`The instrumental catalog could not be loaded: ${error.message}`);
    catalogCount.textContent = 'The instrumental catalog could not be loaded.';
  });
