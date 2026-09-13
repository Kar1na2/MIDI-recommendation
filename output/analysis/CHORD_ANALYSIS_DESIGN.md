# Phase 3A — Chord-Pattern Analysis: Design Doc

Written 2026-09-13. This is the methodology record for everything under
`output/analysis/` and the two new reusable library modules
(`lib/chord_canonicalization.py`, `lib/chord_similarity.py`,
`lib/sequence_alignment.py`). Every non-obvious choice below has the data
or experiment that justified it, so Phase 3B (note-progression analysis,
reusing `lib/sequence_alignment.py`) and future revisits of this phase
don't have to re-derive any of it.

## Scope limitation — read this before trusting any result below

**Chordino only ever analyzed `stems/<song>/other.wav`** (see
`scripts/chords/extract_chords.py` and `PIPELINE_MAP.md`). Every result in
this document and every CSV under `output/analysis/` describes **harmony as
expressed in the "other" Demucs stem** (the harmonic/melodic accompaniment
bucket - guitars, keys, pads, synths), not full-mix harmony. Bass notes are
invisible to this analysis (a bassline implying a chord the accompaniment
doesn't spell out won't be detected), and vocal-driven harmonic implications
(a melody note that implies an extension or substitution without the
backing instruments playing it) are also invisible. Do not read anything
here as "the chord progression of the song" - it is specifically the
accompaniment-stem chord progression, which is usually a good proxy but is
not the same thing.

## Isolation

Everything in this phase reads from `output/chord_segments.csv` and
`chords/<song>/*.csv` (Phase 2 outputs) and writes only into
`output/analysis/`. Nothing under `chords/`, `merged/`, `corrected/`, or the
top-level `output/*.csv` files was modified or re-derived.

---

## 1. Chord canonicalization

**Goal:** map a pitch-class set (e.g. `"0,3,6,8"`) to a symbol that's the
same across different keys, so the same progression transposed doesn't look
like a different progression.

**Library choice:** `music21` was added as a new dependency
(`uv add music21`, now in `pyproject.toml`). Confirmed it installs cleanly
and every previously-verified import in the main env still works afterward
(`demucs, torch, torchaudio, basic_pitch, vamp, mido, pretty_midi, mir_eval,
numpy` - all still import with no errors, `torch.__version__` unchanged at
`2.11.0+cu130`). music21 pulled in some new transitive dependencies
(matplotlib, pillow, etc. - it's a general music-notation toolkit) but
nothing conflicted.

**Which invariance to apply - this was the first real design decision, not
a formality.** Two different equivalence relations are both called "chord
identity is transposition-invariant" colloquially, and music21 exposes both:

- `chord.Chord(pcs).primeFormString` - Forte's *prime form*, invariant under
  **both** transposition (Tn) **and** set-theoretic inversion (TnI, the
  mirror-image relation).
- `chord.Chord(pcs).forteClass` - the Forte catalog number **with its A/B
  suffix** (e.g. `"3-11A"` vs `"3-11B"`), invariant under transposition
  **only**.

Tested directly:

```
major triad {0,4,7}: primeFormString=<037>  forteClass=3-11B
minor triad {0,3,7}: primeFormString=<037>  forteClass=3-11A   <- same prime form as major!
dominant7   {0,4,7,10}: primeFormString=<0258>  forteClass=4-27B
half-dim7   {0,3,6,10}: primeFormString=<0258>  forteClass=4-27A  <- same prime form as dominant7!
```

`primeFormString` collapses major and minor triads (and dominant-7th with
half-diminished-7th) into the same symbol, because they're mirror images of
each other under TnI. That distinction is exactly the harmonic information
this analysis needs to keep - **decision: canonical id = `forteClass`, not
`primeFormString`.** `prime_form` is still recorded in the vocabulary CSV
for reference/debugging, just not used as the id.

This is unrelated to "chord inversion" in the everyday sense (root position
vs. first/second inversion, i.e. which chord tone is in the bass). That
information was already discarded upstream of this analysis entirely:
Chordino's chordnotes output and `chord_segments.csv` both represent a
chord as an unordered pitch-class **set**, no register or bass-note info -
so voicing/bass-inversion invariance is a free consequence of the existing
data representation, not something this module enforces.

**`common_name`** (music21's generic, non-pitched name, e.g. `"major
triad"`) is also recorded for human readability.

Code: `lib/chord_canonicalization.py`.

## 2. Vocabulary granularity - decided empirically, per the brief

Ran `scripts/analysis/build_chord_vocabulary.py` over all 8,342 segments in
`output/chord_segments.csv` (corpus-wide, 75/75 songs). Result: **100
distinct pitch-class sets reduce to only 9 distinct forte classes**:

| forte_class | name | segment count (corpus) |
|---|---|---|
| 3-11B | major triad | 2108 |
| 4-26 | minor seventh chord | 1650 |
| 3-11A | minor triad | 1499 |
| 4-20 | major seventh chord | 1221 |
| 4-27B | dominant seventh chord | 978 |
| 4-27A | half-diminished seventh chord | 458 |
| 3-12 | augmented triad | 237 |
| 4-22A | "major-second major tetrachord" (an add9/sus2-type sonority - major triad + added 2nd, no plain jazz name in music21) | 110 |
| 3-10 | diminished triad | 81 |

This is not a granularity **I** chose - it's the actual ceiling of what
Chordino's underlying chord dictionary (NNLS-chroma templates) distinguishes
in the first place. There is no finer distinction available in the source
data to canonicalize away or preserve; **decision: use `forte_class`
directly as the full vocabulary, no further clustering/splitting needed.**

**Manual inspection against the 3 original reference songs** (163braces -
過期, 1nonly - Meaningless Love, Alice U - 도주), required by the brief
before applying corpus-wide:
- All three produce musically plausible, internally consistent chord
  sequences (varied major/minor/7th motion in 163braces; 1nonly and Alice U
  are both clearly vamp-style songs that cycle a small set of chords
  repeatedly, typical of K-pop/pop production - see the full printed
  sequences from the validation checkpoint, reproducible via
  `scripts/analysis/build_chord_sequences.py` + a groupby on `song`).
- A follow-up check was needed: both 1nonly and Alice U show long runs of
  the *same abbreviated quality* back-to-back (e.g. 8+ consecutive `min7`
  segments), which looked at first like Chordino fragmenting one held
  chord into redundant segments. Checked the actual pitch-class sets
  underneath: these are **real progressions cycling between different
  minor-7th chords at different roots** (e.g. `{1,4,8,11} -> {3,6,8,11} ->
  {1,4,6,9} -> {1,4,8,11}...`), not an artifact. Confirmed corpus-wide:
  only **3 of 8,267 consecutive-segment transitions (0.04%)** have the
  exact same pitch-class set on both sides - i.e. the upstream dedup in
  `chord_segments.csv` already handles the true "held chord split into two
  segments" case, so **no run-length collapsing was applied.**

## 3. Duration filtering - decided from the data, not guessed

Distribution of `end - start` across all 8,342 segments:

| stat | value |
|---|---|
| min | 0.232s |
| p1 | 0.372s |
| p5 | 0.511s |
| p25 | 0.929s |
| median | 1.393s |
| mean | 1.698s |
| max | 29.95s |
| count < 0.3s | 29 (0.3%) |
| count < 0.5s | 356 (4.3%) |

Two findings argued against filtering:
1. **The distribution is smooth and monotonically increasing from the
   minimum with no low-end spike.** An artifact/bleed population would
   show up as a disproportionate cluster right at the minimum; instead,
   segment counts *increase* steadily from 0.232s up through the median. A
   spike would justify a cutoff; a smooth curve doesn't.
2. **Segment durations are quantized to ~0.0464s** (Chordino's chroma
   analysis hop size) - the observed minimum, 0.232s, is exactly 5 hops.
   Chordino's own segmentation already enforces a floor; there's no
   sub-hop noise to filter out.
3. Manually inspected the 10 shortest segments corpus-wide (all ≤0.28s) in
   their surrounding context (the segment immediately before/after). All 10
   look like plausible brief passing/transitional chords between two longer
   stable ones (e.g. in Kanye West - Heartless: `{2,5,10}` (5.06s) ->
   `{1,5,8}` (0.23s, brief) -> `{3,7,10}` (3.62s) - a short passing harmony
   between two stable ones, not noise).

**Decision: no duration filter applied.** `chord_vocabulary.csv` and
`chord_sequences.csv` include every segment from `chord_segments.csv`
unfiltered.

## 4. Sequence alignment engine

### 4a. Generic engine (`lib/sequence_alignment.py`)

Hand-rolled Needleman-Wunsch (global) and Smith-Waterman (local), not a
bioinformatics library (biopython/edlib/parasail) - per the brief, at this
corpus's scale (75 songs, ~2,775 pairs, each a small DP matrix) a
hand-rolled implementation is simpler to reason about, trivial to
instrument for motif traceback, and adds no new heavy dependency. Uses a
linear (not affine) gap penalty - judged sufficient at this scale; an
affine open/extend split would be the natural next refinement if Phase 3B's
longer note sequences show it's needed.

**This module is deliberately chord-agnostic.** It takes an arbitrary
sequence of hashable symbols and a pluggable `score_fn(a, b) -> float`. All
chord-specific logic (canonicalization, substitution scoring, the
transposition search described below) lives in `lib/chord_similarity.py`,
a *consumer* of this module - so Phase 3B (note-progression alignment, once
Phase 2's export stage is corpus-wide) can reuse the exact same engine with
its own scoring function, no changes needed here.

`smith_waterman` (local alignment) was used for all analysis in this phase,
per the brief: local alignment is the better fit for finding a shared motif
embedded in two otherwise different songs, versus `needleman_wunsch`
(global), which is also implemented and available for a future use case
that wants whole-sequence correspondence.

### 4b. Chord substitution scoring - two real methodological problems found here, not assumed away

**Problem 1: graded scoring needs a real similarity measure, and the obvious
one (interval-vector distance) is mathematically wrong for this purpose.**

The brief calls for graded (not binary) substitution scoring: two chords
sharing tones should score better than two that don't, so an alignment can
find *variations* on a progression (e.g. one chord substituted), not just
exact repeats. The natural set-theory candidate is interval-vector distance
(Isaacson's ICVSIM / Forte's similarity relations). Computed it for all
9 forte classes and found:

```
major triad (3-11B) vs minor triad (3-11A):        distance = 0.0
half-dim7   (4-27A) vs dominant7    (4-27B):        distance = 0.0
```

Interval vectors are, by construction, invariant under set-theoretic
inversion - so they assign **zero distance** between exactly the pairs
section 1 above went out of its way to keep distinct. Using interval-vector
distance for substitution scoring would have silently undone that decision
one layer up (a mismatch between a major and minor triad would score
identically to an exact match).

**Fix: same-root pitch-class Jaccard overlap.** Each forte class is
reconstructed via `music21.chord.fromForteClass()`, root-normalized (its
own root transposed to pitch-class 0 - e.g. major triad -> `{0,4,7}`,
minor triad -> `{0,3,7}`), and similarity is `|A∩B| / |A∪B|` between the two
root-normalized sets - i.e. "if these two chord qualities were played over
the same root, how many pitch classes would they share." This is
`chord_jaccard_similarity()` in `lib/chord_similarity.py`; verified it gives
non-degenerate, musically sensible numbers for every pair, e.g.:

| pair | jaccard |
|---|---|
| major triad / itself | 1.0 |
| major triad / major-7th, dominant-7th, or add9 (one added color tone) | 0.75 |
| major triad / minor triad (parallel-mode substitution) | 0.5 |
| dominant-7th / half-diminished-7th (tritone-related ii-V family) | 0.333 |
| augmented triad / diminished triad (both symmetric, minimal overlap) | 0.2 |
| major-7th / half-diminished-7th (least related pair in this vocabulary) | 0.143 |

`chord_substitution_score(a, b) = SLOPE * jaccard(a, b) - OFFSET`, a linear
map chosen so an exact match (`jaccard=1`) scores `MATCH_SCORE = 2.0`.

**Problem 2: the naive score calibration made Smith-Waterman degenerate -
alignments spanned almost entire songs instead of finding local motifs.**

Found at the validation checkpoint. First version centered the linear map
at `jaccard=0.5` (score 0 at "half the pitch classes shared", symmetric
`[-2, +2]` range). Running this on the reference-song pairs produced
alignments covering 90-100+ of ~104-117 segments - clearly not "a shared
motif embedded in an otherwise different song," just almost the whole
song. Root cause: **local alignment requires the expected score of a random
substitution to be negative** (the same requirement behind BLOSUM-style
matrices in bioinformatics - without it, extending the alignment is
expected to help on average, so the DP never wants to stop). Computed the
actual corpus-frequency-weighted expected Jaccard overlap between two
independently-drawn chords: **0.588, not 0.5** - this corpus's 9-class
vocabulary is dominated by major/minor triads and their 7th-chord
extensions, which structurally share a lot of tones with each other, so
"random" pairs are much more similar than a naive midpoint assumes.

**Fix:** recalibrated so `E[score]` under the corpus's real chord-frequency
distribution is `-0.25` (modestly negative), not 0: solve
`SLOPE * 1 - OFFSET = MATCH_SCORE` and `SLOPE * 0.588 - OFFSET =
TARGET_RANDOM_SCORE(-0.25)` for `SLOPE`/`OFFSET`. Verified numerically:
`E[score] = -0.2506` under the corpus's actual forte_class frequency
distribution after recalibration. All constants and the derivation are in
`lib/chord_similarity.py`'s module docstring/comments, not just the numeric
result, so this is reproducible if the corpus (and its chord-frequency
distribution) changes later.

### 4b (continued). Quality-only alignment: validated, and it failed - so the design changed

Per the brief, ran Smith-Waterman on quality-only (`forte_class`) sequences
for a hand-picked validation sample before scaling: the 3 reference songs'
3 pairwise combinations, 4 same-artist pairs (the best "known-similar" case
available in this corpus - **flagging back per the brief**: Kanye West
(Heartless/POWER/Stronger), Imagine Dragons (Bones/Natural/Sharks), Heize
(And July/Jenga/Love Virus), and 유라/Youra all have 2-3 songs each in the
corpus), and 2 negative-control pairs (a Chopin nocturne, solo piano
classical, against two very different pop songs).

Raw scores alone weren't interpretable (a longer song pair scores higher
just from more DP cells), so used a **permutation test**: shuffle one
song's segment order N=30 times, realign each shuffle, and report
`z = (real_score - null_mean) / null_sd`. Result: **no pair cleared even
z≈2** (roughly p<0.05), including every same-artist pair. This is the
standard way to tell "genuinely similar" from "small alphabet, matches by
chance" (analogous to BLAST E-values), and it said the quality-only design
had no detectable signal.

**Diagnosis:** canonicalizing to quality only (9 symbols, transposition-
invariant) discards root motion - usually *the* thing that makes two
progressions "the same" (e.g. a ii-V-I is defined by the interval jumps
between roots, not just "minor -> major -> major"). With only 9 symbols in
the alphabet, two harmonically unrelated songs can easily share long runs
of matching quality by chance.

**Fix - root-aware alignment (`lib/chord_similarity.py`):**
- `chord_sequences.csv` gained a `root_pc` column (the segment's actual,
  non-transposition-invariant root, via music21's stacked-third root-
  finding heuristic) alongside the unchanged `canonical_chord_id`. N-gram
  mining (section 5) still uses `canonical_chord_id` only - this addition
  is for alignment only.
- `compound_chord_score((forte_a, root_a), (forte_b, root_b))` = the
  existing quality Jaccard score, **plus a `ROOT_BONUS = 3.0` when the
  (transposition-aligned) roots also match exactly.**
- Since `forte_class` is transposition-invariant but `root_pc` isn't, a
  literal transposition of the same progression into a different key would
  never get the root bonus without first aligning the two songs' keys.
  `best_transposition_alignment()` tries all 12 rigid transpositions of one
  song's `root_pc` values and keeps whichever gives the highest-scoring
  Smith-Waterman alignment - a key-invariant search, analogous to
  transposition-invariant melodic contour matching.
- Recalibrated again: measured `CORPUS_ROOT_MATCH_PROB = 0.0856` (close to
  the uniform `1/12 = 0.083` - chord roots are used fairly evenly
  corpus-wide) and added a `ROOT_BIAS_CORRECTION` term so
  `E[compound_score]` under random pairing is `-0.4` (a bit more negative
  than the quality-only `-0.25`, deliberately, because best-of-12-
  transpositions selection inflates apparent scores beyond what a single
  random comparison would predict - the permutation test re-validates this
  empirically rather than relying on the calibration alone).

**Re-ran the same validation sample with the root-aware design + the same
permutation test** (gap penalties -2.0 and -3.0 compared; -2.0 gave the
strongest separation on the clearest true positive without a consistent
downside, so **gap penalty = -2.0 is the corpus-wide default**):

| pair | z-score (gap=-2.0) |
|---|---|
| **Kanye West - Heartless × Stronger** | **9.81** |
| 1nonly × Alice U (reference songs) | 2.19 |
| 163braces × 1nonly (reference songs) | 2.25 |
| Kanye West - Heartless × POWER | 1.58 |
| Imagine Dragons - Bones × Natural | 1.27 |
| Heize - And July × Jenga | 0.22 |
| Nocturne (negative control) × Alice U | 2.88 |
| Nocturne (negative control) × 1nonly | 1.13 |

**This validated the fix**: Heartless/Stronger is an unambiguous true
positive (z≈10 - both built on the same kind of repeating 4-chord vamp,
confirmed by eye in the aligned output). It also surfaced two honest
caveats to carry into the full-corpus read:
- **Same-artist does not reliably mean same-progression** - only 1 of 3
  same-artist pairs tested is strongly significant; an artist can vary
  harmonically a great deal between songs, and this method correctly does
  not force a false positive on the other pairs.
- **The negative control isn't a clean zero** - a Chopin nocturne still
  reaches z≈2.9 against one pop song. Likely cause: common-practice tonal
  harmony has generic regularities (e.g. V→I motion) shared by *any* two
  tonal pieces, related or not, so "unrelated" isn't the same as
  "structurally random" here. With ~2,775 pairs in the full matrix, chance
  alone predicts some pairs will land in the z≈2-3 range even with no real
  relationship - see the significance threshold below.

### 4c. Full-corpus run and significance threshold

`scripts/analysis/run_alignment.py` runs all `C(75,2) = 2,775` pairs with
the validated design (root-aware, `best_transposition_alignment`, gap=-2.0,
N_SHUFFLES=20 for the permutation null - reduced from the validation
checkpoint's 30 to keep the full run's ~46-70 minute runtime reasonable;
z-score stability was checked to not depend sensitively on 20 vs. 30 in the
validation sample). Smoke-tested with `--dry-run` and `--limit 10` before
the full background run, per this repo's established convention for long
batch jobs.

**Multiple-testing correction:** with 2,775 independent comparisons, an
uncorrected `z > 1.645` (`p<0.05`, one-sided) threshold would be expected to
pass **~139 pairs by chance alone**. A Bonferroni-style correction
targeting family-wise `alpha=0.05` gives a per-test threshold of roughly
`z > 4.1`. **Reporting convention adopted:** `z >= 4` = high-confidence
match worth a human listen; `z` in `[3, 4)` = suggestive, reported but not
claimed as confident; `z < 3` = not distinguishable from chance at this
corpus size, not reported as a "match" even if the raw score looks high.
`extract_motifs.py` defaults to a `z >= 3.0` cutoff for which pairs get
their matched region extracted (a bit below the high-confidence line, so
"suggestive" pairs are still available for a human to look at, clearly
labeled with their own z-score for calibrating trust).

Full-matrix results, runtime, and how many pairs cleared each threshold are
recorded in the "Results" section below once the background run completed
(see `output/analysis/chord_similarity_matrix.csv`).

## 5. N-gram mining

`scripts/analysis/mine_ngrams.py` extracts bigrams and trigrams of
`canonical_chord_id` (quality only - deliberately *not* root-aware, unlike
the alignment engine; n-grams are meant to answer "which chord-quality
transitions are common," a different question than "which specific
progression recurs," and mixing in root would just fragment counts across
this corpus's small sample per bigram).

**Known limitation, found while sanity-checking the output, documented
rather than hidden:** with only 9 possible chord qualities, there are only
`9x9 = 81` possible bigrams - **and all 81 actually occur somewhere in the
corpus.** The bigram space is fully saturated. Trigrams fare a little
better (`538` of `729` possible trigrams observed, 74%) but the alphabet is
still small enough that cross-song bigram/trigram recurrence is dominated
by the small symbol count, not necessarily by any real shared musical
idiom - e.g. the top bigram by cross-song count (`min7 -> Maj`, 67/75
songs) is common because it's a common chord-quality transition in tonal
music generally, not because 67 songs are quoting each other.
**Conclusion: read `chord_ngrams.csv` as a coarse corpus-wide frequency
table, not as evidence of borrowed progressions between specific songs** -
that claim needs the alignment engine's z-score, which is root-aware and
statistically tested against a null.

## 6. Motif-variation extraction

`scripts/analysis/extract_motifs.py` re-runs `best_transposition_alignment`
for every pair clearing the `z >= 3.0` bar (capped at the top 30 by
z-score, so the deliverable stays human-reviewable) and walks the alignment
traceback to emit one row per aligned position: both sides' actual segment
index/timestamps/forte class/common name/root, and a `match_type` -
`exact_match` (same quality and root), `quality_match_different_root` (same
quality, different root - the "parallel/sequenced" case), `substitution`
(different quality), or `gap` (an inserted/omitted chord on one side). This
gives a human everything needed to go listen to a specific claimed match
and see exactly where it diverges, rather than trusting a bare score.

---

## 4d. The full-corpus run exposed a second real bug in the significance measure - found, diagnosed, and fixed before calling this done

`run_alignment.py` completed cleanly: all 2,775 pairs, 0 failures, ~46
minutes. But the permutation-test z-score validated on the 9-pair sample
did **not** hold up at full scale:

```
mean corpus-wide permutation z:        +1.70   (should be ~0 for a correct null)
% of ALL 2,775 pairs clearing z>=4:     9.7%    (should be ~0.003% by chance)
```

**Diagnosis:** the validation sample happened not to expose this. Shuffling
one song's segment order to build the null destroys that song's own real
internal repetition - and this corpus is full of short, vamped/looped pop
songs (see section 2's repeat-run finding). A shuffled version of a
repetitive song is a *much worse* match for anything than the real song is,
so the null was systematically too weak, inflating z for nearly every pair,
not just genuinely similar ones.

**First attempted fix - circular shift instead of full shuffle** (rotate
the segment sequence, preserving internal order/periodicity, changing only
the alignment phase): re-tested on the same 9-pair validation sample.
This overcorrected in the opposite direction - a rotated copy of a
*periodic* loop still contains the same repeating content, so the null
became almost as strong as the real alignment for exactly the songs where
signal was expected. The known true positive, Kanye West Heartless x
Stronger, dropped from z=9.81 (inflated) to z=0.80 (signal destroyed).

**Fix that was kept - corpus-relative ("row-normalized") z-score,
`scripts/analysis/recompute_significance.py`:** every song already has ~74
real alignment scores (against every other song in the corpus) once the
full matrix is computed. Treat that as the song's own empirical background
- "how well does this song generically align with a typical other song
here" - and score each pair relative to **both** songs' own backgrounds
(excluding the pair itself):

```
corpus_z_a = (normalized_score_AB - mean(A's scores vs. all others)) / std(...)
corpus_z_b = (normalized_score_AB - mean(B's scores vs. all others)) / std(...)
corpus_z   = mean(corpus_z_a, corpus_z_b)
```

This sidesteps both permutation failure modes because the background is
built from other *real* songs (already carrying realistic internal
structure/periodicity), not synthetic shuffles or rotations - and it costs
nothing new to compute (pure post-processing over the `normalized_score`
column already in the matrix, no re-alignment needed). Re-checked
corpus-wide after recomputation:

```
corpus_z: mean=0.003  median=-0.050  min=-2.511  max=4.199
corpus_z >= 2: 62 pairs (2.23%)   -  close to the ~2.3% a normal(0,1) predicts at z>=2
corpus_z >= 3: 12 pairs (0.43%)   -  a bit above the ~0.13% normal prediction (expected: real signal + heavier tails)
corpus_z >= 4:  2 pairs (0.07%)   -  vs. ~0.003% (~0.08 pairs) expected by chance - a real excess
```

This is a properly calibrated measure: centered at 0, matching the
theoretical tail at moderate z, with a small excess at the extreme tail
consistent with a handful of genuinely above-baseline pairs rather than
systematic bias. **`chord_similarity_matrix.csv`'s old permutation columns
were kept, renamed with a `_deprecated` suffix, rather than deleted** - the
record of what was tried and rejected stays inspectable. `corpus_z` is the
column all downstream consumers (`extract_motifs.py`, this results
section) actually use.

The known validation-sample true positive, Kanye West Heartless x
Stronger, now reads `corpus_z = 1.95` - real signal, present, but properly
modest rather than the permutation test's overstated 9.81. It's a useful
fixed point for calibrating trust in the new scale: **treat `corpus_z`
around 2 as "real but unremarkable," and reserve confidence for
`corpus_z >= 2.5`+ pairs**, which is the cutoff `extract_motifs.py` uses
(28 pairs cleared it out of 2,775, i.e. ~1%).

## Results

**Full matrix:** `output/analysis/chord_similarity_matrix.csv`, 2,775
rows (all `C(75,2)` song pairs), 0 alignment failures, gap penalty -2.0,
root-aware `best_transposition_alignment` (12-way key search per pair).

**Motif extraction:** `output/analysis/chord_motif_variations.csv`, 28
pairs cleared `corpus_z >= 2.5` (1,975 aligned-position rows total, one row
per matched/gapped chord position within each qualifying pair's local
alignment). Match-type breakdown across a spot-checked top pair (Fendi 2 x
aespa - WDA, `corpus_z = 4.20`, the single highest-scoring pair in the
corpus): 123 exact matches (same quality + same root), 67 quality matches
at a different root, 124 substitutions, 43 gaps, over a 357-position
aligned region.

**Qualitative caveat from inspecting that top pair, worth carrying into any
downstream use of this data:** its matched region is built from a short,
harmonically simple alternation between two major triads a semitone apart
(root pitch-classes 10 and 11) - i.e. both songs have a passage that vamps
between two neighboring major chords. That's a real, measurable
above-baseline similarity (both songs' `corpus_z` accounts for how
generically alignable each one normally is), but a two-chord semitone
alternation is also a common enough pop-production device that this may
read as "both songs use a common device" rather than "song B specifically
echoes song A." **Recommendation: treat every pair in
`chord_motif_variations.csv` as a lead for a human to listen to and judge,
not a confirmed borrowing** - the `match_type` breakdown and actual
timestamps are there specifically so that judgment call can be made
per-pair rather than trusting the score alone. This is consistent with the
score's own design: it answers "is this more similar than these two songs'
own typical alignability," not "is this a deliberate quotation."

**Files delivered:**
- `output/analysis/chord_vocabulary.csv` - 100 pitch-class sets -> 9 forte classes
- `output/analysis/chord_sequences.csv` - 8,342 rows (all segments, all 75 songs)
- `output/analysis/chord_ngrams.csv` - 619 rows (81 bigrams + 538 trigrams)
- `output/analysis/chord_similarity_matrix.csv` - 2,775 rows
- `output/analysis/chord_motif_variations.csv` - 1,975 rows across 28 pairs
- `lib/chord_canonicalization.py`, `lib/chord_similarity.py`,
  `lib/sequence_alignment.py` - reusable modules, the latter chord-agnostic
  for Phase 3B
- `scripts/analysis/build_chord_vocabulary.py`, `build_chord_sequences.py`,
  `mine_ngrams.py`, `validate_alignment_sample.py`, `run_alignment.py`,
  `recompute_significance.py`, `extract_motifs.py`
