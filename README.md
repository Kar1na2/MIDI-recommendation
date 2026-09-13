# MIDI-recommendation
Recommending new songs through MIDI files

## Repository layout

Pipeline code lives under `scripts/`, grouped by stage (`separation/`,
`transcription/`, `chords/`, `correction/`, `evaluation/`, `export/`,
`analysis/`). Modules reused by more than one stage or by more than one
analysis script (`gt_format.py`, `tag_chord_tones.py`, `inspect_range.py`,
`chord_canonicalization.py`, `chord_similarity.py`, `note_similarity.py`,
`sequence_alignment.py`) live in `lib/` instead of any single stage folder.
Generated corpus-wide CSVs (`review_candidates.csv`, `chord_review_candidates.csv`,
`note_events_other.csv`, `chord_segments.csv`) land under `output/`, and
`output/analysis/` holds Phase 3's cross-song pattern-analysis output - see
`PROJECT_OVERVIEW.md` for what that phase actually found (the two
methodology docs there, `CHORD_ANALYSIS_DESIGN.md` and
`NOTE_ANALYSIS_DESIGN.md`, are the one exception checked into git despite
living under the otherwise-gitignored `output/` - they're authored
reasoning, not regenerable data, same reasoning as `ground_truth/` below).
Data directories (`audio/`, `k-indie/`, `stems/`, `midi/`, `merged/`,
`corrected/`, `chords/`, `ground_truth/`, `ground_truth_anchor/`) stay at
the repo root, unrelated to `scripts/chords/` (a *script* folder) despite
the name overlap. One-off/completed ingestion and experiment scripts that
are no longer part of the resumable pipeline (`archive/`) include the
original Spotify/scraping ingestion tools plus two resolved-and-reverted
one-off transcription experiments - see the `archive/` files' own
docstrings.

Every script is a real Python module now, not a standalone file - it has to
run as `-m <dotted.path>` from the repo root for its `lib`/`scripts` imports
to resolve, so use the **`Makefile`** target for each stage rather than
calling a script by file path directly. Every make target below accepts
`ARGS="..."` to forward CLI flags, e.g. `make extract-chords ARGS="--limit 3"`.

## Recreation 

If you wish to recreate what I have done then do the following 

**SETUP**
- `uv sync` installs the main environment (demucs, torch/cuda, basic-pitch) from `pyproject.toml`.
- Drum transcription needs a second, isolated environment - see "Drum transcription" below.

**RUN** (each stage is resumable - safe to re-run after a crash, it only redoes what's missing)
1. `make separate-stems` - Demucs (htdemucs_ft) splits every mp3 in `audio/` and `k-indie/` into `stems/<song_name>/{drums,bass,other,vocals}.wav`.
2. `make transcribe-harmonic` - Basic Pitch transcribes the `other` and `bass` stems to `midi/<song_name>/{other,bass}.mid`.
3. `make transcribe-drums` - ADTLib transcribes the `drums` stem to `midi/<song_name>/drums.mid`. This target already runs under `tools/adtlib-env/bin/python`, the different interpreter - see below.
4. `make merge-stems` - combines `midi/<song_name>/{other,bass,drums}.mid` into a single multi-track `merged/<song_name>.mid` (one instrument track per stem; merges as soon as any one stem is transcribed, doesn't wait on all three). Prints a per-song sanity check (track count, notes per track, duration) and flags any track that came back with 0 notes - a likely sign that stem's transcription silently failed upstream. `make merge-stems ARGS=--check` re-runs that same sanity check against existing `merged/*.mid` files without redoing any merging.

Each script also takes `--dry-run` (preview without running), `--limit N` (smoke test on the first N unfinished items), and `--retry-failed` (clear that stage's failure log and retry) - pass them via `ARGS`, e.g. `make separate-stems ARGS="--limit 3"`.

## Evaluating transcription accuracy

`scripts/evaluation/evaluate_transcription.py` (run via `make evaluate-transcription`) scores `merged/<song>.mid` against hand-labeled ground truth using `mir_eval`, broken down per stem (`other`/`bass`/`drums`) so it's clear which transcription stage - Basic Pitch or ADTLib - is the weak point, rather than one aggregate number.

**1. Pick a sample:** `make select-eval-sample ARGS="-n 8"` randomly samples 8 songs (seeded, reproducible) from whatever's currently in `merged/`, and scaffolds empty annotation templates at `ground_truth/<song_name>/{other,bass,drums}.csv`. Re-running with a bigger `-n` only adds the shortfall - it never touches songs already sampled or annotations already made. `ARGS="--list"` shows the current sample and how much of it is filled in.

**2. Hand-label ground truth.** Each CSV has columns `onset,offset,pitch,label` (see the docstring in `lib/gt_format.py` for the full spec). `offset` and `pitch` are optional per row - a row with only `onset` filled in is still usable for onset-accuracy scoring, just not for note-transcription scoring. That matters because the three stems are not equally easy to hand-label:

- **drums** - fastest. Load `stems/<song>/drums.wav` (the isolated stem, not the full mix) in [Sonic Visualiser](https://www.sonicvisualiser.org/) (free), add a Time Instants layer, and click each transient in the waveform - drum hits are sharp and visually obvious, easy to place precisely. Label each point `36`/`38`/`42` (kick/snare/hihat, matching the GM mapping `scripts/transcription/transcribe_drums.py` writes) so pitch-tolerance matching actually checks "right drum class," not just "a hit occurred." Export the layer to CSV and reshape into our column format (blank `offset`).
- **bass** - usually monophonic, so full note-level labeling (onset+offset+pitch) is worth the extra effort and still fast. In Sonic Visualiser, run the **pYIN** vamp plugin (Transform > Analysis > pYIN > Notes) on `stems/<song>/bass.wav` to get a candidate Notes layer, then hand-correct it by ear against the spectrogram rather than transcribing from scratch - correcting is much faster than starting blank. Export the Notes layer to CSV.
- **other** - hardest: often polyphonic/multiple instruments, so pitch-by-ear is slow and error-prone. Start with onset-only labeling (Time Instants layer marking note attacks, blank `pitch`/`offset`) for the whole song; if full note-level ground truth is worth the effort later, consider restricting it to a ~20-30s representative excerpt to bound the work rather than the whole track.

Sonic Visualiser's CSV export column names vary a bit by version - after exporting, open the file and reorder/rename into `onset,offset,pitch,label` (a couple minutes of spreadsheet work). [Audacity](https://www.audacityteam.org/)'s label track (`Ctrl+B` to drop a label at the playhead, File > Export > Export Labels) is a lower-effort fallback if you don't want to install Sonic Visualiser, especially for onset-only drum/other labeling - it just doesn't have a piano-roll view for pitch, so it's weaker for bass note-level work.

Unlike `stems/`, `midi/`, and `merged/`, **`ground_truth/` is checked into git** - it's hand-authored and can't be regenerated.

**3. Score it:** `make evaluate-transcription` prints a per-song, per-stem breakdown (onset F-measure, plus note-transcription F-measure with and without offset matching - see the script's docstring for why drums' offset number isn't meaningful), then a summary table averaged across the whole sample. `ARGS="--output-csv output/eval_results.csv"` also writes the per-song, per-stem numbers to a CSV for further analysis. Use `ARGS='--songs "Song A,Song B"'` to score a subset.

The `vocals` stem is separated but intentionally not transcribed - Basic Pitch's pitch model targets melodic/harmonic instruments, not sung pitch, so it's unused output for now pending a purpose-built approach (e.g. melody extraction tuned for singing, or lyric alignment).

**Chord-guided anchor excerpts.** `select_eval_sample.py` samples songs at
random; `make select-anchor-excerpts` instead uses the harmonic
consistency signal (below) to pick two deliberately extreme ~20-30s excerpts
worth labeling first: Anchor A is the highest non-chord-tone-density song's
hottest segment-aligned window (most likely to contain real errors), Anchor
B is the lowest-density song's cleanest window (the pipeline's best case).
Writes `ground_truth_anchor/<song>_excerpt/{other,bass,drums}.wav` (cropped
stem audio for that window), `merged_excerpt.mid` (the matching slice of
transcribed notes, re-zeroed to the excerpt), and empty
`{other,bass,drums}.csv` templates - same schema, same per-stem labeling
strategy (drums/other onset-only, bass full note-level - see "Hand-label
ground truth" above) as the random sample, just targeted instead of random.
Never touches `merged/`, `stems/`, or an existing hand-labeled CSV.

## Harmonic consistency check (diagnostic)

A second, independent signal for spotting likely transcription errors,
alongside `evaluate_transcription.py`'s ground-truth scoring: run chord
detection (Chordino) on the same audio Basic Pitch transcribed, then check
how often the transcribed notes actually agree with the detected chord.
Chord detection is non-ML (Chordino's chroma/NNLS approach, not a neural
model) and reads the audio independently of Basic Pitch, so persistent
disagreement is a useful corroborating flag even without ground truth.

**This is a read-only diagnostic layer.** All three scripts below only ever
read `stems/`, `merged/`, and their own prior output - none of them write to
`midi/`, `stems/`, or `merged/`, or modify any existing MIDI file. Everything
lands in new files under `chords/` and `output/review_candidates.csv`.

1. `make extract-chords` (`scripts/chords/extract_chords.py`) - runs the
   Chordino Vamp plugin over every `stems/<song>/other.wav`, writing
   `chords/<song>/other_chordnotes.csv` (one row per note Chordino judged
   present in each detected chord segment - actual pitch-class content, not
   a chord-name label). Resumable, same `--dry-run`/`--limit`/`--retry-failed`
   flags as the other stages (via `ARGS`).

   Chordino isn't packaged for Ubuntu, and its usual distribution path
   (`sonic-annotator` + prebuilt binaries from `code.soundsoftware.ac.uk`)
   was unreachable from the sandbox this was built in, so the plugin is
   instead built from source and called directly from Python via the `vamp`
   package - see `tools/vamp-build/README.md` for why and how to rebuild it.

2. `make tag-chord-tones` (`lib/tag_chord_tones.py` - shared with the
   correction/verification stages below, hence living in `lib/` rather than
   `scripts/chords/`) - for every note in `merged/<song>.mid`'s `other`
   track, looks up the chord segment active at that note's onset and tags it
   `chord_tone` / `non_chord_tone` / `no_chord_data` (onset falls outside any
   detected segment) by pitch-class membership, writing
   `chords/<song>/other_note_tags.csv` (a sidecar next to that song's
   `other_chordnotes.csv`). Resumable the same way; a song whose chordnotes
   CSV doesn't exist yet is skipped and logged rather than silently dropped.

3. `make rank-review-candidates` (`scripts/chords/rank_review_candidates.py`) -
   two modes. With no arguments, groups every song's tagged notes into chord
   segments, ranks them by non-chord-tone density, and prints the top few to
   the terminal - a corpus-wide list of segments worth a second look. Writes
   two CSVs: `output/review_candidates.csv` (segment rows only) and
   `output/chord_review_candidates.csv` (the same segment rows plus one row
   per song aggregating density across that whole song, tagged by a `scope`
   column, all sorted together by density) - the latter is meant as the
   chord signal's own scoring output, to eventually be merged with a
   model-agreement score once that pipeline exists (not implemented yet).
   With `ARGS='--song "<name>"'`, prints every segment of one song in time
   order with the actual note pitches and which ones disagreed with the
   chord - for checking a specific transcription result rather than browsing
   the whole corpus. `make inspect-range ARGS='--song "<name>" --start S --end E'`
   (`lib/inspect_range.py` - also shared, and directly runnable as a
   spot-check CLI) goes one step further for a specific time window: it
   interleaves chord segments and tagged notes into one chronological,
   side-by-side view for spot-checking a suspicious stretch by ear/eye,
   without computing any verdict of its own.

A non-chord-tone is not automatically "wrong" - passing tones and
suspensions are normal harmonic content and look identical to a genuine
transcription slip at this layer. Treat a high-density segment as "worth
listening to," not as a confirmed error.

## Pitch correction (experimental - not verified against ground truth yet)

Everything above - transcription, chord detection, tagging, ranking,
inspection - is read-only diagnostics: it flags candidates for a human to
listen to, never edits a transcription on its own.
`scripts/correction/correct_notes.py` (run via `make correct-notes`) is the
one script that actually changes notes, and it applies only the
single highest-confidence automated pattern - `stuck_pitch` (a `non_chord_tone`
note in the `other` track whose exact pitch was legitimately a chord tone in
the immediately preceding chord segment - i.e. the harmony moved on at that
chord change but the transcribed pitch didn't follow it). Every other
`non_chord_tone` note is deliberately left alone by this script and written
instead to a proposals file for a human to review by ear - nothing else is
inferred or auto-applied.

```
make correct-notes ARGS="--limit 3"               # smoke test the first 3 songs
make correct-notes ARGS="--song 'Some Song'"       # just one song
make correct-notes ARGS="--dry-run"                # preview only, writes nothing
make correct-notes ARGS="--min-duration-ms 150"    # proposals-file duration threshold
make correct-notes                                 # full corpus
```

The `other` track is identified explicitly (by instrument name, never by
track index) - a song with zero or more than one track named `other` is
skipped and logged rather than guessed at. Output per song, all under
`corrected/`:

- **`<song>.mid`** - only written if >=1 `stuck_pitch` correction was
  actually applied; every other track (`bass`, `drums`) passes through
  byte-identical. `merged/` is only ever opened for parsing, never written to.
- **`<song>_changelog.csv`** - always written (even empty/header-only for a
  clean song): every corrected note's onset/original/corrected pitch and how
  far it moved.
- **`<song>_proposals.csv`** - always written (even empty/header-only): one
  row per remaining `non_chord_tone` note, with the same nearest-chord-tone
  suggestion the `stuck_pitch` pass uses, and an `action` column pre-filled
  `correct` (duration over `ARGS="--min-duration-ms ..."`, default 100ms) or
  `keep` (at/under it - a brief note is more likely a genuine passing tone
  than a transcription slip). Nothing here is applied automatically; a human
  edits `action` by hand after listening (via `inspect_range.py` or
  `rank_review_candidates.py` to find flagged segments first).

**This is a heuristic, not verified ground truth** - Chordino's own chord
segmentation is an HMM smoothing step, not a checked reference, and a
non-chord-tone is normal harmonic content as often as it's a transcription
error (see "Harmonic consistency check" above). Score `corrected/` against
`ground_truth/`/`ground_truth_anchor/` with `make evaluate-transcription`
before trusting it over `merged/`'s original transcription.

A correction that would make a note overlap in time with another note of
the identical resulting pitch is skipped rather than applied - standard
MIDI can't reliably round-trip two overlapping same-pitch notes (note-on/
note-off pairing can cross on re-parse and silently corrupt an unrelated
note's duration), so integrity wins over completeness there.

`make summarize-corrections` reads every song's tags, changelog, and
proposals file and reports corpus-wide totals, the distribution of how far
`stuck_pitch` corrections moved notes (mostly 1-2 semitones is the healthy
pattern), how many notes are sitting in proposals awaiting human review
(split by the pre-filled `correct`/`keep` action), the highest
correction-rate songs (worth spot-checking first), and every zero-correction
song cross-checked against its own tag data - a song that's genuinely clean
looks the same as one where chord extraction silently produced nothing
(all `no_chord_data`) unless that check is made explicitly. Writes
`corrected/_correction_summary.csv`.

## Analysis-ready export

Everything above produces diagnostics and intermediate per-song files;
`scripts/export/export_note_events.py` (run via `make export-note-events`)
flattens them into the two flat CSVs meant to actually be used for
downstream pattern-recognition work on harmonic content, written under
`output/`:

- **`output/note_events_other.csv`** - one row per note in the `other` track only
  (`bass`/`drums` pass through unmodified from `merged/` and are out of
  scope for this export). Columns: `song, onset, offset, pitch, velocity`
  (from `corrected/<song>.mid`'s `other` track where a corrected version
  exists, else `merged/<song>.mid`'s), `chord_onset, chord_offset,
  chord_pitchclasses` (the Chordino chord segment active at that note's
  onset, blank if the onset falls outside any detected segment), and
  `was_corrected, correction_type, original_pitch` (from
  `corrected/<song>_changelog.csv` - blank/`False` if the note was never
  corrected).
- **`output/chord_segments.csv`** - one row per distinct Chordino chord segment
  across every song under `chords/`, independent of how far
  `merged/`/`corrected/` coverage has reached (chord detection runs
  corpus-wide well ahead of transcription): `song, start, end,
  pitch_class_set`.

```
make export-note-events
```

**Read this before treating pitch as ground truth.** Wherever a corrected
MIDI exists, the `pitch` column in `output/note_events_other.csv` reflects
Chordino-guided auto-correction (`correct_notes.py`'s `stuck_pitch` pass),
not raw Basic Pitch transcription output - check `was_corrected` (and join
against `chord_pitchclasses`) if a downstream analysis needs to distinguish
observed transcription from corrected transcription. And correction itself
is deliberately incomplete: only the `stuck_pitch` pattern is auto-applied,
so plenty of `non_chord_tone` notes are intentionally left uncorrected and
sitting instead in `corrected/<song>_proposals.csv` for human review (see
"Pitch correction" above) - such a note will show up here with
`was_corrected=False` sitting on a pitch class outside its own
`chord_pitchclasses`, which is expected, not a bug in this export.

### Drum transcription: why a second environment, and the ADTLib/Omnizart trade-off

`scripts/transcription/transcribe_drums.py` uses [ADTLib](https://github.com/CarlSouthall/ADTLib), which cannot live in the main `.venv`: its drum model needs **TensorFlow 1.15** (built on the graph-mode `tensorflow.contrib` API, deleted in TF 2.0), and its onset-detection dependency **madmom** hasn't had a PyPI release since Nov 2018 - it breaks on Python >=3.10 (`collections.MutableSequence` etc. were removed) and on numpy >=1.24 (`np.float` etc. removed). TF1.15's own wheels only exist for Python 3.6/3.7, both EOL.

`tools/python3.7-base/` is a from-source Python 3.7.17 build (no system package provided one), and `tools/adtlib-env/` is a venv on top of it pinned to numpy==1.18.5, tensorflow==1.15.5, protobuf==3.19.6 (newer protobuf breaks TF1.15's compiled `_pb2.py` files), madmom, and ADTLib. Both directories are gitignored (large, machine-specific build output) - rebuild by pointing `transcribe_drums.py`'s setup steps at a fresh Python 3.7 source build if this ever needs to be reproduced elsewhere. `make transcribe-drums` already invokes `tools/adtlib-env/bin/python -m scripts.transcription.transcribe_drums` for you; only reach for that full command directly if you need to bypass the Makefile.

**Omnizart was evaluated as an alternative** (modern TF2, no legacy-Python requirement) and rejected: its drum module imports `madmom` too, so it hits the exact same broken-dependency wall, while additionally requiring system libraries (portaudio, fluidsynth, vamp SDK) not present on this machine and a much heavier Python dependency stack (tensorflow, sherpa-onnx). Switching to it would not have avoided the hard part (patching madmom) and would have cost more elsewhere. ADTLib's own transcription quality is also a real ceiling worth knowing about: it only detects three drum classes - kick, snare, closed hi-hat - as onset times (no toms, cymbals, or open hi-hat, no velocity), which `transcribe_drums.py` maps to fixed-length General MIDI notes (36/38/42). If that ceiling turns out to matter later, Omnizart's CNN-based drum model is the next thing to try, at the setup cost described above.

### "Other" stem transcription: known limitation on short/soft high notes

`transcribe_harmonic.py` runs Basic Pitch on the `other` stem at the library's
own default thresholds (`onset_threshold=0.5`, `frame_threshold=0.3`) - no
custom override. At those defaults, some short, soft, high-register notes are
missed entirely. Concrete example: a ~23ms G#5 at 10.79s in "1nonly -
Meaningless Love" is confirmed present in the raw model activation
(inspected via Basic Pitch's `--save-model-outputs`, frame activation peak
0.307, onset activation peak 0.474 - both real signal, both clearing their
respective thresholds) but gets filtered out downstream by
`minimum_note_length`, not by the onset/frame thresholds themselves.

This was investigated as a two-part fix - lowering `onset_threshold` to 0.45,
then separately lowering `minimum_note_length` to recover notes like the one
above - and both were reverted. Lowering `minimum_note_length` does recover
such notes, but at the cost of nearly doubling the total note count corpus-
wide, with roughly 17.5% of the newly-recovered notes under 60ms - mostly
model flicker (spurious sub-frame activations), not real notes. Chord-tone
tagging and CREPE-based pitch corroboration were both tried as quality gates
to separate genuine short notes from that noise: CREPE proved structurally
unreliable on this polyphonic material (it failed even on the one
known-true-positive note above), and Omnizart was ruled out as a
corroboration source for the same dependency-wall reason it was ruled out
for drums (see above) - its usable modules still pull in `madmom` /
`pyfluidsynth` / the vamp SDK as unconditional top-level dependencies, not
optional ones.

**Decision: accepted as a known limitation, not pursued further.** Missing
occasional short, soft, high notes in the `other` track is preferable to the
false-positive rate a length-reduced note threshold introduces at corpus
scale. `STEM_THRESHOLDS` in `transcribe_harmonic.py` has no custom `other`
override as a result.

## Approach 

In terms of system design I'll be doing the following 
- Postgres SQL will be my primary database as MIDI files are stored as binary and they'll be the main highlight of this project 
    - I'll also be using pgvector extension for similarity queries 

## Things I have done 

**[7/23 ~ 24]**
- Set up the project before starting on the web 
- 36 songs from my own spotify playlist to use as demo 
- 30 songs from my k-indie playlist to use as a more curated demo on the MIDI analysis

**[8/25 ~ 26]**
- Built the Demucs (htdemucs_ft) stem separation stage: `separate_stems.py`, resumable, per-song failure log, GPU with CPU fallback
- Built the harmonic/bass transcription stage: `transcribe_harmonic.py` (Basic Pitch) for the `other` and `bass` stems
- Built the drum transcription stage: `transcribe_drums.py` (ADTLib), which needed its own Python 3.7 + TensorFlow 1.15 environment (`tools/adtlib-env/`) - see "Drum transcription" above for why, and the trade-off against Omnizart
- `vocals` stem separated but not yet transcribed (flagged as future work)
- Built the merge stage: `merge_stems.py` combines the three transcribed stems into one multi-track `merged/<song>.mid` per song, with a built-in sanity check (track/note/duration summary, flags empty tracks)
- Built the evaluation stage: `select_eval_sample.py` (reproducible random sample + ground-truth CSV templates), `gt_format.py` (shared annotation format, onset/offset/pitch/label), `evaluate_transcription.py` (mir_eval onset + note-transcription scoring per stem, summary table across the sample). Hand-labeling workflow (Sonic Visualiser + pYIN for bass, Time Instants for drums) documented above. Ground truth not yet filled in - next step is actually labeling the ~8-song sample and running a first real accuracy pass before trusting this on the full corpus

**[8/31]**
- Paused the planned ML-model-agreement work (comparing multiple transcription models against each other, e.g. `agreement_score.py`) to build a non-ML corroborating signal first: the harmonic consistency check (see section above) - `extract_chords.py` (Chordino chord detection, built from source since `sonic-annotator`'s usual distribution host wasn't reachable - see `tools/vamp-build/README.md`), `tag_chord_tones.py` (chord-tone/non-chord-tone tagging per note), `rank_review_candidates.py` (corpus-wide ranked flagging + single-song investigation mode). Diagnostic only - verified against the 3 songs currently in `merged/`, no existing files modified.

**[9/12 ~ 13]**
- Ran the Phase 2 backlog (bass/drums transcription → merge → tag → correct → export) to full corpus-wide completion, 75/75 songs, 0 failures (`RUN_REPORT_2026-09-12.md`, `PIPELINE_MAP.md`). Repo reorganized into `scripts/<stage>/` + `lib/` as real Python modules (run via `make <target>`, not bare `python file.py` anymore).
- Built Phase 3A (chord-progression pattern analysis) and Phase 3B (note/melodic-interval pattern analysis) on top of the completed corpus - cross-song sequence alignment (Needleman-Wunsch/Smith-Waterman) finding shared chord progressions and melodic motifs between songs, with a corpus-relative significance score. **See `PROJECT_OVERVIEW.md` for the full writeup** - what was built, several real methodology bugs found and fixed along the way (worth reading before extending either phase), and what's still needed before this connects to a frontend/recommendation UI.