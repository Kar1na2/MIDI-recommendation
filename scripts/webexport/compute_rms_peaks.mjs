#!/usr/bin/env node
/**
 * compute_rms_peaks.mjs - post-Phase-5 visual revision, point 9.
 *
 * Why: wavesurfer.js's default waveform is drawn from raw per-bucket peak
 * (max |sample|) amplitude. Modern loudness-normalized/heavily-compressed
 * masters keep peak amplitude close to flat across most of the track (the
 * "loudness war" effect) - a peak-based waveform on that kind of audio will
 * always look flat no matter how it's drawn, because the underlying peak
 * data genuinely doesn't vary much. RMS (energy) *does* vary a lot more
 * across a track even on a heavily-compressed master, since it reflects
 * loudness/energy content rather than just the sample ceiling.
 *
 * This script decodes each docs/audio/<id>.mp3 via ffmpeg (mono, 22050Hz,
 * f32le - plenty of resolution for a bucketed waveform, cheap to decode),
 * buckets the raw samples into RMS_BUCKETS windows, computes RMS per
 * window, and **max-normalizes each song's own array to peak at ~0.97**
 * (wavesurfer's synthetic-buffer path only auto-normalizes values that
 * exceed +-1 - see docs/README.md's note on this - so an already-in-range
 * but quiet RMS array would otherwise render just as flat as the peak
 * version it's replacing; per-track normalization is what actually surfaces
 * the relative loud/quiet variation across the track).
 *
 * Writes the result as `waveform_peaks` (a flat array of RMS_BUCKETS
 * floats) into each docs/data/songs/<id>.json, read straight back out by
 * app.js's WaveSurfer.create({ peaks: [song.waveform_peaks], duration }) -
 * see DATA_LAYER.md. Also prints each song's peak-based coefficient of
 * variation (std/mean of the OLD max-abs-per-bucket scheme) so the flattest
 * songs - the ones this fix is actually for - can be identified for the
 * before/after comparison.
 *
 * Isolation: reads docs/audio/*.mp3 and docs/data/songs/*.json (both
 * Phase 4/5 build outputs already in docs/, not the Phase 1-3 pipeline's
 * own audio/ or output/ directories) and writes back into
 * docs/data/songs/*.json only. ffmpeg only, no new Python/Node deps.
 *
 * Usage: node compute_rms_peaks.mjs [--limit N] [--only <song_id>,...] [--dry-run]
 */
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DOCS = path.resolve(__dirname, '..', '..', 'docs');
const AUDIO_DIR = path.join(DOCS, 'audio');
const SONGS_DIR = path.join(DOCS, 'data', 'songs');

const RMS_BUCKETS = 2000; // resolution of the stored waveform array
const SAMPLE_RATE = 22050; // plenty for RMS bucketing; not used for playback
const NORMALIZE_PEAK = 0.97; // leave a little headroom below 1.0

const args = process.argv.slice(2);
const limitIdx = args.indexOf('--limit');
const limit = limitIdx >= 0 ? parseInt(args[limitIdx + 1], 10) : Infinity;
const onlyIdx = args.indexOf('--only');
const only = onlyIdx >= 0 ? new Set(args[onlyIdx + 1].split(',')) : null;
const dryRun = args.includes('--dry-run');

function decodeMonoF32(mp3Path) {
  const buf = execFileSync('ffmpeg', [
    '-v', 'error', '-i', mp3Path,
    '-ac', '1', '-ar', String(SAMPLE_RATE), '-f', 'f32le', 'pipe:1',
  ], { maxBuffer: 1024 * 1024 * 1024 });
  return new Float32Array(buf.buffer, buf.byteOffset, buf.length / 4);
}

function bucketStats(samples, numBuckets) {
  const n = samples.length;
  const rms = new Array(numBuckets).fill(0);
  const peak = new Array(numBuckets).fill(0);
  const bucketSize = n / numBuckets;
  for (let b = 0; b < numBuckets; b++) {
    const start = Math.floor(b * bucketSize);
    const end = Math.max(start + 1, Math.floor((b + 1) * bucketSize));
    let sumSq = 0, maxAbs = 0;
    for (let i = start; i < end && i < n; i++) {
      const v = samples[i];
      sumSq += v * v;
      const a = Math.abs(v);
      if (a > maxAbs) maxAbs = a;
    }
    const count = Math.max(1, Math.min(end, n) - start);
    rms[b] = Math.sqrt(sumSq / count);
    peak[b] = maxAbs;
  }
  return { rms, peak };
}

function normalize(arr, targetPeak) {
  const max = Math.max(...arr, 1e-9);
  const scale = targetPeak / max;
  return arr.map((v) => v * scale);
}

function coefficientOfVariation(arr) {
  const mean = arr.reduce((a, b) => a + b, 0) / arr.length;
  if (mean === 0) return 0;
  const variance = arr.reduce((a, b) => a + (b - mean) ** 2, 0) / arr.length;
  return Math.sqrt(variance) / mean;
}

const songFiles = readdirSync(SONGS_DIR).filter((f) => f.endsWith('.json'));
let processed = 0;
const report = [];

for (const file of songFiles) {
  const songId = file.replace(/\.json$/, '');
  if (only && !only.has(songId)) continue;
  if (processed >= limit) break;

  const songPath = path.join(SONGS_DIR, file);
  const song = JSON.parse(readFileSync(songPath, 'utf8'));
  const mp3Path = path.join(AUDIO_DIR, `${songId}.mp3`);

  const samples = decodeMonoF32(mp3Path);
  const { rms, peak } = bucketStats(samples, RMS_BUCKETS);
  const peakCV = coefficientOfVariation(peak);
  const rmsCV = coefficientOfVariation(rms);
  const normalizedRms = normalize(rms, NORMALIZE_PEAK);

  report.push({ songId, title: song.title, peakCV, rmsCV });
  console.log(
    `${songId.padEnd(45)} peak_CV=${peakCV.toFixed(3)}  rms_CV=${rmsCV.toFixed(3)}` +
    (peakCV < 0.3 ? '  <-- flat under the old scheme' : '')
  );

  if (!dryRun) {
    song.waveform_peaks = normalizedRms.map((v) => Math.round(v * 1e5) / 1e5);
    writeFileSync(songPath, JSON.stringify(song));
  }
  processed++;
}

report.sort((a, b) => a.peakCV - b.peakCV);
console.log('\nFlattest songs under the OLD peak-amplitude scheme (lowest peak_CV first):');
report.slice(0, 8).forEach((r) => console.log(`  ${r.peakCV.toFixed(3)}  ${r.songId}  "${r.title}"`));
console.log(`\n${processed} song(s) processed${dryRun ? ' (dry run - no files written)' : ''}.`);
