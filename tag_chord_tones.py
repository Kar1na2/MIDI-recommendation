#!/usr/bin/env python3
"""
Chord-tone tagging stage: for every note in merged/<song_name>.mid's "other"
track, look up the Chordino chord segment active at that note's onset
(chords/<song_name>/other_chordnotes.csv, from extract_chords.py) and tag the
note chord_tone / non_chord_tone / no_chord_data based on pitch-class
membership, writing chords/<song_name>/other_note_tags.csv (a sidecar next
to the chordnotes CSV it was derived from).

This is a diagnostic/corroborating signal, independent of and downstream from
both the ML transcription (Basic Pitch) and the chord detection (Chordino) -
it only ever reads merged/*.mid and chords/*/other_chordnotes.csv, and only
adds a new other_note_tags.csv file next to each. Nothing here modifies
merged/<song_name>.mid, other_chordnotes.csv, or any other existing file.

Tagging rule: a note's pitch class (MIDI pitch % 12) is compared against the
set of pitch classes Chordino's chordnotes output reported present in the
chord segment covering that note's onset (segment membership is [onset,
offset), matched by the note's start time only - a note's own duration and
the chord segment's duration are independent and not compared). A note whose
onset falls in a gap between segments, or before/after all of them, is tagged
no_chord_data rather than guessed at.

This tags every note as chord_tone or non_chord_tone; it does not judge
whether that's "right" or "wrong" - a non-chord-tone is completely normal
(passing tones, suspensions, transcription noise all look the same at this
layer). rank_review_candidates.py turns this into a ranked list of segments
worth a human look.

Resumable: a song is skipped if chords/<song_name>/other_note_tags.csv
already exists and is newer than both its merged/<song_name>.mid and
chords/<song_name>/other_chordnotes.csv inputs.

A song with a merged MIDI but no chordnotes CSV yet (extract_chords.py
hasn't reached it) is skipped and logged, not silently dropped. A song whose
inputs exist but fail to parse (malformed MIDI or CSV) is also logged, not
allowed to crash the batch. Both land in chords/_tag_failures.log - a
separate log from extract_chords.py's own chords/_failures.log, so the two
stages' failures don't get mixed together.

Usage:
    uv run tag_chord_tones.py                # process everything
    uv run tag_chord_tones.py --limit 3       # first 3 unfinished songs (smoke test)
    uv run tag_chord_tones.py --dry-run       # show what would run, do nothing
    uv run tag_chord_tones.py --retry-failed  # clear the failure log and retry those too
"""
import argparse
import bisect
import csv
import sys
import time
import traceback
from pathlib import Path

MERGED_ROOT = Path("merged")
CHORDS_ROOT = Path("chords")  # both the chordnotes input and the note-tags output live here
CHORDNOTES_FILENAME = "other_chordnotes.csv"
OUTPUT_FILENAME = "other_note_tags.csv"
FAILURE_LOG = CHORDS_ROOT / "_tag_failures.log"  # distinct from extract_chords.py's chords/_failures.log

CSV_HEADER = ["onset", "offset", "pitch", "pitch_class", "velocity",
              "segment_onset", "segment_offset", "chord_pitch_classes", "tag"]

TAG_CHORD_TONE = "chord_tone"
TAG_NON_CHORD_TONE = "non_chord_tone"
TAG_NO_CHORD_DATA = "no_chord_data"


def collect_jobs():
    """(song_name, merged_path, chordnotes_path) for every merged/<song>.mid
    that also has a chordnotes CSV. Songs missing the chordnotes CSV are
    reported separately by find_missing_inputs(), not silently dropped here."""
    jobs = []
    if not MERGED_ROOT.is_dir():
        return jobs
    for merged_path in sorted(MERGED_ROOT.glob("*.mid")):
        song_name = merged_path.stem
        chordnotes_path = CHORDS_ROOT / song_name / CHORDNOTES_FILENAME
        if chordnotes_path.exists():
            jobs.append((song_name, merged_path, chordnotes_path))
    return jobs


def find_missing_inputs():
    """(song_name, merged_path) for every merged/<song>.mid whose chordnotes
    CSV doesn't exist yet - e.g. extract_chords.py hasn't reached it."""
    missing = []
    if not MERGED_ROOT.is_dir():
        return missing
    for merged_path in sorted(MERGED_ROOT.glob("*.mid")):
        song_name = merged_path.stem
        chordnotes_path = CHORDS_ROOT / song_name / CHORDNOTES_FILENAME
        if not chordnotes_path.exists():
            missing.append((song_name, merged_path))
    return missing


def is_done(song_name: str, merged_path: Path, chordnotes_path: Path) -> bool:
    out_path = CHORDS_ROOT / song_name / OUTPUT_FILENAME
    if not out_path.exists():
        return False
    out_mtime = out_path.stat().st_mtime
    return out_mtime >= merged_path.stat().st_mtime and out_mtime >= chordnotes_path.stat().st_mtime


def log_failure(song_name: str, error: str):
    FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with FAILURE_LOG.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{song_name}\t{error}\n")


def load_chord_segments(chordnotes_path: Path):
    """chords/<song>/other_chordnotes.csv (one row per note-in-segment) ->
    list of {onset, offset, pitch_classes: set[int]} sorted by onset, one
    entry per distinct chord segment."""
    by_segment = {}
    with chordnotes_path.open(newline="") as f:
        for row in csv.DictReader(f):
            seg = int(row["segment"])
            entry = by_segment.setdefault(seg, {
                "onset": float(row["onset"]),
                "offset": float(row["offset"]),
                "pitch_classes": set(),
            })
            entry["pitch_classes"].add(int(row["pitch_class"]))
    return [by_segment[seg] for seg in sorted(by_segment)]


def find_segment(segments, onsets, t: float):
    """Segment covering time t ([onset, offset)), or None if t falls in a gap
    or outside the covered range. `onsets` is segments' onset times, sorted,
    kept alongside for bisect."""
    i = bisect.bisect_right(onsets, t) - 1
    if i < 0:
        return None
    seg = segments[i]
    if seg["onset"] <= t < seg["offset"]:
        return seg
    return None


def tag_one(merged_path: Path, chordnotes_path: Path):
    import pretty_midi

    segments = load_chord_segments(chordnotes_path)
    onsets = [s["onset"] for s in segments]

    midi = pretty_midi.PrettyMIDI(str(merged_path))
    other_tracks = [inst for inst in midi.instruments if inst.name == "other"]
    if not other_tracks:
        return []

    rows = []
    for inst in other_tracks:
        for note in sorted(inst.notes, key=lambda n: n.start):
            pitch_class = note.pitch % 12
            seg = find_segment(segments, onsets, note.start)
            if seg is None:
                rows.append({
                    "onset": note.start, "offset": note.end, "pitch": note.pitch,
                    "pitch_class": pitch_class, "velocity": note.velocity,
                    "segment_onset": "", "segment_offset": "", "chord_pitch_classes": "",
                    "tag": TAG_NO_CHORD_DATA,
                })
                continue
            tag = TAG_CHORD_TONE if pitch_class in seg["pitch_classes"] else TAG_NON_CHORD_TONE
            rows.append({
                "onset": note.start, "offset": note.end, "pitch": note.pitch,
                "pitch_class": pitch_class, "velocity": note.velocity,
                "segment_onset": seg["onset"], "segment_offset": seg["offset"],
                "chord_pitch_classes": ",".join(str(pc) for pc in sorted(seg["pitch_classes"])),
                "tag": tag,
            })
    return rows


def write_csv(rows, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".csv.tmp")
    with tmp_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        writer.writeheader()
        writer.writerows(rows)
    tmp_path.rename(out_path)  # atomic-ish: a half-written file never looks "done"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None,
                         help="Only process the first N unfinished songs (for smoke testing)")
    parser.add_argument("--dry-run", action="store_true",
                         help="List what would be processed, without tagging")
    parser.add_argument("--retry-failed", action="store_true",
                         help="Clear the failure log first so previously-failed songs are attempted again")
    args = parser.parse_args()

    if args.retry_failed and FAILURE_LOG.exists():
        FAILURE_LOG.unlink()

    jobs = collect_jobs()
    todo = [j for j in jobs if not is_done(*j)]
    print(f"{len(jobs)} songs with both a merged MIDI and chordnotes found, "
          f"{len(jobs) - len(todo)} already tagged, {len(todo)} to process.")

    missing = find_missing_inputs()
    if missing:
        names = ", ".join(n for n, _ in missing[:5]) + (", ..." if len(missing) > 5 else "")
        print(f"{len(missing)} song(s) have a merged MIDI but no chordnotes CSV yet "
              f"(run extract_chords.py) - {'logged to ' + str(FAILURE_LOG) if not args.dry_run else 'would be logged'}: {names}")
        if not args.dry_run:
            for song_name, merged_path in missing:
                expected = CHORDS_ROOT / song_name / CHORDNOTES_FILENAME
                log_failure(song_name, f"missing chordnotes CSV: expected {expected}")

    if args.limit:
        todo = todo[:args.limit]

    if args.dry_run:
        for name, merged_path, chordnotes_path in todo:
            print(f"WOULD TAG: {name}  <-  {merged_path}, {chordnotes_path}")
        return

    if not todo:
        print("Nothing to do.")
        return

    CHORDS_ROOT.mkdir(parents=True, exist_ok=True)
    ok, failed = 0, 0
    for i, (song_name, merged_path, chordnotes_path) in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {song_name} ...", flush=True)
        try:
            rows = tag_one(merged_path, chordnotes_path)
            out_path = CHORDS_ROOT / song_name / OUTPUT_FILENAME
            write_csv(rows, out_path)
            n_ct = sum(1 for r in rows if r["tag"] == TAG_CHORD_TONE)
            n_nct = sum(1 for r in rows if r["tag"] == TAG_NON_CHORD_TONE)
            n_nodata = sum(1 for r in rows if r["tag"] == TAG_NO_CHORD_DATA)
            n = len(rows) or 1  # avoid divide-by-zero when a song's "other" track is empty
            print(f"    {len(rows)} notes tagged: {n_ct} chord_tone ({100.0*n_ct/n:.1f}%), "
                  f"{n_nct} non_chord_tone ({100.0*n_nct/n:.1f}%), "
                  f"{n_nodata} no_chord_data ({100.0*n_nodata/n:.1f}%) -> {out_path}")
            ok += 1
        except Exception as e:
            log_failure(song_name, f"{type(e).__name__}: {e}")
            print(f"  FAILED (see {FAILURE_LOG})")
            traceback.print_exc(file=sys.stderr)
            failed += 1

    print(f"Done. {ok} succeeded, {failed} failed.")
    if failed:
        print(f"See {FAILURE_LOG} for details. Re-run this script to retry (or pass --retry-failed).")


if __name__ == "__main__":
    main()
