#!/usr/bin/env python3
"""
Pick a random sample of songs for hand-labeled evaluation and scaffold empty
ground-truth annotation templates for each one.

Samples from songs that have a merged/<song>.mid (i.e. have been through the
full separate -> transcribe -> merge pipeline), since evaluate_transcription.py
scores against that merged output.

Writes:
    ground_truth/<song_name>/{other,bass,drums}.csv  - empty templates (header only)
    ground_truth/_sample.txt                          - the chosen songs + the seed used

Re-running is safe: songs already in the sample are left alone (existing
ground_truth/<song>/ dirs and any annotations in them are never touched or
overwritten) and excluded from the pool being drawn from to fill out the
rest of the requested sample size, so `--num-songs` acts as a floor you can
raise later without losing labeling work already done.

See README "Hand-labeling ground truth" for the annotation workflow.

Usage (from the repo root; ARGS is forwarded as CLI flags):
    make select-eval-sample                       # sample 8 songs (default), seed 0
    make select-eval-sample ARGS="-n 5 --seed 42"
    make select-eval-sample ARGS="--list"          # show current sample + fill-in progress
"""
import argparse
import random
from pathlib import Path

from lib.gt_format import load_ground_truth, write_template

MERGED_ROOT = Path("merged")
GT_ROOT = Path("ground_truth")
SAMPLE_MANIFEST = GT_ROOT / "_sample.txt"
STEMS = ("other", "bass", "drums")


def existing_sample():
    if not SAMPLE_MANIFEST.exists():
        return []
    return [line.strip() for line in SAMPLE_MANIFEST.read_text().splitlines()
            if line.strip() and not line.startswith("#")]


def fill_status(song_name: str):
    """Which of the 3 stem CSVs have at least one annotated row."""
    filled = []
    for stem in STEMS:
        if load_ground_truth(GT_ROOT / song_name / f"{stem}.csv"):
            filled.append(stem)
    return filled


def print_list(songs):
    if not songs:
        print("No sample selected yet. Run without --list to pick one.")
        return
    print(f"Current sample ({len(songs)} songs):")
    for song in songs:
        filled = fill_status(song)
        missing = [s for s in STEMS if s not in filled]
        status = "ALL STEMS FILLED" if not missing else f"missing: {', '.join(missing)}"
        print(f"  {song}\n    filled: {', '.join(filled) or '(none yet)'}  -  {status}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-n", "--num-songs", type=int, default=8,
                         help="Target sample size (default 8). Acts as a floor: songs already "
                              "sampled count toward it, only the shortfall is newly picked.")
    parser.add_argument("--seed", type=int, default=0, help="Random seed, for a reproducible sample")
    parser.add_argument("--list", action="store_true", help="Show the current sample and fill-in progress, and exit")
    args = parser.parse_args()

    current = existing_sample()

    if args.list:
        print_list(current)
        return

    if not MERGED_ROOT.is_dir() or not any(MERGED_ROOT.glob("*.mid")):
        print(f"No merged songs found under {MERGED_ROOT}/ - run the pipeline (through merge_stems.py) first.")
        return

    available = sorted(p.stem for p in MERGED_ROOT.glob("*.mid"))
    pool = [s for s in available if s not in current]
    need = max(0, args.num_songs - len(current))

    if need == 0:
        print(f"Sample already has {len(current)} song(s) (>= requested {args.num_songs}).")
        print_list(current)
        return

    if need > len(pool):
        print(f"WARNING: only {len(pool)} more merged song(s) available to draw from "
              f"({len(available)} merged total, {len(current)} already sampled) - "
              f"sampling all of them instead of {need}. Run the pipeline on more songs "
              f"and re-run this script to grow the sample later.")
        need = len(pool)

    rng = random.Random(args.seed)
    new_picks = rng.sample(pool, need)

    GT_ROOT.mkdir(parents=True, exist_ok=True)
    for song in new_picks:
        for stem in STEMS:
            write_template(GT_ROOT / song / f"{stem}.csv")

    full_sample = current + new_picks
    with SAMPLE_MANIFEST.open("w") as f:
        f.write(f"# Evaluation sample, seed={args.seed}, {len(full_sample)} songs\n")
        for song in full_sample:
            f.write(song + "\n")

    print(f"Picked {len(new_picks)} new song(s) (seed={args.seed}); sample is now {len(full_sample)} total.")
    for song in new_picks:
        print(f"  {song}")
    print(f"\nTemplates created under {GT_ROOT}/<song_name>/{{other,bass,drums}}.csv - "
          f"columns: onset,offset,pitch,label (offset/pitch optional, see gt_format.py docstring).")
    print("Fill them in by hand (README 'Hand-labeling ground truth'), then run evaluate_transcription.py.")
    print("Run with --list any time to check fill-in progress.")


if __name__ == "__main__":
    main()
