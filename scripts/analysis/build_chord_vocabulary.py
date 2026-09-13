#!/usr/bin/env python3
"""
Phase 3A step 1: build the canonical chord vocabulary from the distinct
pitch-class sets already present corpus-wide in output/chord_segments.csv.

Reads:  output/chord_segments.csv (song, start, end, pitch_class_set) - Phase 2
        output, read-only, never modified (Phase 3A isolation rule).
Writes: output/analysis/chord_vocabulary.csv - one row per distinct
        pitch_class_set, with its canonical forte_class id, prime form,
        generic common name, and corpus frequency (segment count + how many
        distinct songs it appears in).

No duration filtering is applied - see CHORD_ANALYSIS_DESIGN.md section 2
for the data (segment-duration distribution) that justified not filtering.

Usage:
    python -m scripts.analysis.build_chord_vocabulary
"""
import csv
from collections import Counter, defaultdict
from pathlib import Path

from lib.chord_canonicalization import canonicalize_str

SEGMENTS_CSV = Path("output/chord_segments.csv")
OUT_DIR = Path("output/analysis")
VOCAB_CSV = OUT_DIR / "chord_vocabulary.csv"


def main():
    seg_count = Counter()
    songs_seen = defaultdict(set)
    with SEGMENTS_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pcs = row["pitch_class_set"]
            seg_count[pcs] += 1
            songs_seen[pcs].add(row["song"])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for pcs, count in seg_count.items():
        canon = canonicalize_str(pcs)
        rows.append({
            "pitch_class_set": pcs,
            "forte_class": canon.forte_class,
            "prime_form": canon.prime_form,
            "common_name": canon.common_name,
            "cardinality": canon.cardinality,
            "segment_count": count,
            "song_count": len(songs_seen[pcs]),
        })

    # Sort by frequency descending - most common chords first, easiest to sanity-check.
    rows.sort(key=lambda r: (-r["segment_count"], r["pitch_class_set"]))

    with VOCAB_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "pitch_class_set", "forte_class", "prime_form", "common_name",
            "cardinality", "segment_count", "song_count",
        ])
        w.writeheader()
        w.writerows(rows)

    forte_classes = {r["forte_class"] for r in rows}
    print(f"{len(rows)} distinct pitch-class sets -> {len(forte_classes)} distinct forte classes")
    print(f"Wrote {VOCAB_CSV}")


if __name__ == "__main__":
    main()
