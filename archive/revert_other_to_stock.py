#!/usr/bin/env python3
"""
One-off script reverting the "other" stem transcription back to stock Basic
Pitch config, now that transcribe_harmonic.py's STEM_THRESHOLDS override for
"other" (onset_threshold=0.45) has been removed - see that module's
docstring and README, "'other' stem transcription: known limitation on
short/soft high notes", for why the fix (and a follow-on minimum_note_length
investigation) was abandoned.

Every song currently has an other.mid produced under the 0.45 override (the
original 3 songs were re-transcribed onto it, the other 72 got their
first-ever "other" transcription under it - see
midi/_retranscribe_other_results.csv). This script restores stock-config
output for all of them, split into two cases:

- A song with a pre-fix backup at midi_backup_pre_onset_fix/<song>/other.mid
  (the 3 songs that had an "other" transcription before the 0.45 override
  was ever deployed) is restored by a direct file copy from that backup -
  Basic Pitch's inference is deterministic given fixed weights, so the
  backup IS the exact stock-config output; no compute is spent
  regenerating it.
- A song with no pre-fix backup (its only "other" transcription happened
  under the 0.45 override, so there's nothing to restore) is re-transcribed
  from scratch with the now-reverted stock config.

Both cases overwrite midi/<song_name>/other.mid unconditionally. This is a
one-off revert script, not resumable like transcribe_harmonic.py - it is
meant to be run once, over the whole corpus. Does NOT touch bass.mid,
drums.mid, or chord_segments.csv.

Records to midi/_revert_other_results.csv: which method was used, the note
count before and after, and a tolerance-based note diff (same (onset,
pitch)-matching approach as retranscribe_other_onset_fix.py, reused from
there) so a coincidental note-count match isn't mistaken for an identical
transcription.

Usage (from the repo root):
    python3 -m scripts.transcription.revert_other_to_stock                 # everything
    python3 -m scripts.transcription.revert_other_to_stock --limit 3       # smoke test
    python3 -m scripts.transcription.revert_other_to_stock --dry-run       # list only
"""
import argparse
import csv
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.transcription.transcribe_harmonic import STEM_THRESHOLDS  # noqa: E402
from scripts.transcription.retranscribe_other_onset_fix import note_list, diff_notes  # noqa: E402

STEMS_ROOT = Path("stems")
OUTPUT_ROOT = Path("midi")
BACKUP_ROOT = Path("midi_backup_pre_onset_fix")
FAILURE_LOG = OUTPUT_ROOT / "_failures_harmonic.log"
RESULTS_CSV = OUTPUT_ROOT / "_revert_other_results.csv"

RESULTS_FIELDS = [
    "song", "method", "old_note_count", "new_note_count",
    "notes_gained", "notes_lost", "changed",
]

METHOD_RESTORED = "restored_from_backup"
METHOD_RETRANSCRIBED = "re_transcribed"


def collect_songs():
    if not STEMS_ROOT.is_dir():
        return []
    return sorted(
        p.name for p in STEMS_ROOT.iterdir()
        if p.is_dir() and (p / "other.wav").exists()
    )


def load_done_songs():
    if not RESULTS_CSV.exists():
        return set()
    with RESULTS_CSV.open(newline="") as f:
        return {row["song"] for row in csv.DictReader(f)}


def append_result(row: dict):
    is_new = not RESULTS_CSV.exists()
    RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RESULTS_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def log_failure(song_name: str, error: str):
    FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with FAILURE_LOG.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{song_name}\tother\t{error}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None,
                         help="Only process the first N not-yet-done songs (for smoke testing)")
    parser.add_argument("--dry-run", action="store_true",
                         help="List what would be processed, without running anything")
    parser.add_argument("--retry-failed", action="store_true",
                         help="Clear this run's results CSV so previously-processed songs are attempted again")
    args = parser.parse_args()

    if args.retry_failed and RESULTS_CSV.exists():
        RESULTS_CSV.unlink()

    songs = collect_songs()
    done = load_done_songs()
    todo = [s for s in songs if s not in done]
    print(f"{len(songs)} songs found under {STEMS_ROOT}/ with an other.wav, "
          f"{len(songs) - len(todo)} already reverted by this script, {len(todo)} to process.")

    if args.limit:
        todo = todo[:args.limit]

    if args.dry_run:
        for song in todo:
            backup_path = BACKUP_ROOT / song / "other.mid"
            method = METHOD_RESTORED if backup_path.exists() else METHOD_RETRANSCRIBED
            print(f"WOULD REVERT ({method}): {song}/other")
        return

    if not todo:
        print("Nothing to do.")
        return

    import pretty_midi

    n_restore = sum(1 for s in todo if (BACKUP_ROOT / s / "other.mid").exists())
    n_retranscribe = len(todo) - n_restore
    predict = None
    ICASSP_2022_MODEL_PATH = None
    if n_retranscribe:
        print("Loading Basic Pitch model (TensorFlow warm-up)...", file=sys.stderr)
        from basic_pitch.inference import predict, ICASSP_2022_MODEL_PATH  # noqa: F811

    thresholds = STEM_THRESHOLDS["other"]
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    ok, failed = 0, 0
    for i, song in enumerate(todo, 1):
        out_path = OUTPUT_ROOT / song / "other.mid"
        backup_path = BACKUP_ROOT / song / "other.mid"
        method = METHOD_RESTORED if backup_path.exists() else METHOD_RETRANSCRIBED
        print(f"[{i}/{len(todo)}] {song} ({method}) ...", flush=True)
        try:
            old_notes, old_count = [], 0
            if out_path.exists():
                old_pm = pretty_midi.PrettyMIDI(str(out_path))
                old_notes, old_count = note_list(old_pm)

            if method == METHOD_RESTORED:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = out_path.with_suffix(".mid.tmp")
                tmp_path.write_bytes(backup_path.read_bytes())
                tmp_path.rename(out_path)
                new_pm = pretty_midi.PrettyMIDI(str(out_path))
            else:
                wav = STEMS_ROOT / song / "other.wav"
                _, new_pm, _ = predict(
                    str(wav), ICASSP_2022_MODEL_PATH,
                    onset_threshold=thresholds["onset_threshold"],
                    frame_threshold=thresholds["frame_threshold"],
                )
                out_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = out_path.with_suffix(".mid.tmp")
                new_pm.write(str(tmp_path))
                tmp_path.rename(out_path)

            new_notes, new_count = note_list(new_pm)
            gained, lost = diff_notes(old_notes, new_notes)
            changed = gained > 0 or lost > 0

            append_result({
                "song": song,
                "method": method,
                "old_note_count": old_count,
                "new_note_count": new_count,
                "notes_gained": gained,
                "notes_lost": lost,
                "changed": changed,
            })
            print(f"  {method}: old={old_count} new={new_count} gained={gained} lost={lost} changed={changed}")
            ok += 1
        except Exception as e:
            log_failure(song, f"{type(e).__name__}: {e}")
            print(f"  FAILED (see {FAILURE_LOG})")
            traceback.print_exc(file=sys.stderr)
            failed += 1

    print(f"Done. {ok} succeeded, {failed} failed.")
    if failed:
        print(f"See {FAILURE_LOG} for details. Re-run this script to retry (or pass --retry-failed).")
    print(f"Per-song results: {RESULTS_CSV}")


if __name__ == "__main__":
    main()
