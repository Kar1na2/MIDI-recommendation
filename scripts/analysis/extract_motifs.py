#!/usr/bin/env python3
"""
Phase 3A step 5: motif-variation extraction. For song pairs whose alignment
(output/analysis/chord_similarity_matrix.csv) scored above a significance
threshold, re-run the alignment to recover the actual matched region -
which chords lined up, where they diverge - with real timestamps, so a
human can go listen to the claimed match rather than trust a bare score.

Threshold: corpus_z >= --z-threshold (default 2.5). corpus_z is the
corpus-relative, row-normalized z-score from
scripts/analysis/recompute_significance.py (run that BEFORE this script) -
NOT the permutation-test z this repo tried first and rejected as
miscalibrated (see CHORD_ANALYSIS_DESIGN.md section 4d). Empirically,
corpus_z >= 2 clears ~2.2% of the full matrix, >= 3 clears ~0.4%, and only
2 of 2,775 pairs clear >= 4 - so 2.5 was chosen as a practical cutoff
that surfaces a human-reviewable shortlist without being as strict as the
Bonferroni-style >=4 "high confidence" line documented for the (now-
replaced) permutation measure. --top-n additionally caps how many pairs
get written (default 30) so the deliverable stays human-reviewable rather
than dumping every pair that clears the bar.

Reads:  output/analysis/chord_similarity_matrix.csv (must exist - run
        run_alignment.py first)
        output/analysis/chord_sequences.csv (for timestamps + original,
        unshifted root_pc)
Writes: output/analysis/chord_motif_variations.csv - one row per aligned
        position within each qualifying pair's matched region.

Usage:
    python -m scripts.analysis.extract_motifs
    python -m scripts.analysis.extract_motifs --z-threshold 4 --top-n 15
"""
import argparse
import csv
from collections import defaultdict
from pathlib import Path

from lib.chord_similarity import best_transposition_alignment

SEQUENCES_CSV = Path("output/analysis/chord_sequences.csv")
MATRIX_CSV = Path("output/analysis/chord_similarity_matrix.csv")
OUT_CSV = Path("output/analysis/chord_motif_variations.csv")

GAP_PENALTY = -2.0  # must match run_alignment.py's calibrated choice

FIELDNAMES = [
    "song_a", "song_b", "pair_z_score", "pair_transposition",
    "aligned_position",
    "a_segment_index", "a_start", "a_end", "a_forte_class", "a_common_name", "a_root_pc",
    "b_segment_index", "b_start", "b_end", "b_forte_class", "b_common_name", "b_root_pc",
    "match_type",
]


def load_sequences_and_meta():
    """Returns (by_song_symbols, by_song_meta): symbols is [(forte,root_pc)]
    for alignment; meta is [(segment_index, start, end, forte, common_name, root_pc)]
    for reporting, both in segment order."""
    rows_by_song = defaultdict(list)
    with SEQUENCES_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows_by_song[row["song"]].append(row)
    symbols, meta = {}, {}
    for song, rows in rows_by_song.items():
        rows.sort(key=lambda r: int(r["segment_index"]))
        symbols[song] = [(r["canonical_chord_id"], int(r["root_pc"])) for r in rows]
        meta[song] = [
            (int(r["segment_index"]), float(r["start"]), float(r["end"]),
             r["canonical_chord_id"], r["common_name"], int(r["root_pc"]))
            for r in rows
        ]
    return symbols, meta


def classify(a_forte, a_root, b_forte, b_root_shifted):
    if a_forte is None or b_forte is None:
        return "gap"
    if a_forte == b_forte and a_root == b_root_shifted:
        return "exact_match"
    if a_forte == b_forte:
        return "quality_match_different_root"
    return "substitution"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--z-threshold", type=float, default=2.5)
    ap.add_argument("--top-n", type=int, default=30)
    args = ap.parse_args()

    if not MATRIX_CSV.exists():
        print(f"{MATRIX_CSV} not found - run scripts.analysis.run_alignment first.")
        return

    candidates = []
    with MATRIX_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            z = float(row["corpus_z"])
            if z >= args.z_threshold:
                candidates.append(row)
    candidates.sort(key=lambda r: -float(r["corpus_z"]))
    candidates = candidates[:args.top_n]
    print(f"{len(candidates)} pairs cleared z >= {args.z_threshold} (of those, top {args.top_n} kept)")

    symbols, meta = load_sequences_and_meta()
    out_rows = []
    for row in candidates:
        song_a, song_b = row["song_a"], row["song_b"]
        seq_a, seq_b = symbols[song_a], symbols[song_b]
        best_t, result = best_transposition_alignment(seq_a, seq_b, GAP_PENALTY)
        meta_a, meta_b = meta[song_a], meta[song_b]

        for pos, (idx_a, idx_b) in enumerate(result.index_pairs):
            a_seg = meta_a[idx_a] if idx_a is not None else None
            b_seg = meta_b[idx_b] if idx_b is not None else None
            a_forte = a_seg[3] if a_seg else None
            a_root = a_seg[5] if a_seg else None
            b_forte = b_seg[3] if b_seg else None
            b_root_shifted = (b_seg[5] + best_t) % 12 if b_seg else None
            out_rows.append({
                "song_a": song_a, "song_b": song_b,
                "pair_z_score": row["corpus_z"], "pair_transposition": best_t,
                "aligned_position": pos,
                "a_segment_index": a_seg[0] if a_seg else "",
                "a_start": a_seg[1] if a_seg else "",
                "a_end": a_seg[2] if a_seg else "",
                "a_forte_class": a_forte or "",
                "a_common_name": a_seg[4] if a_seg else "",
                "a_root_pc": a_root if a_root is not None else "",
                "b_segment_index": b_seg[0] if b_seg else "",
                "b_start": b_seg[1] if b_seg else "",
                "b_end": b_seg[2] if b_seg else "",
                "b_forte_class": b_forte or "",
                "b_common_name": b_seg[4] if b_seg else "",
                "b_root_pc": b_seg[5] if b_seg else "",
                "match_type": classify(a_forte, a_root, b_forte, b_root_shifted),
            })

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(out_rows)
    print(f"Wrote {len(out_rows)} rows across {len(candidates)} pairs -> {OUT_CSV}")


if __name__ == "__main__":
    main()
