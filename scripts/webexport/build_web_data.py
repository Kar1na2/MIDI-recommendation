#!/usr/bin/env python3
"""
Phase 4 data-layer export: reads Phase 1-3 outputs (read-only - per the
brief's isolation rule, nothing under output/, chords/, merged/, audio/, or
k-indie/ is modified) and writes the flat-file "database" a static
GitHub-Pages frontend can fetch: docs/data/manifest.json,
docs/data/sequences.json, docs/data/songs/<song_id>.json.

Does NOT touch audio (see scripts/webexport/convert_audio.py for the
128kbps re-encode + folder-size report).

Usage:
    uv run python -m scripts.webexport.build_web_data
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import unicodedata
from collections import defaultdict

from scripts.webexport.chord_display_names import chord_display_name

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AUDIO_DIRS = ["audio", "k-indie"]
CHORD_SEQUENCES_CSV = "output/analysis/chord_sequences.csv"
NOTE_SEQUENCES_CSV = "output/analysis/note_sequences.csv"
NOTE_EVENTS_CSV = "output/note_events_other.csv"

WEB_DATA_DIR = "docs/data"
SONGS_DIR = os.path.join(WEB_DATA_DIR, "songs")


def p(*parts):
    return os.path.join(REPO_ROOT, *parts)


# --- 1. song identity: title -> stable slug --------------------------------

def discover_titles() -> list[str]:
    """One title per unique filename stem across audio/ + k-indie/ (23 files
    are byte-duplicates present in both dirs - see PIPELINE_MAP.md's "75
    unique songs, 68 in audio/ + 30 in k-indie/, deduped"). Matches the
    'song' column values used throughout output/analysis/*.csv exactly
    (verified 1:1 before writing this script)."""
    stems = set()
    for d in AUDIO_DIRS:
        dirpath = p(d)
        if not os.path.isdir(dirpath):
            continue
        for fn in os.listdir(dirpath):
            if fn.lower().endswith(".mp3"):
                stems.add(os.path.splitext(fn)[0])
    return sorted(stems)


def find_source_audio(title: str) -> str:
    """Prefer audio/ over k-indie/ when a title exists in both (same file,
    confirmed byte-identical duplicates per PIPELINE_MAP.md)."""
    for d in AUDIO_DIRS:
        candidate = p(d, title + ".mp3")
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(title)


def slugify(title: str) -> str:
    """ASCII, URL/filename-safe slug. Strips non-ASCII (many titles are
    Korean/Japanese/Chinese) rather than percent-encoding or transliterating
    - collisions after stripping are resolved with a numeric suffix below,
    so this doesn't need to be perfectly unique on its own."""
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    s = ascii_title.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if not s:
        s = "song"
    return s[:60].strip("-") or "song"


def build_song_ids(titles: list[str]) -> dict[str, str]:
    """title -> song_id, stable (sorted-title order) and collision-safe."""
    used = {}
    result = {}
    for title in titles:
        base = slugify(title)
        slug = base
        n = 2
        while slug in used:
            slug = f"{base}-{n}"
            n += 1
        used[slug] = title
        result[title] = slug
    return result


# --- 2. duration via ffprobe -------------------------------------------------

def probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return round(float(out.stdout.strip()), 3)


# --- 3. load Phase 3A/3B sequence CSVs --------------------------------------

def load_chord_sequences() -> dict[str, list[dict]]:
    by_song = defaultdict(list)
    with open(p(CHORD_SEQUENCES_CSV), newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_song[row["song"]].append(row)
    for song in by_song:
        by_song[song].sort(key=lambda r: int(r["segment_index"]))
    return by_song


def load_note_sequences() -> dict[str, list[dict]]:
    by_song = defaultdict(list)
    with open(p(NOTE_SEQUENCES_CSV), newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_song[row["song"]].append(row)
    for song in by_song:
        by_song[song].sort(key=lambda r: int(r["note_index"]))
    return by_song


def load_note_events() -> dict[str, list[dict]]:
    by_song = defaultdict(list)
    with open(p(NOTE_EVENTS_CSV), newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_song[row["song"]].append(row)
    for song in by_song:
        by_song[song].sort(key=lambda r: float(r["onset"]))
    return by_song


# --- 4. main -----------------------------------------------------------------

def main():
    titles = discover_titles()
    print(f"Discovered {len(titles)} unique song titles.")
    song_ids = build_song_ids(titles)
    assert len(set(song_ids.values())) == len(titles), "slug collision escaped dedup"

    print("Probing durations via ffprobe...")
    durations = {}
    for title in titles:
        durations[title] = probe_duration(find_source_audio(title))

    chord_seqs = load_chord_sequences()
    note_seqs = load_note_sequences()
    note_events = load_note_events()
    for title in titles:
        assert title in chord_seqs, f"missing chord_sequences rows for {title!r}"
        assert title in note_seqs, f"missing note_sequences rows for {title!r}"
        assert title in note_events, f"missing note_events rows for {title!r}"

    os.makedirs(p(SONGS_DIR), exist_ok=True)

    # -- manifest.json --
    manifest = [
        {"song_id": song_ids[title], "title": title, "duration": durations[title]}
        for title in titles
    ]
    manifest.sort(key=lambda e: e["song_id"])
    with open(p(WEB_DATA_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, separators=(",", ":"))

    # -- sequences.json (compact, corpus-wide, matching-engine input) --
    # canonical_chord_id = forte_class (Phase 3A canonicalization). root_pc is
    # included alongside it even though the brief's field list only names
    # (start, end, canonical_chord_id) - Phase 3A's own validation found
    # quality-only (forte_class alone) alignment surfaces NO significant
    # matches at all (lib/chord_similarity.py's module docstring, section
    # "Root-aware scoring"); root_pc is required for the matching engine to
    # reuse the scoring approach that actually validated, so it's kept here
    # rather than re-deriving it some other way at query time.
    sequences = {}
    for title in titles:
        sid = song_ids[title]
        chords = [
            {"start": float(r["start"]), "end": float(r["end"]),
             "canonical_chord_id": r["canonical_chord_id"], "root_pc": int(r["root_pc"])}
            for r in chord_seqs[title]
        ]
        notes = [
            {"onset": float(r["onset"]),
             "interval_from_prev": (int(r["interval_from_prev"]) if r["interval_from_prev"] != "" else None)}
            for r in note_seqs[title]
        ]
        sequences[sid] = {"chords": chords, "notes": notes}
    with open(p(WEB_DATA_DIR, "sequences.json"), "w", encoding="utf-8") as f:
        json.dump(sequences, f, ensure_ascii=False, separators=(",", ":"))

    # -- songs/<song_id>.json (lazy-loaded per-song detail) --
    for title in titles:
        sid = song_ids[title]
        chords_full = [
            {"start": float(r["start"]), "end": float(r["end"]),
             "canonical_chord_id": r["canonical_chord_id"],
             "chord_name": chord_display_name(r["canonical_chord_id"], int(r["root_pc"]))}
            for r in chord_seqs[title]
        ]
        notes_full = [
            {"onset": float(r["onset"]), "offset": float(r["offset"]),
             "pitch": int(r["pitch"]), "velocity": int(r["velocity"])}
            for r in note_events[title]
        ]
        song_doc = {
            "song_id": sid,
            "title": title,
            "duration": durations[title],
            "audio_url": f"audio/{sid}.mp3",
            "notes": notes_full,
            "chords": chords_full,
        }
        with open(p(SONGS_DIR, f"{sid}.json"), "w", encoding="utf-8") as f:
            json.dump(song_doc, f, ensure_ascii=False, separators=(",", ":"))

    print(f"Wrote manifest.json ({len(manifest)} songs), sequences.json, "
          f"and {len(titles)} songs/<id>.json files.")

    # -- report sizes --
    def sz(*parts):
        return os.path.getsize(p(*parts))

    manifest_kb = sz(WEB_DATA_DIR, "manifest.json") / 1024
    sequences_kb = sz(WEB_DATA_DIR, "sequences.json") / 1024
    songs_total_kb = sum(sz(SONGS_DIR, f"{sid}.json") for sid in song_ids.values()) / 1024
    print(f"manifest.json: {manifest_kb:.1f} KB")
    print(f"sequences.json: {sequences_kb:.1f} KB")
    print(f"songs/*.json total: {songs_total_kb:.1f} KB ({songs_total_kb/1024:.2f} MB)")

    with open(p(WEB_DATA_DIR, "_song_id_map.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["song_id", "title"])
        for title in titles:
            w.writerow([song_ids[title], title])
    print("Wrote docs/data/_song_id_map.csv (build-debug aid, not a deliverable).")


if __name__ == "__main__":
    sys.exit(main())
