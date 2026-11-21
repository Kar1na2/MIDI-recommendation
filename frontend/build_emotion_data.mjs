// Local display assets only. Copies the existing digest without rescoring.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildCatalog } from './model.mjs';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
export const sourceFolder = path.join(root, 'emotion_r2/digest/frontend_data');
export const dataFolder = path.join(root, 'frontend/data/emotion');

export async function localCatalog() {
  process.env.BEPOP_AUDIT = '1';
  const { extra } = await import('./server.mjs');
  const [sample, summary] = await Promise.all(['sample', 'summary'].map(name => fs.readFile(path.join(root, 'mvp', `${name}.csv`), 'utf8')));
  return [...buildCatalog(sample, summary), ...extra];
}

export async function buildEmotionData({ source = sourceFolder, destination = dataFolder, catalog = null, metadataRoot = root } = {}) {
  const songs = catalog || await localCatalog();
  const files = (await fs.readdir(source)).filter(name => name.endsWith('.json') && !['schema.json', 'corpus.json'].includes(name)).sort();
  const tracks = {};
  await fs.mkdir(destination, { recursive: true });
  for (const file of files) {
    const bytes = await fs.readFile(path.join(source, file));
    const data = JSON.parse(bytes);
    if (data.kind !== 'song' || !/^[A-Za-z0-9]+$/.test(data.track_id) || tracks[data.track_id]) throw new Error(`Invalid or duplicate track_id in ${file}`);
    // Copy bytes, including all nulls and source section boundaries.
    await fs.writeFile(path.join(destination, `${data.track_id}.json`), bytes);
    let sections = [];
    try { sections = JSON.parse(await fs.readFile(path.join(metadataRoot, 'emotion_r2/songs', data.track_id, 'sections.json'), 'utf8')).sections; }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    tracks[data.track_id] = {
      scored: data.sections.some(s => [s.words_valence_z, s.background_arousal_z, s.experimental?.voice_arousal, s.experimental?.voice_valence].some(Number.isFinite)),
      lyricSections: data.sections.filter(s => s.section_id.startsWith('lyric-')).length,
      geniusSections: Object.fromEntries(sections.filter(s => ['chorus', 'verse'].includes(s.genius_category)).map(s => [s.section_id, s.genius_category]))
    };
  }
  const withFile = songs.filter(s => tracks[s.id]);
  const instrumentals = [];
  for (const song of songs) {
    const folder = song.summary?.cohort === 'pilot' ? 'mvp_325/songs' : 'outputs';
    try {
      const report = JSON.parse(await fs.readFile(path.join(metadataRoot, folder, song.id, 'report.json'), 'utf8'));
      if (report.ring1_source === 'instrumental' && !report.lyric_sections?.length) instrumentals.push(song.id);
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  const manifest = {
    source: 'emotion_r2/digest/frontend_data',
    tracks,
    coverage: {
      frontendSongs: songs.length,
      withEmotionFile: withFile.length,
      withoutEmotionFile: songs.length - withFile.length,
      withScoredSections: withFile.filter(s => tracks[s.id].scored).length,
      withoutScoredSections: songs.filter(s => !tracks[s.id]?.scored).length,
      instrumentalsWithNoLyricSections: instrumentals.length
    },
    instrumentalTrackIds: instrumentals,
    withoutEmotionTrackIds: songs.filter(s => !tracks[s.id]).map(s => s.id),
    withoutScoredTrackIds: songs.filter(s => !tracks[s.id]?.scored).map(s => s.id)
  };
  await fs.copyFile(path.join(source, 'corpus.json'), path.join(destination, 'corpus.json'));
  await fs.writeFile(path.join(destination, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
  return manifest;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const manifest = await buildEmotionData();
  console.log(JSON.stringify(manifest.coverage, null, 2));
}
