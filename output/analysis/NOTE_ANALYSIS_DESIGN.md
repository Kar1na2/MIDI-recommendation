# Phase 3B — Note-Progression Analysis: Design Doc

Written 2026-09-13. This is the methodology record for everything new under
`output/analysis/` in this phase plus one new reusable library module
(`lib/note_similarity.py`) - the note-analysis analog of
`lib/chord_similarity.py`. Reuses `lib/sequence_alignment.py` unchanged
(that module was deliberately built chord-agnostic in Phase 3A specifically
for this reuse - see `CHORD_ANALYSIS_DESIGN.md` section 4a). Every
non-obvious choice below has the data or experiment that justified it, same
discipline as Phase 3A.

## Scope this builds on

`output/note_events_other.csv` - 75/75 songs, 113,938 rows, corpus-complete
(see `pipeline-state` memory). Same scope caveat as Phase 3A carries
forward: this is Basic Pitch transcription of `stems/<song>/other.wav`
only - not full-mix melody. Uses the `pitch` column (post-correction), not
`original_pitch`, per the brief.

## Isolation

Everything in this phase reads from `output/note_events_other.csv` (Phase 2
export, read-only) and writes only into `output/analysis/`. Nothing under
`chords/`, `merged/`, `corrected/`, or the top-level `output/*.csv` files
was modified.

---

## 1. Sequence representation - melodic intervals

**Goal:** a transposition-invariant "melodic DNA" alphabet, so the same
melody in a different key matches. `interval_from_prev[i] = pitch[i] -
pitch[i-1]` (signed semitones), computed per song in onset order via
`scripts/analysis/build_note_sequences.py`, corpus-wide immediately (cheap,
deterministic - no scoring/alignment involved yet). Each song's first note
gets an empty interval (kept as a row, not dropped, so `note_index` stays a
dense per-song index other columns can still look up).

### Interval distribution - measured before picking anything

113,863 intervals (113,938 notes minus 75 first-of-song notes with no
interval). Range **[-62, +67]** semitones, 117 distinct values, mean ≈ 0,
stdev ≈ 13.9.

| stat | value |
|---|---|
| p1 / p99 | -34 / +34 |
| p5 / p95 | -23 / +23 |
| p10 / p90 | -17 / +17 |
| p25 / p75 | -9 / +9 |
| median | 0 |

Top values by frequency: `-12` (7.80%), `+12` (7.01%), `0` (5.22%), `+7`
(4.65%), `-7` (4.49%), `+4` (3.90%), `+5` (3.87%), `-4` (3.85%), `+3`
(3.69%), `-5` (3.61%) - dominated by octaves, fifths, and small steps,
which is musically unsurprising for tonal accompaniment. `|interval| <= 12`
covers 69.8% of all intervals; `|interval| <= 3` covers 15.7%.

**Decision, per the brief's own guardrail (don't guess a binning scheme,
decide empirically, only fall back to a coarser encoding if exact intervals
turn out too strict):** kept **exact signed semitone intervals** as the
alphabet, no binning, no Parsons-code coarsening. The validation checkpoint
(section 4 below) found real, musically legible matches at the exact-value
granularity - including a stretch of 8+ consecutive intervals matching
exactly between two different songs (Kanye West Heartless × Stronger) - so
there was no empirical reason to coarsen.

### A data-quality finding worth documenting up front: ~19% of "consecutive" note pairs are actually simultaneous

Checked, before trusting the "melodic DNA" framing: how often is the
"previous note" actually a different note starting at a meaningfully
different time, versus multiple notes of the same onset (i.e. a chord
voicing, not melodic motion)? Measured directly from
`output/note_events_other.csv`:

| onset gap to previous note (same song) | % of consecutive pairs |
|---|---|
| ~0 (simultaneous, <0.01s) | 19.20% |
| <0.05s | 29.30% (cumulative) |
| 0.05-0.2s | 32.13% |
| >0.2s | 19.37% |

Nearly 1 in 5 "consecutive-note" intervals is actually the gap between two
notes of the *same chord voicing* (both starting together), not one melody
note following another. This is expected given the source: Chordino/Basic
Pitch's `other` stem is guitars/keys/pads/synths - often genuinely
polyphonic - not a monophonic lead line, so a literal "notes in onset
order" sequence mixes real melodic motion with chord-voicing spread (e.g.
root-then-octave-doubling within the same strum, which is exactly why `±12`
is the single most common interval value above). **This is a scope caveat
to carry forward, analogous to Phase 3A's "other-stem-only, not full-mix
harmony" caveat: `note_sequences.csv`'s interval sequence is not a pure
monophonic melodic line - treat matches found by this analysis as "shared
patterns in the other-stem note sequence" (melody-like, but including
voicing structure), not strictly "shared melody."**

**Decision: did not filter/drop simultaneous-onset notes or attempt a
skyline/monophonic-extraction step.** The brief calls for reusing
`pitch`/`onset` as given and doesn't ask for melody-line extraction; adding
one would be a real scope expansion (voice-separation is its own
non-trivial problem) on top of an already-large phase. Instead,
`note_sequences.csv` retains a full onset-ordered sequence and this caveat
is documented so any consumer of a "motif" match knows what it actually
represents. A `gap_from_prev` column was **not** added to
`note_sequences.csv` (would exceed the brief's specified schema); the
0.01s/0.05s/0.2s breakdown above is fully reproducible from
`output/note_events_other.csv`'s own `onset` column directly, so nothing is
lost by not persisting it.

## 2. Chord-relative encoding (tier 2)

**Not built in this pass**, per the brief's explicit instruction to treat
this as a tier-2 enhancement only after the interval-based v1 is validated.
The interval-based v1 below passed its validation checkpoint on the first
design attempt (after the gap-penalty/calibration fix in section 3), so
there was no forcing function to reach for the harmony-aware encoding in
this phase. Left for a future pass if the plain interval results (section 5
below) turn out to need refinement.

## 3. Substitution scoring and calibration - a real degeneracy bug found here too

`lib/note_similarity.py`. Score function: a triangular distance decay,
`raw_similarity(a, b) = max(0, 1 - |a-b|/WINDOW)` - an exact interval match
scores 1.0, falling linearly to 0 at `WINDOW` semitones apart. `WINDOW = 6`
(a tritone) chosen as a musically meaningful "unit of dissimilarity" that
still gives graded partial credit for "off-by-one-or-two-semitones" (per
the brief: sim=0.833 / 0.667 respectively at 1 / 2 semitones off) while
treating a tritone-or-more difference as unrelated. This directly matches
what the brief asked for without inventing a fancier shape (e.g.
exponential decay was not found necessary).

**Calibration, same linear-map approach as Phase 3A's `chord_substitution_score`:**
`score(sim) = SLOPE*sim - OFFSET`, solved so `score(1.0) = MATCH_SCORE =
2.0` and `score(CORPUS_MEAN_SIMILARITY) = TARGET_RANDOM_SCORE`.
`CORPUS_MEAN_SIMILARITY` - the corpus-frequency-weighted expected
similarity between two *independently drawn* intervals - was measured
directly from the interval distribution above: **0.126** at `WINDOW=6`, far
below Phase 3A's chord baseline (0.588) and below even the naive symmetric
midpoint (0.5) Phase 3A's first (wrong) attempt used. This looked safe
(`E[score]<0` under random pairing even at Phase 3A's own
`TARGET_RANDOM_SCORE=-0.25`) - but measuring the DP's actual behavior, not
just the calibration formula, showed it wasn't.

**Bug found empirically, before touching the full corpus (per "measure
first"):** ran Smith-Waterman on the two longest songs in the corpus
(Bridge Collective - The Wonderful Blood, 4,683 intervals; KARMA, 3,198
intervals - an arbitrary pair, used purely as a stress test, no reason to
expect a real shared motif) with `TARGET_RANDOM_SCORE=-0.25`, `gap=-1.0`:
the local alignment's span covered **70-100% of both songs** - the same
"spans almost the whole song, not a localized motif" symptom Phase 3A's
"Problem 2" hit with its first (uncalibrated) chord scoring.

**Diagnosis:** `E[score]<0` under literal i.i.d. random pairing is
necessary but **not sufficient** for local alignment to behave properly.
Smith-Waterman's optimal path isn't a random sample of pairs - it can
choose favorable diagonal steps and skip unfavorable ones via gaps. With
`TARGET_RANDOM_SCORE=-0.25` at `WINDOW=6`, any two intervals within about 5
semitones of each other already scored net-positive (similarity above
`OFFSET/SLOPE ≈ 0.22`), and given how much of this corpus's interval mass
sits within a narrow band (small steps, thirds, fourths, fifths, octaves -
see the distribution above), the DP found a next positive step almost
anywhere in two long, harmonically ordinary songs and never wanted to stop
extending.

**Fix, found by sweeping `WINDOW` / `TARGET_RANDOM_SCORE` / `gap_penalty`
together and inspecting the resulting alignment span and exact-match
fraction directly** (not trusting the calibration formula alone):
`TARGET_RANDOM_SCORE = -0.5` (steeper than Phase 3A's `-0.25`) combined
with `gap_penalty = -2.0` was the first combination that produced a
properly localized alignment on the same stress-test pair (span shrank
from ~70-100% to ~3-4% of each song). **The two phases' random-baseline
targets are deliberately not required to match** - each is tuned against
its own alphabet's actual DP behavior on real data, not copied from the
other phase for consistency's own sake.

```
SLOPE  = (MATCH_SCORE - TARGET_RANDOM_SCORE) / (1 - CORPUS_MEAN_SIMILARITY)
       = (2.0 - (-0.5)) / (1 - 0.126) = 2.86
OFFSET = SLOPE - MATCH_SCORE = 0.86
MISMATCH_FLOOR = -OFFSET = -0.86  (score when |a-b| >= WINDOW)
```

## 4. Gap penalty and validation checkpoint

`scripts/analysis/validate_note_alignment_sample.py` reused Phase 3A's
exact hand-picked pair set (3 reference-song combinations, 4 same-artist
pairs, 2 negative-control pairs against a Chopin nocturne), swept
`gap_penalty ∈ {-1.5, -2.0, -2.5}` with `TARGET_RANDOM_SCORE=-0.5` fixed,
and reported a permutation-test z-score (20 shuffles/pair) plus the
alignment's span fraction and exact-match fraction for each - this
permutation z is only trusted at this small sample size; per the
Phase 3A memory, it's known to become miscalibrated at full-corpus scale
for this repetitive-pop-song-heavy corpus, so it's a small-sample sanity
check here only, not the measure used at scale (see section 5).

**Results summary (gap=-2.0, the value kept):**

| pair | tag | z (20-shuffle perm.) | span (a / b) | aligned_len | exact% |
|---|---|---|---|---|---|
| Kanye West Heartless × Stronger | same artist | **15.32** | 4.7% / 3.4% | 68 | 29.4% |
| Kanye West Heartless × POWER | same artist | 0.64 | 2.7% / 6.3% | 38 | 52.6% |
| Imagine Dragons Bones × Natural | same artist | 3.35 | 10.5% / 8.3% | 127 | 23.6% |
| Heize And July × Jenga | same artist | 3.38 | 5.4% / 6.2% | 117 | 14.5% |
| 163braces × 1nonly | reference | 1.81 | 2.8% / 2.3% | 43 | 20.9% |
| 163braces × Alice U | reference | 4.67 | 6.7% / 8.4% | 103 | 17.5% |
| 1nonly × Alice U | reference | 3.45 | 4.0% / 6.2% | 77 | 24.7% |
| Nocturne × 1nonly | negative control | 2.07 | 1.6% / 1.9% | 38 | 29.0% |
| Nocturne × Alice U | negative control | 2.26 | 3.1% / 6.2% | 77 | 20.8% |

**Gap penalty choice:** `-1.5` still produced two clearly too-broad spans
(1nonly×Alice U at 42%/65%, Imagine Dragons at 41%/33% - not "a shared
motif embedded in an otherwise different song"). `-2.5` tightened spans
further (down to 1-4%) but weakened real signal (Heize pair z dropped from
3.38 to 1.24; Imagine Dragons z dropped from 3.35 to 1.79) - the same kind
of over-tightening Phase 3A's own gap-penalty sweep flagged as a risk.
**`-2.0` is the best balance found and is the corpus-wide default**, same
numeric value Phase 3A settled on for chords (found independently here,
not copied over).

**Qualitative check (per the brief - "do high-scoring pairs look like real
shared motifs when you look at the actual pitches?"):** yes for the
clearest case. Kanye West Heartless × Stronger's aligned region contains an
8-interval run that matches essentially exactly between the two songs:
`+7 -4 -8 +8 -3 +0 +0 +0 ...` appears in both, an unambiguous shared
melodic/accompaniment fragment. The weaker pairs (Heartless × POWER,
z=0.64 despite a high 52.6% exact-match fraction on a short 38-position
alignment) illustrate the same lesson Phase 3A found for chords: a short
alignment dominated by very common small intervals (±7, ±12, 0) can look
matchy by raw exact-fraction alone while not clearing a real significance
bar once compared to a proper null - exactly why the significance measure,
not the raw score or match fraction, is what downstream consumers should
trust. **Negative controls are not a clean zero** here either (Nocturne
reaches z≈2-2.3 against both pop songs) - same "unrelated tonal music
still shares generic regularities" caveat Phase 3A documented; expect some
part of the full matrix to land in this range by chance alone, same as
before.

**Conclusion: the interval-based v1 design validated successfully.**
Proceeded to full-corpus alignment.

## 5. Scaling - measured, prefilter found unnecessary

Song lengths range 103-4,684 notes/song (mean 1,519) - a >45x spread, and
full pairwise coverage is `C(75,2) = 2,775` pairs, same scale concern the
brief raised. **Measured before deciding anything**, per the brief's
explicit instruction:

- Ran Smith-Waterman on the 3 largest song pairs (4,683×3,198, 4,683×3,163,
  3,198×3,163 intervals). Wall-clock: 2.6-3.75s per pair
  (~4.0M DP cells/sec, using the calibrated `interval_substitution_score` -
  its `lru_cache` stays tiny since only 117 distinct interval values occur
  corpus-wide, ~13,689 possible cached pairs).
- Summed `len_a * len_b` over all 2,775 real pairs: **~6.37 billion DP
  cells total**. At the measured rate, single-pass brute force (one real
  alignment per pair, no permutation shuffles - see section 6 for why
  shuffles are skipped entirely) is **~27 minutes** for the full corpus.

**Decision: no k-mer seed-and-extend prefilter was built.** The brief
frames the prefilter as the fix "if brute-force pairwise alignment isn't
fast enough" - measuring first showed it is fast enough (~27 minutes fits
this repo's established long-batch-run convention comfortably, see
`background-run-conventions` memory - smoke-tested with `--dry-run` and
`--limit`, then launched as a background run). Building, and separately
validating, a prefilter would have added real complexity (a k-mer index,
Jaccard/MinHash sketch, a shortlist-vs-brute-force validation pass) to
solve a scaling problem that doesn't actually exist at this corpus's size.
**`output/analysis/note_kmer_candidates.csv` is therefore not produced** -
the brief listed it as conditional ("if the prefilter is needed"), and it
wasn't. If the corpus grows much larger in the future (order of magnitude
more songs, or much longer average sequences), revisit this measurement
before assuming the same conclusion holds.

## 6. Why the full run skips permutation-test shuffling entirely

Phase 3A's `run_alignment.py` computed a 20-shuffle permutation-test
z-score per pair and only discovered *after* the full 2,775-pair run that
this null is badly miscalibrated at that scale (mean z +1.70 when it
should be ~0) because shuffling a song's own segment/note order destroys
its real internal repetition, and this corpus is full of short
vamped/looped songs (see `chord-analysis-phase3a` memory /
`CHORD_ANALYSIS_DESIGN.md` section 4d - explicitly flagged there as "worth
reading before any Phase 3B sequence-alignment reuse"). That lesson is
already known going into this phase, so `scripts/analysis/run_note_alignment.py`
never computes a permutation null for the full run at all - it goes
straight from raw/normalized score to the corpus-relative z-score
(`scripts/analysis/compute_note_corpus_z.py`, structurally identical to
Phase 3A's `recompute_significance.py`) as a pure post-process, at zero
extra alignment cost. This also saves the ~20x runtime multiplier
permutation shuffling would have added (would have pushed the ~27-minute
single-pass estimate to ~9 hours) for a measure that would have been
thrown away regardless.

The small-sample validation checkpoint (section 4) still used a permutation
null - that's a different, smaller-scale use where Phase 3A's own
validation-sample permutation numbers held up fine; the flaw only appears
at full-corpus scale, which this run avoids relying on it for entirely.

## 7. The full-corpus run exposed two more real bugs in the significance measure - found, diagnosed, and fixed before calling this done

`run_note_alignment.py` completed cleanly: all 2,775 pairs, 0 failures,
~24.7 minutes (close to the ~27-minute estimate from section 5). But a
first pass at corpus_z - literally reusing Phase 3A's
`normalized_score = raw_score / (min(len_a,len_b) * MAX_POSITION_SCORE)`
then row-normalizing, unchanged - failed its own sanity check immediately:
the validated true positive from section 4 (Kanye West Heartless x
Stronger, permutation z=15.32, a genuinely striking near-exact 8-interval
shared run) came back at **corpus_z = -0.117**, indistinguishable from
noise. And the top of the ranking was dominated by one 102-note song
(the corpus's shortest, "Infinite Requiem (Deoksoon)") appearing in 8 of
the top 15 pairs against completely unrelated songs/genres/artists - a
red flag on its face, not a subtle statistical nuance.

**Bug 1 - length confound.** Phase 3A's chord segments were all a few
hundred per song (a narrow length range: the whole corpus's segment counts
cluster tightly). Phase 3B's note sequences span 103-4,684 (>45x). Measured
directly: `Infinite Requiem`'s own background of 74 normalized_scores has
mean=0.076, sd=0.017 (tight and high); a typical mid-length song
(163braces, 1,475 notes) has mean=0.011, sd=0.011 (looser, lower) - a
structurally different background purely from being the shortest song in
nearly every one of its own pairs. Root cause: Smith-Waterman's expected
*maximum* local-alignment score under pure chance grows with **both**
sequence lengths (more candidate start positions in a longer "haystack"
raises the expected max, an extreme-value-statistics effect - the same
reason BLAST's E-values are a function of both sequence lengths, per
Karlin-Altschul theory, not just the shorter one). Tried normalizing by
`sqrt(len_a*len_b)`, the arithmetic mean, and the max instead of the min -
none removed the length correlation (mean `|corr(partner_len,
normalized_score)|` across all 75 songs stayed 0.41-0.64 for every linear
normalization tried), because the true relationship is closer to
logarithmic in both lengths, not a linear ratio of any kind.

**Fix 1: regress `raw_score` against `ln(min_len)` and `ln(max_len)`
across the full 2,775-pair matrix, and row-normalize the RESIDUALS**
instead of a naively-normalized score. This is the length-normalization
Karlin-Altschul-style significance testing actually uses (log of both
sequence lengths, fit empirically rather than assumed), adapted to this
repo's existing row-normalization approach rather than implementing full
E-value theory. R² of length-only regression: 0.019 (length explains very
little of the *overall* score variance - most of it is which specific
songs are being compared - but the *residual* length-vs-partner-length
correlation that was corrupting each song's background dropped
substantially, and Infinite Requiem's domination of the top-15 disappeared).

**Bug 2 - low-complexity songs.** Fixing the length confound surfaced a
different, still-real pattern at the top of the ranking: `Yaeji - Raingurl`
alone appeared in 8 of the new top 15 pairs, paired against genre-unrelated
songs, each with `aligned_len` covering essentially the **entire shorter
song** (e.g. 1,039 of 1,052 notes against `유라 (youra) - Rawww` - 99%,
not a localized motif by any definition). Measured each song's interval-
sequence Shannon entropy corpus-wide (117-symbol alphabet, per-song
distribution): range 3.63-5.75 bits, median 5.16. `Yaeji - Raingurl`:
4.14 bits, only 42 distinct interval values used out of 117, top single
value (`-4`) alone 15% of the song. This is directly analogous to the
well-known low-complexity-alignment problem bioinformatics tools handle
with SEG/DUST masking (a repetitive/low-vocabulary sequence makes chance
local matches cheap to find against almost anything - not evidence of real
shared content). The other repeat offenders at the top (`Fendi 2`,
`Quarto De Hotel`, `유라 - Rawww`, `Infinite Requiem`) are also all in the
bottom 10 of the corpus's entropy distribution.

**Fix 2, partial by design: added `entropy_min`/`entropy_max` (of the pair's
two songs) as two more regression covariates** alongside the length terms
- R² improved to 0.209, and the resulting corpus_z distribution calibrates
close to Phase 3A's own final numbers:

```
corpus_z >= 2: 2.23%   (Phase 3A: 2.23%)
corpus_z >= 3: 0.68%   (Phase 3A: 0.43%)
corpus_z >= 4: 0.29%   (Phase 3A: 0.07%)
```

This did **not** fully remove the low-complexity artifact - `Yaeji -
Raingurl` still dominates the top of the ranking with near-full-song
aligned spans. Diagnosed why: the effect is an *interaction* (two
low-entropy songs paired together score far higher than either song's own
average entropy effect predicts), and more fundamentally it's the exact
same degeneracy class as section 3's original bug (DP never wants to stop
extending) - just re-emerging locally for the sub-population of especially
repetitive song pairs, where `lib/note_similarity.py`'s corpus-wide-average
calibration doesn't hold. **A full fix (proper low-complexity masking, à la
SEG/DUST, with a re-derived null for masked regions) was judged out of
scope for this pass** - real added engineering on top of an already-large
phase, better scoped as its own follow-up than rushed here.

**What was actually shipped instead, consistent with Phase 3A's own
precedent of surfacing an unresolved caveat on the data rather than hiding
it:** `extract_note_motifs.py` computes each song's interval entropy and
writes `a_entropy`/`b_entropy`/`low_complexity_flag` (true if either song's
entropy is below the corpus's own 25th percentile, 4.957 bits) on every
extracted row, so the caveat travels with the data. **24 of the 30 pairs
that cleared `corpus_z >= 2.5` are flagged.** A human reviewing the
shortlist should treat a flagged pair - especially one where `aligned_len`
covers most of the shorter song - with real skepticism; the 6 unflagged
pairs (below) are the more credible leads in this shortlist.

---

## Results

**Full matrix:** `output/analysis/note_similarity_matrix.csv`, 2,775 rows
(all `C(75,2)` pairs), 0 alignment failures, gap penalty -2.0, single-pass
brute force (no prefilter, no permutation shuffles - see sections 5-6).
Runtime: 24.7 minutes.

`corpus_z` (length+entropy-regression-adjusted, row-normalized):
mean=0.006, median=-0.067, min=-2.857, max=6.613. Distribution close to
Phase 3A's own final calibration (see section 7 table above) - `corpus_z
>= 2.5` was kept as the extraction threshold (36 pairs, 1.30%), same
numeric cutoff Phase 3A used, `--top-n 30` capping the deliverable.

**Motif extraction:** `output/analysis/note_motif_variations.csv`, 30
pairs (the top 30 of 36 that cleared `corpus_z >= 2.5`), 18,153
aligned-position rows total. **24 of the 30 flagged `low_complexity_flag`**
- read those specifically as leads needing extra scrutiny, not confirmed
matches (see section 7). The full top-30 ranking, low-complexity flag, and
aligned-region size are all in the design doc's section 7 table / directly
inspectable in the CSV.

**The 6 unflagged pairs - the more credible leads in this shortlist:**

| pair | corpus_z | lengths | aligned positions (% of shorter) | exact / substitution / gap |
|---|---|---|---|---|
| KARMA × Riddle (feat. Khundi Panda) | 4.736 | 3,198 × 1,422 | 316 (22.2%) | 42 / 235 / 39 |
| 1nonly - Meaningless Love × MEOVV - DDI RO RI | 3.303 | 1,815 × 1,707 | 160 (9.4%) | 36 / 101 / 23 |
| Broken × [LIVE CLIP] Luina - Agwi, Suyo | 2.926 | 712 × 817 | 162 (22.8%) | 43 / 101 / 18 |
| Broken × Starjunk 95 - State Your Name | 2.870 | 712 × 422 | 90 (21.3%) | 19 / 67 / 4 |
| Kanye West - Stronger × Starjunk 95 - State Your Name | 2.790 | 1,943 × 422 | 91 (21.6%) | 18 / 68 / 5 |
| Broken × Kanye West - Stronger | 2.784 | 712 × 1,943 | 173 (24.3%) | 43 / 106 / 24 |

`KARMA × Riddle` is the standout: highest z among the unflagged pairs, a
genuinely partial/localized alignment (22% of the shorter song, not
near-full-song), and a healthy exact-match count (42) inside a longer
substitution-heavy region - the kind of result worth an actual human
listen. The other five pairs form a genuinely interesting **cluster**:
`Broken`, `Starjunk 95 - State Your Name`, and `Kanye West - Stronger` are
each pairwise-related to each other (and `Broken` separately to `Luina -
Agwi, Suyo`), all with consistently localized spans (~9-24% of the shorter
song) and a real proportion of exact matches - worth a human's attention as
a group, same as Phase 3A flagged its own same-artist clusters for
follow-up rather than either dismissing or over-trusting a single pair in
isolation.

**Qualitative caveat carried over from Phase 3A's own conclusion, doubly
true here given section 7's findings:** treat every pair in
`note_motif_variations.csv` as a lead for a human to listen to and judge,
not a confirmed borrowing - more so than Phase 3A, since this phase's
significance measure has a known, only partially-corrected blind spot for
low-complexity songs. The `low_complexity_flag`, `a_entropy`/`b_entropy`,
and `aligned_len` relative to each song's own length are the specific
things to check before trusting a flagged row's score.

**Files delivered:**
- `output/analysis/note_sequences.csv` - 113,938 rows (all notes, all 75 songs)
- `output/analysis/note_similarity_matrix.csv` - 2,775 rows (all `C(75,2)` pairs)
- `output/analysis/note_motif_variations.csv` - 18,153 rows across 30 pairs
- `lib/note_similarity.py` - reusable interval-substitution scoring module
- `scripts/analysis/build_note_sequences.py`, `validate_note_alignment_sample.py`,
  `run_note_alignment.py`, `compute_note_corpus_z.py`, `extract_note_motifs.py`
- `output/analysis/note_kmer_candidates.csv` - **not produced**, see section 5
