"""Validate explicitly aligned training pairs and prevent split leakage.

JSONL entries: audio, midi, group_id, split (train/validation/test),
label_source (synthetic/aligned_performance/manually_verified), license,
complete_annotation (true). Paths are relative to the manifest.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pretty_midi
import soundfile as sf

from .bootstrap_yourmt3 import sha256


def load_manifest(path):
    path = Path(path).resolve()
    entries, groups, hashes = [], {}, {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        prefix = f"{path}:{line_number}"
        if row.get("split") not in {"train", "validation", "test"}:
            raise ValueError(f"{prefix}: invalid split")
        if row.get("label_source") not in {"synthetic", "aligned_performance", "manually_verified"}:
            raise ValueError(f"{prefix}: require aligned reference labels, not automatic predictions")
        if row.get("complete_annotation") is not True or not row.get("license") or not row.get("group_id"):
            raise ValueError(f"{prefix}: require complete_annotation=true, license/provenance, and composition group_id")
        group = row["group_id"].strip().casefold()
        if not group:
            raise ValueError(f"{prefix}: empty composition group_id")
        if groups.setdefault(group, row["split"]) != row["split"]:
            raise ValueError(f"{prefix}: composition appears in multiple splits")
        for kind in ("audio", "midi"):
            file = (path.parent / row[kind]).resolve()
            if not file.is_file():
                raise ValueError(f"{prefix}: missing {kind}: {file}")
            row[kind] = str(file)
            row[kind + "_sha256"] = sha256(file)
            key = (kind, row[kind + "_sha256"])
            if hashes.setdefault(key, row["split"]) != row["split"]:
                raise ValueError(f"{prefix}: identical {kind} content appears in multiple splits")
        info = sf.info(row["audio"])
        midi = pretty_midi.PrettyMIDI(row["midi"])
        if info.duration <= 0 or not any(i.notes for i in midi.instruments):
            raise ValueError(f"{prefix}: empty audio or note labels")
        if midi.get_end_time() > info.duration + .1:
            raise ValueError(f"{prefix}: labels extend beyond the audio; verify clock alignment")
        row["duration"] = info.duration
        entries.append(row)
    if not entries:
        raise ValueError("Manifest has no examples")
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        rows = load_manifest(args.manifest)
    except (ValueError, KeyError) as error:
        parser.error(str(error))
    print(json.dumps({"examples": len(rows), "splits": {s: sum(r["split"] == s for r in rows)
          for s in ("train", "validation", "test")},
          "alignment_status": "declared_by_manifest_not_automatically_proven"}, indent=2))


if __name__ == "__main__":
    main()
