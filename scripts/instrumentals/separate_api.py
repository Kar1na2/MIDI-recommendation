"""Persistent Demucs separator for the instrumental-only database.

The model is loaded once; only compact preview WAVs are written to disk.
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "instrumentals/catalog.sqlite"
MODEL = "htdemucs_ft"
STEMS = ("bass", "drums", "other")


def connect():
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def separate_one(track, separator):
    original, separated = separator.separate_audio_file(ROOT / track["mp3_path"])
    source_sr = int(separator.samplerate)
    duration = original.shape[-1] / source_sr
    if abs(duration - float(track["duration"])) > 0.25:
        raise ValueError(f"Decoded MP3 duration differs from its catalog by more than 250 ms ({duration:.3f}s)")
    if not all(part in separated for part in STEMS):
        raise ValueError("Demucs did not return all three required stems")
    mono_stems = {part: separated[part].detach().cpu().numpy().mean(axis=0) for part in STEMS}
    largest_peak = max(float(np.max(np.abs(wave))) for wave in mono_stems.values())
    scale = min(1.0, 0.99 / max(largest_peak, 1e-8))
    target = ROOT / "instrumentals/stems" / track["track_id"]
    target.mkdir(parents=True, exist_ok=True)
    assets = []
    for part in STEMS:
        wave = resample_poly(mono_stems[part] * scale, 22050, source_sr).astype(np.float32)
        temporary = target / f".{part}.pending.wav"
        sf.write(temporary, wave, 22050, subtype="PCM_16")
        info = sf.info(temporary)
        if info.samplerate != 22050 or info.channels != 1 or info.subtype != "PCM_16":
            raise ValueError(f"Invalid {part} WAV encoding")
        if abs(float(info.duration) - duration) > 0.05:
            raise ValueError(f"{part} WAV no longer aligns with the source")
        final = target / f"{part}.wav"
        temporary.replace(final)
        assets.append((track["track_id"], part, final.relative_to(ROOT).as_posix(), float(info.duration), final.stat().st_size))
    with connect() as db:
        db.executemany("INSERT INTO assets(track_id,part,path,duration,bytes) VALUES(?,?,?,?,?) ON CONFLICT(track_id,part) DO UPDATE SET path=excluded.path,duration=excluded.duration,bytes=excluded.bytes", assets)
        db.execute("UPDATE tracks SET status='separated',reason=NULL,updated_at=? WHERE track_id=?",
                   (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), track["track_id"]))
    return sum(row[4] for row in assets)


def reuse_identical_audio(track):
    if not track.get("sha256"):
        return False
    with connect() as db:
        candidates = db.execute("SELECT track_id FROM tracks WHERE sha256=? AND track_id!=? AND status IN ('separated','ready') ORDER BY track_id", (track["sha256"], track["track_id"])).fetchall()
        for candidate in candidates:
            rows = db.execute("SELECT part,path,duration,bytes FROM assets WHERE track_id=? AND part IN ('bass','drums','other')", (candidate["track_id"],)).fetchall()
            if len(rows) != 3 or not all((ROOT / row["path"]).is_file() and abs(float(row["duration"]) - float(track["duration"])) <= 0.25 for row in rows):
                continue
            db.executemany("INSERT INTO assets(track_id,part,path,duration,bytes) VALUES(?,?,?,?,?) ON CONFLICT(track_id,part) DO UPDATE SET path=excluded.path,duration=excluded.duration,bytes=excluded.bytes",
                           [(track["track_id"], row["part"], row["path"], row["duration"], row["bytes"]) for row in rows])
            db.execute("UPDATE tracks SET status='separated',reason=NULL,updated_at=? WHERE track_id=?", (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), track["track_id"]))
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--device", choices=("cuda", "cpu"))
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--watch-interval", type=int, default=60)
    parser.add_argument("--idle-timeout-minutes", type=int, default=20)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    import torch
    from demucs.api import Separator
    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Loading persistent {MODEL} on {device}", flush=True)
    separator = Separator(model=MODEL, device=device, progress=False, shifts=1, overlap=0.25)
    # htdemucs_ft contains one independently weighted model per stem. Retain
    # every model that contributes to bass/drums/other, omitting only the
    # vocal-only model. The three required outputs retain their original
    # weights. An unused vocal estimate is supplied to avoid a zero divisor
    # inside BagOfModels; it is never written or analyzed.
    from demucs.apply import BagOfModels
    full_model = separator.model
    if isinstance(full_model, BagOfModels):
        desired = [full_model.sources.index(part) for part in STEMS]
        retained = [i for i, weights in enumerate(full_model.weights)
                    if any(weights[index] > 0 for index in desired)]
        if len(retained) < len(full_model.models):
            weights = [list(full_model.weights[index]) for index in retained]
            for source_index in range(len(full_model.sources)):
                if not sum(row[source_index] for row in weights):
                    weights[0][source_index] = 1.0
            separator._model = BagOfModels([full_model.models[index] for index in retained], weights)
            print(f"Using {len(retained)} fine-tuned component models; vocal-only inference is omitted", flush=True)
    # Keep the modest model weights resident rather than copying each component
    # between host RAM and the GPU for every song.
    separator.model.to(device)
    processed = set()
    total = 0
    idle_since = time.monotonic()
    while True:
        statuses = ["downloaded", "separating"] + (["separation_failed"] if args.retry_failed else [])
        with connect() as db:
            tracks = [dict(row) for row in db.execute("SELECT * FROM tracks WHERE status IN (" + ",".join("?" for _ in statuses) + ") ORDER BY artist,title", statuses)
                      if row["track_id"] not in processed]
        if args.limit is not None:
            tracks = tracks[:max(0, args.limit - total)]
        for track in tracks:
            started = time.monotonic()
            try:
                with connect() as db:
                    current = db.execute("SELECT status FROM tracks WHERE track_id=?", (track["track_id"],)).fetchone()
                    if not current or current["status"] not in statuses:
                        processed.add(track["track_id"])
                        continue
                    db.execute("UPDATE tracks SET status='separating' WHERE track_id=?", (track["track_id"],))
                if reuse_identical_audio(track):
                    print(f"[{total + 1}] {track['artist']} — {track['title']}: reused stems from identical MP3 bytes", flush=True)
                else:
                    byte_count = separate_one(track, separator)
                    print(f"[{total + 1}] {track['artist']} — {track['title']}: separated ({byte_count:,} bytes, {time.monotonic() - started:.1f}s)", flush=True)
            except Exception as error:
                reason = f"{type(error).__name__}: {error}"[:2000]
                with connect() as db:
                    db.execute("UPDATE tracks SET status='separation_failed',reason=?,updated_at=? WHERE track_id=?",
                               (reason, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), track["track_id"]))
                print(f"[{total + 1}] {track['artist']} — {track['title']}: FAILED: {reason}", flush=True)
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            processed.add(track["track_id"])
            total += 1
            idle_since = time.monotonic()
        if not args.watch or (args.limit is not None and total >= args.limit):
            break
        with connect() as db:
            upstream = db.execute("SELECT count(*) FROM tracks WHERE status IN ('pending','source_found')").fetchone()[0]
            waiting = db.execute("SELECT count(*) FROM tracks WHERE status IN ('downloaded','separating')").fetchone()[0]
        if waiting:
            continue
        if not upstream:
            break
        if time.monotonic() - idle_since >= args.idle_timeout_minutes * 60:
            print(f"No new downloads for {args.idle_timeout_minutes} minutes; leaving {upstream} unresolved source rows for a later resume.", flush=True)
            break
        print(f"Waiting {args.watch_interval}s for the downloader; {upstream} source rows remain", flush=True)
        time.sleep(max(5, args.watch_interval))


if __name__ == "__main__":
    main()
