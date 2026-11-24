#!/usr/bin/env python3
"""Populate a local SQLite database from the pipeline and analysis outputs.

The repository has historically stored its analysis as CSV files rather than
having a database layer. This import creates a reproducible, ignored SQLite
database for the expanded liked-songs corpus. It imports the same exported
note/chord sequences and pairwise alignment results used by the report and
records Spotify IDs separately from legacy filename-based songs.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path


DEFAULT_DB = Path("output/liked_songs_analysis.sqlite")
SONG_TABLES = {
    "chord_segments": """
        CREATE TABLE chord_segments (
            song TEXT NOT NULL, start REAL, end REAL, pitch_class_set TEXT
        )
    """,
    "note_events": """
        CREATE TABLE note_events (
            song TEXT NOT NULL, onset REAL, offset REAL, pitch INTEGER,
            velocity INTEGER, chord_onset REAL, chord_offset REAL,
            chord_pitchclasses TEXT, was_corrected TEXT,
            correction_type TEXT, original_pitch INTEGER
        )
    """,
    "chord_sequences": """
        CREATE TABLE chord_sequences (
            song TEXT NOT NULL, segment_index INTEGER, start REAL, end REAL,
            duration REAL, pitch_class_set TEXT, canonical_chord_id TEXT,
            common_name TEXT, root_pc INTEGER
        )
    """,
    "note_sequences": """
        CREATE TABLE note_sequences (
            song TEXT NOT NULL, note_index INTEGER, onset REAL, pitch INTEGER,
            interval_from_prev INTEGER
        )
    """,
    "chord_pairs": """
        CREATE TABLE chord_pairs (
            song_a TEXT, song_b TEXT, candidate_type TEXT, retrieval_score REAL,
            len_a INTEGER, len_b INTEGER, best_transposition INTEGER,
            raw_score REAL, normalized_score REAL,
            perm_null_mean_deprecated REAL, perm_null_sd_deprecated REAL,
            perm_z_score_deprecated REAL, corpus_z_a REAL, corpus_z_b REAL,
            corpus_z REAL, span_a_start INTEGER, span_a_end INTEGER,
            span_b_start INTEGER, span_b_end INTEGER, aligned_len INTEGER
        )
    """,
    "note_pairs": """
        CREATE TABLE note_pairs (
            song_a TEXT, song_b TEXT, candidate_type TEXT, retrieval_score REAL,
            len_a INTEGER, len_b INTEGER, raw_score REAL,
            normalized_score REAL, len_regression_residual REAL,
            corpus_z_a REAL, corpus_z_b REAL, corpus_z REAL,
            span_a_start INTEGER, span_a_end INTEGER, span_b_start INTEGER,
            span_b_end INTEGER, aligned_len INTEGER
        )
    """,
    "chord_motifs": """
        CREATE TABLE chord_motifs (
            song_a TEXT, song_b TEXT, pair_z_score TEXT, pair_transposition TEXT,
            aligned_position TEXT, a_segment_index TEXT, a_start TEXT, a_end TEXT,
            a_forte_class TEXT, a_common_name TEXT, a_root_pc TEXT,
            b_segment_index TEXT, b_start TEXT, b_end TEXT, b_forte_class TEXT,
            b_common_name TEXT, b_root_pc TEXT, match_type TEXT
        )
    """,
    "note_motifs": """
        CREATE TABLE note_motifs (
            song_a TEXT, song_b TEXT, pair_corpus_z TEXT, a_entropy TEXT,
            b_entropy TEXT, low_complexity_flag TEXT, aligned_position TEXT,
            a_note_index TEXT, a_onset TEXT, a_pitch TEXT, a_interval TEXT,
            b_note_index TEXT, b_onset TEXT, b_pitch TEXT, b_interval TEXT,
            match_type TEXT
        )
    """,
    "chord_ngrams": """
        CREATE TABLE chord_ngrams (
            n TEXT, ngram TEXT, total_occurrences TEXT, song_count TEXT,
            example_song TEXT, example_song_count TEXT
        )
    """,
    "alignment_candidates": """
        CREATE TABLE alignment_candidates (
            mode TEXT, song_a TEXT, song_b TEXT, candidate_type TEXT,
            retrieval_score REAL
        )
    """,
}


def numeric(value: str | None, kind):
    if value in (None, ""):
        return None
    try:
        return kind(value)
    except (TypeError, ValueError):
        return None


def read_rows(path: Path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_metadata(songs_path: Path, manifest_path: Path):
    songs = json.loads(songs_path.read_text(encoding="utf-8")) if songs_path.exists() else []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    if isinstance(songs, dict):
        songs = songs.get("songs", [])
    return songs, manifest


def create_schema(db: sqlite3.Connection) -> None:
    db.executescript("""
        PRAGMA journal_mode = WAL;
        CREATE TABLE songs (
            song TEXT PRIMARY KEY, title TEXT, artist TEXT, album TEXT,
            duration_ms INTEGER, added_at TEXT, source TEXT, audio_path TEXT
        );
        CREATE TABLE spotify_tracks (
            track_id TEXT PRIMARY KEY, title TEXT, artist TEXT, album TEXT,
            duration_ms INTEGER, added_at TEXT, status TEXT,
            pipeline_song TEXT, duplicate_of TEXT, audio_path TEXT,
            FOREIGN KEY (pipeline_song) REFERENCES songs(song)
        );
        CREATE TABLE import_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    for ddl in SONG_TABLES.values():
        db.execute(ddl)


def import_csv_table(db: sqlite3.Connection, table: str, path: Path, converters):
    rows = read_rows(path)
    if not rows:
        return 0
    columns = list(converters)
    placeholders = ",".join("?" for _ in columns)
    sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
    values = [tuple(converters[column](row.get(column)) for column in columns) for row in rows]
    db.executemany(sql, values)
    return len(values)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--songs", type=Path, default=Path("archive/liked_songs.json"))
    parser.add_argument("--manifest", type=Path, default=Path("liked-audio/download_manifest.json"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()

    args.db.parent.mkdir(parents=True, exist_ok=True)
    args.db.unlink(missing_ok=True)
    songs, manifest = load_metadata(args.songs, args.manifest)
    pipeline_names = set()
    for path in (Path("output/chord_segments.csv"), Path("output/note_events_other.csv")):
        for row in read_rows(path):
            pipeline_names.add(row["song"])
    pipeline_names.update(p.stem for p in Path("merged").glob("*.mid"))

    with sqlite3.connect(args.db) as db:
        create_schema(db)
        track_rows = []
        for song in songs:
            track_id = str(song.get("track_id") or "")
            record = manifest.get(track_id, {})
            status = record.get("status", "unresolved")
            path = record.get("path")
            duplicate_of = record.get("duplicate_of")
            pipeline_song = Path(path).stem if path else None
            if status == "duplicate" and duplicate_of:
                pipeline_song = Path(duplicate_of).stem
            if pipeline_song not in pipeline_names:
                pipeline_song = None
            track_rows.append((
                track_id, song.get("title"), song.get("artist"), song.get("album"),
                song.get("duration_ms"), song.get("added_at"), status,
                pipeline_song, duplicate_of, path,
            ))
        db.executemany(
            "INSERT OR IGNORE INTO spotify_tracks VALUES (?,?,?,?,?,?,?,?,?,?)",
            track_rows,
        )

        # Populate songs with Spotify metadata where available, then add any
        # legacy pipeline song that predates the liked-songs import.
        by_pipeline = {}
        for row in track_rows:
            if row[7] and row[6] not in {"duplicate"}:
                by_pipeline.setdefault(row[7], row)
        song_rows = []
        for song_name in sorted(pipeline_names):
            row = by_pipeline.get(song_name)
            if row:
                song_rows.append((song_name, row[1], row[2], row[3], row[4], row[5], "liked", row[9]))
            else:
                song_rows.append((song_name, song_name, None, None, None, None, "legacy", None))
        db.executemany("INSERT OR IGNORE INTO songs VALUES (?,?,?,?,?,?,?,?)", song_rows)

        specs = {
            "chord_segments": (Path("output/chord_segments.csv"), {
                "song": str, "start": lambda x: numeric(x, float),
                "end": lambda x: numeric(x, float), "pitch_class_set": str,
            }),
            "note_events": (Path("output/note_events_other.csv"), {
                "song": str, "onset": lambda x: numeric(x, float),
                "offset": lambda x: numeric(x, float), "pitch": lambda x: numeric(x, int),
                "velocity": lambda x: numeric(x, int), "chord_onset": lambda x: numeric(x, float),
                "chord_offset": lambda x: numeric(x, float), "chord_pitchclasses": str,
                "was_corrected": str, "correction_type": str,
                "original_pitch": lambda x: numeric(x, int),
            }),
            "chord_sequences": (Path("output/analysis/chord_sequences.csv"), {
                "song": str, "segment_index": lambda x: numeric(x, int),
                "start": lambda x: numeric(x, float), "end": lambda x: numeric(x, float),
                "duration": lambda x: numeric(x, float), "pitch_class_set": str,
                "canonical_chord_id": str, "common_name": str,
                "root_pc": lambda x: numeric(x, int),
            }),
            "note_sequences": (Path("output/analysis/note_sequences.csv"), {
                "song": str, "note_index": lambda x: numeric(x, int),
                "onset": lambda x: numeric(x, float), "pitch": lambda x: numeric(x, int),
                "interval_from_prev": lambda x: numeric(x, int),
            }),
            "chord_pairs": (Path("output/analysis/chord_similarity_matrix.csv"), {
                "song_a": str, "song_b": str, "candidate_type": str,
                "retrieval_score": lambda x: numeric(x, float), "len_a": lambda x: numeric(x, int),
                "len_b": lambda x: numeric(x, int), "best_transposition": lambda x: numeric(x, int),
                "raw_score": lambda x: numeric(x, float), "normalized_score": lambda x: numeric(x, float),
                "perm_null_mean_deprecated": lambda x: numeric(x, float),
                "perm_null_sd_deprecated": lambda x: numeric(x, float),
                "perm_z_score_deprecated": lambda x: numeric(x, float),
                "corpus_z_a": lambda x: numeric(x, float), "corpus_z_b": lambda x: numeric(x, float),
                "corpus_z": lambda x: numeric(x, float), "span_a_start": lambda x: numeric(x, int),
                "span_a_end": lambda x: numeric(x, int), "span_b_start": lambda x: numeric(x, int),
                "span_b_end": lambda x: numeric(x, int), "aligned_len": lambda x: numeric(x, int),
            }),
            "note_pairs": (Path("output/analysis/note_similarity_matrix.csv"), {
                "song_a": str, "song_b": str, "candidate_type": str,
                "retrieval_score": lambda x: numeric(x, float), "len_a": lambda x: numeric(x, int),
                "len_b": lambda x: numeric(x, int), "raw_score": lambda x: numeric(x, float),
                "normalized_score": lambda x: numeric(x, float),
                "len_regression_residual": lambda x: numeric(x, float),
                "corpus_z_a": lambda x: numeric(x, float), "corpus_z_b": lambda x: numeric(x, float),
                "corpus_z": lambda x: numeric(x, float), "span_a_start": lambda x: numeric(x, int),
                "span_a_end": lambda x: numeric(x, int), "span_b_start": lambda x: numeric(x, int),
                "span_b_end": lambda x: numeric(x, int), "aligned_len": lambda x: numeric(x, int),
            }),
            "chord_motifs": (Path("output/analysis/chord_motif_variations.csv"), {
                column: str for column in (
                    "song_a", "song_b", "pair_z_score", "pair_transposition",
                    "aligned_position", "a_segment_index", "a_start", "a_end",
                    "a_forte_class", "a_common_name", "a_root_pc", "b_segment_index",
                    "b_start", "b_end", "b_forte_class", "b_common_name", "b_root_pc",
                    "match_type",
                )
            }),
            "note_motifs": (Path("output/analysis/note_motif_variations.csv"), {
                column: str for column in (
                    "song_a", "song_b", "pair_corpus_z", "a_entropy", "b_entropy",
                    "low_complexity_flag", "aligned_position", "a_note_index", "a_onset",
                    "a_pitch", "a_interval", "b_note_index", "b_onset", "b_pitch",
                    "b_interval", "match_type",
                )
            }),
            "chord_ngrams": (Path("output/analysis/chord_ngrams.csv"), {
                column: str for column in (
                    "n", "ngram", "total_occurrences", "song_count",
                    "example_song", "example_song_count",
                )
            }),
            "alignment_candidates": (Path("output/analysis/alignment_candidates.csv"), {
                "mode": str, "song_a": str, "song_b": str,
                "candidate_type": str, "retrieval_score": lambda x: numeric(x, float),
            }),
        }
        counts = {}
        for table, (path, converters) in specs.items():
            counts[table] = import_csv_table(db, table, path, converters)
        db.executemany("INSERT INTO import_metadata VALUES (?,?)", [
            ("spotify_tracks", str(len(track_rows))),
            ("pipeline_songs", str(len(pipeline_names))),
            *[(key, str(value)) for key, value in counts.items()],
        ])
        db.execute("CREATE INDEX idx_note_events_song ON note_events(song)")
        db.execute("CREATE INDEX idx_chord_sequences_song ON chord_sequences(song)")
        db.execute("CREATE INDEX idx_note_sequences_song ON note_sequences(song)")
        db.execute("CREATE INDEX idx_chord_pairs_z ON chord_pairs(corpus_z)")
        db.execute("CREATE INDEX idx_note_pairs_z ON note_pairs(corpus_z)")

    print(f"Imported {len(track_rows)} Spotify tracks and {len(pipeline_names)} pipeline songs -> {args.db}")
    for table, count in counts.items():
        print(f"  {table}: {count:,} rows")


if __name__ == "__main__":
    main()
