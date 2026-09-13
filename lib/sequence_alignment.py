#!/usr/bin/env python3
"""
Generic pairwise sequence alignment: Needleman-Wunsch (global) and
Smith-Waterman (local), hand-rolled rather than pulled from a
bioinformatics library (biopython/edlib/parasail).

Why hand-rolled, and why this module is chord-agnostic (Phase 3A design
decision - see output/analysis/CHORD_ANALYSIS_DESIGN.md section 4): this is
built for reuse in Phase 3B (note-progression alignment, once the Phase 2
export stage is corpus-wide) as well as Phase 3A (chord-progression
alignment). Both callers plug in their own alphabet and their own
substitution-score function; nothing chord-specific lives here. Phase 3A's
chord substitution scoring is in lib/chord_similarity.py instead - a
consumer of this module, not part of it.

At this corpus's scale (75 songs, low hundreds of segments each -> ~2,775
song pairs, each a small O(len_a * len_b) DP matrix) a hand-rolled
implementation is simpler to reason about, trivial to instrument for
motif-extraction (traceback needs to expose which cells matched/gapped),
and adds zero new heavy dependencies.

Both algorithms use a *linear* gap penalty (a flat cost per gapped
position, not an affine open/extend split) - see design doc for why that
was judged sufficient at this corpus size rather than a premature
complication.

Public API:
    needleman_wunsch(seq_a, seq_b, score_fn, gap_penalty) -> AlignmentResult
    smith_waterman(seq_a, seq_b, score_fn, gap_penalty) -> AlignmentResult

`score_fn(a, b) -> float` is the caller-supplied, pluggable substitution
score for a pair of symbols from the two sequences (arbitrary hashable
objects - chord ids, note-event tuples, anything). Score should be higher
for "more similar" symbols and can be graded (not just match/mismatch) -
see chord_similarity.py for the chord version.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Traceback move codes
_DIAG = "D"   # substitution/match: consumes one symbol from each sequence
_UP = "U"     # gap in seq_b: consumes one symbol from seq_a only
_LEFT = "L"   # gap in seq_a: consumes one symbol from seq_b only
_STOP = "S"   # Smith-Waterman only: local alignment start (score reset to 0)

GAP = None  # sentinel used in aligned_a/aligned_b to mark a gap position


@dataclass
class AlignmentResult:
    score: float
    aligned_a: list  # seq_a symbols with GAP inserted where seq_b has an insertion
    aligned_b: list  # seq_b symbols with GAP inserted where seq_a has an insertion
    # index pairs (i, j) into the ORIGINAL seq_a/seq_b for each aligned column,
    # or None on the side that has a gap - convenient for motif extraction
    # (mapping an aligned column back to a segment's start/end time, etc).
    index_pairs: list = field(default_factory=list)
    # Smith-Waterman only: the half-open [start, end) slice of each original
    # sequence that the local alignment covers. None for needleman_wunsch.
    span_a: tuple | None = None
    span_b: tuple | None = None


def _traceback(seq_a, seq_b, moves, i, j, stop_at_zero_score=False, score_matrix=None):
    aligned_a, aligned_b, index_pairs = [], [], []
    while True:
        if stop_at_zero_score and score_matrix[i][j] == 0:
            break
        move = moves[i][j]
        if move == _DIAG:
            aligned_a.append(seq_a[i - 1])
            aligned_b.append(seq_b[j - 1])
            index_pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif move == _UP:
            aligned_a.append(seq_a[i - 1])
            aligned_b.append(GAP)
            index_pairs.append((i - 1, None))
            i -= 1
        elif move == _LEFT:
            aligned_a.append(GAP)
            aligned_b.append(seq_b[j - 1])
            index_pairs.append((None, j - 1))
            j -= 1
        else:  # _STOP or i==0 and j==0
            break
    aligned_a.reverse()
    aligned_b.reverse()
    index_pairs.reverse()
    return aligned_a, aligned_b, index_pairs, i, j


def needleman_wunsch(seq_a, seq_b, score_fn, gap_penalty) -> AlignmentResult:
    """Global alignment: every symbol in both sequences is placed in the
    output (possibly opposite a gap). Best fit for comparing two sequences
    that are expected to correspond over their full length (e.g. two songs
    of similar overall structure). gap_penalty should be a negative number
    (or 0); it is added once per gapped position."""
    n, m = len(seq_a), len(seq_b)
    H = [[0.0] * (m + 1) for _ in range(n + 1)]
    moves = [[None] * (m + 1) for _ in range(n + 1)]

    for i in range(1, n + 1):
        H[i][0] = H[i - 1][0] + gap_penalty
        moves[i][0] = _UP
    for j in range(1, m + 1):
        H[0][j] = H[0][j - 1] + gap_penalty
        moves[0][j] = _LEFT

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            diag = H[i - 1][j - 1] + score_fn(seq_a[i - 1], seq_b[j - 1])
            up = H[i - 1][j] + gap_penalty
            left = H[i][j - 1] + gap_penalty
            best = max(diag, up, left)
            H[i][j] = best
            moves[i][j] = _DIAG if best == diag else (_UP if best == up else _LEFT)

    aligned_a, aligned_b, index_pairs, _, _ = _traceback(seq_a, seq_b, moves, n, m)
    return AlignmentResult(score=H[n][m], aligned_a=aligned_a, aligned_b=aligned_b,
                            index_pairs=index_pairs)


def smith_waterman(seq_a, seq_b, score_fn, gap_penalty) -> AlignmentResult:
    """Local alignment: finds the single highest-scoring contiguous
    subsequence pair, ignoring unrelated flanking material on both sides.
    Better fit than global alignment for finding a shared motif embedded in
    two otherwise different songs (the brief's core use case: a borrowed
    or convergent 3-5 chord progression inside two songs that are not
    alike overall)."""
    n, m = len(seq_a), len(seq_b)
    H = [[0.0] * (m + 1) for _ in range(n + 1)]
    moves = [[_STOP] * (m + 1) for _ in range(n + 1)]

    best_score = 0.0
    best_cell = (0, 0)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            diag = H[i - 1][j - 1] + score_fn(seq_a[i - 1], seq_b[j - 1])
            up = H[i - 1][j] + gap_penalty
            left = H[i][j - 1] + gap_penalty
            best = max(0.0, diag, up, left)
            H[i][j] = best
            if best == 0.0:
                moves[i][j] = _STOP
            elif best == diag:
                moves[i][j] = _DIAG
            elif best == up:
                moves[i][j] = _UP
            else:
                moves[i][j] = _LEFT
            if best > best_score:
                best_score = best
                best_cell = (i, j)

    i, j = best_cell
    end_i, end_j = i, j
    aligned_a, aligned_b, index_pairs, start_i, start_j = _traceback(
        seq_a, seq_b, moves, i, j, stop_at_zero_score=True, score_matrix=H,
    )
    return AlignmentResult(
        score=best_score, aligned_a=aligned_a, aligned_b=aligned_b,
        index_pairs=index_pairs, span_a=(start_i, end_i), span_b=(start_j, end_j),
    )
