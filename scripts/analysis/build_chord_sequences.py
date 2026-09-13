#!/usr/bin/env python3
"""
Phase 3A step 2: convert each song's chord segments (output/chord_segments.csv,
ordered by start time) into a per-song sequence of canonical chord symbols,
retaining duration and position. This is the unit both n-gram mining
(mine_ngrams.py) and sequence alignment (run_alignment.py) operate on.

canonical_chord_id = forte_class (see lib/chord_canonicalization.py for why:
transposition-invariant, but deliberately keeps major/minor and other
inversion-pairs distinct). segment_index is 0-based, per-song, in start-time
order - this is positional information within the song, not a corpus-wide id.

Reads:  output/chord_segments.csv (Phase 2 output, read-only)
Writes: output/analysis/chord_sequences.csv

Usage:
    python -m scripts.analysis.build_chord_sequences
"""
import csv
from collections import defaultdict
from pathlib import Path

from lib.chord_canonicalization import canonicalize_str

SEGMENTS_CSV = Path("output/chord_segments.csv")
OUT_DIR = Path("output/analysis")
SEQUENCES_CSV = OUT_DIR / "chord_sequences.csv"


def main():
    by_song = defaultdict(list)
    with SEGMENTS_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_song[row["song"]].append(row)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_rows = []
    for song in sorted(by_song):
        rows = sorted(by_song[song], key=lambda r: float(r["start"]))
        for idx, r in enumerate(rows):
            start = float(r["start"])
            end = float(r["end"])
            canon = canonicalize_str(r["pitch_class_set"])
            out_rows.append({
                "song": song,
                "segment_index": idx,
                "start": start,
                "end": end,
                "duration": end - start,
                "pitch_class_set": r["pitch_class_set"],
                "canonical_chord_id": canon.forte_class,
                "common_name": canon.common_name,
                # Absolute (not transposition-invariant) root pitch class of THIS
                # segment - added after the alignment validation checkpoint showed
                # quality-only canonical_chord_id discards the root-motion signal
                # alignment needs (see CHORD_ANALYSIS_DESIGN.md section 4b /
                # lib/chord_similarity.py). n-gram mining still uses
                # canonical_chord_id only, unchanged.
                "root_pc": canon.root_pc,
            })

    with SEQUENCES_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "song", "segment_index", "start", "end", "duration",
            "pitch_class_set", "canonical_chord_id", "common_name", "root_pc",
        ])
        w.writeheader()
        w.writerows(out_rows)

    print(f"{len(by_song)} songs, {len(out_rows)} segments -> {SEQUENCES_CSV}")


if __name__ == "__main__":
    main()
