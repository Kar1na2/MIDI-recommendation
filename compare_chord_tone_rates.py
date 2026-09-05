#!/usr/bin/env python3
"""
Before/after consistency check for auto_correct_pitches.py: re-runs the same
chord-tone tagging logic tag_chord_tones.py used on merged/ (chord_tone /
non_chord_tone / no_chord_data, by Chordino segment pitch-class membership),
but against corrected/<song>.mid's "other" track instead - then compares the
two tag distributions, per song and corpus-wide.

This is NOT a ground-truth accuracy check (nothing here is compared against
ground_truth/ or ground_truth_anchor/). It's a consistency check that the
correction actually did what it was supposed to: move notes that were
non_chord_tone against the Chordino read into chord tones. The retagging
here is fully independent of auto_correct_pitches.py's own bookkeeping - it
re-derives chord-segment membership from chords/<song>/other_chordnotes.csv
from scratch, rather than trusting the changelog's recorded
chord_segment_pitchclasses column - so a passing result here is a real
second opinion, not a restatement of what the correction script already
claimed to have done. (For that same by-construction reason, the after
non_chord_tone rate should be very low but is not automatically zero - see
"skipped_pitch_collision" in auto_correct_pitches.py and the duration
threshold in Pass 2, both of which deliberately leave some non_chord_tone
notes uncorrected.)

Two phases:

1. Retagging: for every song with a corrected/<song>.mid (i.e. it had >=1
   correction applied - a song with zero corrections has no corrected/<song>.mid
   at all, and trivially has identical before/after stats), calls
   tag_chord_tones.tag_one() against corrected/<song>.mid instead of
   merged/<song>.mid, writing corrected/<song>_note_tags.csv (a sidecar next
   to corrected/<song>.mid and _changelog.csv, parallel to how
   chords/<song>/other_note_tags.csv sits next to other_chordnotes.csv).
   Resumable like the rest of the pipeline: skipped if that file is already
   newer than both corrected/<song>.mid and the chordnotes CSV. Failures go
   to corrected/_before_after_failures.log.

2. Comparison: reads chords/<song>/other_note_tags.csv ("before") and
   corrected/<song>_note_tags.csv if it exists, else the same "before" file
   again for a zero-correction song ("after"), computes the chord_tone /
   non_chord_tone / no_chord_data rate for each, and writes one row per song
   plus corpus-wide totals to corrected/_before_after_comparison.csv.

   Any song whose AFTER non_chord_tone rate is still above --watch-threshold
   (default 15%) is flagged "watch", and above --high-threshold (default
   20%) "high" - these are listed as known limitations (genuinely complex
   harmony Chordino is simplifying, or a chord-extraction miss for that
   specific song) rather than something this script tries to fix further;
   they're the ones worth a manual listen if higher confidence is ever
   needed there.

Usage:
    uv run compare_chord_tone_rates.py                # full corpus
    uv run compare_chord_tone_rates.py --limit 3       # smoke test the retag phase
    uv run compare_chord_tone_rates.py --dry-run       # preview retagging, write nothing
    uv run compare_chord_tone_rates.py --retry-failed
"""
import argparse
import csv
import sys
import time
import traceback
from pathlib import Path

from tag_chord_tones import tag_one, write_csv as write_tags_csv, CHORDNOTES_FILENAME
from tag_chord_tones import OUTPUT_FILENAME as BEFORE_TAGS_FILENAME

MERGED_ROOT = Path("merged")
CHORDS_ROOT = Path("chords")
CORRECTED_ROOT = Path("corrected")
CHANGELOG_SUFFIX = "_changelog.csv"
AFTER_TAGS_SUFFIX = "_note_tags.csv"  # corrected/<song>_note_tags.csv

FAILURE_LOG = CORRECTED_ROOT / "_before_after_failures.log"
COMPARISON_CSV = CORRECTED_ROOT / "_before_after_comparison.csv"

DEFAULT_WATCH_THRESHOLD = 0.15  # after-correction non_chord_tone rate above this: flagged "watch"
DEFAULT_HIGH_THRESHOLD = 0.20   # above this: flagged "high"

COMPARISON_HEADER = [
    "song", "status", "total_notes",
    "before_chord_tone", "before_non_chord_tone", "before_no_chord_data",
    "before_chord_tone_pct", "before_non_chord_tone_pct",
    "after_chord_tone", "after_non_chord_tone", "after_no_chord_data",
    "after_chord_tone_pct", "after_non_chord_tone_pct",
    "delta_chord_tone_pct", "delta_non_chord_tone_pct", "flag",
]


def eligible_songs():
    """Songs with everything needed to compare: a merged MIDI, its "before"
    tags, the chordnotes CSV (needed to retag), and a changelog (i.e.
    auto_correct_pitches.py has actually run on it)."""
    if not MERGED_ROOT.is_dir():
        return []
    songs = []
    for merged_path in sorted(MERGED_ROOT.glob("*.mid")):
        song_name = merged_path.stem
        song_dir = CHORDS_ROOT / song_name
        if (song_dir / BEFORE_TAGS_FILENAME).exists() and (song_dir / CHORDNOTES_FILENAME).exists() \
                and (CORRECTED_ROOT / f"{song_name}{CHANGELOG_SUFFIX}").exists():
            songs.append(song_name)
    return songs


def log_failure(song_name: str, error: str):
    FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with FAILURE_LOG.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{song_name}\t{error}\n")


def failed_songs() -> set:
    if not FAILURE_LOG.exists():
        return set()
    names = set()
    for line in FAILURE_LOG.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            names.add(parts[1])
    return names


def retag_jobs(songs: list):
    """(song_name, corrected_path, chordnotes_path, after_tags_path) for
    every song that HAS a corrected/<song>.mid (i.e. >=1 correction was
    applied) and whose after-tags file is missing or stale. A song with no
    corrected/<song>.mid needs no retagging - its after stats are identical
    to its before stats by definition (nothing was written to correct it)."""
    jobs = []
    for song_name in songs:
        corrected_path = CORRECTED_ROOT / f"{song_name}.mid"
        if not corrected_path.exists():
            continue
        chordnotes_path = CHORDS_ROOT / song_name / CHORDNOTES_FILENAME
        after_tags_path = CORRECTED_ROOT / f"{song_name}{AFTER_TAGS_SUFFIX}"
        if after_tags_path.exists():
            out_mtime = after_tags_path.stat().st_mtime
            if out_mtime >= corrected_path.stat().st_mtime and out_mtime >= chordnotes_path.stat().st_mtime:
                continue
        jobs.append((song_name, corrected_path, chordnotes_path, after_tags_path))
    return jobs


def load_tag_rows(path: Path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def tag_stats(rows: list) -> dict:
    n = len(rows)
    ct = sum(1 for r in rows if r["tag"] == "chord_tone")
    nct = sum(1 for r in rows if r["tag"] == "non_chord_tone")
    nd = sum(1 for r in rows if r["tag"] == "no_chord_data")
    return {
        "total": n, "chord_tone": ct, "non_chord_tone": nct, "no_chord_data": nd,
        "chord_tone_pct": (ct / n) if n else 0.0,
        "non_chord_tone_pct": (nct / n) if n else 0.0,
        "no_chord_data_pct": (nd / n) if n else 0.0,
    }


def classify_flag(after_non_chord_tone_pct: float, watch_threshold: float, high_threshold: float) -> str:
    if after_non_chord_tone_pct > high_threshold:
        return "high"
    if after_non_chord_tone_pct > watch_threshold:
        return "watch"
    return ""


def compare_song(song_name: str, failed_retag: set, watch_threshold: float, high_threshold: float) -> dict:
    before_path = CHORDS_ROOT / song_name / BEFORE_TAGS_FILENAME
    corrected_path = CORRECTED_ROOT / f"{song_name}.mid"
    after_path = CORRECTED_ROOT / f"{song_name}{AFTER_TAGS_SUFFIX}"

    before_rows = load_tag_rows(before_path)
    before = tag_stats(before_rows)

    if not corrected_path.exists():
        # No corrections were applied to this song at all - after is
        # identical to before by definition, no retagging needed or possible.
        after = before
        status = "ok_no_corrections"
    elif song_name in failed_retag:
        after = {k: 0 if isinstance(v, int) else 0.0 for k, v in before.items()}
        status = "retag_failed"
    elif after_path.exists():
        after = tag_stats(load_tag_rows(after_path))
        status = "ok"
    else:
        after = {k: 0 if isinstance(v, int) else 0.0 for k, v in before.items()}
        status = "not_retagged"  # e.g. skipped by --limit this run

    row = {
        "song": song_name, "status": status, "total_notes": before["total"],
        "before_chord_tone": before["chord_tone"], "before_non_chord_tone": before["non_chord_tone"],
        "before_no_chord_data": before["no_chord_data"],
        "before_chord_tone_pct": before["chord_tone_pct"], "before_non_chord_tone_pct": before["non_chord_tone_pct"],
        "after_chord_tone": after["chord_tone"], "after_non_chord_tone": after["non_chord_tone"],
        "after_no_chord_data": after["no_chord_data"],
        "after_chord_tone_pct": after["chord_tone_pct"], "after_non_chord_tone_pct": after["non_chord_tone_pct"],
        "delta_chord_tone_pct": after["chord_tone_pct"] - before["chord_tone_pct"],
        "delta_non_chord_tone_pct": after["non_chord_tone_pct"] - before["non_chord_tone_pct"],
        "flag": classify_flag(after["non_chord_tone_pct"], watch_threshold, high_threshold) if status in ("ok", "ok_no_corrections") else "",
    }
    return row


def print_report(rows: list, watch_threshold: float, high_threshold: float):
    comparable = [r for r in rows if r["status"] in ("ok", "ok_no_corrections")]
    not_retagged = [r for r in rows if r["status"] == "not_retagged"]
    retag_failed = [r for r in rows if r["status"] == "retag_failed"]

    print("=== Before/after chord-tone rate comparison ===")
    print(f"{len(rows)} eligible song(s): {len(comparable)} compared, "
          f"{len(not_retagged)} not yet retagged, {len(retag_failed)} retag failed")
    if len(rows) < 10:
        print(f"NOTE: only {len(rows)} song(s) in the corpus right now - every figure below is over a small pool.")
    print()

    if comparable:
        total_notes = sum(r["total_notes"] for r in comparable)
        before_ct = sum(r["before_chord_tone"] for r in comparable)
        before_nct = sum(r["before_non_chord_tone"] for r in comparable)
        after_ct = sum(r["after_chord_tone"] for r in comparable)
        after_nct = sum(r["after_non_chord_tone"] for r in comparable)
        before_pct = before_ct / total_notes if total_notes else 0.0
        after_pct = after_ct / total_notes if total_notes else 0.0
        before_nct_pct = before_nct / total_notes if total_notes else 0.0
        after_nct_pct = after_nct / total_notes if total_notes else 0.0

        print("=== Corpus-wide (weighted by note count) ===")
        print(f"  total notes:                {total_notes}")
        print(f"  chord_tone%:      before {before_pct:6.1%}  ->  after {after_pct:6.1%}   "
              f"(delta {after_pct - before_pct:+.1%})")
        print(f"  non_chord_tone%:  before {before_nct_pct:6.1%}  ->  after {after_nct_pct:6.1%}   "
              f"(delta {after_nct_pct - before_nct_pct:+.1%})")
        print()

        print("=== Per song ===")
        for r in sorted(comparable, key=lambda r: -r["after_non_chord_tone_pct"]):
            flag_str = f"  <-- {r['flag'].upper()}" if r["flag"] else ""
            print(f"  {r['song']}")
            print(f"      chord_tone%:      before {r['before_chord_tone_pct']:6.1%}  ->  after {r['after_chord_tone_pct']:6.1%}"
                  f"   (delta {r['delta_chord_tone_pct']:+.1%})")
            print(f"      non_chord_tone%:  before {r['before_non_chord_tone_pct']:6.1%}  ->  after {r['after_non_chord_tone_pct']:6.1%}"
                  f"   (delta {r['delta_non_chord_tone_pct']:+.1%}){flag_str}")
        print()

        flagged = [r for r in comparable if r["flag"]]
        print(f"=== Known limitations: songs still >{watch_threshold:.0%} non_chord_tone after correction ===")
        if not flagged:
            print(f"  (none - every song is at or below {watch_threshold:.0%} after correction)")
        else:
            print("  Not being fixed further here - either genuinely complex harmony Chordino is")
            print("  simplifying, or chord extraction itself may be off for these specific songs.")
            print("  Worth a manual listen before trusting these songs' 'other' track harmonically.")
            for r in flagged:
                severity = f">{high_threshold:.0%}" if r["flag"] == "high" else f">{watch_threshold:.0%}"
                print(f"    [{r['flag']:5s} {severity}]  {r['song']}: "
                      f"{r['after_non_chord_tone_pct']:.1%} non_chord_tone after correction "
                      f"({r['after_non_chord_tone']}/{r['total_notes']} notes)")
        print()

    if not_retagged:
        print(f"=== Not yet retagged (run again without --limit, or check {FAILURE_LOG}) ===")
        for r in not_retagged:
            print(f"  {r['song']}")
        print()
    if retag_failed:
        print(f"=== Retagging failed (see {FAILURE_LOG}) ===")
        for r in retag_failed:
            print(f"  {r['song']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None,
                         help="Only retag the first N unfinished songs (for smoke testing)")
    parser.add_argument("--dry-run", action="store_true",
                         help="Show what would be retagged, without writing anything")
    parser.add_argument("--retry-failed", action="store_true",
                         help="Clear the retag failure log first so previously-failed songs are attempted again")
    parser.add_argument("--watch-threshold", type=float, default=DEFAULT_WATCH_THRESHOLD,
                         help=f"After-correction non_chord_tone rate above which a song is flagged 'watch' "
                              f"(default {DEFAULT_WATCH_THRESHOLD:.0%})")
    parser.add_argument("--high-threshold", type=float, default=DEFAULT_HIGH_THRESHOLD,
                         help=f"After-correction non_chord_tone rate above which a song is flagged 'high' "
                              f"(default {DEFAULT_HIGH_THRESHOLD:.0%})")
    args = parser.parse_args()

    if args.retry_failed and FAILURE_LOG.exists():
        FAILURE_LOG.unlink()

    songs = eligible_songs()
    if not songs:
        print("No eligible songs found (need merged/<song>.mid, chords/<song>/other_note_tags.csv, "
              "chords/<song>/other_chordnotes.csv, and corrected/<song>_changelog.csv). "
              "Run tag_chord_tones.py and auto_correct_pitches.py first.")
        return

    # --- Phase 1: retag corrected/<song>.mid for every song that has one ---
    jobs = retag_jobs(songs)
    print(f"{len(songs)} eligible song(s), {len(jobs)} need retagging"
          + (" [DRY RUN]" if args.dry_run else "") + ".")

    if args.limit:
        jobs = jobs[:args.limit]

    already_failed = failed_songs()
    if args.dry_run:
        for song_name, _, _, _ in jobs:
            print(f"WOULD RETAG: {song_name}")
    else:
        for i, (song_name, corrected_path, chordnotes_path, after_tags_path) in enumerate(jobs, 1):
            print(f"[{i}/{len(jobs)}] retagging {song_name} ...", flush=True)
            try:
                rows = tag_one(corrected_path, chordnotes_path)
                write_tags_csv(rows, after_tags_path)
            except Exception as e:
                log_failure(song_name, f"{type(e).__name__}: {e}")
                print(f"    FAILED (see {FAILURE_LOG})")
                traceback.print_exc(file=sys.stderr)

    if args.dry_run:
        print("[DRY RUN] - nothing written, comparison skipped.")
        return

    # --- Phase 2: compare before vs. after for every eligible song ---
    failed_this_run = failed_songs()
    rows = [compare_song(song_name, failed_this_run, args.watch_threshold, args.high_threshold)
            for song_name in songs]

    print_report(rows, args.watch_threshold, args.high_threshold)

    CORRECTED_ROOT.mkdir(parents=True, exist_ok=True)
    with COMPARISON_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COMPARISON_HEADER)
        writer.writeheader()
        for r in rows:
            out = dict(r)
            for pct_field in ("before_chord_tone_pct", "before_non_chord_tone_pct",
                              "after_chord_tone_pct", "after_non_chord_tone_pct",
                              "delta_chord_tone_pct", "delta_non_chord_tone_pct"):
                out[pct_field] = f"{r[pct_field]:.4f}"
            writer.writerow(out)
    print(f"\nWritten to {COMPARISON_CSV}")


if __name__ == "__main__":
    main()
