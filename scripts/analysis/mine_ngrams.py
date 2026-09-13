#!/usr/bin/env python3
"""
Phase 3A step 3: n-gram mining over canonical chord sequences.

Extracts bigrams and trigrams of canonical_chord_id (forte_class) per song,
then aggregates corpus-wide: how many times each n-gram recurs within a
song (summed across all songs it appears in) and how many distinct songs
it appears in at all (the more interesting "cross-song" signal - a
progression common in one song's chorus is less interesting than one that
shows up in many different songs).

n-grams are extracted from the segment sequence as-is (no time gap check
between consecutive segments) - adjacent segments in chord_sequences.csv
are already contiguous in time by construction (see build_chord_sequences.py),
so a bigram here always represents two chords that were harmonically
adjacent, not two chords separated by an untranscribed gap.

Reads:  output/analysis/chord_sequences.csv
Writes: output/analysis/chord_ngrams.csv

Usage:
    python -m scripts.analysis.mine_ngrams
"""
import csv
from collections import Counter, defaultdict
from pathlib import Path

SEQUENCES_CSV = Path("output/analysis/chord_sequences.csv")
OUT_CSV = Path("output/analysis/chord_ngrams.csv")
NS = (2, 3)


def main():
    by_song = defaultdict(list)
    with SEQUENCES_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_song[row["song"]].append((int(row["segment_index"]), row["canonical_chord_id"]))

    rows = []
    for n in NS:
        occurrence_count = Counter()   # ngram -> total occurrences corpus-wide
        songs_with_ngram = defaultdict(set)  # ngram -> set of songs
        per_song_count = defaultdict(Counter)  # song -> ngram -> count in that song

        for song, seq in by_song.items():
            seq.sort(key=lambda t: t[0])
            symbols = [s for _, s in seq]
            for i in range(len(symbols) - n + 1):
                ngram = tuple(symbols[i:i + n])
                occurrence_count[ngram] += 1
                songs_with_ngram[ngram].add(song)
                per_song_count[song][ngram] += 1

        for ngram, total in occurrence_count.items():
            song_set = songs_with_ngram[ngram]
            # cheap example: song with the highest in-song repeat count for this ngram
            example_song = max(song_set, key=lambda s: per_song_count[s][ngram])
            rows.append({
                "n": n,
                "ngram": "-".join(ngram),
                "total_occurrences": total,
                "song_count": len(song_set),
                "example_song": example_song,
                "example_song_count": per_song_count[example_song][ngram],
            })

    # Cross-song recurrence is the headline signal - sort by song_count first.
    rows.sort(key=lambda r: (-r["song_count"], -r["total_occurrences"], r["n"], r["ngram"]))

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "n", "ngram", "total_occurrences", "song_count",
            "example_song", "example_song_count",
        ])
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {len(rows)} n-gram rows -> {OUT_CSV}")
    print("\nTop 15 by cross-song recurrence:")
    for r in rows[:15]:
        print(f"  n={r['n']} {r['ngram']:<20s} songs={r['song_count']:3d} total={r['total_occurrences']:4d}  e.g. {r['example_song']}")


if __name__ == "__main__":
    main()
