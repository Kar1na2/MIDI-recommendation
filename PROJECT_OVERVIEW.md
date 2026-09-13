# PROJECT_OVERVIEW.md

What this repo actually does today, end to end — from raw audio to
cross-song pattern analysis — what's shaky or unfinished, and concretely
what's needed to put a frontend/recommendation product on top of it.
`README.md` is the narrative day-to-day reference and `PIPELINE_MAP.md` is
the pipeline's own operational snapshot; this doc is the one-stop "what has
been built and what's next" read, written after Phase 3B and a repo cleanup
pass. Written 2026-09-13.

## 1. What this project is

Given a personal music collection (two source playlists, 75 songs total),
transcribe each song into MIDI-level note/chord data, then look for songs
that share real melodic or harmonic material — the long-term goal (per
README's "Approach" section) being a recommendation system: "songs like
this one, and here's specifically why."

Two layers exist right now:

1. **The pipeline** (stages 1–8 below) — turns raw mp3s into structured,
   per-note and per-chord-segment CSVs. Fully built, fully run, 75/75 songs,
   0 failures anywhere.
2. **The analysis layer** (Phase 3A/3B) — takes those CSVs and finds
   cross-song similarity via sequence alignment (the same class of
   algorithm used for DNA/protein matching — Needleman-Wunsch/Smith-Waterman
   — applied to chord progressions and melodic intervals instead of
   nucleotides). This is the part that actually answers "which songs are
   similar and where." Built and run corpus-wide; several real methodology
   bugs were found and fixed along the way (§3.3), and one is only
   partially fixed and explicitly flagged in the output (§3.3, bug 3).

**Nothing web-facing exists yet.** There is no API, no database, no served
audio, no UI. §5 covers exactly what that needs.

## 2. The pipeline, stage by stage

Full detail (I/O, CLI flags, dependency stories) is in `README.md`;
`PIPELINE_MAP.md` has the stage-graph table this section condenses. Every
stage is a resumable script under `scripts/<stage>/`, run via `make
<target>` (never call a script by file path directly — see README's note on
why, module imports need it run as `-m` from the repo root).

| # | Stage | What it does | Status |
|---|---|---|---|
| 1 | `separate-stems` | Demucs splits each mp3 into `{drums,bass,other,vocals}.wav` | 75/75 |
| 2a | `transcribe-harmonic` | Basic Pitch transcribes `other`+`bass` stems to MIDI | 75/75 |
| 2b | `transcribe-drums` | ADTLib transcribes `drums` to MIDI (isolated Python 3.7 env — see README) | 75/75 |
| 3 | `merge-stems` | Combines the 3 stem MIDIs into one multi-track `merged/<song>.mid` | 75/75 |
| 4 | `extract-chords` | Chordino (Vamp plugin, built from source) detects chord segments from `other.wav` | 75/75 |
| 5 | `tag-chord-tones` | Tags each transcribed note chord-tone / non-chord-tone / no-chord-data | 75/75 |
| 6 | `rank-review-candidates` | Ranks segments/songs by non-chord-tone density (diagnostic, not a verdict) | recomputed fresh each run |
| 7 | `correct-notes` | Auto-corrects only the high-confidence `stuck_pitch` pattern; everything else goes to a human-review proposals file | 75/75 |
| 8 | `export-note-events` | Flattens everything into `output/note_events_other.csv` (113,938 rows) + `output/chord_segments.csv` (8,342 rows) | done |

**What this output actually represents, important for anything built on
top of it:** every downstream number in this repo (pipeline and analysis
both) describes **the `other` Demucs stem only** — guitars/keys/pads/synths,
the "harmonic accompaniment" bucket. Bass notes and vocal-implied harmony
are invisible to it. `pitch` in `note_events_other.csv` reflects
Chordino-guided auto-correction where it ran (`stuck_pitch` fixes only —
check the `was_corrected` column), not raw transcription. `vocals` is
separated but never transcribed (no melody-from-singing extraction exists —
flagged as future work since README's first draft).

**Transcription accuracy is not independently verified against ground
truth yet.** `ground_truth/` and `ground_truth_anchor/` have the scaffolding
and hand-labeling workflow built (`make evaluate-transcription`), but the
actual hand-labeling was never completed — there is currently no `mir_eval`
accuracy number for this corpus. Known limitations that *are* documented
(README): the "other" stem misses some short/soft high notes at Basic
Pitch's default `minimum_note_length` (investigated, a length-reduced
threshold was tried and reverted — false-positive rate wasn't worth it);
ADTLib only detects 3 drum classes (kick/snare/closed-hihat) with no
velocity.

## 3. The analysis layer (Phase 3A + 3B)

Both phases follow the same shape: convert each song into a sequence of
symbols, align every pair of songs with the same generic engine
(`lib/sequence_alignment.py` — hand-rolled Needleman-Wunsch/Smith-Waterman,
built alphabet-agnostic on purpose so Phase 3B could reuse it unchanged),
score how significant each pair's alignment is relative to the rest of the
corpus, and extract the actual matched region for the strongest pairs so a
human can look at what specifically lines up.

### 3.1 Phase 3A — chord-progression analysis

**Input:** `output/chord_segments.csv` (8,342 Chordino-detected chord
segments, 75 songs). **Alphabet:** each segment's pitch-class set
canonicalized to a Forte class (`lib/chord_canonicalization.py`, via
`music21`) — 100 distinct pitch-class sets reduce to 9 actual chord
qualities in this corpus (major/minor triad, major/minor-7th,
dominant-7th, half-diminished-7th, augmented, diminished, one add9-type
sonority). Because quality alone (transposition-invariant) turned out to
have no real cross-song signal (validated, then rejected — see design doc),
alignment is **root-aware**: each segment also carries its actual root
pitch-class, and alignment tries all 12 rigid transpositions to find the
best key-invariant match (`lib/chord_similarity.py`).

**Output:** `output/analysis/chord_similarity_matrix.csv` (all 2,775 song
pairs, `corpus_z` significance column), `output/analysis/chord_motif_variations.csv`
(1,975 rows across the 28 pairs that cleared `corpus_z ≥ 2.5`, with actual
timestamps/pitch-classes for human review), plus `chord_vocabulary.csv` and
`chord_ngrams.csv`. Full reasoning in `output/analysis/CHORD_ANALYSIS_DESIGN.md`.

### 3.2 Phase 3B — note-progression ("melodic DNA") analysis

**Input:** `output/note_events_other.csv` (113,938 notes, 75 songs).
**Alphabet:** signed semitone intervals between consecutive notes
(`pitch[i]-pitch[i-1]`) — transposition-invariant by construction, so
(unlike Phase 3A) no transposition search is needed; exact-interval
granularity was validated to find real matches, so no Parsons-code-style
coarsening was needed either (`lib/note_similarity.py`).

**Output:** `output/analysis/note_similarity_matrix.csv` (2,775 pairs,
`corpus_z` column), `output/analysis/note_motif_variations.csv` (18,153
rows across the top 30 pairs clearing `corpus_z ≥ 2.5`, each row carrying
`low_complexity_flag` — see §3.3). Full reasoning in
`output/analysis/NOTE_ANALYSIS_DESIGN.md`.

**Best current unflagged lead:** `KARMA × Riddle (feat. Khundi Panda)`,
`corpus_z = 4.74`, a genuinely localized 316-position match (~22% of the
shorter song, 42 exact-interval matches) — the kind of result worth an
actual human listen.

### 3.3 What needs attention — real bugs found, one still open

Both phases followed the same validate-before-scaling discipline (small
hand-picked sample first, checked spans/scores look musically real, *then*
ran the full corpus) and it caught real problems each time — worth reading
in full before extending either phase (`chord-analysis-phase3a` and
`note-analysis-phase3b` entries in the project's session-memory system have
the condensed version):

1. **Local-alignment scoring must be calibrated so a random pairing scores
   net-negative, empirically, not by assumption.** Both phases initially
   used a naive score range and got degenerate alignments spanning almost
   entire songs instead of a localized motif — Phase 3A's fix was a
   corpus-frequency-weighted chord-similarity recalibration; Phase 3B's fix
   needed an even steeper target after the naive fix still wasn't enough
   (§3, `NOTE_ANALYSIS_DESIGN.md`).
2. **A significance null must be validated at the SAME scale it runs at.**
   Phase 3A's permutation-test null looked fine on a 9-pair hand sample and
   was badly miscalibrated at the full 2,775-pair scale (this corpus is
   full of short, vamped/looped songs, which breaks shuffle-based nulls).
   Fixed with a corpus-relative ("row-normalized") z-score instead. Phase
   3B knew this going in and skipped permutation testing for the full run
   entirely, going straight to the row-normalized approach — saved ~8.5
   hours of compute that would have been thrown away regardless.
3. **Still-open: a low-complexity-song artifact in Phase 3B's significance
   score.** Songs with an unusually repetitive/narrow interval vocabulary
   (measured via Shannon entropy) align suspiciously well against almost
   anything — the same problem BLAST solves with SEG/DUST low-complexity
   masking. Partially corrected (added entropy as a regression covariate,
   improved calibration close to Phase 3A's own numbers) but **not fully
   removed** — a proper masking algorithm was judged out of scope for this
   pass. **Consequence: 24 of the top 30 note-similarity pairs are flagged
   `low_complexity_flag=True` and should not be trusted at face value** —
   this flag needs to be surfaced in any UI built on top of
   `note_motif_variations.csv`, not silently dropped or silently trusted.
4. **~19% of "consecutive" notes are actually simultaneous** (same onset,
   <0.01s apart) — the `other` stem is often polyphonic (chord voicings),
   not a monophonic melody line, so Phase 3B's interval sequence mixes real
   melodic motion with chord-voicing spread (this is why octave intervals
   are the single most common value). Documented, not filtered — a
   monophonic/skyline melody extraction was judged out of scope but would
   be the natural fix if this turns out to matter.
5. **Motif detail is only computed for a shortlist, not every pair.**
   `chord_motif_variations.csv`/`note_motif_variations.csv` only cover the
   ~28–30 pairs that cleared `corpus_z ≥ 2.5` in each domain. A frontend
   that lets a user click into *any* similar-song pair for a "why" view
   will hit pairs with no motif row — see §5.

## 4. Repository structure (post-cleanup)

```
scripts/
  separation/       stage 1 (Demucs)
  transcription/     stages 2a-3 (Basic Pitch, ADTLib, merge) + inspect_basic_pitch_npz.py (diagnostic)
  chords/            stage 4, 6 (Chordino, review ranking)
  correction/         stage 7 (auto-correction + comparison/summary tools)
  evaluation/         ground-truth sampling/scoring
  export/             stage 8 (flatten to output/*.csv)
  analysis/           Phase 3A + 3B (chord + note pattern analysis)
lib/                  modules shared across >1 stage or analysis script
output/               generated CSVs (gitignored) + output/analysis/ (see below)
output/analysis/      Phase 3 deliverables - *.md tracked in git (authored
                       reasoning), *.csv gitignored (regenerable via `make
                       run-alignment` / `make run-note-alignment` etc.)
archive/               one-off/completed scripts no longer in the active
                       pipeline: original Spotify/audio ingestion tools,
                       plus two resolved-and-reverted transcription
                       experiments (onset-threshold tuning)
ground_truth/,
ground_truth_anchor/   hand-labeled accuracy data (tracked - not regenerable)
tools/                 Chordino/Vamp build + isolated ADTLib Python 3.7 env
                       (both gitignored, machine-specific build output)
```

**Cleanup done alongside this commit:** moved `tmp.py` (undocumented at
repo root) to `scripts/transcription/inspect_basic_pitch_npz.py` with a
proper usage docstring and a `make` target; moved `web_scraper.py` to
`archive/download_songs.py` with a docstring explaining its place in the
ingestion chain; removed `main.py` (unused `uv init` boilerplate, nothing
referenced it); archived the two resolved onset-threshold experiment
scripts and deleted their now-redundant pre-experiment MIDI backup
(`midi_backup_pre_onset_fix/` — the experiment was fully reverted and
verified identical to stock output, so the backup no longer serves a
purpose); added `scripts/analysis/__init__.py` (was missing — every other
stage folder has one); added `Makefile` targets for every Phase 3A/3B
script (previously only runnable via bare `python -m`, inconsistent with
every other stage).

## 5. What's needed to connect this to a frontend

Nothing web-facing exists in this repo yet — no API, no database, no served
audio/MIDI, no UI. Below is a concrete, ordered list of what building one
actually requires, given what's real today.

### 5.1 Song identity is the first blocker

Every CSV in this repo — pipeline and analysis both — keys songs by their
**raw source-file title string** (e.g. `"163braces - 過期 (lyrics music
video)"`, `"Alice U (앨리스유) - 도주 (Official Video)"`). That's fine for a
95%-Python, human-reading-CSVs project; it is not fine for a database
primary key or a URL path segment — these strings contain slashes,
non-ASCII, punctuation, and inconsistent "Artist - Title (extra)" shapes
scraped from YouTube-style filenames (already visible as the escaped
filenames in `git status` for `ground_truth/`). **First concrete step:**
build a `songs` table/mapping with a stable id (slug or UUID) and, where
extractable, actual structured `artist`/`title` fields separate from the
raw source filename — every other table (note events, chord segments,
similarity matrices, motif variations) should be re-keyed to that id once
it exists, rather than re-deriving string-matching logic in every consumer.

### 5.2 Database — the README already names the target: Postgres + pgvector

A reasonable first schema, loadable directly from what already exists on
disk:

- `songs (id, title, artist, source_path, audio_path, midi_path, duration_sec, …)`
- `note_events (id, song_id, onset, offset, pitch, velocity, chord_pitchclasses, was_corrected, …)` — from `note_events_other.csv`
- `chord_segments (id, song_id, start, end, pitch_class_set, canonical_chord_id, root_pc, …)` — from `chord_sequences.csv`
- `song_similarity (song_a_id, song_b_id, domain ['chord'|'note'], raw_score, normalized_score, corpus_z, span_a_*, span_b_*, aligned_len)` — from both `*_similarity_matrix.csv` files; this table **is** the recommendation signal (`ORDER BY corpus_z DESC WHERE song_a_id = ?`)
- `motif_variations (song_a_id, song_b_id, aligned_position, a_*, b_*, match_type, low_complexity_flag …)` — from both `*_motif_variations.csv` files, for a "why are these similar" detail view

**On pgvector specifically:** the current analysis is pairwise sequence
alignment (an exact, all-pairs O(n²) computation — 2,775 real alignments at
75 songs), not a vector-embedding nearest-neighbor search, so there's no
existing per-song vector to index yet. pgvector becomes relevant if/when
the corpus grows past what brute-force pairwise alignment can cover (see
§5.4) and a fixed-length per-song summary vector (e.g. an interval-n-gram
frequency histogram) is built as an approximate-nearest-neighbor
prefilter — that's new work, not a repackaging of what's already computed.

### 5.3 API layer

Nothing exists. Minimum viable surface for the schema above:

- `GET /songs`, `GET /songs/:id` — browse/search the corpus
- `GET /songs/:id/similar?domain=chord|note` — ranked list from `song_similarity`, **filtering or clearly labeling `low_complexity_flag` pairs** (§3.3) rather than presenting them as equal-confidence recommendations
- `GET /pairs/:a/:b/motif` — the matched-region detail, when it exists (§5.5)
- Audio/MIDI serving — currently everything is local files (`audio/`,
  `k-indie/`, `merged/`, `corrected/`); needs an actual file-serving or
  object-storage layer before a frontend can play anything, and a
  clip-extraction step (using `span_a_start/end` from the similarity
  matrix) to actually let a user hear the matched region, not just see
  numbers.

### 5.4 Scale ceiling — this corpus's size hid a real constraint

The "no prefilter needed" decision in both design docs was **measured, not
assumed** — but it was measured at 75 songs / 2,775 pairs. Full pairwise
Smith-Waterman is `O(n²)` in the number of songs; brute force took ~25-70
minutes at this scale. A catalog of a few thousand songs (~50M+ pairs) would
not be brute-forceable the same way — the k-mer seed-and-extend prefilter
both design docs describe (and deliberately didn't build, because it wasn't
needed yet) is the documented next step if the corpus grows meaningfully
past its current 75 songs. Don't assume the current "brute force is fine"
conclusion still holds without re-measuring at the new scale — the design
docs say this explicitly.

### 5.5 Motif coverage is a shortlist, not universal

`extract_motifs.py`/`extract_note_motifs.py` only compute the actual
matched region for pairs clearing `corpus_z ≥ 2.5` (28–30 pairs per
domain, out of 2,775). If the frontend needs a "why is this similar" view
for every pair the `song_similarity` table can rank (not just the current
top shortlist), that means either lowering `--z-threshold`/raising
`--top-n` and re-running those scripts, or computing a pair's motif
on-demand at request time (the alignment itself is fast per-pair — the
slow part was doing all 2,775 of them, not any single one).

### 5.6 Product/legal caveat, not just technical

`audio/` and `k-indie/` are the author's own personal playlists (per
README's "Things I have done" entries), not licensed content. Fine for a
personal/demo project; worth flagging explicitly before any plan that
involves serving this audio to other people or beyond a local demo.

## 6. Suggested next-step order

1. Song-identity table (§5.1) — everything downstream depends on this and nothing else does.
2. Load existing CSVs into Postgres per §5.2 — this alone unlocks "similar songs" queries without writing any new analysis code.
3. Minimal read-only API (§5.3) over that schema.
4. Decide low-complexity-flag UX (§3.3 bug 3) before shipping note-similarity recommendations — don't surface flagged pairs as confident matches.
5. Only then: audio serving/clip extraction, wider motif coverage (§5.5), and (if the corpus is going to grow) the k-mer prefilter (§5.4).
