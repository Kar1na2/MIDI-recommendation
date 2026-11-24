"""Separate downloaded instrumental files into bass, drums, and other stems.

Outputs stay under instrumentals/stems and are never mixed with the original
song corpus. Demucs' vocal residual is intentionally not published.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "instrumentals"
DB = LIBRARY / "catalog.sqlite"
MODEL = "htdemucs_ft"
PARTS = ("bass", "drums", "other")


def db_connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    return db


def finalize(track, produced):
    if not all((produced / f"{part}.wav").is_file() for part in (*PARTS, "vocals")):
        raise RuntimeError("Demucs returned without the complete four-stem output")

    target = LIBRARY / "stems" / track["track_id"]
    target.mkdir(parents=True, exist_ok=True)
    assets = []
    source_duration = float(track["duration"] or 0)
    for part in PARTS:
        wav = produced / f"{part}.wav"
        info = sf.info(wav)
        duration = float(info.duration)
        if info.frames < 1 or not 0.9 * source_duration <= duration <= 1.1 * source_duration:
            raise RuntimeError(f"{part} duration {duration:.2f}s does not align with MP3 {source_duration:.2f}s")
        final = target / f"{part}.wav"
        temporary = target / f".{part}.preview.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-ar", "22050", "-ac", "1", "-c:a", "pcm_s16le", str(temporary)], check=True)
        compact_info = sf.info(temporary)
        if compact_info.samplerate != 22050 or compact_info.channels != 1 or compact_info.subtype != "PCM_16":
            raise RuntimeError(f"{part} preview WAV did not convert to mono 22.05 kHz PCM-16")
        if abs(compact_info.duration - duration) > 0.05:
            raise RuntimeError(f"{part} preview conversion changed duration by more than 50 ms")
        temporary.replace(final)
        duration = float(compact_info.duration)
        assets.append((track["track_id"], part, final.relative_to(ROOT).as_posix(), duration, final.stat().st_size))

    with db_connect() as db:
        db.executemany("INSERT INTO assets(track_id,part,path,duration,bytes) VALUES(?,?,?,?,?) ON CONFLICT(track_id,part) DO UPDATE SET path=excluded.path,duration=excluded.duration,bytes=excluded.bytes", assets)
        missing = db.execute("SELECT count(*) FROM assets WHERE track_id=? AND part IN ('bass','drums','other')", (track["track_id"],)).fetchone()[0]
        if missing == 3:
            db.execute("UPDATE tracks SET status='separated',reason=NULL,updated_at=? WHERE track_id=?", (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), track["track_id"]))
    shutil.rmtree(produced, ignore_errors=True)
    return sum(row[4] for row in assets)


def separate_batch(tracks, device):
    work = LIBRARY / ".partial" / "separation"
    for track in tracks:
        produced = work / MODEL / track["track_id"]
        if produced.exists():
            shutil.rmtree(produced)
    work.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "demucs", "-n", MODEL, "-d", device,
               "-o", str(work), *(str(ROOT / track["mp3_path"]) for track in tracks)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Demucs failed")[-2500:])
    outcomes = []
    for track in tracks:
        produced = work / MODEL / track["track_id"]
        outcomes.append((track, finalize(track, produced)))
    return outcomes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--device", choices=("cuda", "cpu"), default=None)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--watch", action="store_true", help="Wait for more downloads and separate them as they appear")
    parser.add_argument("--watch-interval", type=int, default=60)
    parser.add_argument("--idle-timeout-minutes", type=int, default=20)
    parser.add_argument("--compress-existing", action="store_true",
                        help="Convert already separated WAVs to compact mono PCM-16 previews")
    args = parser.parse_args()
    import torch
    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    if args.compress_existing:
        total = 0
        with db_connect() as db:
            rows = db.execute("SELECT track_id,part,path FROM assets WHERE part IN ('bass','drums','other') ORDER BY track_id,part").fetchall()
            for row in rows:
                wav = ROOT / row["path"]
                temporary = wav.with_name("." + wav.stem + ".preview.wav")
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-ar", "22050", "-ac", "1", "-c:a", "pcm_s16le", str(temporary)], check=True)
                info = sf.info(temporary)
                if info.samplerate != 22050 or info.channels != 1 or info.subtype != "PCM_16":
                    raise RuntimeError(f"Invalid compressed preview: {temporary}")
                temporary.replace(wav)
                db.execute("UPDATE assets SET duration=?,bytes=? WHERE track_id=? AND part=?", (float(info.duration), wav.stat().st_size, row["track_id"], row["part"]))
                total += wav.stat().st_size
                print(f"{row['track_id']} {row['part']}: {wav.stat().st_size:,} bytes", flush=True)
        print(f"Compressed {len(rows)} WAVs: {total:,} total bytes", flush=True)
        return
    def pending_tracks():
        with db_connect() as db:
            rows = db.execute("SELECT * FROM tracks WHERE status='downloaded' ORDER BY artist,title").fetchall()
            tracks = [dict(row) for row in rows]
            if args.retry_failed:
                tracks.extend(dict(row) for row in db.execute("SELECT * FROM tracks WHERE status='separation_failed' ORDER BY artist,title"))
        return tracks

    def mark_failure(track, error):
        reason = f"{type(error).__name__}: {error}"[:2500]
        with db_connect() as db:
            db.execute("UPDATE tracks SET status='separation_failed',reason=?,updated_at=? WHERE track_id=?",
                       (reason, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), track["track_id"]))
        print(f"{track['artist']} — {track['title']}: FAILED: {reason}", flush=True)

    processed = set()
    idle_since = time.monotonic()
    total = 0
    while True:
        tracks = [track for track in pending_tracks() if track["track_id"] not in processed]
        if args.limit is not None:
            tracks = tracks[:max(0, args.limit - total)]
        if tracks:
            print(f"{len(tracks)} instrumentals queued for {MODEL} on {device}", flush=True)
            idle_since = time.monotonic()
            batch_size = max(1, min(args.batch_size, 16))
            for start in range(0, len(tracks), batch_size):
                batch = tracks[start:start + batch_size]
                try:
                    outcomes = separate_batch(batch, device)
                    for track, byte_count in outcomes:
                        processed.add(track["track_id"])
                        total += 1
                        print(f"[{total}] {track['artist']} — {track['title']}: separated ({byte_count:,} bytes)", flush=True)
                except Exception as batch_error:
                    print(f"Batch failed ({type(batch_error).__name__}); retrying its {len(batch)} song(s) individually", flush=True)
                    for track in batch:
                        try:
                            source = ROOT / track["mp3_path"]
                            work = LIBRARY / ".partial" / "separation"
                            work.mkdir(parents=True, exist_ok=True)
                            result = subprocess.run([sys.executable, "-m", "demucs", "-n", MODEL, "-d", device,
                                                     "-o", str(work), str(source)], capture_output=True, text=True)
                            if result.returncode:
                                raise RuntimeError((result.stderr or result.stdout or str(batch_error))[-2500:])
                            byte_count = finalize(track, work / MODEL / track["track_id"])
                            print(f"[{total + 1}] {track['artist']} — {track['title']}: separated ({byte_count:,} bytes)", flush=True)
                        except Exception as error:
                            mark_failure(track, error)
                        processed.add(track["track_id"])
                        total += 1
            if not args.watch or (args.limit is not None and total >= args.limit):
                break
            continue
        if not args.watch or (args.limit is not None and total >= args.limit):
            break
        with db_connect() as db:
            unfinished = db.execute("SELECT count(*) FROM tracks WHERE status IN ('pending','source_found')").fetchone()[0]
        if unfinished == 0:
            break
        if time.monotonic() - idle_since >= args.idle_timeout_minutes * 60:
            print(f"No completed downloads for {args.idle_timeout_minutes} minutes; leaving {unfinished} unresolved search/download rows for a later resume.", flush=True)
            break
        print(f"Waiting {args.watch_interval}s for the downloader; {unfinished} source/search rows are still active", flush=True)
        time.sleep(max(5, args.watch_interval))


if __name__ == "__main__":
    main()
