# MIDI-recommendation
Recommending new songs through MIDI files

## Recreation 

If you wish to recreate what I have done then do the following 

**SETUP**
- `uv sync` installs the main environment (demucs, torch/cuda, basic-pitch) from `pyproject.toml`.
- Drum transcription needs a second, isolated environment - see "Drum transcription" below.

**RUN** (each stage is resumable - safe to re-run after a crash, it only redoes what's missing)
1. `uv run separate_stems.py` - Demucs (htdemucs_ft) splits every mp3 in `audio/` and `k-indie/` into `stems/<song_name>/{drums,bass,other,vocals}.wav`.
2. `uv run transcribe_harmonic.py` - Basic Pitch transcribes the `other` and `bass` stems to `midi/<song_name>/{other,bass}.mid`.
3. `tools/adtlib-env/bin/python transcribe_drums.py` - ADTLib transcribes the `drums` stem to `midi/<song_name>/drums.mid`. Note the different interpreter - see below.
4. `uv run merge_stems.py` - combines `midi/<song_name>/{other,bass,drums}.mid` into a single multi-track `merged/<song_name>.mid` (one instrument track per stem; merges as soon as any one stem is transcribed, doesn't wait on all three). Prints a per-song sanity check (track count, notes per track, duration) and flags any track that came back with 0 notes - a likely sign that stem's transcription silently failed upstream. `uv run merge_stems.py --check` re-runs that same sanity check against existing `merged/*.mid` files without redoing any merging.

Each script also takes `--dry-run` (preview without running), `--limit N` (smoke test on the first N unfinished items), and `--retry-failed` (clear that stage's failure log and retry).

## Evaluating transcription accuracy

`evaluate_transcription.py` scores `merged/<song>.mid` against hand-labeled ground truth using `mir_eval`, broken down per stem (`other`/`bass`/`drums`) so it's clear which transcription stage - Basic Pitch or ADTLib - is the weak point, rather than one aggregate number.

**1. Pick a sample:** `uv run select_eval_sample.py -n 8` randomly samples 8 songs (seeded, reproducible) from whatever's currently in `merged/`, and scaffolds empty annotation templates at `ground_truth/<song_name>/{other,bass,drums}.csv`. Re-running with a bigger `-n` only adds the shortfall - it never touches songs already sampled or annotations already made. `--list` shows the current sample and how much of it is filled in.

**2. Hand-label ground truth.** Each CSV has columns `onset,offset,pitch,label` (see the docstring in `gt_format.py` for the full spec). `offset` and `pitch` are optional per row - a row with only `onset` filled in is still usable for onset-accuracy scoring, just not for note-transcription scoring. That matters because the three stems are not equally easy to hand-label:

- **drums** - fastest. Load `stems/<song>/drums.wav` (the isolated stem, not the full mix) in [Sonic Visualiser](https://www.sonicvisualiser.org/) (free), add a Time Instants layer, and click each transient in the waveform - drum hits are sharp and visually obvious, easy to place precisely. Label each point `36`/`38`/`42` (kick/snare/hihat, matching the GM mapping `transcribe_drums.py` writes) so pitch-tolerance matching actually checks "right drum class," not just "a hit occurred." Export the layer to CSV and reshape into our column format (blank `offset`).
- **bass** - usually monophonic, so full note-level labeling (onset+offset+pitch) is worth the extra effort and still fast. In Sonic Visualiser, run the **pYIN** vamp plugin (Transform > Analysis > pYIN > Notes) on `stems/<song>/bass.wav` to get a candidate Notes layer, then hand-correct it by ear against the spectrogram rather than transcribing from scratch - correcting is much faster than starting blank. Export the Notes layer to CSV.
- **other** - hardest: often polyphonic/multiple instruments, so pitch-by-ear is slow and error-prone. Start with onset-only labeling (Time Instants layer marking note attacks, blank `pitch`/`offset`) for the whole song; if full note-level ground truth is worth the effort later, consider restricting it to a ~20-30s representative excerpt to bound the work rather than the whole track.

Sonic Visualiser's CSV export column names vary a bit by version - after exporting, open the file and reorder/rename into `onset,offset,pitch,label` (a couple minutes of spreadsheet work). [Audacity](https://www.audacityteam.org/)'s label track (`Ctrl+B` to drop a label at the playhead, File > Export > Export Labels) is a lower-effort fallback if you don't want to install Sonic Visualiser, especially for onset-only drum/other labeling - it just doesn't have a piano-roll view for pitch, so it's weaker for bass note-level work.

Unlike `stems/`, `midi/`, and `merged/`, **`ground_truth/` is checked into git** - it's hand-authored and can't be regenerated.

**3. Score it:** `uv run evaluate_transcription.py` prints a per-song, per-stem breakdown (onset F-measure, plus note-transcription F-measure with and without offset matching - see the script's docstring for why drums' offset number isn't meaningful), then a summary table averaged across the whole sample. `--output-csv results.csv` also writes the per-song, per-stem numbers to a CSV for further analysis. Use `--songs "Song A,Song B"` to score a subset.

The `vocals` stem is separated but intentionally not transcribed - Basic Pitch's pitch model targets melodic/harmonic instruments, not sung pitch, so it's unused output for now pending a purpose-built approach (e.g. melody extraction tuned for singing, or lyric alignment).

**Chord-guided anchor excerpts.** `select_eval_sample.py` samples songs at
random; `uv run select_anchor_excerpts.py` instead uses the harmonic
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
lands in new files under `chords/` and `review_candidates.csv`.

1. `uv run extract_chords.py` - runs the Chordino Vamp plugin over every
   `stems/<song>/other.wav`, writing `chords/<song>/other_chordnotes.csv`
   (one row per note Chordino judged present in each detected chord segment -
   actual pitch-class content, not a chord-name label). Resumable, same
   `--dry-run`/`--limit`/`--retry-failed` flags as the other stages.

   Chordino isn't packaged for Ubuntu, and its usual distribution path
   (`sonic-annotator` + prebuilt binaries from `code.soundsoftware.ac.uk`)
   was unreachable from the sandbox this was built in, so the plugin is
   instead built from source and called directly from Python via the `vamp`
   package - see `tools/vamp-build/README.md` for why and how to rebuild it.

2. `uv run tag_chord_tones.py` - for every note in `merged/<song>.mid`'s
   `other` track, looks up the chord segment active at that note's onset
   and tags it `chord_tone` / `non_chord_tone` / `no_chord_data` (onset falls
   outside any detected segment) by pitch-class membership, writing
   `chords/<song>/other_note_tags.csv` (a sidecar next to that song's
   `other_chordnotes.csv`). Resumable the same way; a song whose chordnotes
   CSV doesn't exist yet is skipped and logged rather than silently dropped.

3. `uv run rank_review_candidates.py` - two modes. With no arguments, groups
   every song's tagged notes into chord segments, ranks them by
   non-chord-tone density, and prints the top few to the terminal - a
   corpus-wide list of segments worth a second look. Writes two CSVs:
   `review_candidates.csv` (segment rows only) and
   `chord_review_candidates.csv` (the same segment rows plus one row per
   song aggregating density across that whole song, tagged by a `scope`
   column, all sorted together by density) - the latter is meant as the
   chord signal's own scoring output, to eventually be merged with a
   model-agreement score once that pipeline exists (not implemented yet).
   With `--song "<name>"`, prints every segment of one song in time order
   with the actual note pitches and which ones disagreed with the chord -
   for checking a specific transcription result rather than browsing the
   whole corpus. `inspect_range.py --song "<name>" --start S --end E` goes
   one step further for a specific time window: it interleaves chord
   segments and tagged notes into one chronological, side-by-side view for
   spot-checking a suspicious stretch by ear/eye, without computing any
   verdict of its own.

A non-chord-tone is not automatically "wrong" - passing tones and
suspensions are normal harmonic content and look identical to a genuine
transcription slip at this layer. Treat a high-density segment as "worth
listening to," not as a confirmed error.

## Manual correction layer (human-initiated, not part of the pipeline)

Everything above - transcription, chord detection, tagging, ranking,
inspection - is read-only diagnostics: it flags candidates for a human to
listen to, never edits a transcription on its own. `correct_notes.py` is the
one script that actually changes notes, and it does so only from a
corrections CSV a human fills in by hand after listening to a flagged
segment (via `inspect_range.py` or `rank_review_candidates.py`) and deciding
a specific note is actually wrong - nothing here infers or auto-applies a
correction; it is not run by any of the pipeline stages and has no
`--retry-failed`-style automation.

`corrections_template.csv` has the column reference and a few commented
example rows - copy it, uncomment/edit the rows you want, and run:

```
uv run correct_notes.py my_corrections.csv          # apply
uv run correct_notes.py my_corrections.csv --dry-run # preview only, writes nothing
```

Each row identifies a note by its current onset time and pitch (matched
within `--tolerance`, default 50ms) in `merged/<song>.mid`'s `other` track,
and gives either a corrected pitch, corrected onset/offset, or the word
`delete` to remove a spurious note entirely. The result is written to
**`corrected/<song>.mid` - a new directory, parallel to `merged/`.
`merged/` is never written to**; `correct_notes.py` only ever opens it for
parsing. Every row's outcome (applied, or why it wasn't - no matching note
found, or the row didn't parse) is appended to `corrected/correction_log.csv`
so a correction that silently missed its target is never invisible.

## Computed pitch correction (experimental - not verified against ground truth yet)

`auto_correct_pitches.py` is a second way to populate `corrected/`, using
the Chordino tags as the correction signal instead of a human: for each
`non_chord_tone` note in `merged/<song>.mid`'s `other` track, it snaps the
pitch to the nearest chord tone of that note's own chord segment, under two
patterns - `stuck_pitch` (a pitch that was legitimately a chord tone earlier
stays unchanged while the chord moves on around it - the highest-confidence
case) and `duration_filter` (any other non-chord-tone note longer than
`--min-duration-ms`, default 100ms - brief notes are left alone as more
likely genuine passing tones). Writes `corrected/<song>.mid` and a
`corrected/<song>_changelog.csv` per song (every corrected note's
onset/original/corrected pitch, which pass caught it, and how far it moved).
**This is a heuristic, not verified ground truth** - Chordino's own chord
segmentation is an HMM smoothing step, not a checked reference, and a
non-chord-tone is normal harmonic content as often as it's a transcription
error (see "Harmonic consistency check" above). Score `corrected/` against
`ground_truth/`/`ground_truth_anchor/` with `evaluate_transcription.py`
before trusting it over `merged/`'s original transcription. Same
resumable/`--dry-run`/`--limit`/`--retry-failed` shape as the rest of the
pipeline; `merged/` is only ever opened for parsing, never written to.

A correction that would make a note overlap in time with another note of
the identical resulting pitch is skipped rather than applied - standard
MIDI can't reliably round-trip two overlapping same-pitch notes (note-on/
note-off pairing can cross on re-parse and silently corrupt an unrelated
note's duration), so integrity wins over completeness there.

`uv run summarize_corrections.py` reads every song's tags and changelog and
reports corpus-wide totals, the distribution of how far corrections moved
notes (mostly 1-2 semitones is the healthy pattern), the highest
correction-rate songs (worth spot-checking first), and every zero-correction
song cross-checked against its own tag data - a song that's genuinely clean
looks the same as one where chord extraction silently produced nothing
(all `no_chord_data`) unless that check is made explicitly. Writes
`corrected/_correction_summary.csv`.

## Analysis-ready export

Everything above produces diagnostics and intermediate per-song files;
`export_note_events.py` flattens them into the two flat CSVs meant to
actually be used for downstream pattern-recognition work on harmonic
content:

- **`note_events_other.csv`** - one row per note in the `other` track only
  (`bass`/`drums` pass through unmodified from `merged/` and are out of
  scope for this export). Columns: `song, onset, offset, pitch, velocity`
  (from `corrected/<song>.mid`'s `other` track where a corrected version
  exists, else `merged/<song>.mid`'s), `chord_onset, chord_offset,
  chord_pitchclasses` (the Chordino chord segment active at that note's
  onset, blank if the onset falls outside any detected segment), and
  `was_corrected, correction_type, original_pitch` (from
  `corrected/<song>_changelog.csv` - blank/`False` if the note was never
  corrected).
- **`chord_segments.csv`** - one row per distinct Chordino chord segment
  across every song under `chords/`, independent of how far
  `merged/`/`corrected/` coverage has reached (chord detection runs
  corpus-wide well ahead of transcription): `song, start, end,
  pitch_class_set`.

```
uv run export_note_events.py
```

**Read this before treating pitch as ground truth.** Wherever a corrected
MIDI exists, the `pitch` column in `note_events_other.csv` reflects
Chordino-guided auto-correction (`auto_correct_pitches.py`), not raw Basic
Pitch transcription output - check `was_corrected` (and join against
`chord_pitchclasses`) if a downstream analysis needs to distinguish
observed transcription from corrected transcription. And correction itself
is deliberately incomplete: a `non_chord_tone` note briefer than
`auto_correct_pitches.py`'s `--min-duration-ms` threshold (100ms by
default) is intentionally left uncorrected, on the theory that a brief
non-chord-tone is more likely a genuine passing tone/ornament than a
transcription slip (see "Computed pitch correction" above) - such a note
will show up with `was_corrected=False` sitting on a pitch class outside
its own `chord_pitchclasses`, which is expected, not a bug in this export.

### Drum transcription: why a second environment, and the ADTLib/Omnizart trade-off

`transcribe_drums.py` uses [ADTLib](https://github.com/CarlSouthall/ADTLib), which cannot live in the main `.venv`: its drum model needs **TensorFlow 1.15** (built on the graph-mode `tensorflow.contrib` API, deleted in TF 2.0), and its onset-detection dependency **madmom** hasn't had a PyPI release since Nov 2018 - it breaks on Python >=3.10 (`collections.MutableSequence` etc. were removed) and on numpy >=1.24 (`np.float` etc. removed). TF1.15's own wheels only exist for Python 3.6/3.7, both EOL.

`tools/python3.7-base/` is a from-source Python 3.7.17 build (no system package provided one), and `tools/adtlib-env/` is a venv on top of it pinned to numpy==1.18.5, tensorflow==1.15.5, protobuf==3.19.6 (newer protobuf breaks TF1.15's compiled `_pb2.py` files), madmom, and ADTLib. Both directories are gitignored (large, machine-specific build output) - rebuild by pointing `transcribe_drums.py`'s setup steps at a fresh Python 3.7 source build if this ever needs to be reproduced elsewhere.

**Omnizart was evaluated as an alternative** (modern TF2, no legacy-Python requirement) and rejected: its drum module imports `madmom` too, so it hits the exact same broken-dependency wall, while additionally requiring system libraries (portaudio, fluidsynth, vamp SDK) not present on this machine and a much heavier Python dependency stack (tensorflow, sherpa-onnx). Switching to it would not have avoided the hard part (patching madmom) and would have cost more elsewhere. ADTLib's own transcription quality is also a real ceiling worth knowing about: it only detects three drum classes - kick, snare, closed hi-hat - as onset times (no toms, cymbals, or open hi-hat, no velocity), which `transcribe_drums.py` maps to fixed-length General MIDI notes (36/38/42). If that ceiling turns out to matter later, Omnizart's CNN-based drum model is the next thing to try, at the setup cost described above.

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