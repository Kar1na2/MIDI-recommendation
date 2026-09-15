#!/usr/bin/env python3
"""
Phase 4 audio export: re-encodes the 75 unique source mp3s (audio/ +
k-indie/, deduped) to a modest web bitrate into docs/audio/<song_id>.mp3,
then reports the total resulting folder size (per the brief - determines
whether the repo stays comfortably within GitHub Pages' practical size
expectations, or needs a lower bitrate).

Read-only against audio/ and k-indie/ - only writes under docs/audio/, per
the Phase 4 isolation rule.

Usage:
    uv run python -m scripts.webexport.convert_audio [--bitrate 128k] [--workers N]
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AUDIO_DIRS = ["audio", "k-indie"]
SONG_ID_MAP_CSV = "docs/data/_song_id_map.csv"
OUT_DIR = "docs/audio"


def p(*parts):
    return os.path.join(REPO_ROOT, *parts)


def find_source_audio(title: str) -> str:
    for d in AUDIO_DIRS:
        candidate = p(d, title + ".mp3")
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(title)


def load_song_id_map() -> list[tuple[str, str]]:
    with open(p(SONG_ID_MAP_CSV), newline="", encoding="utf-8") as f:
        return [(row["song_id"], row["title"]) for row in csv.DictReader(f)]


def encode_one(song_id: str, title: str, bitrate: str) -> tuple[str, bool, str]:
    src = find_source_audio(title)
    dst = p(OUT_DIR, f"{song_id}.mp3")
    result = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", src,
         "-vn", "-map_metadata", "-1", "-codec:a", "libmp3lame", "-b:a", bitrate, "-ar", "44100",
         dst],
        capture_output=True, text=True,
    )
    return song_id, result.returncode == 0, result.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bitrate", default="128k")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--limit", type=int, default=None, help="smoke-test: only convert the first N songs")
    args = ap.parse_args()

    os.makedirs(p(OUT_DIR), exist_ok=True)
    entries = load_song_id_map()
    if args.limit:
        entries = entries[: args.limit]
    print(f"Converting {len(entries)} songs to {args.bitrate} mp3 with {args.workers} workers...")

    failures = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(encode_one, sid, title, args.bitrate): sid for sid, title in entries}
        for fut in as_completed(futures):
            song_id, ok, err = fut.result()
            done += 1
            if not ok:
                failures.append((song_id, err))
                print(f"[{done}/{len(entries)}] FAILED {song_id}: {err.strip()[:200]}")
            elif done % 10 == 0 or done == len(entries):
                print(f"[{done}/{len(entries)}] ...")

    if failures:
        print(f"\n{len(failures)} FAILURES:")
        for sid, err in failures:
            print(f"  {sid}: {err.strip()[:300]}")
        return 1

    total_bytes = sum(
        os.path.getsize(p(OUT_DIR, f))
        for f in os.listdir(p(OUT_DIR)) if f.endswith(".mp3")
    )
    n_files = len([f for f in os.listdir(p(OUT_DIR)) if f.endswith(".mp3")])
    print(f"\nDone. {n_files} files in docs/audio/, total size "
          f"{total_bytes/1024/1024:.1f} MB ({total_bytes/1024/1024/1024:.3f} GB).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
