"""Export instrumental analyses and truthful preview availability for Pages."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "instrumentals/catalog.sqlite"
DATA = ROOT / "docs/data/instrumentals"
SONG_OUTPUT = DATA / "songs"
UPLOAD_CSV = ROOT / "instrumental-audio-host/upload_manifest.csv"
REPORT = ROOT / "output/analysis/INSTRUMENTAL_PATTERN_ANALYSIS_REPORT.md"
FILES = {"instrumental": ("original.mp3", "audio/mpeg"),
         "bass": ("bass.wav", "audio/wav"), "drums": ("drums.wav", "audio/wav"),
         "other": ("other.wav", "audio/wav")}


def index_original_audio(directory: Path):
    """Map the original library's ``[Spotify ID].mp3`` files by track ID."""
    indexed = {}
    for path in directory.glob("*.mp3"):
        match = re.search(r"\[([A-Za-z0-9]{22})\]\.mp3$", path.name, re.IGNORECASE)
        if match:
            indexed[match.group(1)] = path
    return indexed


def main():
    if os.name == "nt":
        raise RuntimeError("Run catalog tools inside WSL. Opening an active WSL SQLite WAL database from native Windows can interrupt its writers.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-base-url", default=os.environ.get("INSTRUMENTAL_AUDIO_BASE_URL"))
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()
    config_path = DATA / "media-config.json"
    prior_base = json.loads(config_path.read_text(encoding="utf-8")).get("audio_base_url") if config_path.exists() else None
    base = str(args.audio_base_url if args.audio_base_url is not None else (prior_base or "")).strip().rstrip("/")
    SONG_OUTPUT.mkdir(parents=True, exist_ok=True)
    UPLOAD_CSV.parent.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    originals = index_original_audio(ROOT / "liked-audio")
    with sqlite3.connect(DB) as db:
        db.row_factory = sqlite3.Row
        tracks = [dict(row) for row in db.execute("SELECT * FROM tracks ORDER BY artist,title,track_id")]
        analyses = {row["track_id"]: json.loads(row["result_json"]) for row in db.execute("SELECT track_id,result_json FROM analysis")}
        assets = {}
        for row in db.execute("SELECT * FROM assets"):
            assets.setdefault(row["track_id"], {})[row["part"]] = dict(row)

    manifest_rows = []
    upload_rows = []
    for track in tracks:
        track_id = track["track_id"]
        track_assets = assets.get(track_id, {})
        data = analyses.get(track_id)
        original_mp3 = originals.get(track_id)
        instrumental_source = ROOT / track["mp3_path"] if track.get("mp3_path") else None
        instrumental_source_available = bool(instrumental_source and instrumental_source.is_file())
        usable = bool(data) and all(part in track_assets and (ROOT / track_assets[part]["path"]).is_file()
                                    for part in ("bass", "drums", "other"))
        status = track["status"]
        reason = track.get("reason")
        if status == "ready" and not usable:
            status, reason = "asset_missing", "Analysis is recorded, but one or more required local audio files are absent. Rerun separation/export before hosting."
        elif status == "ready" and original_mp3 is None:
            reason = "Pattern data and isolated stems are ready, but the original-song MP3 is missing from liked-audio. The main-view preview cannot be played until that original file is recovered."
        elif status == "ready" and not base:
            reason = "Pattern data is ready. Audio preview links will be added after the separate audio host is deployed and synced."
        elif status == "ready":
            reason = None

        record_path = None
        if data and usable and status == "ready":
            data["views"]["instrumental"]["preview"]["file"] = "original.mp3"
            data["source"] = {"url": track.get("source_url"), "title": track.get("source_title"),
                              "kind": track.get("source_kind"), "metadata_match_score": track.get("match_score")}
            data["audio"] = {}
            for view, (filename, mime) in FILES.items():
                if view == "instrumental":
                    object_key = f"songs/{track_id}/{filename}"
                    if original_mp3 is None:
                        data["audio"][view] = {"url": None, "object_key": object_key,
                                              "mime": mime, "bytes": None, "status": "missing_original"}
                        continue
                    local = original_mp3
                else:
                    item = track_assets[view]
                    local = ROOT / item["path"]
                    object_key = f"songs/{track_id}/{filename}"
                size = local.stat().st_size
                data["audio"][view] = {"url": f"{base}/v1/{track_id}/{filename}" if base else None,
                                      "object_key": object_key, "mime": mime,
                                      "bytes": size, "status": "ready" if base else "awaiting_host"}
                import hashlib
                digest = hashlib.sha256()
                with local.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                upload_rows.append((track_id, object_key, local.relative_to(ROOT).as_posix(), mime, size, digest.hexdigest()))
            target = SONG_OUTPUT / f"{track_id}.json"
            target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            record_path = f"data/instrumentals/songs/{track_id}.json"
        manifest_rows.append({"track_id": track_id, "title": track["title"], "artist": track["artist"],
                              "duration": round(float(track.get("duration") or track.get("duration_expected") or 0), 2),
                              "status": status, "reason": reason, "analysis_url": record_path,
                              "instrumental_source_available": instrumental_source_available,
                              "original_audio_available": original_mp3 is not None,
                              "available_preview_views": (3 + int(original_mp3 is not None)) if usable and status == "ready" else 0,
                              "audio_ready": bool(usable and base and original_mp3 is not None),
                              "audio_host_configured": bool(base)})

    (DATA / "media-config.json").write_text(json.dumps({"audio_base_url": base or None}, indent=2), encoding="utf-8")
    manifest = {"generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                "total_tracks": len(manifest_rows), "audio_base_url": base or None, "tracks": manifest_rows}
    (DATA / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    with UPLOAD_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("track_id", "object_key", "local_path", "content_type", "bytes", "sha256"))
        writer.writerows(upload_rows)

    counts = Counter(row["status"] for row in manifest_rows)
    print(f"Exported {len(manifest_rows)} tracks; statuses={dict(counts)}; R2 upload objects={len(upload_rows)} ({sum(x[4] for x in upload_rows):,} bytes); audio host={'configured' if base else 'not configured'}")
    if args.write_report:
        from scripts.instrumentals.report import write_report
        report_analyses = {row["track_id"]: analyses[row["track_id"]] for row in manifest_rows if row["status"] == "ready" and row["track_id"] in analyses}
        write_report(manifest_rows, report_analyses, tracks, assets, upload_rows, REPORT)
        print(f"Wrote {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
