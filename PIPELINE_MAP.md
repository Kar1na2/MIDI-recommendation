# PIPELINE_MAP.md

Source of truth for how this repo's pipeline actually works and where the
75-song corpus currently stands. Written 2026-09-12 by walking the repo,
reading every stage script, and cross-checking against real files on disk
(not just docstrings) — see "How this was verified" at the bottom for the
exact commands run.

**This duplicates nothing from `README.md` on purpose where the README is
already accurate and current** — the README's stage-by-stage prose (I/O,
CLI flags, the ADTLib/Omnizart dependency story, the "other"-stem onset-
threshold decision) was read in full and is correct as of this writing. This
file adds what the README doesn't: a single stage-graph table, the verified
corpus-wide completion count per stage *right now*, and the backlog worklist
for Phase 2. Treat README.md as the narrative reference and this file as the
operational snapshot + worklist.

## Corrections to prior assumptions

The task brief this was written against assumed only 3 songs have completed
the pipeline **end-to-end**. That's still true for the final stages, but two
earlier stages are already corpus-wide complete, and a third stage is
*further along than it looks* because of a since-resolved experiment. Don't
re-run `separate-stems` or `extract-chords` on the full corpus — they're
done. See the stage table below for the real per-stage state.

## Stage graph

| # | Stage | Script (Makefile target) | Input | Output | Key deps | Resumable? |
|---|-------|---------------------------|-------|--------|----------|------------|
| 1 | Stem separation | `scripts/separation/separate_stems.py` (`make separate-stems`) | `audio/*.mp3`, `k-indie/*.mp3` | `stems/<song>/{drums,bass,other,vocals}.wav` | Demucs `htdemucs_ft`, torch/CUDA (GPU, CPU fallback) | Yes — skips existing stem dirs |
| 2a | Harmonic transcription | `scripts/transcription/transcribe_harmonic.py` (`make transcribe-harmonic`) | `stems/<song>/{other,bass}.wav` | `midi/<song>/{other,bass}.mid` | Basic Pitch (CPU; TF can't see GPU here) | Yes — skips `(song,stem)` pairs whose `.mid` exists |
| 2b | Drum transcription | `scripts/transcription/transcribe_drums.py` (`make transcribe-drums`) | `stems/<song>/drums.wav` | `midi/<song>/drums.mid` | ADTLib + madmom + TensorFlow 1.15, **must run under `tools/adtlib-env/bin/python`** (isolated Python 3.7.17 env, not the main `.venv`) | Yes — skips songs with existing `drums.mid` |
| 3 | Merge | `scripts/transcription/merge_stems.py` (`make merge-stems`) | `midi/<song>/{other,bass,drums}.mid` (any subset ≥1) | `merged/<song>.mid` (multi-track) | pretty_midi | Yes — re-merges if fewer than 3 stems present or any stem is newer than the merged file; also has `--check` (re-run sanity check only) |
| 4 | Chord detection | `scripts/chords/extract_chords.py` (`make extract-chords`) | `stems/<song>/other.wav` | `chords/<song>/other_chordnotes.csv` | Chordino (nnls-chroma Vamp plugin), built from source at `tools/vamp-plugins/`; script sets `VAMP_PATH` itself | Yes — skips songs with existing chordnotes CSV |
| 5 | Chord-tone tagging | `lib/tag_chord_tones.py` (`make tag-chord-tones`) | `merged/<song>.mid` + `chords/<song>/other_chordnotes.csv` | `chords/<song>/other_note_tags.csv` | none beyond stdlib/mido | Yes — needs both inputs, skips+logs if chordnotes missing |
| 6 | Review ranking | `scripts/chords/rank_review_candidates.py` (`make rank-review-candidates`) | `chords/<song>/other_note_tags.csv` (all songs) | `output/review_candidates.csv`, `output/chord_review_candidates.csv` | none | Recomputed fresh each run (cheap, corpus-wide scan) |
| 7 | Auto-correction | `scripts/correction/auto_correct_pitches.py` / `scripts/correction/correct_notes.py` (`make correct-notes`) | `merged/<song>.mid` + tagged chord data | `corrected/<song>.mid` (only if ≥1 fix applied), `_changelog.csv`, `_proposals.csv` | none | Yes |
| 8 | Export | `scripts/export/export_note_events.py` (`make export-note-events`) | `corrected/` or `merged/` (whichever exists) + `chords/` | `output/note_events_other.csv`, `output/chord_segments.csv` | none | Recomputed fresh each run |

Evaluation (`scripts/evaluation/evaluate_transcription.py` and friends) is a
side-branch against hand-labeled `ground_truth/` — not on the critical path
to corpus coverage, see README.

## Corpus-wide completion, verified today (2026-09-12)

Corpus = 75 unique songs (68 in `audio/` + 30 in `k-indie/`, deduped — same
count the README/prior notes use).

| Stage | Complete | Notes |
|---|---|---|
| `stems/` | **75/75** | 0 entries in any failure log; confirmed via directory count |
| `midi/<song>/other.mid` | **75/75** | See "Onset-threshold experiment" below — this only just became true |
| `chords/<song>/other_chordnotes.csv` | **75/75** | 0 failures |
| `midi/<song>/bass.mid` | **3/75** | `transcribe-harmonic --dry-run` confirms exactly 72 pending jobs |
| `midi/<song>/drums.mid` | **3/75** | not yet run on the backlog |
| `merged/<song>.mid` | **3/75** | blocked on bass+drums (see note below — could partially advance now) |
| `chords/<song>/other_note_tags.csv` | **3/75** | depends on `merged/` |
| `corrected/<song>.mid` | **3/75** | depends on `merged/` + tags |
| `output/note_events_other.csv` rows | **3 songs** | depends on `corrected/`/`merged/` |
| `output/chord_segments.csv` rows | **75/75** | only needs `chords/`, already corpus-wide |

**The real remaining backlog is narrower than "72 songs through the whole
pipeline"**: stems and chord detection are done for everyone. What's left is
transcription (bass + drums) → merge → tag → correct → export for the 72
backlog songs.

Note on `merge-stems`: per its own logic, it merges as soon as **any** stem
is present, so running it right now would already produce a `merged/<song>.mid`
for all 72 backlog songs containing just their `other` track — useful as an
intermediate checkpoint, but not "done" (it'll re-merge automatically once
bass/drums land, since fewer-than-3-stems songs are always eligible for
re-merge). Not necessary to run separately; the full run in Phase 2 will
merge each song once all its available stems exist.

## Onset-threshold experiment (why `other.mid` jumped to 75/75)

Not part of the normal linear pipeline — found via two **untracked** scripts
and result CSVs sitting in the working tree:

1. `scripts/transcription/retranscribe_other_onset_fix.py` (one-off): tried
   lowering Basic Pitch's `other`-stem onset threshold 0.5→0.45 to recover
   some short/soft high notes (diagnosed in README's "known limitation"
   section). It re-transcribed `other.mid` for **all 75 songs** — the
   original 3 (backed up first to `midi_backup_pre_onset_fix/<song>/other.mid`)
   and, as a side effect, the first-ever `other` transcription for the other
   72 (see `midi/_retranscribe_other_results.csv`).
2. The fix was reverted (per README: false-positive rate at corpus scale
   wasn't worth it). `scripts/transcription/revert_other_to_stock.py`
   (also one-off, also untracked) restored stock-threshold output for all
   75: the 3 with a backup via direct file copy (deterministic model, no
   recompute needed), the other 72 by **re-transcribing at stock config**
   (verified: `transcribe_harmonic.py`'s current `STEM_THRESHOLDS` has no
   override for `other` — both stems use library defaults). Recorded in
   `midi/_revert_other_results.csv`, 75 rows, no failures.

**Net effect, confirmed:** every song's `midi/<song>/other.mid` currently on
disk is stock-Basic-Pitch output, byte-for-byte what `transcribe_harmonic.py`
would produce today. No action needed on it. `bass.mid`/`drums.mid` were
never touched by this experiment.

**Housekeeping flag, not a blocker:** `retranscribe_other_onset_fix.py`,
`revert_other_to_stock.py`, and `midi_backup_pre_onset_fix/` are untracked in
git (`git status` shows `??`). They're one-off scripts per their own
docstrings, already served their purpose, and are reasonable to keep
uncommitted as a historical record or to delete — that's a judgment call for
you, not something this session changed unprompted.

## Dependency inventory (verified working today)

- **Main env** (`.venv` via `uv sync`, Python 3.11): demucs, torch/torchaudio
  2.11.0 (cu130), basic-pitch, mido, pretty_midi, mir_eval, `vamp` — all
  import cleanly.
- **Chordino / Vamp**: `tools/vamp-plugins/nnls-chroma.{so,n3,cat}` present;
  `vamp.list_plugins()` only sees it when `VAMP_PATH` points there —
  `extract_chords.py` sets that env var itself before importing `vamp`, so
  this is not a live failure point, just don't invoke Chordino code paths
  standalone without that env var set.
- **ADTLib env** (`tools/adtlib-env/`, isolated Python 3.7.17 build):
  confirmed `import ADTLib, madmom, tensorflow` succeeds, TF reports 1.15.5
  as expected. Must be invoked via `tools/adtlib-env/bin/python -m
  scripts.transcription.transcribe_drums` (`make transcribe-drums` does this).
- **No active failure logs** anywhere in `stems/`, `chords/`, `midi/`,
  `merged/` right now — clean slate going into Phase 2.
- Disk: 1.2T free on the volume — not a constraint for this corpus size.

## Runtime costs (measured or carried over from prior runs)

- `separate-stems` (Demucs, GPU): ~1m28s/song (from a prior session's timing
  notes; not re-measured this session since this stage is already 75/75).
- `extract-chords` (Chordino, CPU): noticeably faster than Demucs per song
  (same prior source; already 75/75, not re-measured).
- `transcribe-harmonic` (Basic Pitch, CPU): not separately timed yet; library
  is described in its own docstring as fast per file. Get a real number from
  the Phase 2 smoke test.
- `transcribe-drums` (ADTLib, isolated env): no timing data yet — get this
  from the Phase 2 smoke test too, since it's the one stage never run at
  scale before.
- `merge-stems`: trivial (pretty_midi file I/O only, no model inference).

## Relevant for future analysis work

(Per the forward-looking note in the task brief — chord-pattern /
sequence-alignment work on top of this pipeline.)

- **Chordino output is note-level, not chord-label text.**
  `chords/<song>/other_chordnotes.csv` columns:
  `segment,onset,offset,pitch,pitch_class` — one row per note Chordino
  judged present in a detected chord segment (a segment id groups rows).
  There is no "Cmaj7"-style label anywhere in the pipeline; harmonic content
  is represented purely as pitch-class sets.
- **Chord segments, deduplicated:** `output/chord_segments.csv` —
  `song,start,end,pitch_class_set` (pitch classes as a comma-joined string
  in one cell, e.g. `"0,3,6,8"`), one row per distinct segment, corpus-wide
  already (75/75).
- **Tagged notes:** `chords/<song>/other_note_tags.csv` —
  `onset,offset,pitch,pitch_class,velocity,segment_onset,segment_offset,
  chord_pitch_classes,tag` where `tag` ∈ `chord_tone`/`non_chord_tone`/
  `no_chord_data`. This is the per-note harmonic-agreement signal.
- **Final flattened note-event schema** (`output/note_events_other.csv`,
  the intended input to downstream pattern work): `song, onset, offset,
  pitch, velocity, chord_onset, chord_offset, chord_pitchclasses,
  was_corrected, correction_type, original_pitch`. `other` track only;
  `bass`/`drums` are out of scope for this export as currently built.
  **Pitch reflects Chordino-guided auto-correction where `corrected/`
  exists** — check `was_corrected` before treating `pitch` as raw
  transcription.
- **No sequence-alignment / bioinformatics libraries are in `pyproject.toml`
  yet** (checked: demucs, torch, torchaudio, mido, pretty-midi, mir-eval,
  numpy, basic-pitch, vamp — nothing like biopython/edlib/parasail). Nothing
  to reuse there; that layer would start from scratch on dependencies too.
- **No existing similarity/matching code** in the repo beyond the harmonic-
  consistency ranking in `rank_review_candidates.py` (which ranks by
  non-chord-tone density, not by cross-song similarity).
- Onset/offset values throughout are **absolute seconds as floats**, not
  MIDI ticks or beats — relevant for any alignment approach that assumes a
  regular grid (there isn't one; no tempo detection anywhere in the
  pipeline, see `transcribe_drums.py`'s fixed 120 BPM placeholder used only
  for MIDI tick math, not reflecting the song's real tempo).

## Exact commands (Phase 2 will use these)

```
make transcribe-harmonic ARGS="--dry-run"     # confirm job count before running
make transcribe-harmonic ARGS="--limit 5"     # smoke test
make transcribe-harmonic                      # full backlog (bass only; other is done)

tools/adtlib-env/bin/python -m scripts.transcription.transcribe_drums --dry-run
make transcribe-drums ARGS="--limit 5"
make transcribe-drums

make merge-stems ARGS="--dry-run"
make merge-stems

make tag-chord-tones
make rank-review-candidates
make correct-notes
make export-note-events
```

All accept `--retry-failed` to clear that stage's failure log and retry.

## How this was verified

Ran directly against the repo, not inferred from docs:
`git status`, full top-level `find`, per-song file counts in `midi/`,
`ls merged/*.mid | wc -l`, `ls chords | wc -l`, `ls corrected/*.mid | wc -l`,
read `Makefile`/`README.md`/`pyproject.toml`/`main.py` in full, read
`transcribe_harmonic.py`/`transcribe_drums.py`/`merge_stems.py` source in
full, read `retranscribe_other_onset_fix.py`/`revert_other_to_stock.py`
docstrings + their result CSVs, ran `transcribe-harmonic --dry-run` to
confirm the 72-song bass backlog, imported `ADTLib`/`madmom`/`tensorflow`
under `tools/adtlib-env/bin/python`, imported `vamp` with `VAMP_PATH` set
and confirmed `nnls-chroma:chordino` is visible, checked for failure logs
under every data directory, checked free disk space.
