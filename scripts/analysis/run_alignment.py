#!/usr/bin/env python3
"""
Phase 3A step 4 (full corpus): pairwise Smith-Waterman local alignment of
every song's canonical chord sequence against every other song's, using the
root-aware, key-invariant scoring validated in
scripts/analysis/validate_alignment_sample.py (see
output/analysis/CHORD_ANALYSIS_DESIGN.md section 4 for the full design
history, including the quality-only version this replaced after it found no
significant matches at all).

For each of the C(75,2)=2,775 song pairs:
  1. best_transposition_alignment() tries all 12 rigid transpositions of one
     song's root_pc values and keeps the highest-scoring Smith-Waterman
     alignment (lib/chord_similarity.py).
  2. A permutation-test null distribution is built by shuffling the other
     song's segment order N_SHUFFLES times and re-running step 1 each time
     (same procedure, so the null accounts for the same best-of-12 selection
     bias as the real score - a raw score alone is not comparable across
     pairs of different lengths/transposition luck).
  3. z = (real_score - null_mean) / null_sd was the ORIGINAL similarity
     measure this script computed. IMPORTANT - this permutation-based z
     turned out to be badly miscalibrated at full-corpus scale (mean z
     +1.70 across all 2,775 pairs when a correct null should average ~0;
     9.7% of ALL pairs cleared z>=4 when ~0.003% was expected) because
     shuffling a song's own segment order destroys that song's own real
     internal repetition (this corpus is full of short vamped/looped
     songs), making the null systematically weaker than reality. See
     CHORD_ANALYSIS_DESIGN.md section 4d.
     scripts/analysis/recompute_significance.py MUST be run after this
     script - it replaces this permutation z (kept in the output, renamed
     with a _deprecated suffix, for the record) with a corpus-relative
     ("row-normalized") z-score that doesn't have this flaw. A future
     from-scratch rerun could skip N_SHUFFLES entirely and save ~40 of
     this script's ~46-70 minutes, since recompute_significance.py's
     measure only needs `normalized_score`, not the permutation columns -
     they were left wired in here because this script had already been
     launched and completed before the flaw was found on its output.

Runtime: ~70 minutes for the full corpus at N_SHUFFLES=20 (measured warmup
timing during the validation checkpoint) - this is a long batch run per this
repo's usual conventions (see the "Background run conventions" project
memory): --dry-run / --limit smoke test first, then launch with
run_in_background and monitor the log.

Reads:  output/analysis/chord_sequences.csv
Writes: output/analysis/chord_similarity_matrix.csv (streamed, one row per
        pair, flushed after every row so a long run can be tailed/resumed
        conceptually - though this script does NOT skip already-written
        pairs on rerun, unlike the Phase 2 stage scripts; rerun writes a
        fresh file).

Usage:
    python -m scripts.analysis.run_alignment --dry-run
    python -m scripts.analysis.run_alignment --limit 20      # smoke test
    python -m scripts.analysis.run_alignment                 # full corpus
"""
import argparse
import csv
import itertools
import random
import statistics
import sys
import time
import traceback
from collections import defaultdict
from pathlib import Path

from lib.chord_similarity import best_transposition_alignment, MATCH_SCORE, ROOT_BONUS

SEQUENCES_CSV = Path("output/analysis/chord_sequences.csv")
OUT_CSV = Path("output/analysis/chord_similarity_matrix.csv")
FAILURE_LOG = Path("output/analysis/_alignment_failures.log")

GAP_PENALTY = -2.0   # chosen at the validation checkpoint - see design doc
N_SHUFFLES = 20      # permutation-test null sample size - see design doc
MAX_POSITION_SCORE = MATCH_SCORE + ROOT_BONUS  # score of a perfect (quality+root) match, for normalization

FIELDNAMES = [
    "song_a", "song_b", "len_a", "len_b", "best_transposition",
    "raw_score", "normalized_score", "null_mean", "null_sd", "z_score",
    "span_a_start", "span_a_end", "span_b_start", "span_b_end", "aligned_len",
]


def load_sequences():
    by_song = defaultdict(list)
    with SEQUENCES_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_song[row["song"]].append((int(row["segment_index"]), row["canonical_chord_id"], int(row["root_pc"])))
    out = {}
    for song, rows in by_song.items():
        rows.sort(key=lambda t: t[0])
        out[song] = [(q, r) for _, q, r in rows]
    return out


def align_pair(seq_a, seq_b, rng):
    best_t, real = best_transposition_alignment(seq_a, seq_b, GAP_PENALTY)
    shuffled = list(seq_b)
    nulls = []
    for _ in range(N_SHUFFLES):
        rng.shuffle(shuffled)
        _, r = best_transposition_alignment(seq_a, shuffled, GAP_PENALTY)
        nulls.append(r.score)
    mean = statistics.mean(nulls)
    sd = statistics.pstdev(nulls) or 1e-9
    z = (real.score - mean) / sd
    norm = real.score / (min(len(seq_a), len(seq_b)) * MAX_POSITION_SCORE)
    return best_t, real, mean, sd, z, norm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="process only the first N pairs (smoke test)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    by_song = load_sequences()
    songs = sorted(by_song)
    pairs = list(itertools.combinations(songs, 2))
    print(f"{len(songs)} songs -> {len(pairs)} pairs")

    if args.dry_run:
        print(f"Would run {len(pairs)} pairs, gap={GAP_PENALTY}, N_SHUFFLES={N_SHUFFLES}, writing to {OUT_CSV}")
        return

    if args.limit:
        pairs = pairs[:args.limit]
        print(f"--limit {args.limit}: processing first {len(pairs)} pairs only")

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    t_start = time.time()
    n_failed = 0

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f, FAILURE_LOG.open("w", encoding="utf-8") as flog:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for i, (song_a, song_b) in enumerate(pairs, 1):
            try:
                seq_a, seq_b = by_song[song_a], by_song[song_b]
                best_t, real, mean, sd, z, norm = align_pair(seq_a, seq_b, rng)
                w.writerow({
                    "song_a": song_a, "song_b": song_b,
                    "len_a": len(seq_a), "len_b": len(seq_b),
                    "best_transposition": best_t,
                    "raw_score": round(real.score, 3),
                    "normalized_score": round(norm, 4),
                    "null_mean": round(mean, 3), "null_sd": round(sd, 3),
                    "z_score": round(z, 3),
                    "span_a_start": real.span_a[0], "span_a_end": real.span_a[1],
                    "span_b_start": real.span_b[0], "span_b_end": real.span_b[1],
                    "aligned_len": len(real.aligned_a),
                })
                f.flush()
            except Exception:
                n_failed += 1
                flog.write(f"{song_a} | {song_b}\n{traceback.format_exc()}\n")
                flog.flush()

            if i % 25 == 0 or i == len(pairs):
                elapsed = time.time() - t_start
                rate = i / elapsed
                eta = (len(pairs) - i) / rate if rate > 0 else float("inf")
                print(f"[{i}/{len(pairs)}] elapsed={elapsed:.0f}s eta={eta:.0f}s failed={n_failed}", flush=True)

    print(f"Done. {len(pairs)} pairs, {n_failed} failed -> {OUT_CSV}")
    if n_failed:
        print(f"See {FAILURE_LOG}")


if __name__ == "__main__":
    main()
