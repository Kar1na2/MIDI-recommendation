#!/usr/bin/env python3
"""
Note-interval substitution scoring for the generic alignment engine
(lib/sequence_alignment.py). This is Phase 3B's "pluggable score_fn" - the
note-analysis analog of lib/chord_similarity.py. See
output/analysis/NOTE_ANALYSIS_DESIGN.md section 2 for the full rationale;
short version below.

Alphabet: signed semitone intervals between consecutive notes
(pitch[i] - pitch[i-1]), corpus-measured range [-62, +67], 117 distinct
values. This alphabet is transposition-invariant by construction (unlike
Phase 3A's chord root_pc), so - unlike chord_similarity.py - there is no
transposition search needed here: alignment is run directly on interval
sequences.

Substitution score: a triangular distance decay,
    raw_similarity(a, b) = max(0, 1 - |a - b| / WINDOW)
i.e. an exact match scores 1.0, and similarity falls off linearly to 0 at
WINDOW semitones apart (0 beyond that - a full mismatch, not partial
credit). WINDOW=6 (a tritone) was chosen as a musically meaningful "unit of
dissimilarity" that still gives graded partial credit for the brief's
"off-by-one-or-two-semitones" case (sim=0.833 / 0.667 respectively) while
treating anything a tritone or more away as unrelated. This directly
mirrors the brief's ask ("exact interval match scores highest, off-by-
one-or-two-semitones scores partial credit, larger mismatches penalized")
without inventing a new shape - alternatives (e.g. exponential decay) were
not found necessary; WINDOW's sensitivity was checked at the validation
checkpoint (see design doc) rather than assumed.

Calibration - same discipline as chord_similarity.py, but a DIFFERENT real
bug was found here, worth recording the same way Phase 3A's two bugs were.

Measured the corpus-frequency-weighted expected similarity between two
INDEPENDENTLY drawn intervals (from output/analysis/note_sequences.csv's
actual interval distribution): CORPUS_MEAN_SIMILARITY = 0.126 at WINDOW=6 -
far below the 0.5 naive-midpoint Phase 3A's first (wrong) chord attempt
used, and well below Phase 3A's own recalibrated random-chord baseline of
0.588. That looked like it meant this alphabet was already safe (E[score]
under random pairing negative even with the same TARGET_RANDOM_SCORE=-0.25
Phase 3A used) - but it was NOT sufficient in practice.

**Bug found empirically (measure-first, per the brief's own guardrail),
diagnosed before touching the full corpus:** ran Smith-Waterman on the two
longest songs in the corpus (4,683 and 3,198 intervals - an arbitrary,
presumably-UNRELATED pair, used purely as a stress test) with
TARGET_RANDOM_SCORE=-0.25 and got a span covering 70-100% of BOTH songs -
the same "degenerate, not local" symptom Phase 3A's Problem 2 hit, despite
E[score]<0 under iid random pairing being satisfied. Root cause, confirmed
by sweeping WINDOW/TARGET_RANDOM_SCORE/gap_penalty and inspecting the
resulting span fraction and exact-match fraction directly (not just
trusting the calibration formula): **E[score]<0 under literal iid random
pairing is necessary but not sufficient.** Smith-Waterman's optimal path
does not have to sample pairs iid - it can choose favorable diagonal moves
and skip unfavorable ones via gaps, so as long as a large-enough fraction
of *possible* pairs score positive (here: any two intervals within ~4-5
semitones of each other, since WINDOW=6 and OFFSET=0.57 meant similarity
above ~0.22 was already net-positive), the DP keeps finding a next
positive step almost anywhere in two long, harmonically-ordinary songs and
never wants to stop extending. A steeper negative target (fewer pairs net
positive) plus a larger gap penalty (extension itself costs more) both
push against this; empirically, TARGET_RANDOM_SCORE=-0.5 combined with
gap_penalty=-2.0 was the first combination that produced a properly local
alignment on this same stress-test pair (span shrank to ~3-4% of each
song, aligned_len 143, vs. spanning ~100% of one song at the -0.25/-1.0
starting point) - confirmed further at the formal validation checkpoint
below on real reference/same-artist/negative-control pairs, the same way
Phase 3A validated its own fix.

**Decision: TARGET_RANDOM_SCORE = -0.5** (steeper than Phase 3A's -0.25 -
the two phases' random-baseline targets are NOT meant to match; each was
tuned against its own alphabet's actual DP behavior, not against each
other).
"""
from __future__ import annotations

from functools import lru_cache

MATCH_SCORE = 2.0             # score for an exact interval match (sim=1)
WINDOW = 6                    # semitones; sim decays to 0 at this distance (a tritone)
CORPUS_MEAN_SIMILARITY = 0.126  # E[sim] under corpus-weighted random interval pairing, WINDOW=6 (measured once - see design doc)
TARGET_RANDOM_SCORE = -0.5    # desired E[score] under random pairing - see the degeneracy bug above for why this isn't -0.25

# Solve score(sim) = SLOPE*sim - OFFSET for:
#   score(1.0)                     = MATCH_SCORE
#   score(CORPUS_MEAN_SIMILARITY)  = TARGET_RANDOM_SCORE
SLOPE = (MATCH_SCORE - TARGET_RANDOM_SCORE) / (1 - CORPUS_MEAN_SIMILARITY)
OFFSET = SLOPE - MATCH_SCORE
MISMATCH_FLOOR = -OFFSET  # score at sim=0 (any pair >= WINDOW semitones apart)


@lru_cache(maxsize=None)
def interval_similarity(a: int, b: int) -> float:
    """Triangular-decay similarity in [0, 1]; 1.0 iff a == b, 0.0 once
    |a - b| >= WINDOW."""
    d = abs(a - b)
    if d >= WINDOW:
        return 0.0
    return 1 - d / WINDOW


@lru_cache(maxsize=None)
def interval_substitution_score(a: int, b: int) -> float:
    """score_fn for lib.sequence_alignment - pass this directly to
    needleman_wunsch()/smith_waterman() on interval sequences. No
    transposition search is needed (unlike chord_similarity.py) because
    signed semitone intervals are already transposition-invariant."""
    sim = interval_similarity(a, b)
    return SLOPE * sim - OFFSET
