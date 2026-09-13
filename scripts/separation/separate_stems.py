#!/usr/bin/env python3
"""
Batch-run Demucs (htdemucs_ft, 4-stem) over every mp3 in audio/ and k-indie/,
writing stems to stems/<song_name>/{drums,bass,other,vocals}.wav.

Resumable: a song is skipped if stems/<song_name>/ already has all four
stem files, so a re-run after a crash only redoes what's missing.

Failures are logged to stems/_failures.log (one line per failure) instead
of aborting the batch.

Uses the GPU if available, falls back to CPU with a warning.

Usage (from the repo root; ARGS is forwarded as CLI flags):
    make separate-stems                              # process everything
    make separate-stems ARGS="--limit 3"              # process first 3 unfinished songs (smoke test)
    make separate-stems ARGS="--dry-run"              # show what would run, do nothing
    make separate-stems ARGS="--retry-failed"         # clear the failure log and retry those songs too
"""
import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

MODEL = "htdemucs_ft"
STEMS = ("drums", "bass", "other", "vocals")
SOURCE_DIRS = ["audio", "k-indie"]
OUTPUT_ROOT = Path("stems")
FAILURE_LOG = OUTPUT_ROOT / "_failures.log"
DEMUCS_TMP = OUTPUT_ROOT / ".demucs_tmp"  # scratch dir demucs writes into before we move results into place


def gpu_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def collect_songs():
    """Sorted list of (song_name, source_path), deduped by song_name (first match wins).

    audio/ and k-indie/ share a bunch of identical files (same song, two folders);
    we only need to separate each unique song once. Dedup + the resumability check
    below both key off song_name, so a duplicate is naturally skipped.
    """
    seen = {}
    for d in SOURCE_DIRS:
        folder = Path(d)
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.mp3")):
            seen.setdefault(path.stem, path)
    return sorted(seen.items())


def is_complete(song_name: str) -> bool:
    out_dir = OUTPUT_ROOT / song_name
    return all((out_dir / f"{s}.wav").exists() for s in STEMS)


def log_failure(song_name: str, path: Path, error: str):
    FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with FAILURE_LOG.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{song_name}\t{path}\t{error}\n")


def separate_one(song_name: str, path: Path, device: str) -> bool:
    """Run demucs on a single file; return True on success."""
    DEMUCS_TMP.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "demucs",
        "-n", MODEL,
        "-d", device,
        "-o", str(DEMUCS_TMP),
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    produced = DEMUCS_TMP / MODEL / path.stem

    if result.returncode != 0 or not all((produced / f"{s}.wav").exists() for s in STEMS):
        err = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown error"
        if result.returncode == 0:
            err = "demucs exited 0 but stem files are missing: " + err
        log_failure(song_name, path, err)
        shutil.rmtree(produced, ignore_errors=True)
        return False

    final_out = OUTPUT_ROOT / song_name
    if final_out.exists():
        shutil.rmtree(final_out)
    shutil.move(str(produced), str(final_out))
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None,
                         help="Only process the first N not-yet-complete songs (for smoke testing)")
    parser.add_argument("--dry-run", action="store_true",
                         help="List what would be processed, without running demucs")
    parser.add_argument("--retry-failed", action="store_true",
                         help="Clear the failure log first so previously-failed songs are attempted again")
    args = parser.parse_args()

    if args.retry_failed and FAILURE_LOG.exists():
        FAILURE_LOG.unlink()

    device = "cuda" if gpu_available() else "cpu"
    if device == "cpu":
        print("WARNING: no GPU detected, falling back to CPU. This will be much slower.", file=sys.stderr)
    else:
        print(f"Using GPU (device={device}).")

    songs = collect_songs()
    todo = [(name, path) for name, path in songs if not is_complete(name)]
    print(f"{len(songs)} unique songs found, {len(songs) - len(todo)} already complete, {len(todo)} to process.")

    if args.limit:
        todo = todo[:args.limit]

    if args.dry_run:
        for name, path in todo:
            print(f"WOULD PROCESS: {name}  <-  {path}")
        return

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    ok, failed = 0, 0
    for i, (name, path) in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {name} ...", flush=True)
        if separate_one(name, path, device):
            ok += 1
        else:
            failed += 1
            print(f"  FAILED (see {FAILURE_LOG})")

    shutil.rmtree(DEMUCS_TMP, ignore_errors=True)
    print(f"Done. {ok} succeeded, {failed} failed.")
    if failed:
        print(f"See {FAILURE_LOG} for details. Re-run this script to retry (or pass --retry-failed).")


if __name__ == "__main__":
    main()
