#!/usr/bin/env python3
"""
One-off Stage 3 script for the "other" onset-threshold fix (see
transcribe_harmonic.py's STEM_THRESHOLDS / DEFAULT_ONSET_THRESHOLD comment
for the diagnosis). Re-transcribes the "other" stem ONLY, for every song
under stems/, using the new onset_threshold=0.45 (STEM_THRESHOLDS["other"]),
overwriting midi/<song_name>/other.mid.

Does NOT touch bass.mid or drums.mid - those keep whatever thresholds/output
they already have.

Before overwriting a song's other.mid for the first time, the pre-fix file is
copied to midi_backup_pre_onset_fix/<song_name>/other.mid (skipped if a
backup already exists there, so re-running this script never clobbers the
original pre-fix backup with an already-fixed file). A song with no prior
other.mid at all (first-ever transcription, not a re-transcription) gets no
backup and is reported separately.

For every song processed, records to
midi/_retranscribe_other_results.csv whether the new other.mid differs from
the pre-fix backup (note-level diff on (rounded onset, pitch) pairs, not
just a note-count comparison) - a note count can coincidentally match while
individual notes differ.

Resumable: a song already recorded in the results CSV from a previous run of
THIS script is skipped (distinct from the normal midi/<song>/other.mid
existence check, which would look "done" even for the pre-fix version).
Failures are logged to midi/_failures_harmonic.log (same log
transcribe_harmonic.py uses, since it's the same underlying failure mode).

Usage (from the repo root):
    python3 -m scripts.transcription.retranscribe_other_onset_fix                    # everything
    python3 -m scripts.transcription.retranscribe_other_onset_fix --limit 3          # smoke test
    python3 -m scripts.transcription.retranscribe_other_onset_fix --dry-run          # list only
    python3 -m scripts.transcription.retranscribe_other_onset_fix --retry-failed     # clear harmonic failure log entries for 'other' and retry
"""
import argparse
import csv
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.transcription.transcribe_harmonic import STEM_THRESHOLDS  # noqa: E402

STEMS_ROOT = Path("stems")
OUTPUT_ROOT = Path("midi")
BACKUP_ROOT = Path("midi_backup_pre_onset_fix")
FAILURE_LOG = OUTPUT_ROOT / "_failures_harmonic.log"
RESULTS_CSV = OUTPUT_ROOT / "_retranscribe_other_results.csv"

RESULTS_FIELDS = [
    "song", "had_prior_transcription", "old_note_count", "new_note_count",
    "notes_gained", "notes_lost", "changed",
]


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


ONSET_MATCH_TOLERANCE = 0.05  # seconds (~4 Basic Pitch frames)


def note_list(pretty_midi_obj):
    """(onset, pitch) pairs across every instrument track."""
    notes = []
    for inst in pretty_midi_obj.instruments:
        notes.extend(inst.notes)
    return [(n.start, n.pitch) for n in notes], len(notes)


def diff_notes(old_notes, new_notes, tolerance=ONSET_MATCH_TOLERANCE):
    """Tolerance-based nearest-onset match per pitch, not an exact-timestamp
    set diff: changing the onset threshold shifts which activation peak the
    note-formation step picks as "the" onset by a frame or two even for a
    note that's genuinely unchanged, so an exact (rounded onset, pitch)
    comparison would overcount that timing jitter as churn. A note only
    counts as gained/lost if no same-pitch note within `tolerance` seconds
    exists on the other side.

    Returns (gained, lost) - gained: new notes with no old match; lost: old
    notes with no new match.
    """
    from collections import defaultdict
    old_by_pitch, new_by_pitch = defaultdict(list), defaultdict(list)
    for t, p in old_notes:
        old_by_pitch[p].append(t)
    for t, p in new_notes:
        new_by_pitch[p].append(t)

    gained, lost = 0, 0
    for pitch in set(old_by_pitch) | set(new_by_pitch):
        old_times = sorted(old_by_pitch.get(pitch, []))
        new_times = sorted(new_by_pitch.get(pitch, []))
        used_new = [False] * len(new_times)
        for ot in old_times:
            best_idx, best_dist = None, None
            for i, nt in enumerate(new_times):
                if used_new[i]:
                    continue
                d = abs(nt - ot)
                if d <= tolerance and (best_dist is None or d < best_dist):
                    best_idx, best_dist = i, d
            if best_idx is not None:
                used_new[best_idx] = True
            else:
                lost += 1
        gained += used_new.count(False)
    return gained, lost


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None,
                         help="Only process the first N not-yet-done songs (for smoke testing)")
    parser.add_argument("--dry-run", action="store_true",
                         help="List what would be processed, without running Basic Pitch")
    parser.add_argument("--retry-failed", action="store_true",
                         help="Clear this run's results CSV so previously-processed songs are attempted again")
    args = parser.parse_args()

    if args.retry_failed and RESULTS_CSV.exists():
        RESULTS_CSV.unlink()

    songs = collect_songs()
    done = load_done_songs()
    todo = [s for s in songs if s not in done]
    print(f"{len(songs)} songs found under {STEMS_ROOT}/ with an other.wav, "
          f"{len(songs) - len(todo)} already re-transcribed by this script, {len(todo)} to process.")

    if args.limit:
        todo = todo[:args.limit]

    if args.dry_run:
        for song in todo:
            print(f"WOULD RE-TRANSCRIBE: {song}/other")
        return

    if not todo:
        print("Nothing to do.")
        return

    import pretty_midi
    print("Loading Basic Pitch model (TensorFlow warm-up)...", file=sys.stderr)
    from basic_pitch.inference import predict, ICASSP_2022_MODEL_PATH

    thresholds = STEM_THRESHOLDS["other"]
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)

    ok, failed = 0, 0
    for i, song in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {song} ...", flush=True)
        wav = STEMS_ROOT / song / "other.wav"
        out_path = OUTPUT_ROOT / song / "other.mid"
        backup_path = BACKUP_ROOT / song / "other.mid"
        try:
            had_prior = out_path.exists()
            old_notes, old_count = [], 0
            if had_prior:
                if not backup_path.exists():
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    backup_path.write_bytes(out_path.read_bytes())
                old_pm = pretty_midi.PrettyMIDI(str(backup_path))
                old_notes, old_count = note_list(old_pm)

            _, new_pm, _ = predict(
                str(wav), ICASSP_2022_MODEL_PATH,
                onset_threshold=thresholds["onset_threshold"],
                frame_threshold=thresholds["frame_threshold"],
            )
            new_notes, new_count = note_list(new_pm)

            out_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = out_path.with_suffix(".mid.tmp")
            new_pm.write(str(tmp_path))
            tmp_path.rename(out_path)

            gained, lost = diff_notes(old_notes, new_notes) if had_prior else (0, 0)
            changed = had_prior and (gained > 0 or lost > 0)

            append_result({
                "song": song,
                "had_prior_transcription": had_prior,
                "old_note_count": old_count if had_prior else "",
                "new_note_count": new_count,
                "notes_gained": gained if had_prior else "",
                "notes_lost": lost if had_prior else "",
                "changed": changed if had_prior else "new",
            })
            status = "new (no prior baseline)" if not had_prior else ("changed" if changed else "unchanged")
            print(f"  {status}: old={old_count if had_prior else 'n/a'} new={new_count} "
                  f"gained={gained if had_prior else 'n/a'} lost={lost if had_prior else 'n/a'}")
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
