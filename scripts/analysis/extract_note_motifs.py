#!/usr/bin/env python3
"""
Phase 3B motif-variation extraction. For song pairs whose alignment
(output/analysis/note_similarity_matrix.csv, after
scripts/analysis/compute_note_corpus_z.py has added corpus_z) scored above
a significance threshold, re-run the alignment to recover the actual
matched region - which notes/intervals lined up, where they diverge - with
real pitches/onsets, so a human can go listen to the claimed match rather
than trust a bare score. Same shape and purpose as Phase 3A's
scripts/analysis/extract_motifs.py.

Threshold: corpus_z >= --z-threshold. Full-corpus run landed at 2.23% of
pairs clearing corpus_z>=2 (matches Phase 3A's own 2.23% almost exactly)
and 1.30% clearing >=2.5, so 2.5/30 (same defaults Phase 3A used) was kept
as the "human-reviewable shortlist" cutoff rather than re-derived from
scratch - see design doc "Results" section for the full distribution.

**Read this before trusting the top of the output blindly**: see
output/analysis/NOTE_ANALYSIS_DESIGN.md section 7 for a real residual
artifact found in corpus_z - a handful of song pairs where BOTH songs have
an unusually repetitive/low-vocabulary interval sequence still align
across nearly the ENTIRE shorter song (a degenerate "matches almost
everything" case, not a localized motif) despite the length+entropy
regression correction applied upstream. This script computes each pair's
own interval-sequence Shannon entropy and writes `a_entropy`/`b_entropy`/
`low_complexity_flag` (true if EITHER song's entropy is below the corpus's
own 25th percentile) on every row specifically so this caveat travels with
the data - a human reviewing a flagged pair should apply extra scrutiny to
whether `aligned_len` covers most of the shorter song (a bad sign) before
trusting the score.

Reads:  output/analysis/note_similarity_matrix.csv (must have corpus_z -
        run compute_note_corpus_z.py first)
        output/analysis/note_sequences.csv (for pitches/onsets/entropy)
Writes: output/analysis/note_motif_variations.csv - one row per aligned
        position within each qualifying pair's matched region.

Usage:
    python -m scripts.analysis.extract_note_motifs
    python -m scripts.analysis.extract_note_motifs --z-threshold 3 --top-n 15
"""
import argparse
import csv
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from lib.sequence_alignment import smith_waterman
from lib.note_similarity import interval_substitution_score

SEQUENCES_CSV = Path("output/analysis/note_sequences.csv")
MATRIX_CSV = Path("output/analysis/note_similarity_matrix.csv")
OUT_CSV = Path("output/analysis/note_motif_variations.csv")

GAP_PENALTY = -2.0  # must match run_note_alignment.py's calibrated choice

FIELDNAMES = [
    "song_a", "song_b", "pair_corpus_z",
    "a_entropy", "b_entropy", "low_complexity_flag",
    "aligned_position",
    "a_note_index", "a_onset", "a_pitch", "a_interval",
    "b_note_index", "b_onset", "b_pitch", "b_interval",
    "match_type",
]


def song_entropy(seq):
    c = Counter(seq)
    n = len(seq)
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def load_sequences_and_meta():
    """Returns (by_song_intervals, by_song_meta): intervals is [int, ...]
    (interval_from_prev, first note of each song excluded - it has none)
    for alignment; meta is the matching [(note_index, onset, pitch,
    interval), ...] for reporting, both in note order, same length/index
    alignment as intervals (meta[i] corresponds to intervals[i])."""
    rows_by_song = defaultdict(list)
    with SEQUENCES_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows_by_song[row["song"]].append(row)
    intervals, meta = {}, {}
    for song, rows in rows_by_song.items():
        rows.sort(key=lambda r: int(r["note_index"]))
        rows = [r for r in rows if r["interval_from_prev"] != ""]  # drop each song's first note (no interval)
        intervals[song] = [int(r["interval_from_prev"]) for r in rows]
        meta[song] = [
            (int(r["note_index"]), float(r["onset"]), int(r["pitch"]), int(r["interval_from_prev"]))
            for r in rows
        ]
    return intervals, meta


def classify(a_interval, b_interval):
    if a_interval is None or b_interval is None:
        return "gap"
    if a_interval == b_interval:
        return "exact_match"
    return "substitution"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--z-threshold", type=float, default=2.5)
    ap.add_argument("--top-n", type=int, default=30)
    args = ap.parse_args()

    if not MATRIX_CSV.exists():
        print(f"{MATRIX_CSV} not found - run scripts.analysis.run_note_alignment then "
              f"scripts.analysis.compute_note_corpus_z first.")
        return

    candidates = []
    with MATRIX_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if "corpus_z" not in row or row["corpus_z"] == "":
                print(f"{MATRIX_CSV} has no corpus_z column - run scripts.analysis.compute_note_corpus_z first.")
                return
            z = float(row["corpus_z"])
            if z >= args.z_threshold:
                candidates.append(row)
    candidates.sort(key=lambda r: -float(r["corpus_z"]))
    candidates = candidates[:args.top_n]
    print(f"{len(candidates)} pairs cleared corpus_z >= {args.z_threshold} (of those, top {args.top_n} kept)")

    intervals, meta = load_sequences_and_meta()
    entropies = {song: song_entropy(seq) for song, seq in intervals.items()}
    entropy_p25 = statistics.quantiles(list(entropies.values()), n=4)[0]
    print(f"corpus interval-entropy p25={entropy_p25:.3f} bits (low_complexity_flag threshold)")

    out_rows = []
    for row in candidates:
        song_a, song_b = row["song_a"], row["song_b"]
        seq_a, seq_b = intervals[song_a], intervals[song_b]
        result = smith_waterman(seq_a, seq_b, interval_substitution_score, GAP_PENALTY)
        meta_a, meta_b = meta[song_a], meta[song_b]
        ea, eb = entropies[song_a], entropies[song_b]
        low_complexity = ea < entropy_p25 or eb < entropy_p25

        for pos, (idx_a, idx_b) in enumerate(result.index_pairs):
            a_seg = meta_a[idx_a] if idx_a is not None else None
            b_seg = meta_b[idx_b] if idx_b is not None else None
            a_interval = a_seg[3] if a_seg else None
            b_interval = b_seg[3] if b_seg else None
            out_rows.append({
                "song_a": song_a, "song_b": song_b,
                "pair_corpus_z": row["corpus_z"],
                "a_entropy": round(ea, 3), "b_entropy": round(eb, 3),
                "low_complexity_flag": low_complexity,
                "aligned_position": pos,
                "a_note_index": a_seg[0] if a_seg else "",
                "a_onset": a_seg[1] if a_seg else "",
                "a_pitch": a_seg[2] if a_seg else "",
                "a_interval": a_interval if a_interval is not None else "",
                "b_note_index": b_seg[0] if b_seg else "",
                "b_onset": b_seg[1] if b_seg else "",
                "b_pitch": b_seg[2] if b_seg else "",
                "b_interval": b_interval if b_interval is not None else "",
                "match_type": classify(a_interval, b_interval),
            })

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(out_rows)
    print(f"Wrote {len(out_rows)} rows across {len(candidates)} pairs -> {OUT_CSV}")


if __name__ == "__main__":
    main()
