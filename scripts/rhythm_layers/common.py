from __future__ import annotations

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output/analysis/rhythm_layers"
PRIVATE = OUT / "private"
PREVIEW = ROOT / "preview/rhythm-layers"
PARTS = ("bass", "drums", "other")
SAMPLES = ("6QIY4JAyzPH6UuFsyndaPs", "7FT7a5DV7Mk6Mc9ZRJnX70", "6rQCPgrLJMQavladOGpwZt")


def read_json(path, default=None):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).is_file() else default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def inventory():
    """Use ID-indexed exports and files; never open the live SQLite catalog."""
    manifest = read_json(ROOT / "docs/data/instrumentals/manifest.json")
    originals = {}
    for directory in ("audio", "k-indie", "liked-audio"):
        for path in (ROOT / directory).glob("*.mp3"):
            match = re.search(r"\[([A-Za-z0-9]{22})\]$", path.stem)
            if match:
                originals[match[1]] = path.relative_to(ROOT).as_posix()
    # The first 75 recordings predate ID-based filenames. The ingestion log
    # explicitly links each Spotify ID to its existing local recording.
    downloads = read_json(ROOT / "liked-audio/download_manifest.json", {})
    for tid, record in downloads.items():
        reference = record.get("duplicate_of")
        if tid not in originals and reference and (ROOT / reference).is_file():
            originals[tid] = Path(reference).as_posix()
    known_hashes = {record["sha256"]: originals[tid] for tid, record in downloads.items()
                    if record.get("sha256") and tid in originals}
    for tid, record in downloads.items():
        if tid not in originals and record.get("sha256") in known_hashes:
            originals[tid] = known_hashes[record["sha256"]]
    stem_dirs = {}
    for path in (ROOT / "stems").iterdir():
        match = re.search(r"\[([A-Za-z0-9]{22})\]$", path.name)
        if path.is_dir() and match:
            stem_dirs[match[1]] = path
    dedicated = {}
    with (ROOT / "instrumental-audio-host/upload_manifest.csv").open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            part = Path(row["object_key"]).stem
            if part in PARTS:
                dedicated.setdefault(row["track_id"], {})[part] = row["local_path"]
    rows = []
    for track in manifest["tracks"]:
        tid = track["track_id"]
        source = stem_dirs.get(tid)
        if source is None and originals.get(tid):
            candidate = ROOT / "stems" / Path(originals[tid]).stem
            source = candidate if candidate.is_dir() else None
        original_parts = {p: (source / f"{p}.wav").relative_to(ROOT).as_posix()
                          for p in (*PARTS, "vocals") if source and (source / f"{p}.wav").is_file()}
        parts = dedicated.get(tid, {})
        available = len(parts) == 3 and all((ROOT / p).is_file() for p in parts.values())
        rows.append({**track, "original_mp3": originals.get(tid),
                     "original_source_status": downloads.get(tid, {}).get("status"),
                     "original_source_reason": downloads.get(tid, {}).get("error"),
                     "original_parts": original_parts,
                     "parts": parts if available else {p: original_parts[p] for p in PARTS if p in original_parts},
                     "pattern_source": "dedicated_instrumental" if available else "separated_original",
                     "instrumental_recording": bool(re.search(r"\binstrumental\b", track["title"], re.I))})
    rows.sort(key=lambda x: (x["track_id"] not in SAMPLES, SAMPLES.index(x["track_id"]) if x["track_id"] in SAMPLES else 99, x["artist"], x["title"]))
    return rows
