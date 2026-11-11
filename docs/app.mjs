import { buildCatalog, buildSong, formatBoundary, formatTime, songAngle } from './model.mjs';

import { TimelinePlayer } from './player.mjs';

import { summarizeSections, friendly as instrumentName } from './summaries.mjs';



const $ = id => document.getElementById(id);

const node = (tag, text, className) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (className) e.className = className; return e; };

const svg = (tag, attrs) => { const e = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const [key, val] of Object.entries(attrs)) e.setAttribute(key, val); return e; };

let catalog = [], skippedSongs = [], matches = [], activeResult = -1, song = null, selected = null, displayed = null, loadToken = 0, needle;

let lastState = { playing: false, position: 0, duration: 0 }, audioFailure = '';

const announce = text => { $('announcement').textContent = text; };

function searchMessage(message, error = false) { $('searchMessage').textContent = message; $('searchMessage').classList.toggle('error', error); $('searchMessage').hidden = !message; }

async function fetchSource(url, json = true) {

  const response = await fetch(url, { cache: 'no-store' });

  if (!response.ok) { let message = `${url}: HTTP ${response.status}.`; try { message = (await response.json()).error || message; } catch {} throw new Error(message); }

  try { return json ? await response.json() : await response.text(); } catch { throw new Error(`${url}: the source could not be parsed.`); }

}

const player = new TimelinePlayer($('audio'), state => {

  lastState = state;

  $('timeline').value = String(state.position);

  $('timeline').style.setProperty('--progress', `${state.duration ? state.position / state.duration * 100 : 0}%`);

  $('timeline').setAttribute('aria-valuetext', `${formatBoundary(state.position)} of ${formatBoundary(state.duration)}${state.window ? `; selected window ${formatBoundary(state.window.start)} to ${formatBoundary(state.window.end)}` : ''}`);

  $('currentTime').textContent = formatTime(state.position); $('totalTime').textContent = formatTime(state.duration);

  if (needle) needle.setAttribute('transform', `rotate(${state.duration ? songAngle(state.position, state.duration) : 0} 300 300)`);

  $('playShape').replaceChildren(svg('path', { d: state.playing ? 'M7 5h4v14H7zm7 0h4v14h-4z' : 'm8 5 11 7-11 7z' }));

  $('play').setAttribute('aria-label', state.playing ? 'Pause' : state.starting ? 'Cancel loading audio' : 'Play');

  $('play').title = state.playing ? 'Pause' : 'Play';

  for (const id of ['timeline', 'play', 'back', 'forward', 'hub']) $(id).disabled = !state.duration || state.blocked;

  const scope = $('selectedRange'); scope.hidden = !state.window;

  if (state.window) { scope.style.left = `${state.window.start / state.duration * 100}%`; scope.style.width = `${(state.window.end - state.window.start) / state.duration * 100}%`; }

  const button = $('windowPlay'); if (button) button.disabled = state.blocked;

}, message => { audioFailure = message; if (song) showDetails(displayed?.ring || song.rings[0], displayed?.section || null); announce(message); });



function hideResults() { $('results').hidden = true; $('search').setAttribute('aria-expanded', 'false'); $('search').removeAttribute('aria-activedescendant'); activeResult = -1; }

function filter() {

  const query = $('search').value.normalize('NFKC').trim().toLocaleLowerCase();

  $('results').replaceChildren(); activeResult = -1;

  if (!query) { hideResults(); searchMessage(''); return; }

  if (!catalog.length) { searchMessage('The MVP catalog has not loaded. Song search is unavailable.', true); return; }

  matches = catalog.filter(s => `${s.artist} ${s.title}`.normalize('NFKC').toLocaleLowerCase().includes(query)).slice(0, 12);

  if (!matches.length) { hideResults(); const skipped = skippedSongs.filter(s => `${s.artist} ${s.title}`.normalize('NFKC').toLocaleLowerCase().includes(query)); searchMessage(skipped.length ? skipped.map(s => `${s.artist} — ${s.title}: ${s.reason}`).join(' · ') : `No matching song in the ${catalog.length}-song catalog.`); return; }

  searchMessage('');

  matches.forEach((item, i) => {

    const li = node('li'); li.role = 'option'; li.id = `result-${i}`; li.setAttribute('aria-selected', 'false');

    const text = node('span', null, 'result-text'); text.append(node('strong', item.title), node('small', item.artist));

    if (item.summary.status === 'failed') text.append(node('small', 'Analysis unavailable', 'result-unavailable'));

    li.append(text, node('span', formatTime(item.duration), 'result-time'));

    li.addEventListener('mousedown', e => e.preventDefault()); li.addEventListener('click', () => openSong(item));

    $('results').append(li);

  });

  $('results').hidden = false; $('search').setAttribute('aria-expanded', 'true');

}

function focusResult(index) {

  activeResult = (index + matches.length) % matches.length;

  [...$('results').children].forEach((e, i) => { e.classList.toggle('active', i === activeResult); e.setAttribute('aria-selected', String(i === activeResult)); });

  $('search').setAttribute('aria-activedescendant', `result-${activeResult}`);

  $('results').children[activeResult].scrollIntoView({ block: 'nearest' });

}

$('search').addEventListener('input', filter);

$('search').addEventListener('keydown', event => {

  if (event.key === 'Escape') { hideResults(); searchMessage(''); }

  if (['ArrowDown', 'ArrowUp'].includes(event.key) && matches.length) { event.preventDefault(); if ($('results').hidden) filter(); focusResult(activeResult + (event.key === 'ArrowDown' ? 1 : -1)); }

});

$('searchForm').addEventListener('submit', event => { event.preventDefault(); if (matches.length && !$('results').hidden) openSong(matches[Math.max(0, activeResult)]); else filter(); });

document.addEventListener('pointerdown', event => { if (!event.target.closest('.search-shell')) hideResults(); });



async function openSong(item) {

  const request = ++loadToken; player.pause(); song = null; needle = null; selected = null; displayed = null; audioFailure = '';

  hideResults(); searchMessage(''); $('search').value = `${item.artist} — ${item.title}`; $('search').blur();

  document.body.classList.add('has-song', 'loading'); $('stage').hidden = false; $('details').hidden = true;

  $('hubArtist').textContent = item.artist; $('hubTitle').textContent = item.title; $('hubDuration').textContent = formatTime(item.duration);

  $('onion').replaceChildren();

  for (const id of ['play', 'back', 'forward', 'timeline', 'hub']) $(id).disabled = true;

  announce(`Loading ${item.artist}, ${item.title}.`);

  const names = ['report', 'beats', 'lyrics', 'patterns', 'layers'];

  const responses = await Promise.allSettled(names.map(name => fetchSource(`app-data/songs/${item.id}/${name}.json`)));

  if (request !== loadToken) return;

  const files = Object.fromEntries(names.map((name, i) => [name, responses[i].status === 'fulfilled' ? responses[i].value : { error: responses[i].reason.message }]));

  song = buildSong(item, files); song.summaries = summarizeSections(song); document.body.classList.remove('loading');

  renderRecord(); $('timeline').max = String(song.duration); player.load(song);

  document.title = `${song.title} · bepop`;

  if (song.rings.some(r => r.error) || song.fileErrors.length) {

    const broken = song.rings.find(r => r.error) || song.rings[0]; showDetails(broken, null);

    announce(`${song.title} loaded with unavailable analysis. Details explain the missing source data.`);

  } else announce(`${song.title} loaded. Choose a ring section to inspect it.`);

}



function point(radius, time) { const a = time / song.duration * Math.PI * 2 - Math.PI / 2; return [300 + Math.cos(a) * radius, 300 + Math.sin(a) * radius]; }

function annulus(start, end, inner, outer) {

  if (end - start >= song.duration - .00001) return `M300 ${300 - outer} A${outer} ${outer} 0 1 1 300 ${300 + outer} A${outer} ${outer} 0 1 1 300 ${300 - outer} L300 ${300 - inner} A${inner} ${inner} 0 1 0 300 ${300 + inner} A${inner} ${inner} 0 1 0 300 ${300 - inner} Z`;

  const [a, b, c, d] = [point(outer, start), point(outer, end), point(inner, end), point(inner, start)];

  const large = end - start > song.duration / 2 ? 1 : 0;

  return `M${a} A${outer} ${outer} 0 ${large} 1 ${b} L${c} A${inner} ${inner} 0 ${large} 0 ${d} Z`;

}

function color(ring, section) {

  if (section.unassigned) return 'url(#unassigned)';

  let h = 0; const key = section.colorKey || section.label || section.id;

  for (const char of key) h = (Math.imul(h, 31) + char.charCodeAt(0)) | 0;

  const shift = Math.abs(h) % 6;

  if (ring.key === 'vocals') return `hsl(38 58% ${58 + shift * 3}%)`;

  if (ring.key === 'major') return `hsl(148 38% ${45 + shift * 4}%)`;

  return `hsl(163 29% ${30 + shift * 4}%)`;

}

function renderRecord() {

  const root = $('onion'); root.replaceChildren();

  const defs = svg('defs', {}), hatch = svg('pattern', { id: 'unassigned', width: 7, height: 7, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(35)' });

  hatch.append(svg('rect', { width: 7, height: 7, fill: '#13231b' }), svg('line', { x1: 0, y1: 0, x2: 0, y2: 7, stroke: '#718c7955', 'stroke-width': 1 })); defs.append(hatch); root.append(defs);

  for (const ring of song.rings) {

    const [inner, outer] = ring.radius;

    if (ring.error) {

      const shape = svg('path', { d: annulus(0, song.duration, inner, outer), fill: 'url(#unassigned)', class: 'unavailable-ring', role: 'button', tabindex: 0, 'aria-label': `${ring.name}: analysis unavailable` });

      const reveal = () => showDetails(ring, null);

      shape.addEventListener('click', reveal); shape.addEventListener('focus', reveal); shape.addEventListener('mouseenter', reveal); shape.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); reveal(); } }); root.append(shape); continue;

    }

    for (const section of ring.segments) {

      const shape = svg('path', { d: annulus(section.start, section.end, inner, outer), fill: color(ring, section), class: 'section-arc', 'data-section': section.id, tabindex: 0, role: 'button', 'aria-label': `${ring.name}: ${section.label}, ${formatBoundary(section.start)} to ${formatBoundary(section.end)}${section.labelUnverified ? ', label unverified' : ''}` });

      shape.append(svg('title', {})); shape.firstChild.textContent = `${ring.name} · ${section.label} · ${formatBoundary(section.start)}–${formatBoundary(section.end)}`;

      shape.addEventListener('mouseenter', () => { if (!selected) showDetails(ring, section); });

      shape.addEventListener('focus', () => showDetails(ring, section));

      shape.addEventListener('click', () => choose(ring, section));

      shape.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(ring, section); } });

      root.append(shape);

    }

    for (const fraction of [.25, .5, .75]) root.append(svg('circle', { cx: 300, cy: 300, r: inner + (outer - inner) * fraction, class: 'record-groove' }));

  }

  needle = svg('g', { class: 'needle', 'aria-hidden': 'true', transform: 'rotate(0 300 300)' });

  needle.append(svg('line', { x1: 300, y1: 300, x2: 300, y2: 40 }), svg('circle', { cx: 300, cy: 40, r: 3.3 }), svg('circle', { cx: 300, cy: 300, r: 4 })); root.append(needle);

}

function choose(ring, section) {

  selected = section; player.select(section);

  $('onion').querySelectorAll('[data-section]').forEach(p => p.classList.toggle('selected', p.dataset.section === section.id));

  showDetails(ring, section);

  announce(`${section.label}, ${formatBoundary(section.start)} to ${formatBoundary(section.end)}. Playback is limited to this window.`);

}

function dismissDetails() { $('details').hidden = true; displayed = null; }

function clearSelection() { selected = null; player.window = null; player.emit(); $('onion').querySelectorAll('.selected').forEach(e => e.classList.remove('selected')); }

const friendly = name => name.replaceAll('__', ' · ').replaceAll('_', ' ');

function factList(items) {

  const dl = node('dl', null, 'detail-facts');

  for (const [key, value] of items) if (value !== null && value !== undefined && value !== '') { const box = node('div'); box.append(node('dt', key), node('dd', String(value))); dl.append(box); }

  return dl;

}

function showDetails(ring, section) {

  if (!song) return;

  displayed = { ring, section }; const panel = $('details'); panel.replaceChildren(); panel.hidden = false;

  const close = node('button', '×', 'close-details'); close.title = 'Close section details'; close.setAttribute('aria-label', 'Close section details'); close.addEventListener('click', dismissDetails);

  panel.append(close, node('p', ring.name, 'eyebrow'), node('h1', section?.label || (ring.error ? 'Analysis unavailable' : 'Audio preview unavailable'), 'detail-title'));

  if (section) {

    panel.append(node('p', `${formatBoundary(section.start)} — ${formatBoundary(section.end)}  ·  ${(section.end - section.start).toFixed(2)} s`, 'window-time'));

    const play = node('button', null, 'window-play'); play.id = 'windowPlay'; play.append(node('span', 'Play selected window'), node('span', '▶')); play.disabled = Boolean(audioFailure);

    play.addEventListener('click', () => { choose(ring, section); player.play(); }); panel.append(play);

  }

  if (audioFailure) panel.append(node('p', audioFailure, 'error-note'));

  if (ring.error) panel.append(node('p', ring.error, 'error-note'));

  if (song.summary.status === 'failed') panel.append(node('p', `${song.summary.failure_category}: ${song.summary.error}`, 'error-note'));

  if (song.summary.status === 'ok_with_warning') panel.append(node('p', 'The backend reports an EOS retry warning for this transcription.', 'detail-note warning'));

  if (section?.unassigned) panel.append(node('p', 'No timed section was supplied for this interval. This gap does not establish that the recording is silent or instrumental.', 'detail-note warning'));

  else if (section && ring.key === 'vocals') {

    if (section.instrumental) panel.append(node('p', 'The backend explicitly identifies this recording as instrumental. No vocal-section labels have been added.', 'detail-note'));

    else if (section.activityOnly) panel.append(node('p', 'Vocal-activity timing only. This is not a lyric-aligned section or a verified Verse/Chorus label.', 'detail-note warning'));

    else {

      if (section.labelUnverified) panel.append(node('p', 'This numbered vocal segment has no verified Verse/Chorus label in the supplied analysis.', 'detail-note warning'));

      panel.append(factList([['Timing source', section.timing_source], ['Timing validated', section.validated == null ? 'Not supplied' : section.validated ? 'Yes' : 'No'], ['Source group', section.group], ['Match confidence', typeof section.match_confidence === 'number' ? section.match_confidence.toFixed(3) : 'Not supplied']]));

      if (section.flags?.length) panel.append(node('p', section.flags.join(' · '), 'detail-note warning'));

    }

  }

  else if (section && ['major', 'detail'].includes(ring.key)) {

    appendSummary(panel, section);

    if (ring.hasVoice) panel.append(node('p', 'Voice may influence the supplied section boundaries; it is excluded from these instrument summaries.', 'detail-note warning'));

  }

  if (section?.clipped) panel.append(node('p', `The source interval is ${section.sourceStart.toFixed(3)}–${section.sourceEnd.toFixed(3)} s. The displayed/playable arc is its intersection with the original recording. No endpoint has been relabeled as a detected boundary.`, 'detail-note warning'));

  if (ring.warnings.length) panel.append(node('p', ring.warnings.join(' '), 'detail-note warning'));

  const disclosure = node('details', null, 'data-disclosure'); disclosure.append(node('summary', 'Source & timing'));

  disclosure.append(node('p', `${ring.source}. One turn of the record and the bar below it both represent the entire original song. Window playback is limited to the selected start/end. Five-second skips stay inside that window.`));

  if (song.fileErrors.length) disclosure.append(node('p', song.fileErrors.join('\n'), 'error-note'));

  panel.append(disclosure);

}

function appendSummary(panel, section) {

  if (song.summaries.error) { panel.append(node('p', song.summaries.error, 'error-note')); return; }

  const profile = song.summaries.sections.find(s => s.section.rhythmic_section_id === section.rhythmic_section_id);

  if (!profile) { panel.append(node('p', 'No matching rhythmic section was supplied.', 'error-note')); return; }

  panel.querySelector('.detail-title').textContent = `${profile.label} · bars ${section.start_bar}–${section.end_bar - 1} · ${formatTime(section.start)}–${formatTime(section.end)}`;

  const line = (heading, text) => { panel.append(node('h3', heading), node('p', text, 'detail-note')); };

  if (!profile.regularCount) line('What plays', 'Only irregular bars are available; this section is not compared.');

  else {

    line('What plays', profile.instruments.size ? [...profile.instruments].map(([name, value]) => `${instrumentName(name)}: ${value.text}`).join(' · ') : 'No instrument is active in at least half of the comparable bars.');

    const changes = profile.changes;

    line('Why it starts here', changes === null ? 'Comparison unavailable: no previous comparable section or note events are missing.' : changes.length ? changes.slice(0,2).join(' · ') + (changes.length > 2 ? ` · +${changes.length-2} more` : '') : 'No described instrument change.');

    line('vs next', profile.nextChanges === null ? 'Comparison unavailable: no next comparable section or note events are missing.' : profile.nextChanges[0] || 'No described instrument change.');

    line('Boundary', profile.boundary || 'Unavailable: a previous comparable section and complete stability evidence are required.');

  }

  const bars = node('details', null, 'data-disclosure'); bars.append(node('summary', 'Show bars'));

  for (const entry of profile.bars) {

    const button = node('button', null, 'bar-detail');

    const description = entry.irregular ? entry.text : entry.instruments.size ? [...entry.instruments].map(([name,v])=>`${instrumentName(name)}: ${v.text}`).join(' · ') : 'No active instrumental layer recorded.';

    button.textContent = `Bar ${entry.bar.bar} · ${formatTime(entry.bar.start_s)}–${formatTime(entry.bar.end_s)} — ${description}`;

    button.addEventListener('click', () => { const bar = {id:`bar-${entry.bar.bar}`, label:`Bar ${entry.bar.bar}`, start: Math.max(0,entry.bar.start_s), end:Math.min(song.duration,entry.bar.end_s)}; player.select(bar); player.play(); announce(`Playing bar ${entry.bar.bar}.`); });

    bars.append(button);

  }

  panel.append(bars);

  const raw = node('details', null, 'data-disclosure'); raw.append(node('summary', 'Pattern IDs'));

  raw.append(node('p', section.active_pattern_ids?.length ? section.active_pattern_ids.join(' · ') : 'No pattern IDs supplied.', 'detail-note')); panel.append(raw);

}

$('play').addEventListener('click', () => player.play());

$('back').addEventListener('click', () => player.skip(-5)); $('forward').addEventListener('click', () => player.skip(5));

$('timeline').addEventListener('input', () => player.seek(Number($('timeline').value)));

$('hub').addEventListener('click', () => { clearSelection(); player.select(null); dismissDetails(); player.play(); });

document.addEventListener('keydown', event => { if (event.key === 'Escape' && !$('details').hidden && document.activeElement !== $('search')) dismissDetails(); });

document.querySelector('.brand').addEventListener('click', event => { event.preventDefault(); loadToken++; player.pause(); player.duration = 0; player.emit(); song = null; selected = null; displayed = null; needle = null; document.body.classList.remove('has-song', 'loading'); $('stage').hidden = true; $('details').hidden = true; $('search').value = ''; hideResults(); searchMessage(''); document.title = 'bepop'; $('search').focus(); });

fetchSource('app-data/catalog.json').then(({catalog: records, skipped}) => { catalog = records; skippedSongs = skipped; announce(`${catalog.length} MVP songs are searchable.`); }).catch(error => { searchMessage(`Song search is unavailable. ${error.message}`, true); announce(error.message); });
