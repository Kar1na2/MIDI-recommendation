# Rhythm + lyric-section pipeline

Analyze the original song mix on a measured beat/downbeat grid, compress note
patterns by instrument, and join rhythmic sections to timestamped Genius labels.
There is no mood classifier.

The legacy source and history are recoverable at the annotated `legacy-pipelines`
tag (documentation commit `eb8d891`). See [pipeline history](../PIPELINE_HISTORY.md)
and the [source inventory](../LEGACY_INVENTORY.json). Audio, annotations,
downloaded songs and previous outputs remain on disk. New analysis never imports
old MIDI/events or old aligned section times.

## Install and run

Use Python **3.11** and **uv**, in Linux/WSL as configured by the repository's CUDA
Torch index. Dependencies are pinned in pyproject.toml/uv.lock, including the
MuScriptor Git revision. To preserve existing model environments:

```sh
UV_PROJECT_ENVIRONMENT=outputs/runtime uv sync --locked
source outputs/runtime/bin/activate
python -m pipeline run --song aespa-attitude --device cuda
python -m pipeline run --all --device cuda
python -m pipeline diagnose --song aespa-attitude --start 0 --end 20 --device cuda
python -m unittest discover -s tests -v
```

If uv is not on your WSL PATH, use `~/.local/bin/uv`. CPU is the CLI default;
GPU acceleration is selected explicitly with `--device cuda`. With an active
environment the required entry points are simply `python -m pipeline run` and
`python -m pipeline diagnose`.

MuScriptor uses `transcription.muscriptor_size` from the song manifest; ATTITUDE
is configured to `large`. Before loading, the pipeline confirms Hugging Face
login, checks the local checkpoint cache, and reports GPU memory. Accept terms
for the configured model at Hugging Face and authenticate with `hf auth login`
or `HF_TOKEN`. Load errors stop that stage. The size is never downgraded
automatically; change the manifest or use `--model small|medium|large` yourself.
The streaming event list creates both the note JSON and `transcription.mid` in a
single inference pass. No token is written to a file.

Set `GENIUS_ACCESS_TOKEN` in the environment or repository `.env` for official
Genius API search. A raw token and a value prefixed with `Bearer ` are both
accepted; the API client adds the header prefix exactly once.
Successful page HTML is cached under `data/genius_html/`. Requests to the page
are serialized, spaced at least five seconds apart, and retry only transient
429/5xx/timeouts. On 403, save a browser copy (Webpage, HTML only) to
`data/genius_html/<song-id>.html`; the optional romanized page can be saved as
`<song-id>.romanized.html`. `--force lyrics` reuses cached HTML.

Edit [songs.yaml](../../songs.yaml) to add songs. Each entry requires id, audio_path,
artist, title and genius_url (original language). Paths are relative to the
manifest. Optional fields: genius_url_romanized, instrument_hints (list of
MuScriptor instrument group names), lrclib_id, manual_sections_csv, and language
for WhisperX. ATTITUDE is a **Japanese** release; its original source URL is
`https://genius.com/Aespa-attitude-lyrics`, so the manifest uses `language: ja`.
Korean songs use `ko`. Japanese matching compares direct text and Hepburn/kana
readings while preserving repeated line order and mixed English.

```sh
python -m pipeline run --song aespa-attitude --force transcription
python -m pipeline run --song aespa-attitude --steps-per-bar 12
python -m pipeline run --song aespa-attitude --novelty-threshold 0.5 \
  --onset-distance 2 --pitch-distance 2 --section-window 2 --min-section-bars 4
```

Stages: `beats`, `separation`, `transcription`, `quantize`, `patterns`, `lyrics`,
`join`. Repeat `--force` for several stages. Diagnostic retries use
`--force diagnose`. Cache keys include source/code/lock/parameters/upstream
results; changed inputs invalidate affected caches. A failed cache is retained
until forced or its inputs change. No hand-entered overrides are guessed/imported.

Rhythmic patterns use per-instrument onset Hamming and pitch-interval edit
clusters. Two-bar window novelty selects section boundaries, with a four-bar
minimum except where irregular-meter bars interrupt a section. Join reports
boundary agreement against 1,000 random measured-downbeat baselines.

Beats, separation and transcription run independently. Lyrics depend on beats
and need separation only when WhisperX is selected. Quantization needs beats and
transcription; rhythm compression needs quantization; join, report and timeline
need lyrics and rhythm. A failed stage blocks only its dependents, and report
`stages` lists `ok`, `failed`, or `blocked_by:<stage>` with a reason.

The manifest's `beats.meter_policy` defaults to `strict` with four beats per bar.
Raw beat_this detections are retained. Resolvable irregular runs are corrected
only when their endpoint phase supports complete four-beat bars; unresolved
regions stay marked as `odd[n]` bars and are excluded from pattern matching,
but remain in the timeline and can break sections. `as_detected` keeps raw
downbeats. Both modes write `outputs/<id>/click.wav`, with high downbeat and low
beat clicks over the mix.

## Timing sources and outputs

Manual CSV (`label,start_s,end_s`) wins. It must include all Genius labels in
order; Instrumental rows may be added. Invalid manual truth stops the stage.
Otherwise LRCLIB must match audio duration within 2 seconds and pass ordered
line/section matching; repeated choruses use separate occurrences. If LRCLIB
fails, the pipeline prints and records a fallback to WhisperX on the fresh
htdemucs vocal stem. No usable source means a failed stage, not invented times.

WhisperX 3.8.6 requires Torch 2.8 and therefore uses a separate environment:

```sh
uv venv --python 3.11 outputs/whisperx-env
uv pip install --python outputs/whisperx-env/bin/python -r requirements-whisperx.txt
python -m pipeline run --song aespa-attitude --whisperx-python outputs/whisperx-env/bin/python
```

The aligner uses the language-specific CTC model and actual vocal waveform.
Genius source HTML is parsed identically whether fetched or manually saved; a
403 is not retried or bypassed. `--force lyrics` recomputes timing from cached
HTML. Romanized lyric text is not passed to an original-script WhisperX CTC model.

Each stage writes `outputs/<id>/<stage>.json`; htdemucs retains all four WAV
stems. Beat output also records raw and DBN bar counts, meter corrections,
unresolved regions and click path. Final artifacts are `report.json` and `timeline.png`. The report is a
flat set of tables documented in [REPORT_SCHEMA.md](../REPORT_SCHEMA.md).
Low confidence, missing lines, variable meter, tempo-octave ambiguity and poor
quantization remain flagged. The Jaccard threshold is not tuned to make lyric
boundaries appear to agree.

The diagnostic writes side-by-side mix/bass/drums/other/vocals piano rolls,
note/onset counts per bar and the share of attacks within 30 ms of the measured
grid. All inputs use the same backend, crop and conditioning. This reveals
mix/stem differences; it does not prove transcription accuracy without labels.

## YourMT3+ comparison

The adapter retains the reviewed upstream source/checkpoint lock from the tagged
legacy version and never reads old predictions. Use the already installed
`output/amt/yourmt3` model tree and compatible `output/amt/env/bin/python`, or
provide `--yourmt3-root` / `--yourmt3-python` explicitly:

```sh
python -m pipeline diagnose --song aespa-attitude --start 0 --end 20 \
  --backend yourmt3 --yourmt3-python output/amt/env/bin/python --device cuda
```

Its dependency stack is recorded in the history note and archive tag. It is
optional and never selected because MuScriptor failed. Unsupported instrument
conditioning is warned explicitly. Model agreement is not ground truth.

Retained `lib/`, acquisition/catalog utilities and static legacy viewers are
listed under “Unsure — kept” in the history. Their optional dependencies can be
installed with `uv sync --extra utilities`. Existing static viewers depend on
local untracked legacy exports; rebuilding/deploying them is outside this change.
