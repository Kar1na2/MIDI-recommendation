# Repository guide

Start here to find a feature or distinguish application code from media. The existing media trees and unrelated experiments have not been moved.

## Features

| Feature | Code | Page / generated output | Status |
| --- | --- | --- | --- |
| Rhythm patterns + separate vocal form | `scripts/rhythm_layers/` | `preview/rhythm-layers/`; `output/analysis/RHYTHM_VOCAL_PREVIEW_REPORT.md` | Local review; main disc has 3 rings, stem discs have 2 |
| Other-stem method audit | `scripts/rhythm_layers/diagnose_other.py` | `output/analysis/MEANINGFUL_LOVE_OTHER_DIAGNOSTIC.md`; generated figures/HTML in `output/analysis/meaningful_love_other/` | Exact WAV fingerprint, unassisted versus supplied boundaries, pitch and attack evidence |
| Published instrumental record player | `scripts/instrumentals/` | `docs/layers.html`, `docs/css/instrumentals.css`, `docs/js/instrumentals.js`, `docs/data/instrumentals/` | Current published feature; not overwritten by the local review |
| Audio hosting | `instrumental-audio-host/` | Worker, upload manifest, host verification | Separate from GitHub Pages |
| Earlier song-layer experiment | `scripts/webexport/build_song_layers.py` | `docs/data/layers/`, `output/analysis/SONG_PATTERN_LAYERS_REPORT.md` | Earlier exploratory export |
| Audio → stems → MIDI → correction | `scripts/separation/`, `scripts/transcription/`, `scripts/chords/`, `scripts/correction/` | Media/transcription folders below | Operational pipeline; use the stage commands in `README.md` |
| Evaluation and hand-reviewed references | `scripts/evaluation/`, `ground_truth/`, `ground_truth_anchor/` | Evaluation reports in `output/` | Separate from automatic pattern candidates |
| Cross-song analysis experiments | `scripts/analysis/`, shared `lib/` | `output/analysis/` | Existing research; not part of the new local player |
| Completed ingestion / one-off work | `archive/` | Local import manifests and logs | Historical tools |

## Audio, scores, and local data

| Directory | Contains | Use |
| --- | --- | --- |
| `audio/`, `k-indie/`, `liked-audio/` | Original MP3 files plus ingestion logs | Main-record playback and source recordings |
| `stems/<recording>/` | `bass.wav`, `drums.wav`, `other.wav`, `vocals.wav` | Stems separated from the original recording; vocals use this original clock |
| `instrumentals/audio/` | Dedicated instrumental MP3 recordings | Instrumental-only source database |
| `instrumentals/stems/<Spotify ID>/` | Instrumental `bass.wav`, `drums.wav`, `other.wav` | Stem-disc playback and rhythm/harmony analysis |
| `instrumentals/catalog.sqlite` | Active instrumental catalog | **Use catalog tools inside WSL only. Do not open the active database from Windows.** |
| `midi/`, `merged/`, `corrected/` | Derived MIDI files | Transcriptions and corrections; not original audio |
| `chords/` | Derived chord-note tables and tags | Harmonic research |
| `output/analysis/rhythm_layers/features/` | Cached onset/chroma/spectral representations | Regenerable local rhythm features |
| `output/analysis/rhythm_layers/private/` | Source lyric text, word alignments, model files | Local working data; never export to the webpage |
| `output/analysis/rhythm_layers/records/` | Sanitized per-song rhythm/vocal summaries | Generated data with section headings and timestamps only |
| `docs/` | Published static website and sanitized data | No full MP3/WAV files or lyric transcripts |

The old 75 recordings have historical filenames. The new inventory links them through `liked-audio/download_manifest.json`, including explicit duplicate/hash references, instead of guessing from titles.

## Run the local review

Inside WSL, from the repository root:

```sh
.venv/bin/python -m scripts.rhythm_layers.fetch_lyrics --limit 3
.venv/bin/python -m scripts.rhythm_layers.align_vocals --limit 3
.venv/bin/python -m scripts.rhythm_layers.build_preview --samples
.venv/bin/python -m unittest scripts.rhythm_layers.test_rhythm -v
node scripts/rhythm_layers/test_player.cjs
.venv/bin/python -m scripts.rhythm_layers.diagnose_other
```

Then serve from Windows or WSL:

```sh
python -m scripts.rhythm_layers.serve_preview --port 8767
```

Open `http://127.0.0.1:8767/`. The read-only server supports byte-range MP3/WAV seeking and serves only registered audio files. Its static root excludes the private lyric/alignment directory. Stop it with Ctrl+C when review is finished.

The Silo Other-stem audit is linked from the local player and served at `/diagnostics/meaningful-love-other/`. Its plots and reproducible metrics stay under the ignored local output directory; only the written Markdown report is tracked.

See `scripts/rhythm_layers/README.md` for method, dependencies, and resuming. Nothing in this workflow pushes a commit or uploads a GitHub Pages export. Publication follows the user's review.
