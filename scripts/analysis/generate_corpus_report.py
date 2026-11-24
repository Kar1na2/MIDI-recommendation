#!/usr/bin/env python3
"""Write a human-readable harmonic/melodic corpus report."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from datetime import date
from collections import Counter, defaultdict
from pathlib import Path


DEFAULT_OUT = Path("LIKED_SONGS_ANALYSIS_REPORT_2026-10-30.md")
QUALITY_NAMES = {
    "3-11B": "major",
    "3-11A": "minor",
    "4-26": "minor seventh",
    "4-20": "major seventh",
    "4-27B": "dominant seventh",
    "4-27A": "half-diminished seventh",
    "3-12": "augmented",
    "4-22A": "add9",
    "3-10": "diminished",
}


def rows(path: Path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def entropy(values: list[int]) -> float:
    if not values:
        return 0.0
    counts = Counter(values)
    n = len(values)
    return -sum((count / n) * math.log2(count / n) for count in counts.values())


def top_lines(counter: Counter, total: int, limit: int = 8) -> list[str]:
    return [
        f"- `{key}` — {count:,} occurrences ({100 * count / total:.1f}%)"
        for key, count in counter.most_common(limit)
    ] if total else ["- No completed rows were available."]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    chord_rows = rows(Path("output/analysis/chord_sequences.csv"))
    note_rows = rows(Path("output/analysis/note_sequences.csv"))
    chord_pairs = rows(Path("output/analysis/chord_similarity_matrix.csv"))
    note_pairs = rows(Path("output/analysis/note_similarity_matrix.csv"))
    metadata_path = Path("archive/liked_songs.json")
    manifest_path = Path("liked-audio/download_manifest.json")
    liked_tracks = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    download_status = Counter(record.get("status", "unresolved") for record in manifest.values())
    songs = sorted({row["song"] for row in chord_rows} | {row["song"] for row in note_rows})

    quality_counts = Counter(row["canonical_chord_id"] for row in chord_rows)
    root_motion = Counter()
    chord_by_song: dict[str, list[dict]] = defaultdict(list)
    for row in chord_rows:
        chord_by_song[row["song"]].append(row)
    for song_rows in chord_by_song.values():
        song_rows.sort(key=lambda row: int(row["segment_index"]))
        for before, after in zip(song_rows, song_rows[1:]):
            delta = (int(after["root_pc"]) - int(before["root_pc"])) % 12
            root_motion[delta] += 1

    intervals_by_song: dict[str, list[int]] = defaultdict(list)
    for row in note_rows:
        if row["interval_from_prev"]:
            intervals_by_song[row["song"]].append(int(row["interval_from_prev"]))
    interval_counts = Counter(value for values in intervals_by_song.values() for value in values)
    abs_interval_counts = Counter(abs(value) for values in intervals_by_song.values() for value in values)

    # Heuristic categories use the same exported sequences as the alignment
    # layer. They are descriptive structural clusters, not genre labels.
    categories = defaultdict(list)
    for song in songs:
        crows = chord_by_song.get(song, [])
        ivals = intervals_by_song.get(song, [])
        n_chords = len(crows) or 1
        qualities = Counter(row["canonical_chord_id"] for row in crows)
        triad_share = sum(qualities[k] for k in ("3-11A", "3-11B", "3-12", "3-10")) / n_chords
        rich_share = sum(qualities[k] for k in ("4-20", "4-26", "4-27A", "4-27B", "4-22A")) / n_chords
        small_share = sum(1 for value in ivals if abs(value) <= 2) / max(1, len(ivals))
        leap_share = sum(1 for value in ivals if abs(value) >= 5) / max(1, len(ivals))
        chord_pairs_local = Counter(
            (crows[i]["canonical_chord_id"], crows[i + 1]["canonical_chord_id"])
            for i in range(len(crows) - 1)
        )
        repeat_share = max(chord_pairs_local.values(), default=0) / max(1, len(crows) - 1)
        iv_entropy = entropy(ivals)
        if repeat_share >= 0.22 and iv_entropy <= 2.8:
            category = "Loop / vamp driven"
        elif rich_share >= 0.55:
            category = "Seventh / extended harmony"
        elif triad_share >= 0.65 and small_share >= 0.45:
            category = "Triadic, stepwise melody"
        elif leap_share >= 0.35:
            category = "Leap driven melody"
        else:
            category = "Mixed structural profile"
        categories[category].append((song, triad_share, rich_share, small_share, leap_share, iv_entropy))

    def pair_lines(pair_rows, score_key="corpus_z", limit=10):
        if not pair_rows:
            return ["- No pairwise alignment matrix was available."]
        def score(row):
            try:
                return float(row.get(score_key, row.get("normalized_score", 0)) or 0)
            except ValueError:
                return 0.0
        selected = sorted(pair_rows, key=score, reverse=True)[:limit]
        return [
            f"- **{row['song_a']}** ↔ **{row['song_b']}** — "
            f"{score(row):.3f} ({row.get('candidate_type', 'all-pairs')}; "
            f"aligned span {row.get('aligned_len', '?')})"
            for row in selected
        ]

    chord_ngrams = rows(Path("output/analysis/chord_ngrams.csv"))
    note_ngrams = Counter()
    for values in intervals_by_song.values():
        note_ngrams.update(",".join(map(str, values[i:i + 5])) for i in range(len(values) - 4))

    lines = [
        "# Liked Songs Harmonic and Melodic Analysis",
        "",
        f"Generated from the completed local MIDI pipeline on {date.today().isoformat()}.",
        "The report describes structural patterns in the transcribed `other` Demucs stem: harmonic accompaniment, keys, guitars, and synths. It does not claim to measure vocals, bass, drums, genre, authorship, or influence. Categories are transparent heuristics intended for exploration.",
        "",
        "## Corpus coverage",
        "",
        f"- Spotify Liked Songs records fetched: **{len(liked_tracks):,}**",
        f"- Local ingestion status: **{download_status.get('downloaded', 0):,}** downloaded, **{download_status.get('existing', 0):,}** existing-corpus aliases, **{download_status.get('duplicate', 0):,}** exact duplicates, **{download_status.get('failed', 0):,}** failed",
        f"- Songs with chord sequences: **{len({row['song'] for row in chord_rows}):,}**",
        f"- Songs with melodic interval sequences: **{len(intervals_by_song):,}**",
        f"- Chord segments: **{len(chord_rows):,}** across **{len(quality_counts)}** canonical qualities",
        f"- Note intervals: **{sum(len(values) for values in intervals_by_song.values()):,}**",
        f"- Chord alignment pairs retained: **{len(chord_pairs):,}**",
        f"- Note alignment pairs retained: **{len(note_pairs):,}**",
        "",
        "## Harmonic patterns",
        "",
        "Chordino reduces detected sonorities to pitch-class sets, then the analysis canonicalizes them to transposition-invariant Forte classes. The most common qualities are:",
        "",
    ]
    lines.extend(top_lines(Counter({QUALITY_NAMES.get(k, k): v for k, v in quality_counts.items()}), sum(quality_counts.values())))
    lines.extend([
        "",
        "Root motion is most often short-distance motion within a tonal center, with a smaller but meaningful set of fourth/fifth and chromatic transitions. The eight most frequent directed pitch-class moves are:",
        "",
    ])
    lines.extend(top_lines(Counter({f"+{key} semitones": value for key, value in root_motion.items()}), sum(root_motion.values())))
    lines.extend(["", "The most frequent recurring chord n-grams (quality sequences, independent of absolute key) are:", ""])
    if chord_ngrams:
        for row in sorted(chord_ngrams, key=lambda row: int(row["song_count"]), reverse=True)[:8]:
            lines.append(f"- `{row['ngram']}` — {row['song_count']} songs, {row['total_occurrences']} occurrences")
    else:
        lines.append("- No chord n-gram export was available.")

    lines.extend(["", "## Melodic patterns", "", "Melodic similarity is computed on signed semitone intervals between successive transcribed notes, so the representation is invariant to whole-song transposition.", ""])
    lines.extend(top_lines(interval_counts, sum(interval_counts.values()), 12))
    lines.extend(["", "The dominant interval sizes by absolute distance are:", ""])
    lines.extend(top_lines(Counter({f"{key} semitones": value for key, value in abs_interval_counts.items()}), sum(abs_interval_counts.values()), 10))
    lines.extend(["", "The most common five-interval contours are:", ""])
    for contour, count in note_ngrams.most_common(8):
        lines.append(f"- `{contour}` — {count:,} occurrences")

    lines.extend(["", "## Structural groups", "", "Each song is assigned to one exploratory group using chord repetition, chord-quality mix, interval entropy, stepwise share, and leap share. A group is useful for browsing and recommendation features; it is not a genre classifier.", ""])
    for category, values in sorted(categories.items(), key=lambda item: (-len(item[1]), item[0])):
        lines.append(f"### {category} ({len(values):,} songs)")
        examples = sorted(values, key=lambda value: (value[5], value[0]))[:5]
        for song, triad, rich, small, leap, ent in examples:
            lines.append(f"- `{song}` — triadic {triad:.0%}, seventh/extended {rich:.0%}, stepwise {small:.0%}, leaps {leap:.0%}, interval entropy {ent:.2f}")
        lines.append("")

    lines.extend(["## Strongest cross-song leads", "", "These are leads for human review. Pair scores are corpus-relative and should not be read as proof of shared composition or sampling.", "", "### Harmonic alignment", ""])
    lines.extend(pair_lines(chord_pairs))
    lines.extend(["", "### Melodic alignment", ""])
    lines.extend(pair_lines(note_pairs))
    lines.extend([
        "",
        "## Method and limits",
        "",
        "- Stem separation, Basic Pitch/ADTLib transcription, Chordino extraction, chord-tone tagging, correction, export, sequence construction, and the validated Smith–Waterman scoring were run in the repository’s documented order.",
        "- For a library this size, candidate retrieval selects high-overlap n-gram neighbors and adds deterministic background pairs before the same alignment scorer runs. This keeps the analysis tractable while retaining a real-corpus comparison set; the `candidate_type` column records the distinction.",
        "- Transcription errors, missing short notes, and the `other`-stem scope can change both counts and alignments. Treat the groups and pair rankings as review queues rather than definitive musical judgments.",
    ])
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {args.output} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
