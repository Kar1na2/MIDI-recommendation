#!/usr/bin/env python3
"""
Chord-specific substitution scoring for the generic alignment engine
(lib/sequence_alignment.py). This is the "pluggable score_fn" for Phase 3A;
it is intentionally kept separate from sequence_alignment.py so that module
stays alphabet-agnostic for Phase 3B reuse.

See output/analysis/CHORD_ANALYSIS_DESIGN.md section 4 for the full
rationale and the numbers behind the constants below. Short version:

- Two canonical chords (forte_class strings, e.g. "3-11B") are compared by
  the pitch-class overlap they would have IF sounded over the same root -
  each forte class is reconstructed via music21.chord.fromForteClass(),
  then transposed so its own root is 0 (e.g. major triad -> {0,4,7}, minor
  triad -> {0,3,7}), and similarity is the Jaccard index of those two sets.
- Interval-vector distance was tried first and rejected: it is
  mathematically invariant under set-theoretic inversion, which makes it
  assign *zero distance* between exactly the pairs this analysis most needs
  to keep distinct (major vs. minor triad; dominant-7th vs.
  half-diminished-7th) - see the design doc for the numbers that showed
  this. Same-root pitch-class Jaccard overlap doesn't have that degeneracy.
- The result is a graded score, not binary match/mismatch: e.g. major
  triad vs. its own major-7th/dominant-7th/add9 extensions score highly
  (0.75 overlap - a single added color tone), while chords with little in
  common score negatively.

Calibration (why the constants below aren't a naive [0,1] -> [-2,2] map):
a first version linearly mapped Jaccard 0->1 to score -2->+2 (crossing zero
at 0.5 overlap). That produced degenerate Smith-Waterman alignments that
covered almost an entire song instead of a localized motif. Cause, found
during the Phase 3A validation checkpoint: this corpus's actual 9-class
chord vocabulary is dominated by major/minor triads and their 7th-chord
extensions, which structurally share a lot of pitch classes with each
other - the corpus-frequency-weighted EXPECTED Jaccard overlap between two
*independently drawn* (i.e. musically unrelated) chords is ~0.588, not
0.5. A local-alignment score function needs the expected score of a random
pairing to be negative (the same requirement behind e.g. BLOSUM matrices in
bioinformatics) or the DP has no incentive to stop extending. CORPUS_MEAN_JACCARD
below is that measured constant (from output/analysis/chord_sequences.csv,
computed once - see scripts/analysis/validate_alignment_sample.py's
calibration check - and then fixed here rather than recomputed at import
time, so this module stays import-safe and reusable without a live corpus
file present).
"""
from __future__ import annotations

from functools import lru_cache

from music21 import chord as m21chord

from lib.sequence_alignment import smith_waterman, needleman_wunsch, AlignmentResult

MATCH_SCORE = 2.0            # score for an exact forte_class match (jaccard=1)
CORPUS_MEAN_JACCARD = 0.588  # E[jaccard] under corpus-weighted random pairing (measured once, see above)
TARGET_RANDOM_SCORE = -0.25  # desired E[score] under random pairing - modestly negative

# Solve the linear map score(j) = SLOPE*j - OFFSET for the two constraints:
#   score(1.0)                = MATCH_SCORE          (an exact match scores +2)
#   score(CORPUS_MEAN_JACCARD) = TARGET_RANDOM_SCORE  (random pairing scores slightly negative)
SLOPE = (MATCH_SCORE - TARGET_RANDOM_SCORE) / (1 - CORPUS_MEAN_JACCARD)
OFFSET = SLOPE - MATCH_SCORE
MISMATCH_FLOOR = -OFFSET  # score at jaccard=0 (hypothetical; not reached by this corpus's 9 classes)


@lru_cache(maxsize=None)
def _root_normalized_pitch_classes(forte_class: str) -> frozenset:
    c = m21chord.fromForteClass(forte_class)
    root_pc = c.root().pitchClass
    return frozenset((pc - root_pc) % 12 for pc in c.pitchClasses)


@lru_cache(maxsize=None)
def chord_jaccard_similarity(forte_class_a: str, forte_class_b: str) -> float:
    """Same-root pitch-class overlap in [0, 1]; 1.0 iff forte_class_a == forte_class_b
    (every forte class trivially has full overlap with itself)."""
    a = _root_normalized_pitch_classes(forte_class_a)
    b = _root_normalized_pitch_classes(forte_class_b)
    return len(a & b) / len(a | b)


def chord_substitution_score(forte_class_a: str, forte_class_b: str) -> float:
    """score_fn for lib.sequence_alignment - pass this (or a functools.partial
    over different MATCH_SCORE/MISMATCH_FLOOR constants) as the score_fn
    argument to needleman_wunsch()/smith_waterman()."""
    sim = chord_jaccard_similarity(forte_class_a, forte_class_b)
    return SLOPE * sim - OFFSET


# --- Root-aware scoring (added after the Phase 3A alignment validation ---
# --- checkpoint - see CHORD_ANALYSIS_DESIGN.md section 4b) --------------
#
# chord_substitution_score() above is quality-only (transposition-invariant
# by construction - it never sees a root). A permutation-test validation
# check (shuffle one song's chord sequence N times, compare the real
# Smith-Waterman score to that null distribution as a z-score) showed
# quality-only alignment finds NO significant similarity above chance, even
# for the best "known-similar" pairs available in this corpus (same-artist
# song pairs). Root motion - which actual chord follows which, not just
# which quality follows which - is most of what makes two progressions "the
# same" (e.g. ii-V-I is defined by the interval jumps between roots), and
# quality-only canonicalization throws that away entirely.
#
# Fix: track each segment's actual (forte_class, root_pc) and score a pair
# of segments as quality-similarity plus a bonus when the roots ALSO match
# - but only after aligning the two songs' keys. Since forte_class is
# already transposition-invariant, "the same song transposed a fifth up"
# would otherwise never get the root bonus; best_transposition_alignment()
# below tries all 12 rigid transpositions of one song's root_pc values and
# keeps whichever gives the highest-scoring alignment (analogous to
# key-invariant melodic contour matching). This is a chord-domain concern
# and deliberately lives here, not in sequence_alignment.py.
ROOT_BONUS = 3.0             # additive bonus when root_pc matches exactly (after transposition)
CORPUS_ROOT_MATCH_PROB = 0.0856  # P(two independently drawn segments share a root_pc), measured
                                  # once from output/analysis/chord_sequences.csv (close to the
                                  # uniform 1/12 = 0.0833 - root usage is fairly even corpus-wide)
_ROOT_BIAS_MARGIN = 0.15
# Correction subtracted from every compound score so E[compound score] under
# random (quality, root) pairing stays clearly negative (same local-alignment
# requirement as chord_substitution_score's own calibration above), even
# after adding a root-match bonus that is positive in expectation on its own:
#   E[compound] = E[chord_substitution_score] + ROOT_BONUS * CORPUS_ROOT_MATCH_PROB - ROOT_BIAS_CORRECTION
#               = -0.25 + 3.0*0.0856 - ROOT_BIAS_CORRECTION  =  -0.4  (a bit more negative than
#                 the quality-only -0.25, extra margin because best-of-12-transpositions selection
#                 inflates apparent scores - the permutation test still re-validates this empirically).
ROOT_BIAS_CORRECTION = ROOT_BONUS * CORPUS_ROOT_MATCH_PROB + _ROOT_BIAS_MARGIN


def compound_chord_score(sym_a: tuple[str, int], sym_b: tuple[str, int]) -> float:
    """score_fn over (forte_class, root_pc) tuples - use with
    best_transposition_alignment(), not directly with a fixed transposition,
    since root_pc is absolute/not transposition-invariant on its own."""
    forte_a, root_a = sym_a
    forte_b, root_b = sym_b
    score = chord_substitution_score(forte_a, forte_b) - ROOT_BIAS_CORRECTION
    if root_a == root_b:
        score += ROOT_BONUS
    return score


def best_transposition_alignment(seq_a: list[tuple[str, int]], seq_b: list[tuple[str, int]],
                                  gap_penalty: float, method="smith_waterman") -> tuple[int, AlignmentResult]:
    """Try all 12 rigid transpositions of seq_b's root_pc against seq_a
    (seq_a is left fixed) and return (best_transposition, best_result).
    This is the "key-invariant" alignment entry point chord analysis should
    use - it internally calls the generic engine once per transposition."""
    align_fn = smith_waterman if method == "smith_waterman" else needleman_wunsch
    best_t, best_result = None, None
    for t in range(12):
        shifted_b = [(forte, (root + t) % 12) for forte, root in seq_b]
        result = align_fn(seq_a, shifted_b, compound_chord_score, gap_penalty)
        if best_result is None or result.score > best_result.score:
            best_t, best_result = t, result
    return best_t, best_result
