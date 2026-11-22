// Local display assets only. Copies the existing digest without rescoring.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildCatalog } from './model.mjs';
import { makeCalibration, buildSongReadings } from './emotion_readings.mjs';

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
  const entries = [];
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
    entries.push({ data, sections });
    tracks[data.track_id] = {
      scored: data.sections.some(s => [s.words_valence_z, s.background_arousal_z, s.experimental?.voice_arousal, s.experimental?.voice_valence].some(Number.isFinite)),
      lyricSections: data.sections.filter(s => s.section_id.startsWith('lyric-')).length,
      geniusSections: Object.fromEntries(sections.filter(s => ['chorus', 'verse'].includes(s.genius_category)).map(s => [s.section_id, s.genius_category]))
    };
  }
  const calibration = makeCalibration(entries);
  const calibrationSummary = { ...calibration };
  delete calibrationSummary.distributions;
  await fs.writeFile(path.join(destination, 'calibration.json'), JSON.stringify(calibrationSummary, null, 2) + '\n');
  let models = [];
  try {
    const manifest = JSON.parse(await fs.readFile(path.join(metadataRoot, 'emotion_r2/models_amended.json'), 'utf8'));
    models = manifest.models.filter(model => ['T1', 'T3', 'L1'].includes(model.role)).map(model => ({
      role: model.role,
      id: model.id,
      family: model.family || null,
      coverageNote: model.coverage_note || null,
      distinctFromT1: model.distinct_from_T1 || null,
      relatedFamily: model.related_family || null,
      languages: model.languages || [],
      trainingData: model.training_data || null
    }));
    if (new Set(models.map(model => model.role)).size !== 3) throw new Error('Expected model manifest entries for T1, T3 and L1.');
  } catch (error) { if (error.code !== 'ENOENT') throw error; }
  await fs.writeFile(path.join(destination, 'models.json'), JSON.stringify({ source: 'emotion_r2/models_amended.json', models }, null, 2) + '\n');
  for (const entry of entries) {
    const readOptional = async filename => {
      try { return JSON.parse(await fs.readFile(path.join(metadataRoot, filename), 'utf8')); }
      catch (error) { if (error.code === 'ENOENT') return null; throw error; }
    };
    const id = entry.data.track_id;
    const [probabilities, l1, descriptions] = await Promise.all([
      readOptional(`emotion_r2/display/text_model_probs/${id}.json`),
      readOptional(`emotion_r2/scores/L1/${id}.json`),
      readOptional(`emotion_r2/display/l1_readings/${id}.json`)
    ]);
    for (const source of [probabilities, l1, descriptions]) if (source && source.track_id !== id) throw new Error(`Mismatched track ID for ${id}`);
    for (const section of descriptions?.sections || []) {
      if (section.reading && (section.overlap_flags?.four_word_overlap !== false || section.overlap_flags?.four_cjk_character_overlap !== false)) {
        throw new Error(`L1 reading did not pass both overlap checks for ${id}/${section.section_id}`);
      }
    }
    const reading = buildSongReadings(entry.data, entry.sections, calibration, { probabilities, l1, descriptions });
    await fs.writeFile(path.join(destination, `${entry.data.track_id}.readings.json`), JSON.stringify(reading, null, 2) + '\n');
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
