# Pipeline history

Audited 2026-11-05 before replacement. Baseline HEAD: `dccd461`. Python is
3.11 (`.python-version`); dependency manager is uv. The working tree also contained
uncommitted AMT, MIDI-first, and rhythm-preview experiments. Those source files
are deliberately included in the documentation commit tagged `legacy-pipelines`,
so the tag preserves the code actually audited, not merely the older HEAD.
`docs/LEGACY_INVENTORY.json` lists source/config files, hashes, entry points,
imports, and output-path evidence. No project notebooks were found. Downloaded
environments, vendored models, binary audio, and generated catalogs were inspected
as dependencies/data, not mistaken for project source.

## v0 — Spotify metadata / full-mix Basic Pitch prototype

- Entry points: `archive/fetch_liked_songs.py`, `fetch_playlist_songs.py`,
  `download_songs.py`, `extract_audio.py`, `convert.py`. Example:
  `python archive/convert.py --help`; its parser accepts input/output directories.
  `python archive/extract_audio.py --songs archive/liked_songs.json` requests
  Spotify analysis, including bars, beats and sections. It is an independent
  API experiment, not the later transcription pipeline's beat grid.
- Steps: Spotipy OAuth metadata -> yt-dlp YouTube resolution and MP3 downloads
  -> Basic Pitch `predict_and_save`, ICASSP 2022 model, default thresholds;
  alternatively Spotify audio-analysis JSON. Wrapper/library versions were not
  locked at the original prototype commit; do not infer a historical install
  version from today's environment.
- Outputs: playlist/song JSON, MP3 and download manifests; conversion MIDI,
  optional sonification/model-output/event files; Spotify per-track analysis,
  section and pitch JSON beneath the chosen output directory.
- Commits: `afc9384` (prototype), `77b4e49` (reorganization), `17ca75f`
  (archive), `78c514b` (liked-library ingestion). The archive tag captures the
  exact surviving versions.
- Failure/limit: whole-mix polyphony and instrument attribution; Spotify API
  availability is separate from measured note accuracy. No meter-aware pattern
  compressor was connected to this prototype.
- Reuse: acquisition utilities and original recordings are kept. No model
  predictions are promoted to ground truth.

## v1 — Demucs / Basic Pitch / ADTLib corpus and chord-note analysis

- Commands, in order (original Makefile, later `archive/Makefile.legacy`):
  `make separate-stems`, `make transcribe-harmonic`, `make transcribe-drums`,
  `make merge-stems`, `make extract-chords`, `make tag-chord-tones`,
  `make correct-notes`, `make correct-octave-duplicates`,
  `make export-note-events`. Targets dispatch `python -m scripts.<stage>.<name>`
  using uv, except drums use `tools/adtlib-env/bin/python`.
- Separation: Demucs **htdemucs_ft**, not htdemucs. Four stems from original
  MP3; Torch GPU when available, warned CPU fallback. Wrapper used CLI defaults
  for overlap/shifts; no beat/downbeat inference. Current observed versions:
  demucs 4.1.0, torch/torchaudio 2.11.0+cu130.
- Harmonic transcription: Basic Pitch 0.4.0, ICASSP 2022 model, `other` and
  `bass`, onset threshold 0.5, frame threshold 0.3, remaining predict defaults.
  Temporary 0.45 onset experiment was reverted. TensorFlow CPU inference.
- Drums: ADTLib under Python 3.7.17, TensorFlow 1.15.5, NumPy 1.18.5,
  protobuf 3.19.6, madmom; unpinned ADTLib/madmom source versions were not
  recorded. Kick/snare/closed hat -> GM 36/38/42, velocity 100, 50 ms duration.
  MIDI 120 BPM/480 PPQ is a seconds-to-ticks convention, **not** detected tempo.
- Merge: pretty_midi; one named Other/Bass/Drums track per stem. Chordino
  `nnls-chroma:chordino`, `chordnotes` output through Vamp (native plugin
  build unversioned; retained build instructions). Chord-tone tags feed
  stuck-pitch and octave-duplicate heuristics, then flattened CSV. Export
  prefers `corrected/` over `merged/`, so stale correction ancestry matters.
- Analysis: `scripts/analysis/` builds forte-class chord and signed-note-
  interval sequences, n-grams, Smith–Waterman alignments, corpus significance,
  motifs and SQLite reports. `lib/` contains generic alignment and scoring;
  embedded corpus calibration belongs to legacy predictions. No beat grid.
- Additional `python -m scripts.analysis.analyze_tidal_patterns`: estimated
  **constant** BPM from MIDI onsets, normalized to 60–180 BPM, arbitrary time-zero
  origin, 16 slots/bar and two-bar windows; all seven stem combinations,
  zlib level 9, pitch-class fingerprints. This did have an assumed grid, but
  neither measured downbeats nor a performance-following beat grid. Its ratio
  is compressed bytes / raw bytes, unlike the new bars / runs definition.
- Outputs: `stems/<song>/*.wav`, `midi/<song>/*.mid`, `merged/<song>.mid`,
  `chords/<song>/other_chordnotes.csv`, `other_note_tags.csv`,
  `corrected/<song>.mid`, changelogs/proposals; `output/note_events_other.csv`,
  `chord_segments.csv`, `output/analysis/*.csv`, SQLite and
  `output/analysis/tidal_patterns/`. See inventory for the entire consumer graph.
- Commits: `77b4e49`, `17ca75f`, `ff3f03b` (chord/note analysis), `875026a`
  (web export), `78c514b` (1,128-song expansion). Tidal analyzer is previously
  uncommitted; preserved at `legacy-pipelines`.
- Failure: soft/short high notes disappear at thresholds; false positives and
  octave artifacts survive corrections. Seconds-only interval sequences cannot
  express reliable bar-phase recurrence; guessed tempo/time-zero drifts from
  real musical bars. No literal "no rhythmic consensus" message in this branch.
- Reuse: hand annotations, source IDs, original audio and generic `lib/` are
  kept. None of the automatic pitch corrections are carried into the new path.

## v1b — Acoustic layer explorers / rhythm-harmony-v1 / lyric preview

- Earlier entry points: `python -m scripts.webexport.build_song_layers`;
  `python -m scripts.instrumentals.ingest --download`, `separate` / `separate_api`,
  `analyze`, `export_site`, `report`. Instrumental catalog has separate source
  recordings and clocks. Commits `9009892`, `acf076c`, `fba7f64`, `dccd461`.
- Instrumental analysis: librosa chroma/onset/spectral features and one-second
  summaries, hierarchical limited cuts and six-second recurrence candidates.
  htdemucs_ft -> 22,050 Hz mono PCM16 bass/drums/other previews; vocal residual
  discarded. Those files cannot substitute for the original-mix vocal stem.
- Later, previously uncommitted commands:
  `.venv/bin/python -m scripts.rhythm_layers.fetch_lyrics --limit 3`,
  `...align_vocals --limit 3`, `...build_preview --samples`,
  `python -m scripts.rhythm_layers.serve_preview`, `...diagnose_other`.
- Rhythm-harmony-v1: audio resampled to 11,025 Hz; FFT 2048/hop 256;
  log-mel positive flux, onset delta .09/wait 2; librosa beat_track on the
  **mean stem flux**, start_bpm 110/tightness 75/trim false. Four subdivisions
  per beat, four beats per display cycle. Sparse pulse silently becomes a
  120-BPM grid with zero confidence. No measured meter/downbeats.
- Cycle summary checks lags 1/2/4/8/16 beats. Requires two cycles and four
  combined attacks per eligible part. Rhythm has a one-slot tolerance weighted
  .35. Combined score = .75 rhythm + .25 harmony; shortest lag within .025 of
  best is selected. **Equivalent of "no rhythmic consensus":**
  `scripts/rhythm_layers/patterns.py::cycle_summary` returns `beats=None` when
  no lag qualifies, or when score < .60 or rhythm similarity < .55.
  `make_layers` then emits "No short stable attack cycle met the recurrence
  threshold." The phrase itself was not found literally in project source.
  Polyphonic flux merges/misses retriggers, pitch-class features erase register,
  stem noise creates extra attacks, and the unverified pulse/bar phase makes
  corresponding attacks fail these thresholds. Stem artifacts remain a
  hypothesis; this scorer does not prove their cause.
- Cuts compare 32/16-slot contexts, robust peak threshold and limited 4/6 cuts;
  some user listening anchors override cuts. They are annotations, not detected
  structure. Cross-recording constrained DTW can fail separately from recurrence.
- Lyrics: cached Genius headings/text -> stable-ts 2.19.1, Whisper large-v3
  cross-attention/DTW on original vocal stem, original_split true, regroup false,
  suppress_silence false; independent ASR review. Fallback token/character
  proportional grouping can merge or misplace Pre-Chorus/Chorus boundaries.
  No LRCLIB order-preserving line matcher. Versions observed: librosa .11.0,
  NumPy 1.26.4, SoundFile .14.0, beautifulsoup4 4.15.0, rapidfuzz 3.14.6.
- Outputs: `instrumentals/catalog.sqlite`, `catalog.json`, `instrumentals/audio/`,
  `instrumentals/stems/`; `docs/data/layers/`, `docs/data/instrumentals/`;
  `output/analysis/rhythm_layers/{features,private,tracks}/`, private lyrics,
  alignments and indexes; `preview/rhythm-layers/data.json`, diagnostics;
  `output/analysis/meaningful_love_other/` and review Markdown.
- Reuse: original Genius URL, source identity and explicit manual overrides
  retained; no old aligned timestamps or imposed rhythm cuts are imported.

## v1c — Specialist transcription / arpeggio experiments

- Commands: `python -m scripts.transcription.transcribe_specialist --engine
  piano|pyin|adtof --audio <stem> --output <new.mid>`; analysis
  `inspect_meaningless_love`, `search_arpeggio_triples`, `search_classical_apex`;
  exporter `build_arpeggio_experiment_page_data`.
- ByteDance piano note/pedal model (explicit checkpoint hash recorded per run),
  monophonic librosa pYIN bass (27.5–440 Hz, hop 160, frame 2048, probability
  >=.2, minimum .05 s), ADTOF drums; mono polyphase resampling to 16 kHz.
  Experiment environment versions/checkpoint hashes live in generated run JSON,
  not a universal pinned project config. External score MIDI is a separate
  reference clock. No claim of verified general-purpose mix transcription.
- Outputs: `output/analysis/meaningless_love/`, `arpeggio_experiment/`,
  diagnostic CSV/PNG/NPZ, MIDI/pedal events, specialist run JSON, external scores;
  `docs/data/experiment/`. Commits `160143a`, `fc2b166`, `91ff134`, `1af605d`,
  `bb42a50`, `c198bfb`; specialist wrapper was uncommitted before the archive tag.
- Failure/limit: piano-specific model and monophonic F0 assumptions don't
  generalize to synthesizer polyphony. Search shapes and visual agreement do
  not establish accuracy. Keep downloaded scores, audio and authored reports.

## v2 — YourMT3+ AMT and independent MIDI-first migration

- Previously uncommitted entry points: `python -m scripts.amt.bootstrap_yourmt3`,
  `output/amt/env/bin/python -m scripts.amt.transcribe --audio <file>
  --output-dir <new-dir>`, `...scripts.amt.midi_patterns --midi <file>
  --output <json>`. Later `make midi-regression-midi`, `midi-separate`,
  `python -m scripts.midi_pipeline.run --audio <file> --source-id <id>`,
  `phase3`, `phase3b`, `phase3c`, `review_workbook`. Tests/fixtures/training
  experiments are also in those packages. Archived in the documentation commit;
  there is no earlier commit hash for this working-tree-only version.
- Source/checkpoint: HF Space `mimbres/YourMT3` revision
  `5e66c1ea173a8186e0d20432b841d3180cc015b5`, experiment
  `mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops`,
  checkpoint SHA256 `ae38e415c79efd5592dcb9b658cdb99ddb11d4c4e1eaa364cab04a052473fc25`.
  Multi-T5 26 layers, perceiver-tf, MoE 8 experts/top 2, width factor 4,
  SiLU/RoPE, mc13_full_plus_256 task, 16-kHz mono polyphase input,
  batch size 1/float32, native upstream decoder/writer. Source-lock verifies
  78 downloaded source/model files in the later path.
- Tested WSL stack: Python 3.11.14, torch/torchaudio 2.11.0+cu130,
  NumPy 1.26.4, transformers 4.45.1, lightning/pytorch-lightning 2.6.6,
  scipy 1.17.1, SoundFile .14.0, pretty-midi .2.11.post0, einops .8.2,
  matplotlib 3.11.2, wandb .31.0, demucs 4.1.0, mir-eval .8.2, mido 1.3.3.
- Both **full mix and Other stem** were transcribed in the Phase 3 comparison;
  it is inaccurate to describe all v2 runs as stem-only. New separations default
  to htdemucs_ft, shifts 0/overlap .25, preserve all stems and clock provenance.
- Analysis: attack groups anchored within 30 ms; contiguous 4/6/8/12/16-group
  exact IOI buckets in early AMT; later 3–24-group descriptor shortlist and DTW
  (max cost .22), relative timing/duration/pitch/contour. Beat grid only when
  `--bpm` and origin explicitly supplied; otherwise `none_seconds_only` /
  `unavailable_seconds_only`. No inferred bar meter/downbeat sections.
- Outputs: `output/amt/` models/env, native transcription MIDI, notes CSV,
  run JSON/adapters/fixtures; `output/midi_pipeline/runs/<content-id>/` raw MIDI,
  notes JSON (absolute recording clock), token NPZ, candidate JSON, PNG,
  reconstruction WAV and review HTML; separations, phase3 comparison crops,
  screenshots, workbook exports and human-review JSON. These are all preserved.
- Failure: existing Phase 3 report records 206 mix events/30 seconds; 31 of 38
  pitched focus-window events shorter than 50 ms, decoding errors and unverified
  accuracy. Model MIDI drum offsets/writer velocities are conventions. Better
  symbolic diagnostics did not repair the missing measured grid or establish
  acoustic note accuracy. No literal consensus message; empty/weak candidates
  reflect sparse groups, shortlist/DTW cost and poor transcription.
- Reuse: the pinned YourMT3 inference implementation becomes an explicit
  comparison backend; its source/checkpoint provenance remains. No implicit
  fallback from MuScriptor, no old event caches, no training/mood classifier.

## Removal / output retention

After the note commit and annotated tag exist, remove `scripts/{amt,midi_pipeline,
rhythm_layers,separation,transcription,correction,chords,evaluation,export,analysis}`
and the obsolete exporters/validation fixtures under `scripts/webexport/`;
remove `archive/{convert,retranscribe_other_onset_fix,revert_other_to_stock}.py`,
`archive/Makefile.legacy`, and instrumental analyze/separate/report/export scripts.
Replace the top-level Makefile/README and dead primary dependencies. Preserve
`scripts/rhythm_layers/source_overrides.json` by moving it to
`annotations/legacy_source_overrides.json` before deleting code: it contains
hand-supplied listening notes. Record every deleted file in the inventory.

Leave all prior output folders on disk and untracked: `stems/`, `midi/`, `merged/`,
`chords/`, `corrected/`, `midi_backup_pre_onset_fix/` (if present), `output/`
(including authored reports), `run-logs/`, `instrumentals/{audio,stems,.partial}/`,
`instrumentals/catalog.{json,sqlite}`, `docs/data/`, `preview/rhythm-layers/`.
`docs/audio/`, `audio/`, `k-indie/`, `liked-audio/` and all other recordings are
preserved. Existing tracked audio stays tracked. Generated exports are removed
from Git's index only; they are not deleted from disk. No Git history rewritten.

## Unsure — kept

- `lib/`: generic annotation, inspection, canonicalization and sequence/scoring
  utilities. Old corpus-calibrated scores are not used by the new pipeline.
- Acquisition scripts `archive/{download_songs,extract_audio,fetch_liked_songs,
  fetch_playlist_songs}.py`, Spotify/song lists and download manifests.
- Instrumental `ingest.py`, `audit_sources.py`, `check_catalog.py`, and authored
  `instrumentals/source_overrides.json`: acquisition/catalog repair may remain
  useful. They are not connected to the new rhythm/lyric pipeline.
- Static `docs/` viewers, audio-host Worker/config/checker and generic audio/
  chord-name web utilities: these support existing published listening tools.
  They do not generate new rhythm/lyric results and still require their local
  legacy exports. Frontend redesign/deployment is outside this change.
- Historical root Markdown, screenshots, `tools/` build instructions and local
  environments, all ground_truth/ground_truth_anchor data, manual corrections,
  review workbooks/exports, external score MIDI. Keep authorship/provenance.

The default new pipeline will measure beats on the original full mix, retain
unquantized transcription, quantify errors, and compare mix/stem transcription
with the same model and window. That diagnostic can reveal degradation; without
independent labels, note count/grid adherence alone cannot prove note accuracy.
