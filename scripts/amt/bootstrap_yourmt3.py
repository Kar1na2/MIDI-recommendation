"""Fetch one pinned public YourMT3+ inference/training source and checkpoint.

No upstream web app, authentication examples, or datasets are downloaded.
Run in an isolated environment; see AMT_RESEARCH.md.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import urllib.request
import time

REPO = "https://huggingface.co/spaces/mimbres/YourMT3"
REVISION = "5e66c1ea173a8186e0d20432b841d3180cc015b5"
EXPERIMENT = "mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops"
CHECKPOINT = f"amt/logs/2024/{EXPERIMENT}/checkpoints/last.ckpt"
DEFAULT_ROOT = Path("output/amt/yourmt3")


def sha256(path):
    with Path(path).open("rb") as fp:
        return hashlib.file_digest(fp, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    with urllib.request.urlopen(
        f"https://huggingface.co/api/spaces/mimbres/YourMT3/tree/{REVISION}/amt?recursive=true&limit=1000", timeout=60
    ) as response:
        entries = json.load(response)
    selected = [e for e in entries if e["type"] == "file" and
                ((e["path"].startswith("amt/src/") and "/extras/" not in e["path"] and
                  e["path"].endswith((".py", ".md", ".txt"))) or e["path"] == CHECKPOINT)]
    selected.append({"path": "model_helper.py"})
    args.root.mkdir(parents=True, exist_ok=True)

    def download(entry):
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid remote file path")
        target = args.root / relative
        expected = entry.get("lfs", {}).get("oid")
        if target.exists() and (entry.get("size") is None or target.stat().st_size == entry["size"]):
            if expected is None or sha256(target) == expected:
                return {"path": entry["path"], "sha256": sha256(target)}
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".partial")
        print(f"Downloading {entry['path']}", flush=True)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(f"{REPO}/resolve/{REVISION}/{entry['path']}", timeout=30) as response, temporary.open("wb") as fp:
                    while block := response.read(1024 * 1024):
                        fp.write(block)
                break
            except (OSError, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(1 + attempt)
        if entry.get("size") is not None and temporary.stat().st_size != entry["size"]:
            raise ValueError(f"Incomplete download: {entry['path']}")
        digest = sha256(temporary)
        if expected is not None and digest != expected:
            raise ValueError(f"Checkpoint checksum mismatch: {entry['path']}")
        temporary.replace(target)
        return {"path": entry["path"], "sha256": digest}

    with ThreadPoolExecutor(max_workers=6) as pool:
        files = list(pool.map(download, selected))
    (args.root / "provenance.json").write_text(json.dumps(
        {"repository": REPO, "revision": REVISION, "checkpoint": CHECKPOINT,
         "files": files}, indent=2), encoding="utf-8")
    print(f"Ready: {args.root} ({len(files)} files)")


if __name__ == "__main__":
    main()
