#!/usr/bin/env python3
"""
Phase 3A validation checkpoint (per the brief: "Validate on a small sample
before scaling"). Runs root-aware Smith-Waterman local alignment
(lib.chord_similarity.best_transposition_alignment) on a hand-picked set of
song pairs - the 3 original reference songs' pairwise combinations, a few
same-artist pairs (the best available "known-similar" validation case in
this corpus), and two negative-control pairs (an unrelated classical
solo-piano piece against two very different pop songs, expected to score
low) - and reports a permutation-test z-score for each pair (real score vs.
a null distribution built by shuffling one song's segment order N times and
re-running the SAME best-of-12-transpositions alignment), so a human can
judge whether high scores actually look like real shared progressions
before running the full ~2,775-pair matrix.

History: an earlier quality-only version of this script (forte_class
symbols with no root information) found NO significant pairs at all - see
CHORD_ANALYSIS_DESIGN.md section 4b. This version adds root_pc + a
transposition search specifically to test the fix that finding motivated.

Not one of the final deliverables - scripts/analysis/run_alignment.py is
the corpus-wide script this validates the design of.

Usage:
    python -m scripts.analysis.validate_alignment_sample
"""
import csv
import random
import statistics
from collections import defaultdict

from lib.chord_similarity import best_transposition_alignment

SEQUENCES_CSV = "output/analysis/chord_sequences.csv"

PAIRS = [
    ("163braces - 過期 (lyrics music video)", "1nonly - Meaningless Love (Official Music Video)", "reference x reference"),
    ("163braces - 過期 (lyrics music video)", "Alice U (앨리스유) - 도주 (Official Video)", "reference x reference"),
    ("1nonly - Meaningless Love (Official Music Video)", "Alice U (앨리스유) - 도주 (Official Video)", "reference x reference"),
    ("Kanye West - Heartless", "Kanye West - Stronger", "same artist"),
    ("Kanye West - Heartless", "Kanye West - POWER", "same artist"),
    ("Imagine Dragons - Bones (Official Music Video)", "Imagine Dragons - Natural", "same artist"),
    ("헤이즈 (Heize) - And July (Feat. DEAN, DJ Friz) MV", "헤이즈 (Heize) - Jenga (Feat. Gaeko) MV", "same artist"),
    ("Nocturne in B flat minor, Op. 9 no. 1", "1nonly - Meaningless Love (Official Music Video)", "negative control"),
    ("Nocturne in B flat minor, Op. 9 no. 1", "Alice U (앨리스유) - 도주 (Official Video)", "negative control"),
]

GAP_PENALTIES = [-2.0, -3.0]
N_SHUFFLES = 20
random.seed(42)


def load_sequences():
    by_song = defaultdict(list)
    with open(SEQUENCES_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_song[row["song"]].append((int(row["segment_index"]), row["canonical_chord_id"], int(row["root_pc"])))
    for song in by_song:
        by_song[song].sort(key=lambda t: t[0])
        by_song[song] = [(q, r) for _, q, r in by_song[song]]
    return by_song


def zscore(seq_a, seq_b, gap):
    best_t, real = best_transposition_alignment(seq_a, seq_b, gap)
    nulls = []
    shuffled = list(seq_b)
    for _ in range(N_SHUFFLES):
        random.shuffle(shuffled)
        _, r = best_transposition_alignment(seq_a, shuffled, gap)
        nulls.append(r.score)
    mean = statistics.mean(nulls)
    sd = statistics.pstdev(nulls) or 1e-9
    return real.score, best_t, mean, sd, (real.score - mean) / sd, real


def main():
    by_song = load_sequences()
    missing = {s for pair in PAIRS for s in pair[:2] if s not in by_song}
    if missing:
        print("MISSING from chord_sequences.csv:", missing)
        return

    for gap in GAP_PENALTIES:
        print(f"\n{'='*100}\nGAP PENALTY = {gap}  ({N_SHUFFLES} shuffles/pair)\n{'='*100}")
        for song_a, song_b, tag in PAIRS:
            score, t, mean, sd, z, result = zscore(by_song[song_a], by_song[song_b], gap)
            print(f"\n[{tag}] {song_a}  <->  {song_b}")
            print(f"  best_t={t}  score={score:.2f}  null_mean={mean:.2f}  null_sd={sd:.2f}  z={z:.2f}")
            print(f"  span_a={result.span_a} span_b={result.span_b} aligned_len={len(result.aligned_a)}")
            a_str = " ".join("-" if sym is None else f"{sym[0]}@{sym[1]}" for sym in result.aligned_a)
            b_str = " ".join("-" if sym is None else f"{sym[0]}@{sym[1]}" for sym in result.aligned_b)
            print("  a:", a_str)
            print("  b:", b_str)


if __name__ == "__main__":
    main()
