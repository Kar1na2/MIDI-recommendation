"""Check the local catalog and optionally recover fully written stem sets."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path

import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "instrumentals/catalog.sqlite"
STEMS = ("bass", "drums", "other")


def main():
    if os.name == "nt":
        raise RuntimeError("Run database checks in WSL while catalog workers are active")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recover-complete-stems", action="store_true")
    parser.add_argument("--backup", action="store_true")
    args = parser.parse_args()
    with sqlite3.connect(DB, timeout=30) as db:
        db.row_factory = sqlite3.Row
        integrity = db.execute("PRAGMA quick_check").fetchall()
        print("SQLite quick_check:", [row[0] for row in integrity], flush=True)
        if len(integrity) != 1 or integrity[0][0] != "ok":
            raise RuntimeError("Catalog integrity check failed")
        rows = [dict(row) for row in db.execute("SELECT * FROM tracks")]
        snapshot = ROOT / "instrumentals/catalog.json"
        if snapshot.exists():
            old_rows = {row["track_id"]: row for row in json.loads(snapshot.read_text(encoding="utf-8"))}
            changed = sum(any(row.get(key) != old_rows.get(row["track_id"], {}).get(key)
                              for key in ("source_url", "sha256", "status")) for row in rows)
            print(f"Rows differing from the latest download snapshot: {changed}", flush=True)
        if args.backup:
            target = ROOT / "instrumentals/.partial/catalog-before-resume.sqlite"
            target.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(target) as backup:
                db.backup(backup)
            print("Saved pre-resume backup", flush=True)
        recovered = 0
        if args.recover_complete_stems:
            for row in rows:
                if row["status"] not in {"downloaded", "separating", "separation_failed"}:
                    continue
                if not row.get("mp3_path") or not row.get("duration"):
                    continue
                mp3 = ROOT / row["mp3_path"]
                files = {part: ROOT / "instrumentals/stems" / row["track_id"] / f"{part}.wav" for part in STEMS}
                if not mp3.is_file() or not all(path.is_file() for path in files.values()):
                    continue
                if any(path.stat().st_mtime < mp3.stat().st_mtime - 1 for path in files.values()):
                    continue
                infos = {part: sf.info(path) for part, path in files.items()}
                if any(abs(float(info.duration) - row["duration"]) > 0.25 or info.subtype != "PCM_16" or info.channels != 1 or info.samplerate != 22050 for info in infos.values()):
                    continue
                assets = [(row["track_id"], part, files[part].relative_to(ROOT).as_posix(), float(infos[part].duration), files[part].stat().st_size) for part in STEMS]
                db.executemany("INSERT INTO assets(track_id,part,path,duration,bytes) VALUES(?,?,?,?,?) ON CONFLICT(track_id,part) DO UPDATE SET path=excluded.path,duration=excluded.duration,bytes=excluded.bytes", assets)
                db.execute("UPDATE tracks SET status='separated',reason=NULL WHERE track_id=?", (row["track_id"],))
                recovered += 1
                print(f"Recovered completed stem set: {row['artist']} — {row['title']}", flush=True)
        print(f"Recovered stem sets: {recovered}", flush=True)
        print("Status counts:", dict(db.execute("SELECT status,count(*) FROM tracks GROUP BY status").fetchall()), flush=True)
        duplicate_rows = db.execute("SELECT sha256,count(*) FROM tracks WHERE sha256 IS NOT NULL GROUP BY sha256 HAVING count(*)>1").fetchall()
        print(f"Exact-byte duplicate audio groups: {len(duplicate_rows)}; reusable duplicate tracks: {sum(row[1]-1 for row in duplicate_rows)}", flush=True)


if __name__ == "__main__":
    main()
