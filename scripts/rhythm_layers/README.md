# Rhythm + vocal-form review

This feature keeps vocal form separate from instrumental pattern analysis. It does not train a classifier or compare songs with one another.

The local player lives in `preview/rhythm-layers/`. Production remains in `docs/` until the review is approved.

## Files

| File | Responsibility |
| --- | --- |
| `common.py` | ID-based inventory, historical source aliases, and generated paths; never opens the active SQLite catalog |
| `fetch_lyrics.py` | Public Genius source lookup and robust section-heading parsing; stores full text only in ignored private data |
| `source_overrides.json` | Supplied URLs, known language, timing review targets, and explicitly attributed listening annotations |
| `align_vocals.py` | Stable-ts word alignment against known text, preserved source-section splits, and independent bounded ASR review |
| `patterns.py` | Binary attack grids, normalized harmonic sequences, recurrent figures, two pattern rings, and instrumental/original clock matching |
| `build_preview.py` | Sanitized page data, vocal/instrumental boundary statistics, missing-source reporting, and sample report |
| `serve_preview.py` | Read-only localhost preview, registered audio routes, and HTTP byte-range seeking |
| `test_rhythm.py` | Gain/ramp invariance, source-heading timing, drum pitch independence, private-data export, and clock safety checks |
| `test_player.cjs` | Actual player regression checks for absolute record position, passage seeking, pause/end, and the WAV clock |
| `diagnose_other.py` | Reproduce Silo Other analysis without listening overrides; generate plots, candidate scores, source fingerprint, and a self-contained visual report |

## Dependencies and commands

Run analysis in the repository's WSL `.venv` with its existing librosa, NumPy, SciPy, SoundFile, Numba, PyTorch, and CUDA setup. The optional lyric-alignment additions are `stable-ts==2.19.1`, `beautifulsoup4`, and `rapidfuzz`. The default aligner is Whisper large-v3. `serve_preview.py` uses only the Python standard library and can run from Windows.

```sh
.venv/bin/python -m scripts.rhythm_layers.fetch_lyrics --limit 3
.venv/bin/python -m scripts.rhythm_layers.align_vocals --limit 3
.venv/bin/python -m scripts.rhythm_layers.build_preview --samples
python -m scripts.rhythm_layers.serve_preview
node scripts/rhythm_layers/test_player.cjs
.venv/bin/python -m scripts.rhythm_layers.diagnose_other
```

Use `--track-id <Spotify ID>` to inspect one recording. Fetch/alignment scripts retain their local indexes; `--force` refreshes a selected record. `build_preview --resume` reuses sanitized per-track results; omit `--resume` after changing methodology or inputs. Without `--samples` or `--limit`, the builder attempts the catalog, recording missing inputs explicitly. Missing lyric sources remain pending; it does not invent section names or assign another version's lyrics.

Normal public page failures, source ambiguity, and access restrictions are recorded. No access controls are bypassed. A source title marked instrumental is excluded from vocal-lyric alignment even when a related lyric URL is supplied.

## What the rings mean

- Main record: vocal sections, broad instrumental rhythm/harmony sections, finer motifs/variations.
- Bass/Drums/Other: two pattern rings each. Vocals never enter these features.
- Attacks use spectral-flux onset detection, then binary event positions. Section comparison uses event timing and normalized pitch-class sequences, with a small texture contribution; it does not use RMS or loudness-trend labels.
- Drum recurrence uses attack positions without assigning melodic pitch-class meaning to percussion.
- A beat grid is estimated. Four subdivisions per beat and four beats per Tidal-style cycle are display conventions, not a confirmed meter or a playable note transcription.
- Broad rings retain the strongest four automatic changes; finer rings retain the strongest six plus inherited cuts. Supplied listening anchors are visibly attributed. No extra split is required simply to fill a ring.
- Dedicated instrumental versions can have different intros or edits. A constrained onset/harmony match maps them to the original MP3. Unreliable mappings disable main-disc pattern playback while stem WAV windows retain their own clock.

Vocal timings are model candidates. Preserved source-heading splits are preferred over fallback token/character grouping. Source/ASR text agreement is useful evidence, not proof of word accuracy. Long gaps without aligned source lyrics receive explicit gap windows; ad-libs may still exist. Listen before treating any timing as ground truth.

## Local and public data

`output/analysis/rhythm_layers/private/` contains lyric text and full word/ASR results, and remains ignored. `preview/rhythm-layers/data.json` is generated and ignored. The exporter accepts an allowlist of vocal metadata and rejects transcript/word fields before writing the page data. `output/analysis/RHYTHM_VOCAL_PREVIEW_REPORT.md` is the review artifact.

The localhost audio endpoint supports partial MP3/WAV reads. Playback seeks to the selected window, stops at its end, and resumes from the paused position. The slider shows progress within the passage; the radial stylus shows absolute time in the whole displayed recording, on the same clock as its ring arcs. Main-disc arcs use the original MP3 clock; stem-disc arcs use that instrumental WAV's clock.

## Inspect a missed Other pattern

`diagnose_other` reads the registered WAV and the same feature extractor, pulse estimator, grid, and cycle scorer as the player. It reruns cuts **without** the supplied listening anchors, plots those separately, and verifies candidate-score parity with `cycle_summary`. Its extra constant-Q spectrogram preserves pitch register for inspection only; it does not silently change the current detector. Generated charts/metrics and the embedded-figure HTML stay local under `output/analysis/meaningful_love_other/`; a served copy is at `/diagnostics/meaningful-love-other/`. The authored Markdown findings are in `output/analysis/MEANINGFUL_LOVE_OTHER_DIAGNOSTIC.md`.
