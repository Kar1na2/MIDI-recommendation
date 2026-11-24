#!/usr/bin/env python3
"""Build a scalable set of alignment candidates for a large song corpus.

The original 75-song analysis aligned every possible pair. That grows
quadratically and is no longer practical for a four-digit library. This step
uses transposition-invariant n-gram cosine retrieval to keep the most likely
neighbors for each song, then adds deterministic random background pairs so
the existing per-song significance calibration still has a real-corpus null.
The downstream Smith-Waterman scoring itself is unchanged.
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from collections import defaultdict
from pathlib import Path


CHORD_SEQUENCES = Path("output/analysis/chord_sequences.csv")
NOTE_SEQUENCES = Path("output/analysis/note_sequences.csv")
OUT_CSV = Path("output/analysis/alignment_candidates.csv")
FIELDNAMES = ["mode", "song_a", "song_b", "candidate_type", "retrieval_score"]


def chord_documents() -> dict[str, set[str]]:
    rows: dict[str, list[tuple[int, str, int]]] = defaultdict(list)
    with CHORD_SEQUENCES.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows[row["song"]].append((
                int(row["segment_index"]), row["canonical_chord_id"], int(row["root_pc"]),
            ))
    documents = {}
    for song, values in rows.items():
        values.sort()
        seq = [(quality, root) for _, quality, root in values]
        features = set()
        for index in range(len(seq) - 2):
            window = seq[index:index + 3]
            base_root = window[0][1]
            features.add("|".join(
                f"{quality}:{(root - base_root) % 12}" for quality, root in window
            ))
        documents[song] = features
    return documents


def note_documents() -> dict[str, set[str]]:
    rows: dict[str, list[tuple[int, int]]] = defaultdict(list)
    with NOTE_SEQUENCES.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["interval_from_prev"] != "":
                rows[row["song"]].append((
                    int(row["note_index"]), int(row["interval_from_prev"]),
                ))
    documents = {}
    for song, values in rows.items():
        values.sort()
        seq = [interval for _, interval in values]
        documents[song] = {
            ",".join(map(str, seq[index:index + 5]))
            for index in range(len(seq) - 4)
        }
    return documents


def retrieve_pairs(
    documents: dict[str, set[str]], top_k: int, background_k: int, seed: int,
) -> dict[tuple[str, str], tuple[str, float]]:
    songs = sorted(documents)
    song_index = {song: index for index, song in enumerate(songs)}
    postings: dict[str, list[int]] = defaultdict(list)
    for song, features in documents.items():
        index = song_index[song]
        for feature in features:
            postings[feature].append(index)

    n_songs = len(songs)
    max_df = max(2, int(n_songs * 0.25))
    weights = {
        feature: math.log((n_songs + 1) / (len(ids) + 1)) + 1.0
        for feature, ids in postings.items()
        if 2 <= len(ids) <= max_df
    }
    norms = [0.0] * n_songs
    for feature, weight in weights.items():
        square = weight * weight
        for index in postings[feature]:
            norms[index] += square
    norms = [math.sqrt(value) or 1.0 for value in norms]

    pairs: dict[tuple[str, str], tuple[str, float]] = {}
    for left_index, left_song in enumerate(songs):
        scores: dict[int, float] = defaultdict(float)
        for feature in documents[left_song]:
            weight = weights.get(feature)
            if weight is None:
                continue
            square = weight * weight
            for right_index in postings[feature]:
                if right_index != left_index:
                    scores[right_index] += square
        ranked = sorted(
            (
                (dot / (norms[left_index] * norms[right_index]), right_index)
                for right_index, dot in scores.items()
            ),
            reverse=True,
        )[:top_k]
        for score, right_index in ranked:
            pair = tuple(sorted((left_song, songs[right_index])))
            previous = pairs.get(pair)
            if previous is None or score > previous[1]:
                pairs[pair] = ("retrieval", score)

    rng = random.Random(seed)
    for left_index, left_song in enumerate(songs):
        possible = [song for index, song in enumerate(songs) if index != left_index]
        for right_song in rng.sample(possible, min(background_k, len(possible))):
            pair = tuple(sorted((left_song, right_song)))
            pairs.setdefault(pair, ("background", 0.0))
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--background-k", type=int, default=12)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=OUT_CSV)
    args = parser.parse_args()

    modes = {
        "chord": chord_documents(),
        "note": note_documents(),
    }
    rows = []
    for mode, documents in modes.items():
        pairs = retrieve_pairs(documents, args.top_k, args.background_k, args.seed)
        counts = defaultdict(int)
        for (song_a, song_b), (candidate_type, score) in sorted(pairs.items()):
            counts[candidate_type] += 1
            rows.append({
                "mode": mode,
                "song_a": song_a,
                "song_b": song_b,
                "candidate_type": candidate_type,
                "retrieval_score": round(score, 6),
            })
        print(
            f"{mode}: {len(documents)} songs -> {len(pairs)} candidate pairs "
            f"({counts['retrieval']} retrieval, {counts['background']} background)"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows -> {args.output}")


if __name__ == "__main__":
    main()
