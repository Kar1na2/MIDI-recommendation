// Local preview only: whitelist reads of MVP data and exact original MP3s.
// No pipeline imports, analysis, export, uploads, or filesystem writes.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseCSV } from './model.mjs';

const webRoot = path.dirname(fileURLToPath(import.meta.url));
const root = path.dirname(webRoot);
const originals = new Set(parseCSV(fs.readFileSync(path.join(root, 'mvp/sample.csv'), 'utf8')).map(s => s.id));
if (originals.size !== 300) throw new Error(`Expected 300 original songs, got ${originals.size}.`);
const extraRows = parseCSV(fs.readFileSync(path.join(root, 'mvp_325/summary.csv'), 'utf8')).filter(s => s.cohort === 'pilot');
if (extraRows.length !== 25 || new Set(extraRows.map(s => s.track_id)).size !== 25 || extraRows.some(s => originals.has(s.track_id))) throw new Error('The 25 additional song IDs are missing, duplicated, or overlap the original catalog.');
const extra = [], skipped = [];
for (const row of extraRows) {
  const dir = path.join(root, 'mvp_325/songs', row.track_id);
  try {
    const read = name => JSON.parse(fs.readFileSync(path.join(dir, name + '.json'), 'utf8'));
    const report = read('report'), beats = read('beats'), patterns = read('patterns');
    for (const name of ['lyrics', 'layers', 'tuning']) { const stage = read(name); if (stage.status === 'failed' || !stage.data) throw new Error(`${name} analysis is unavailable`); }
    if (report.schema_version !== 'core-mvp/1' || !report.title || !report.artist || !report.rhythmic_sections?.length || !beats.data?.bars?.length || !(beats.data.duration_s > 0) || beats.data.chosen_grid !== 'raw') throw new Error('Required song, rhythm, or raw beat-grid data is missing');
    if (!patterns.data?.patterns?.length || !patterns.data?.runs?.length || !patterns.data?.bars?.length) throw new Error(`No usable pattern definitions or runs${beats.data.bars.every(b => b.beats_per_bar !== 4) ? '; all bars are irregular' : ''}`);
    fs.statSync(sourceAudio(row.track_id, dir));
    const parsed = {};
    for (const field of ['grid_steps_per_instrument', 'instrument_pattern_metrics']) parsed[field] = row[field] ? JSON.parse(row[field]) : null;
    extra.push({id: row.track_id, title: report.title, artist: report.artist, duration: beats.data.duration_s, summary: {...row, ...parsed}});
  } catch (error) {
    let title = row.title, artist = 'Artist unavailable';
    try { const report = JSON.parse(fs.readFileSync(path.join(dir, 'report.json'), 'utf8')); title = report.title || title; artist = report.artist || artist; } catch {}
    skipped.push({id: row.track_id, title, artist, reason: error.message});
  }
}
const extraIds = new Set(extra.map(s => s.id));
const ids = new Set([...originals, ...extraIds]);
function songDir(id) { return extraIds.has(id) ? path.join(root, 'mvp_325/songs', id) : path.join(root, 'outputs', id); }
const staticFiles = new Map(['index.html', 'styles.css', 'app.mjs', 'model.mjs', 'player.mjs', 'summaries.mjs', 'emotion.mjs', 'emotion_view.mjs', 'emotion_readings.mjs'].map(f => ['/' + f, path.join(webRoot, f)]));
staticFiles.set('/', path.join(webRoot, 'index.html'));
const dataFiles = new Set(['report.json', 'beats.json', 'lyrics.json', 'patterns.json', 'layers.json']);
const mime = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8', '.csv': 'text/csv; charset=utf-8', '.mp3': 'audio/mpeg' };
function sourceAudio(id, dir = songDir(id)) {
  const record = JSON.parse(fs.readFileSync(path.join(dir, 'tuning.json'), 'utf8'));
  const source = record.data?.source_audio_path;
  if (typeof source !== 'string' || !source.startsWith('/mnt/d/')) throw new Error('The source MP3 has no supported explicit /mnt/d/ path.');
  const absolute = path.resolve('D:/', source.slice('/mnt/d/'.length));
  const rel = path.relative(path.join(root, 'liked-audio'), absolute);
  if (rel.startsWith('..') || path.isAbsolute(rel) || path.extname(absolute).toLowerCase() !== '.mp3') throw new Error('The registered source is outside liked-audio or is not an MP3.');
  return absolute;
}
function sendError(res, status, message) { res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' }); res.end(JSON.stringify({ error: message })); }
const server = http.createServer((req, res) => {
  if (!['GET', 'HEAD'].includes(req.method)) return sendError(res, 405, 'This preview is read-only. Only GET and HEAD are supported.');
  let file; const url = new URL(req.url, 'http://127.0.0.1');
  try {
    if (url.pathname === '/catalog/additions') { res.writeHead(200, {'Content-Type':'application/json; charset=utf-8', 'Cache-Control':'no-store'}); return res.end(JSON.stringify({extra, skipped})); }
    if (staticFiles.has(url.pathname)) file = staticFiles.get(url.pathname);
    else if (['/mvp/sample.csv', '/mvp/summary.csv'].includes(url.pathname)) file = path.join(root, url.pathname.slice(1));
    else {
      const emotion = url.pathname.match(/^\/data\/emotion\/([A-Za-z0-9]+)(\.readings)?\.json$/);
      const match = url.pathname.match(/^\/outputs\/([A-Za-z0-9]+)\/([^/]+)$/);
      const audio = url.pathname.match(/^\/audio\/([A-Za-z0-9]+)$/);
      if (emotion && (ids.has(emotion[1]) || !emotion[2] && ['corpus', 'manifest', 'calibration'].includes(emotion[1]))) file = path.join(webRoot, 'data/emotion', emotion[1] + (emotion[2] || '') + '.json');
      else if (match && ids.has(match[1]) && dataFiles.has(match[2])) file = path.join(songDir(match[1]), match[2]);
      else if (audio && ids.has(audio[1])) file = sourceAudio(audio[1]);
      else return sendError(res, 404, 'This route is not part of the frontend preview.');
    }
    const stat = fs.statSync(file);
    if (!stat.isFile()) return sendError(res, 404, 'The requested source is not a file.');
    const headers = { 'Content-Type': mime[path.extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-store', 'Accept-Ranges': 'bytes', 'X-Content-Type-Options': 'nosniff' };
    let start = 0, end = stat.size - 1, status = 200;
    if (req.headers.range) {
      const range = req.headers.range.match(/^bytes=(\d*)-(\d*)$/);
      if (!range || (!range[1] && !range[2])) return sendError(res, 416, 'Unsupported byte range.');
      if (range[1]) { start = Number(range[1]); end = range[2] ? Math.min(Number(range[2]), end) : end; }
      else start = Math.max(0, stat.size - Number(range[2]));
      if (start > end || start >= stat.size) { res.setHeader('Content-Range', `bytes */${stat.size}`); return sendError(res, 416, 'The audio byte range is outside this recording.'); }
      status = 206; headers['Content-Range'] = `bytes ${start}-${end}/${stat.size}`;
    }
    headers['Content-Length'] = end - start + 1;
    res.writeHead(status, headers);
    if (req.method === 'HEAD') return res.end();
    const stream = fs.createReadStream(file, { start, end });
    stream.on('error', error => res.destroy(error)); stream.pipe(res);
  } catch (error) { sendError(res, error.code === 'ENOENT' ? 404 : 422, `${url.pathname}: ${error.code === 'ENOENT' ? 'The source file is missing.' : error.message}`); }
});
const port = Number(process.env.BEPOP_FRONTEND_PORT || 8771);
const host = process.env.BEPOP_FRONTEND_HOST || '127.0.0.1';
if (process.env.BEPOP_AUDIT !== '1') server.listen(port, host, () => process.stdout.write(`Bepop frontend preview: http://${host}:${port}/\nRead-only MVP routes. No publication.\n`));
export { extra, skipped };
