#!/usr/bin/env python3
"""
Phase 3B validation checkpoint (per the brief: "Validate on a small sample
before scaling", same discipline as Phase 3A's
scripts/analysis/validate_alignment_sample.py - reuses the SAME hand-picked
pair set: the 3 original reference songs' pairwise combinations, the
same-artist pairs available in this corpus, and the Chopin nocturne as a
negative control - so a human can judge whether high-scoring pairs look
like real shared melodic motifs before running the full ~2,775-pair matrix.

Unlike Phase 3A, no transposition search is needed here (interval
sequences are already transposition-invariant), so this only sweeps
GAP_PENALTY candidates against the calibrated interval_substitution_score
from lib/note_similarity.py.

Reports a permutation-test z-score (shuffle one song's interval sequence N
times, realign, z = (real - null_mean)/null_sd) for the SMALL sample only -
per the Phase 3A memory (chord-analysis-phase3a), this measure is known to
become miscalibrated at full-corpus scale for this corpus (many short
vamped/looped songs), so scripts/analysis/run_note_alignment.py does NOT
use permutation z at scale - it goes straight to the corpus-relative
("row-normalized") z-score that Phase 3A had to retrofit after discovering
the miscalibration. Here at small scale it is still a reasonable sanity
check (it was fine on Phase 3A's small sample too - the flaw only showed up
at full scale), which is all this checkpoint needs.

Not a final deliverable - scripts/analysis/run_note_alignment.py is the
corpus-wide script this validates the design of.

Usage:
    python -m scripts.analysis.validate_note_alignment_sample
"""
import csv
import random
import statistics
from collections import defaultdict

from lib.sequence_alignment import smith_waterman
from lib.note_similarity import interval_substitution_score

SEQUENCES_CSV = "output/analysis/note_sequences.csv"

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

GAP_PENALTIES = [-1.5, -2.0, -2.5]
N_SHUFFLES = 20
random.seed(42)


def load_sequences():
    by_song = defaultdict(list)
    with open(SEQUENCES_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["interval_from_prev"] != "":
                by_song[row["song"]].append((int(row["note_index"]), int(row["interval_from_prev"])))
    for song in by_song:
        by_song[song].sort(key=lambda t: t[0])
        by_song[song] = [v for _, v in by_song[song]]
    return by_song


def zscore(seq_a, seq_b, gap):
    real = smith_waterman(seq_a, seq_b, interval_substitution_score, gap)
    nulls = []
    shuffled = list(seq_b)
    for _ in range(N_SHUFFLES):
        random.shuffle(shuffled)
        r = smith_waterman(seq_a, shuffled, interval_substitution_score, gap)
        nulls.append(r.score)
    mean = statistics.mean(nulls)
    sd = statistics.pstdev(nulls) or 1e-9
    return real.score, mean, sd, (real.score - mean) / sd, real


def main():
    by_song = load_sequences()
    missing = {s for pair in PAIRS for s in pair[:2] if s not in by_song}
    if missing:
        print("MISSING from note_sequences.csv:", missing)
        return

    for gap in GAP_PENALTIES:
        print(f"\n{'='*100}\nGAP PENALTY = {gap}  ({N_SHUFFLES} shuffles/pair)\n{'='*100}")
        for song_a, song_b, tag in PAIRS:
            score, mean, sd, z, result = zscore(by_song[song_a], by_song[song_b], gap)
            len_a, len_b = len(by_song[song_a]), len(by_song[song_b])
            span_frac_a = (result.span_a[1] - result.span_a[0]) / len_a if len_a else 0
            span_frac_b = (result.span_b[1] - result.span_b[0]) / len_b if len_b else 0
            n_exact = sum(1 for x, y in zip(result.aligned_a, result.aligned_b) if x == y and x is not None)
            exact_frac = n_exact / len(result.aligned_a) if result.aligned_a else 0
            print(f"\n[{tag}] {song_a}  <->  {song_b}  (len {len_a} x {len_b})")
            print(f"  score={score:.2f}  null_mean={mean:.2f}  null_sd={sd:.2f}  z={z:.2f}")
            print(f"  span_a={result.span_a} ({span_frac_a:.2%})  span_b={result.span_b} ({span_frac_b:.2%})  "
                  f"aligned_len={len(result.aligned_a)}  exact_frac={exact_frac:.2%}")
            a_str = " ".join("-" if v is None else f"{v:+d}" for v in result.aligned_a[:40])
            b_str = " ".join("-" if v is None else f"{v:+d}" for v in result.aligned_b[:40])
            print("  a[:40]:", a_str)
            print("  b[:40]:", b_str)


if __name__ == "__main__":
    main()
