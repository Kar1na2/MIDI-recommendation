#!/usr/bin/env python3
"""
Phase 3B full-corpus step: pairwise Smith-Waterman local alignment of every
song's melodic-interval sequence against every other song's, using the
calibration validated in scripts/analysis/validate_note_alignment_sample.py
(see output/analysis/NOTE_ANALYSIS_DESIGN.md for the full design history).

Two deliberate departures from Phase 3A's scripts/analysis/run_alignment.py,
both justified in the design doc rather than assumed:

1. No transposition search. Chord alignment needed
   best_transposition_alignment() because root_pc is NOT transposition-
   invariant on its own (forte_class is, root_pc isn't). Signed semitone
   intervals ARE transposition-invariant by construction (pitch[i]-pitch[i-1]
   doesn't change if you transpose the whole song), so alignment runs
   directly on interval sequences - one Smith-Waterman call per pair, not 12.

2. No permutation-test null / shuffling. Phase 3A's run_alignment.py
   computed a shuffle-based permutation z-score per pair (N_SHUFFLES=20,
   i.e. 21x the alignment work) and only discovered AFTER the full run that
   this null is badly miscalibrated at this corpus's scale (mean z +1.70
   when it should be ~0 - see chord-analysis-phase3a memory / design doc
   section 4d) because shuffling a song's own segment order destroys its
   real internal repetition, and this corpus is full of short vamped/looped
   songs. That lesson is already known going into Phase 3B, so this script
   skips the permutation step entirely and goes straight to the
   corpus-relative ("row-normalized") z-score that Phase 3A had to retrofit
   - scripts/analysis/compute_note_corpus_z.py computes it as a
   post-process over this script's `normalized_score` column, exactly like
   Phase 3A's recompute_significance.py, just without ever having computed
   (or spent ~40 of ~46-70 minutes on) the flawed intermediate.

Timing: measured single-pass brute force (no shuffles) on this corpus
BEFORE deciding whether a seed-and-extend k-mer prefilter (the brief's
suggested fallback for "if brute force isn't fast enough") was needed:
total DP cells summed over all C(75,2)=2,775 pairs = ~6.37 billion; at the
measured ~4.0M cells/sec for this score_fn (bounded by a small cache - only
117 distinct interval values observed corpus-wide, ~13,689 possible pairs,
trivial memory), that is ~27 minutes single-pass. That fits this repo's
established "long batch run" convention comfortably (see
background-run-conventions memory) - a k-mer prefilter would add real
complexity (build, validate against brute force, maintain) to solve a
problem that measuring first showed doesn't actually exist here.
**Decision: no prefilter used; output/analysis/note_kmer_candidates.csv is
not produced** - see design doc for this reasoning spelled out for anyone
revisiting the scale question later (e.g. if the corpus grows much larger).

Reads:  output/analysis/note_sequences.csv
Writes: output/analysis/note_similarity_matrix.csv (streamed, flushed after
        every row; rerun overwrites, no resume/skip - same convention as
        Phase 3A's run_alignment.py)

Usage:
    python -m scripts.analysis.run_note_alignment --dry-run
    python -m scripts.analysis.run_note_alignment --limit 20     # smoke test
    python -m scripts.analysis.run_note_alignment                # full corpus
"""
import argparse
import csv
import itertools
import time
import traceback
from collections import defaultdict
from pathlib import Path

from lib.sequence_alignment import smith_waterman
from lib.note_similarity import interval_substitution_score, MATCH_SCORE

SEQUENCES_CSV = Path("output/analysis/note_sequences.csv")
OUT_CSV = Path("output/analysis/note_similarity_matrix.csv")
FAILURE_LOG = Path("output/analysis/_note_alignment_failures.log")

GAP_PENALTY = -2.0  # chosen at the validation checkpoint - see design doc
MAX_POSITION_SCORE = MATCH_SCORE  # score of a perfect match, for normalization (no root-bonus term here, unlike chords)

FIELDNAMES = [
    "song_a", "song_b", "len_a", "len_b",
    "raw_score", "normalized_score",
    "span_a_start", "span_a_end", "span_b_start", "span_b_end", "aligned_len",
]


def load_sequences():
    by_song = defaultdict(list)
    with SEQUENCES_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["interval_from_prev"] != "":
                by_song[row["song"]].append((int(row["note_index"]), int(row["interval_from_prev"])))
    out = {}
    for song, rows in by_song.items():
        rows.sort(key=lambda t: t[0])
        out[song] = [v for _, v in rows]
    return out


def align_pair(seq_a, seq_b):
    result = smith_waterman(seq_a, seq_b, interval_substitution_score, GAP_PENALTY)
    norm = result.score / (min(len(seq_a), len(seq_b)) * MAX_POSITION_SCORE)
    return result, norm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="process only the first N pairs (smoke test)")
    args = ap.parse_args()

    by_song = load_sequences()
    songs = sorted(by_song)
    pairs = list(itertools.combinations(songs, 2))
    print(f"{len(songs)} songs -> {len(pairs)} pairs")

    if args.dry_run:
        print(f"Would run {len(pairs)} pairs, gap={GAP_PENALTY}, writing to {OUT_CSV}")
        return

    if args.limit:
        pairs = pairs[:args.limit]
        print(f"--limit {args.limit}: processing first {len(pairs)} pairs only")

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    n_failed = 0

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f, FAILURE_LOG.open("w", encoding="utf-8") as flog:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for i, (song_a, song_b) in enumerate(pairs, 1):
            try:
                seq_a, seq_b = by_song[song_a], by_song[song_b]
                result, norm = align_pair(seq_a, seq_b)
                w.writerow({
                    "song_a": song_a, "song_b": song_b,
                    "len_a": len(seq_a), "len_b": len(seq_b),
                    "raw_score": round(result.score, 3),
                    "normalized_score": round(norm, 4),
                    "span_a_start": result.span_a[0], "span_a_end": result.span_a[1],
                    "span_b_start": result.span_b[0], "span_b_end": result.span_b[1],
                    "aligned_len": len(result.aligned_a),
                })
                f.flush()
            except Exception:
                n_failed += 1
                flog.write(f"{song_a} | {song_b}\n{traceback.format_exc()}\n")
                flog.flush()

            if i % 100 == 0 or i == len(pairs):
                elapsed = time.time() - t_start
                rate = i / elapsed
                eta = (len(pairs) - i) / rate if rate > 0 else float("inf")
                print(f"[{i}/{len(pairs)}] elapsed={elapsed:.0f}s eta={eta:.0f}s failed={n_failed}", flush=True)

    print(f"Done. {len(pairs)} pairs, {n_failed} failed -> {OUT_CSV}")
    if n_failed:
        print(f"See {FAILURE_LOG}")


if __name__ == "__main__":
    main()
