#!/usr/bin/env python3
"""
Merge midi/<song_name>/{other,bass,drums}.mid into a single multi-track file
at merged/<song_name>.mid, one instrument track per source stem.

- "drums" -> is_drum=True, program=0 (GM "Standard Kit" - program numbers on
  the drum channel select a kit, not a melodic instrument, so this is correct,
  not a placeholder).
- "bass"  -> program=33 (Electric Bass, finger).
- "other" -> whatever program Basic Pitch assigned on transcription (it's a
  residual bucket of whatever demucs didn't call drums/bass/vocals, so there's
  no single "correct" GM program for it).
Each track is also named after its stem for easy identification.

A song is merged as soon as at least one of its three stems has been
transcribed - tracks for stems that aren't transcribed yet are simply left
out (and reported as "missing" in the summary), not blocked on. Resumable:
a song is skipped once merged/<song>.mid exists, reflects all 3 stems, and
is newer than all of them; a song with fewer than 3 stems available is
always re-merged (cheap) in case more have landed since the last run.

Failures are logged to merged/_failures.log instead of aborting the batch.

After merging, each song gets a printed sanity-check summary: track count,
notes per track, and total duration. A track with 0 notes is flagged - that
usually means its stem's transcription silently produced nothing upstream.

Usage:
    uv run merge_stems.py                # merge everything
    uv run merge_stems.py --limit 3      # first 3 unfinished songs (smoke test)
    uv run merge_stems.py --dry-run      # show what would run, do nothing
    uv run merge_stems.py --retry-failed # clear the failure log and retry those too
    uv run merge_stems.py --check        # re-inspect existing merged/ files, no merging
"""
import argparse
import sys
import time
import traceback
from pathlib import Path

import pretty_midi

STEM_ORDER = ("other", "bass", "drums")
MIDI_ROOT = Path("midi")
MERGED_ROOT = Path("merged")
FAILURE_LOG = MERGED_ROOT / "_failures.log"

GM_PROGRAM = {"bass": 33}  # Electric Bass (finger); "drums" is handled separately, "other" keeps its source program


def collect_jobs():
    """(song_name, song_dir, [stems present]) for every midi/<song>/ with at least one stem .mid."""
    jobs = []
    if not MIDI_ROOT.is_dir():
        return jobs
    for song_dir in sorted(p for p in MIDI_ROOT.iterdir() if p.is_dir()):
        stems_present = [s for s in STEM_ORDER if (song_dir / f"{s}.mid").exists()]
        if stems_present:
            jobs.append((song_dir.name, song_dir, stems_present))
    return jobs


def is_complete(song_dir: Path, merged_path: Path, stems_present: list) -> bool:
    if not merged_path.exists():
        return False
    if len(stems_present) < len(STEM_ORDER):
        return False  # more stems may still land - cheap to retry until all 3 are in
    merged_mtime = merged_path.stat().st_mtime
    return all((song_dir / f"{s}.mid").stat().st_mtime <= merged_mtime for s in stems_present)


def log_failure(song_name: str, error: str):
    FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with FAILURE_LOG.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{song_name}\t{error}\n")


def merge_one(song_name: str, song_dir: Path, stems_present: list) -> pretty_midi.PrettyMIDI:
    merged = pretty_midi.PrettyMIDI()
    for stem in stems_present:
        src = pretty_midi.PrettyMIDI(str(song_dir / f"{stem}.mid"))
        is_drum = stem == "drums"
        program = 0 if is_drum else GM_PROGRAM.get(stem, src.instruments[0].program if src.instruments else 0)
        inst = pretty_midi.Instrument(program=program, is_drum=is_drum, name=stem)
        if len(src.instruments) > 1:
            print(f"    NOTE: {song_name}/{stem}.mid has {len(src.instruments)} instrument tracks, "
                  f"merging notes from all of them into one '{stem}' track", file=sys.stderr)
        for src_inst in src.instruments:
            inst.notes.extend(src_inst.notes)
            inst.pitch_bends.extend(src_inst.pitch_bends)
            inst.control_changes.extend(src_inst.control_changes)
        inst.notes.sort(key=lambda n: n.start)
        merged.instruments.append(inst)

    out_path = MERGED_ROOT / f"{song_name}.mid"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".mid.tmp")
    merged.write(str(tmp_path))
    tmp_path.rename(out_path)  # atomic-ish: a half-written file never looks "done"
    return merged


def print_summary(song_name: str, merged: pretty_midi.PrettyMIDI, stems_present: list):
    print(f"  {song_name}")
    print(f"    tracks: {len(merged.instruments)}   duration: {merged.get_end_time():.1f}s")
    any_empty = False
    for inst in merged.instruments:
        note_count = len(inst.notes)
        kind = "drum kit" if inst.is_drum else f"program={inst.program}"
        line = f"      {inst.name:8s} notes={note_count:5d}  ({kind})"
        if note_count == 0:
            line += "   <-- EMPTY: likely a silent transcription failure upstream, check midi/_failures_*.log"
            any_empty = True
        print(line)
    missing = [s for s in STEM_ORDER if s not in stems_present]
    if missing:
        print(f"      not yet transcribed (excluded from this merge): {', '.join(missing)}")
    return any_empty


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None,
                         help="Only process the first N unfinished songs (for smoke testing)")
    parser.add_argument("--dry-run", action="store_true",
                         help="List what would be processed, without merging")
    parser.add_argument("--retry-failed", action="store_true",
                         help="Clear the failure log first so previously-failed songs are attempted again")
    parser.add_argument("--check", action="store_true",
                         help="Skip merging - just re-run the sanity-check summary over existing merged/*.mid files")
    args = parser.parse_args()

    if args.check:
        files = sorted(MERGED_ROOT.glob("*.mid"))
        if args.limit:
            files = files[:args.limit]
        print(f"Checking {len(files)} merged file(s) under {MERGED_ROOT}/...")
        any_empty_overall = False
        for path in files:
            song_name = path.stem
            stems_present = [s for s in STEM_ORDER if (MIDI_ROOT / song_name / f"{s}.mid").exists()]
            try:
                merged = pretty_midi.PrettyMIDI(str(path))
            except Exception as e:
                print(f"  {song_name}: FAILED TO LOAD ({type(e).__name__}: {e})")
                any_empty_overall = True
                continue
            if print_summary(song_name, merged, stems_present):
                any_empty_overall = True
        if any_empty_overall:
            print("\nSome tracks came back empty - see flags above.")
        return

    if args.retry_failed and FAILURE_LOG.exists():
        FAILURE_LOG.unlink()

    jobs = collect_jobs()
    todo = [j for j in jobs if not is_complete(j[1], MERGED_ROOT / f"{j[0]}.mid", j[2])]
    print(f"{len(jobs)} songs with at least one transcribed stem found under {MIDI_ROOT}/, "
          f"{len(jobs) - len(todo)} already fully merged, {len(todo)} to process.")

    if args.limit:
        todo = todo[:args.limit]

    if args.dry_run:
        for name, _, stems_present in todo:
            print(f"WOULD MERGE: {name}  <-  {', '.join(stems_present)}")
        return

    if not todo:
        print("Nothing to do.")
        return

    MERGED_ROOT.mkdir(parents=True, exist_ok=True)
    ok, failed, any_empty_overall = 0, 0, False
    for i, (song_name, song_dir, stems_present) in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {song_name} ({', '.join(stems_present)}) ...", flush=True)
        try:
            merged = merge_one(song_name, song_dir, stems_present)
            if print_summary(song_name, merged, stems_present):
                any_empty_overall = True
            ok += 1
        except Exception as e:
            log_failure(song_name, f"{type(e).__name__}: {e}")
            print(f"  FAILED (see {FAILURE_LOG})")
            traceback.print_exc(file=sys.stderr)
            failed += 1

    print(f"Done. {ok} succeeded, {failed} failed.")
    if failed:
        print(f"See {FAILURE_LOG} for details. Re-run this script to retry (or pass --retry-failed).")
    if any_empty_overall:
        print("Some merged tracks came back empty - see flags above.")


if __name__ == "__main__":
    main()
