#!/usr/bin/env python3
"""
Phase 3B step 1: convert each song's note events (output/note_events_other.csv,
ordered by onset) into a per-song sequence of melodic semitone intervals -
the key-invariant "melodic DNA" alphabet for Phase 3B (see
output/analysis/NOTE_ANALYSIS_DESIGN.md section 1 for the full rationale).

interval_from_prev[i] = pitch[i] - pitch[i-1] (signed semitones), empty for
each song's first note (no previous note to diff against - NOT dropped,
kept as a row with an empty interval so note_index stays a dense 0..N-1
per-song index and downstream consumers can still look up its own pitch/
onset). Uses the `pitch` column (post-correction), not `original_pitch| -
per the brief, all analysis should read post-correction pitches.

This step is cheap and deterministic (no scoring/alignment), so unlike the
alignment steps below it runs corpus-wide immediately, no smoke test needed.

Reads:  output/note_events_other.csv (Phase 2 export, read-only)
Writes: output/analysis/note_sequences.csv

Usage:
    python -m scripts.analysis.build_note_sequences
"""
import csv
from collections import defaultdict
from pathlib import Path

NOTE_EVENTS_CSV = Path("output/note_events_other.csv")
OUT_DIR = Path("output/analysis")
SEQUENCES_CSV = OUT_DIR / "note_sequences.csv"


def main():
    by_song = defaultdict(list)
    with NOTE_EVENTS_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_song[row["song"]].append(row)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_rows = []
    for song in sorted(by_song):
        # Sort by onset - note_events_other.csv is already documented as
        # correctly onset-ordered per song (see pipeline-state memory), but
        # re-sort defensively rather than assume/trust silently.
        rows = sorted(by_song[song], key=lambda r: float(r["onset"]))
        prev_pitch = None
        for idx, r in enumerate(rows):
            pitch = int(r["pitch"])
            interval = "" if prev_pitch is None else pitch - prev_pitch
            out_rows.append({
                "song": song,
                "note_index": idx,
                "onset": r["onset"],
                "pitch": pitch,
                "interval_from_prev": interval,
            })
            prev_pitch = pitch

    with SEQUENCES_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["song", "note_index", "onset", "pitch", "interval_from_prev"])
        w.writeheader()
        w.writerows(out_rows)

    print(f"{len(by_song)} songs, {len(out_rows)} notes -> {SEQUENCES_CSV}")


if __name__ == "__main__":
    main()
