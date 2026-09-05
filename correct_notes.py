#!/usr/bin/env python3
"""
Human-initiated correction layer: applies hand-verified pitch/onset/offset
edits (and deletions) to notes in merged/<song>.mid's "other" track, from a
corrections CSV you fill in yourself (see corrections_template.csv). Writes
the result to corrected/<song>.mid - a new directory, parallel to merged/.

merged/ is read-only to this script: it is only ever opened for parsing
(pretty_midi.PrettyMIDI(...)), never written to, and there is no flag to
point output anywhere but corrected/. This is deliberately NOT part of the
automated pipeline (separate_stems.py -> transcribe_*.py -> merge_stems.py)
and is not run by any of it - it's a separate, opt-in step for after a human
has looked at specific notes (e.g. via inspect_range.py or
rank_review_candidates.py's flagged segments) and decided a specific
correction is actually warranted. Nothing upstream ever infers or applies a
correction on its own; every row in the corrections CSV is something a human
typed in.

Corrections CSV columns:
    song            - matches a merged/<song>.mid filename (without .mid)
    onset_time      - the note's current onset in seconds, as transcribed
    original_pitch  - the note's current MIDI pitch (0-127), as transcribed
    new_pitch       - the corrected MIDI pitch, or the literal word "delete"
                       to remove the note entirely
    new_onset       - optional: corrected onset in seconds (blank = unchanged)
    new_offset      - optional: corrected offset in seconds (blank = unchanged)
Lines starting with # (leading whitespace allowed) and blank lines are
skipped, so a template row can be left commented out as a format example.

Matching: for each row, the note in merged/<song>.mid's "other" track (there
may be more than one instrument named "other") whose pitch equals
original_pitch and whose onset is within --tolerance seconds (default 0.05 =
50ms) of onset_time. If more than one note matches, the closest onset wins
and this is noted in the log - narrow --tolerance if that's happening a lot.
A note already consumed by an earlier row in the same run can't be matched
again by a later row.

Every row's outcome is logged to corrected/correction_log.csv (appended,
never truncated, so it's a running history across correction sessions) -
whether it was applied, and if not, why (no matching note found, or the row
itself didn't parse) - so a correction that silently failed to find its
target is never invisible.

Usage:
    uv run correct_notes.py my_corrections.csv
    uv run correct_notes.py my_corrections.csv --tolerance 0.08
    uv run correct_notes.py my_corrections.csv --dry-run   # preview only, writes nothing
"""
import argparse
import csv
import sys
import time
from collections import defaultdict
from pathlib import Path

MERGED_ROOT = Path("merged")          # read-only: only ever parsed, never written
OUTPUT_ROOT = Path("corrected")        # the only place this script writes MIDI
LOG_PATH = OUTPUT_ROOT / "correction_log.csv"
LOG_HEADER = ["timestamp", "song", "csv_line", "onset_time", "original_pitch",
              "new_pitch", "new_onset", "new_offset", "status", "detail"]

DELETE_SENTINEL = "delete"
DEFAULT_TOLERANCE = 0.05  # seconds


def read_correction_rows(path: Path):
    """Raw CSV rows from a corrections file, skipping comment (#) and blank
    lines so a template's example row can stay commented out."""
    with path.open(newline="") as f:
        lines = [line for line in f if line.strip() and not line.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


def parse_row(row: dict, line_no: int):
    """Validate + coerce one corrections CSV row. Raises ValueError with a
    human-readable reason on anything malformed."""
    song = (row.get("song") or "").strip()
    if not song:
        raise ValueError("missing song")

    onset_time = float(row["onset_time"])
    original_pitch = int(row["original_pitch"])
    if not 0 <= original_pitch <= 127:
        raise ValueError(f"original_pitch {original_pitch} out of MIDI range 0-127")

    new_pitch_raw = (row.get("new_pitch") or "").strip()
    delete = new_pitch_raw.lower() == DELETE_SENTINEL
    new_pitch = None
    if not delete:
        if not new_pitch_raw:
            raise ValueError("new_pitch is blank (use a pitch number or 'delete')")
        new_pitch = int(new_pitch_raw)
        if not 0 <= new_pitch <= 127:
            raise ValueError(f"new_pitch {new_pitch} out of MIDI range 0-127")

    def opt_float(key):
        raw = (row.get(key) or "").strip()
        return float(raw) if raw else None

    new_onset = opt_float("new_onset")
    new_offset = opt_float("new_offset")
    if new_onset is not None and new_offset is not None and new_offset <= new_onset:
        raise ValueError(f"new_offset ({new_offset}) must be after new_onset ({new_onset})")

    return {
        "line_no": line_no, "song": song, "onset_time": onset_time,
        "original_pitch": original_pitch, "delete": delete, "new_pitch": new_pitch,
        "new_onset": new_onset, "new_offset": new_offset,
    }


def find_match(pool, onset_time: float, pitch: int, tolerance: float):
    """Closest not-yet-consumed note in `pool` matching pitch within
    tolerance of onset_time. Returns (note, warning_or_None) or (None, None)
    if nothing matches. `pool` is mutated (the match is removed) so a later
    row can't re-match the same note."""
    candidates = [n for n in pool if n.pitch == pitch and abs(n.start - onset_time) <= tolerance]
    if not candidates:
        return None, None
    candidates.sort(key=lambda n: abs(n.start - onset_time))
    warning = (f"{len(candidates)} notes matched within {tolerance}s tolerance - used the closest onset"
               if len(candidates) > 1 else None)
    match = candidates[0]
    pool.remove(match)
    return match, warning


def apply_song(song_name: str, corrections: list, tolerance: float, dry_run: bool):
    """Returns (log_rows, midi_or_None). midi_or_None is the modified
    pretty_midi object if >=1 correction actually applied and not dry_run,
    else None (nothing to write for this song)."""
    import pretty_midi

    log_rows = []
    merged_path = MERGED_ROOT / f"{song_name}.mid"
    if not merged_path.exists():
        for c in corrections:
            log_rows.append(_log_row(c, "no_match", f"merged MIDI not found: {merged_path}"))
        return log_rows, None

    midi = pretty_midi.PrettyMIDI(str(merged_path))
    other_tracks = [inst for inst in midi.instruments if inst.name == "other"]
    if not other_tracks:
        for c in corrections:
            log_rows.append(_log_row(c, "no_match", f"no 'other' track in {merged_path}"))
        return log_rows, None

    pool = [n for inst in other_tracks for n in inst.notes]
    applied = 0

    for c in corrections:
        note, warning = find_match(pool, c["onset_time"], c["original_pitch"], tolerance)
        if note is None:
            log_rows.append(_log_row(c, "no_match",
                             "no note with this pitch found within tolerance of this onset"))
            continue

        old_pitch, old_onset, old_offset = note.pitch, note.start, note.end
        if dry_run:
            action = "delete" if c["delete"] else f"pitch {old_pitch}->{c['new_pitch']}"
            status = "would_delete" if c["delete"] else "would_edit"
            detail = action + (f", onset {old_onset:.3f}->{c['new_onset']:.3f}" if c["new_onset"] is not None else "") \
                             + (f", offset {old_offset:.3f}->{c['new_offset']:.3f}" if c["new_offset"] is not None else "") \
                             + (f"  [{warning}]" if warning else "")
            log_rows.append(_log_row(c, status, detail))
            continue

        if c["delete"]:
            for inst in other_tracks:
                if note in inst.notes:
                    inst.notes.remove(note)
                    break
            detail = f"deleted (was pitch {old_pitch} @ {old_onset:.3f}-{old_offset:.3f})"
            detail += f"  [{warning}]" if warning else ""
            log_rows.append(_log_row(c, "deleted", detail))
        else:
            note.pitch = c["new_pitch"]
            if c["new_onset"] is not None:
                note.start = c["new_onset"]
            if c["new_offset"] is not None:
                note.end = c["new_offset"]
            detail = f"pitch {old_pitch}->{c['new_pitch']}"
            if c["new_onset"] is not None:
                detail += f", onset {old_onset:.3f}->{c['new_onset']:.3f}"
            if c["new_offset"] is not None:
                detail += f", offset {old_offset:.3f}->{c['new_offset']:.3f}"
            detail += f"  [{warning}]" if warning else ""
            log_rows.append(_log_row(c, "edited", detail))
        applied += 1

    return log_rows, (midi if applied and not dry_run else None)


def _log_row(c: dict, status: str, detail: str) -> dict:
    return {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "song": c["song"], "csv_line": c["line_no"], "onset_time": c["onset_time"],
        "original_pitch": c["original_pitch"],
        "new_pitch": DELETE_SENTINEL if c["delete"] else c["new_pitch"],
        "new_onset": c["new_onset"] if c["new_onset"] is not None else "",
        "new_offset": c["new_offset"] if c["new_offset"] is not None else "",
        "status": status, "detail": detail,
    }


def append_log(rows: list):
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    write_header = not LOG_PATH.exists()
    with LOG_PATH.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_HEADER)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("corrections_csv", type=str, help="Path to a corrections CSV (see corrections_template.csv)")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                         help=f"Onset-matching tolerance in seconds (default {DEFAULT_TOLERANCE})")
    parser.add_argument("--dry-run", action="store_true",
                         help="Show what would be matched/applied - writes nothing (no corrected/*.mid, no log)")
    args = parser.parse_args()

    corrections_path = Path(args.corrections_csv)
    if not corrections_path.exists():
        print(f"ERROR: {corrections_path} not found.", file=sys.stderr)
        sys.exit(1)

    raw_rows = read_correction_rows(corrections_path)
    if not raw_rows:
        print(f"No correction rows found in {corrections_path} (comments/blank lines don't count).")
        return

    parsed, parse_errors = [], []
    for line_no, row in enumerate(raw_rows, start=2):  # header is line 1
        try:
            parsed.append(parse_row(row, line_no))
        except Exception as e:
            parse_errors.append((line_no, row, str(e)))

    by_song = defaultdict(list)
    for c in parsed:
        by_song[c["song"]].append(c)

    print(f"{len(parsed)} correction row(s) parsed ({len(parse_errors)} malformed), "
          f"across {len(by_song)} song(s), tolerance={args.tolerance}s"
          + (" [DRY RUN - nothing will be written]" if args.dry_run else ""))

    all_log_rows = []
    for line_no, row, error in parse_errors:
        all_log_rows.append({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "song": row.get("song", ""), "csv_line": line_no,
            "onset_time": row.get("onset_time", ""), "original_pitch": row.get("original_pitch", ""),
            "new_pitch": row.get("new_pitch", ""), "new_onset": row.get("new_onset", ""),
            "new_offset": row.get("new_offset", ""),
            "status": "parse_error", "detail": error,
        })
        print(f"  PARSE ERROR (line {line_no}): {error}", file=sys.stderr)

    written, applied_total, no_match_total = 0, 0, 0
    for song_name, corrections in sorted(by_song.items()):
        log_rows, midi = apply_song(song_name, corrections, args.tolerance, args.dry_run)
        all_log_rows.extend(log_rows)

        n_ok = sum(1 for r in log_rows if r["status"] in ("edited", "deleted", "would_edit", "would_delete"))
        n_no_match = sum(1 for r in log_rows if r["status"] == "no_match")
        applied_total += n_ok
        no_match_total += n_no_match
        print(f"  {song_name}: {n_ok}/{len(corrections)} matched" + (f", {n_no_match} no match" if n_no_match else ""))

        if midi is not None:
            out_path = OUTPUT_ROOT / f"{song_name}.mid"
            assert out_path.resolve() != (MERGED_ROOT / f"{song_name}.mid").resolve(), \
                "refusing to write over merged/ - this should be unreachable"
            OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
            midi.write(str(out_path))
            written += 1
            print(f"    -> {out_path}")

    if not args.dry_run:
        append_log(all_log_rows)
        print(f"\n{applied_total} correction(s) applied, {no_match_total} unmatched, "
              f"{len(parse_errors)} parse error(s) - full log appended to {LOG_PATH}")
    else:
        print(f"\n{applied_total} correction(s) would apply, {no_match_total} would not match, "
              f"{len(parse_errors)} parse error(s) - dry run, nothing written")

    print(f"corrected MIDI written for {written} song(s) under {OUTPUT_ROOT}/")
    if no_match_total or parse_errors:
        print(f"Check {LOG_PATH if not args.dry_run else '(dry run - see above)'} for rows that need attention.")


if __name__ == "__main__":
    main()
