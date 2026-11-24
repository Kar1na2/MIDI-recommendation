// Export only data used by the public homepage. Source analysis stays read-only.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildCatalog, parseCSV } from './model.mjs';
process.env.BEPOP_AUDIT = '1';
const {extra, skipped} = await import('./server.mjs');
const here = path.dirname(fileURLToPath(import.meta.url)), root = path.dirname(here);
const out = path.resolve(process.env.BEPOP_SITE_EXPORT_DIR || path.join(here, 'site-export'));
if (fs.existsSync(out)) throw new Error('site-export already exists; choose or clear the previous export explicitly.');
const read = f => fs.readFileSync(path.join(root, f), 'utf8');
const original = buildCatalog(read('mvp/sample.csv'), read('mvp/summary.csv'));
const host = new Map(parseCSV(read('instrumental-audio-host/upload_manifest.csv')).filter(x => x.object_key === `songs/${x.track_id}/original.mp3`).map(x => [x.track_id,x]));
const extraIds = new Set(extra.map(s=>s.id));
const keep = (o, fields) => Object.fromEntries(fields.filter(k=>Object.hasOwn(o,k)).map(k=>[k,o[k]]));
const catalog = [], audioMissing = [];
fs.mkdirSync(out, {recursive:true});
for (const item of [...original,...extra]) {
  const dir = path.join(root, extraIds.has(item.id) ? 'mvp_325/songs' : 'outputs', item.id);
  const source = name => { try { return JSON.parse(fs.readFileSync(path.join(dir,name+'.json'),'utf8')); } catch (e) { if (e.code === 'ENOENT') return {error:`${name}.json: source file is missing.`}; throw e; } };
  const tuning=source('tuning'), hosted=host.get(item.id);
  const matching = hosted && typeof tuning.data?.source_sha256 === 'string' && hosted.sha256 === tuning.data.source_sha256;
  const audioUrl = matching ? `https://song-pattern-audio.bepop.workers.dev/v1/${item.id}/original.mp3` : null;
  if (!audioUrl) audioMissing.push({id:item.id,artist:item.artist,title:item.title,reason:hosted ? 'Hosted recording does not have a matching source hash.' : 'Original recording is not listed on the existing audio host.'});
  catalog.push({id:item.id,artist:item.artist,title:item.title,duration:item.duration,summary:keep(item.summary,['status','failure_category','error']),audioUrl});
  const report=source('report'),beats=source('beats'),lyrics=source('lyrics'),patterns=source('patterns'),layers=source('layers');
  if (!report.error) {
    report.rhythmic_sections=report.rhythmic_sections?.map(s=>keep(s,['rhythmic_section_id','start_bar','end_bar','start_s','end_s','boundary_novelty','boundary_source','irregular_bars','active_pattern_ids']));
    report.lyric_sections=report.lyric_sections?.map(s=>keep(s,['label','display_label','labels','group','raw_start_s','raw_end_s','timing_source','validated','match_confidence','flags']));
  }
  const compactReport = report.error ? report : keep(report,['schema_version','ring1_source','rhythmic_sections','lyric_sections','patterns']);
  if (compactReport.patterns) compactReport.patterns=compactReport.patterns.map(p=>keep(p,['instrument']));
  const compact = {
    report:compactReport,
    beats:beats.error ? beats : {status:beats.status,data:keep(beats.data||{},['chosen_grid','bars'])},
    lyrics:lyrics.error ? lyrics : {status:lyrics.status,data:keep(lyrics.data||{},['ring1_segments'])},
    patterns:patterns.error ? patterns : {status:patterns.status,data:{patterns:(patterns.data?.patterns||[]).map(p=>keep(p,['pattern_id','instrument'])),runs:(patterns.data?.runs||[]).map(r=>keep(r,['pattern_id'])),bars:(patterns.data?.bars||[]).filter(r=>r.active===true && !r.instrument.startsWith('voice__') && !r.instrument.endsWith('__irregular')).map(r=>keep(r,['bar','instrument','active','events','steps_per_bar','onset_count','pattern_id'])),irregular_bars:patterns.data?.irregular_bars}},
    layers:layers.error ? layers : {status:layers.status,data:{pattern_notes:(layers.data?.pattern_notes||[]).filter(n=>!n.instrument.startsWith('voice__') && !n.instrument.endsWith('__irregular')).map(n=>keep(n,['bar','instrument','step','pitch','onset_s','offset_s']))}}
  };
  const dest=path.join(out,'app-data','songs',item.id); fs.mkdirSync(dest,{recursive:true});
  for (const [name,value] of Object.entries(compact)) fs.writeFileSync(path.join(dest,name+'.json'),JSON.stringify(value));
}
fs.mkdirSync(path.join(out,'app-data'),{recursive:true});
fs.writeFileSync(path.join(out,'app-data','catalog.json'),JSON.stringify({catalog,skipped}));
fs.writeFileSync(path.join(out,'app-data','audio-unavailable.json'),JSON.stringify(audioMissing,null,2));
for (const f of ['index.html','styles.css','app.mjs','model.mjs','player.mjs','summaries.mjs','emotion.mjs','emotion_readings.mjs','music_readings.mjs','emotion_view.mjs']) fs.copyFileSync(path.join(here,f),path.join(out,f));
fs.cpSync(path.join(here, 'data/emotion'), path.join(out, 'data/emotion'), { recursive: true });
let html=fs.readFileSync(path.join(out,'index.html'),'utf8').replace('href="/" aria-label="bepop home"','href="./" aria-label="bepop home"');
fs.writeFileSync(path.join(out,'index.html'),html);
let app=fs.readFileSync(path.join(out,'app.mjs'),'utf8');
app=app.replace('`/outputs/${item.id}/${name}.json`','`app-data/songs/${item.id}/${name}.json`');
const boot=/Promise\.all\(\[\s*fetchSource\('\/mvp\/sample\.csv', false\),[\s\S]*?\]\)\.then\(\(\[sample, summary, additions, manifest, corpus, models\]\) => \{[\s\S]*?\}\)\.catch\(error => \{ searchMessage\(`Song search is unavailable\. \$\{error\.message\}`, true\); announce\(error\.message\); \}\);/;
if (!boot.test(app)) throw new Error('Frontend bootstrap changed; static export must be updated.');
app=app.replace(boot,`Promise.all([
  fetchSource('app-data/catalog.json'),
  fetchSource('./data/emotion/manifest.json').catch(() => null),
  fetchSource('./data/emotion/corpus.json').catch(() => null),
  fetchSource('./data/emotion/models.json').catch(() => null)
]).then(([records, manifest, corpus, models]) => {
  catalog = records.catalog; skippedSongs = records.skipped;
  emotionManifest = manifest; emotionCorpus = corpus; emotionModels = models;
  announce(\`\${catalog.length} MVP songs are searchable.\`);
}).catch(error => { searchMessage(\`Song search is unavailable. \${error.message}\`, true); announce(error.message); });`);
fs.writeFileSync(path.join(out,'app.mjs'),app);
let player=fs.readFileSync(path.join(out,'player.mjs'),'utf8');
const audioLine="    this.audio.src = `/audio/${song.id}`; this.audio.preload = 'metadata'; this.audio.load(); this.emit();";
if (!player.includes(audioLine)) throw new Error('Player loading changed; static export must be updated.');
player=player.replace(audioLine,"    if (!song.audioUrl) { this.blocked = true; this.audio.removeAttribute('src'); this.emit(); this.failed('Original audio preview is unavailable: this recording is not verified on the audio host.'); return; }\n    this.audio.src = song.audioUrl; this.audio.preload = 'metadata'; this.audio.load(); this.emit();");
fs.writeFileSync(path.join(out,'player.mjs'),player);
fs.writeFileSync(path.join(out,'.nojekyll'),'');
console.log(JSON.stringify({songs:catalog.length,hosted:catalog.length-audioMissing.length,audioUnavailable:audioMissing.length,skipped},null,2));
