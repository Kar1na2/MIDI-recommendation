#!/usr/bin/env python3
"""
Side-by-side inspector for auto_correct_pitches.py's output: for one song and
one time window, shows every note in the merged "other" track next to what
(if anything) auto_correct_pitches.py did to it, with the harmonic context
(active Chordino chord segment and its pitch classes) that decision was
based on. This is the primary tool for visually verifying auto-correction
before moving on to alignment.

Per note, prints:
    - onset time
    - original pitch (merged/<song>.mid, read fresh - never mutated here)
    - corrected pitch (corrected/<song>.mid), or "unchanged" if
      auto_correct_pitches.py didn't touch this note
    - the active chord segment's pitch classes (from
      chords/<song>/other_note_tags.csv, tag_chord_tones.py's output)
    - the correction type if corrected (stuck_pitch / duration_filter), from
      corrected/<song>_changelog.csv
    - whether the pitch actually being used (corrected if corrected, else
      original) is a chord tone - for a corrected note this should ALWAYS
      be true by construction (that's the entire point of the snap); a
      corrected note that isn't is flagged as a BUG, not silently noted.

Also cross-checks the three sources against each other (merged MIDI,
corrected MIDI, changelog CSV) and flags any disagreement between them as a
bug - e.g. a pitch that differs between merged/ and corrected/ with no
matching changelog row, or vice versa. This check runs over the WHOLE song,
not just the printed window, since a pipeline bug matters regardless of
where you happened to point --start/--end.

A compact "diff" view is also printed, showing only the notes that actually
changed - for quick scanning without wading through every unchanged note.

If --start/--end are omitted, the window defaults to the densest cluster of
corrections in the changelog (a sliding window of --window seconds, padded
by --padding seconds of context on each side) - the most interesting part of
the song to eyeball first. If the song has zero corrections at all, it falls
back to the first --window seconds instead (there's no "densest cluster" to
find, but the note-by-note table over merged/ vs. corrected/ is still worth
a glance, e.g. to confirm the song is clean and not just silently broken).

Read-only: reads merged/, corrected/, and chords/*/other_note_tags.csv, and
never modifies any of them.

Usage:
    uv run compare_corrections.py --song "Some Song"                # densest cluster
    uv run compare_corrections.py --song "Some Song" --start 60 --end 75
    uv run compare_corrections.py --song "Some Song" --start 1:00 --end 1:15
    uv run compare_corrections.py --list                            # list songs available to inspect
"""
import argparse
import bisect
import csv
import sys
from pathlib import Path

from inspect_range import parse_time, pitch_name, format_chord_set, resolve_song, load_notes
from auto_correct_pitches import (
    MERGED_ROOT, CHORDS_ROOT, OUTPUT_ROOT as CORRECTED_ROOT, NOTE_TAGS_FILENAME,
    match_notes_to_tags, parse_pitch_classes, ONSET_MATCH_TOLERANCE,
)

CHANGELOG_SUFFIX = "_changelog.csv"

DEFAULT_WINDOW_S = 20.0    # width of the sliding window used to find the densest correction cluster
DEFAULT_PADDING_S = 3.0    # context padding added around that cluster
DEFAULT_FALLBACK_S = 30.0  # window shown when a song has zero corrections at all

# Mirrors inspect_range.py's TAG_MARK convention (visual aid only, not a new judgment).
TAG_MARK = {"chord_tone": " ", "non_chord_tone": "x", "no_chord_data": "?"}


def discover_songs():
    """Songs this tool can compare: a merged MIDI, note tags, AND a
    changelog (i.e. auto_correct_pitches.py has actually run on it - the
    changelog is written even for a song with zero corrections)."""
    if not MERGED_ROOT.is_dir():
        return []
    songs = []
    for merged_path in sorted(MERGED_ROOT.glob("*.mid")):
        song_name = merged_path.stem
        if (CHORDS_ROOT / song_name / NOTE_TAGS_FILENAME).exists() and \
           (CORRECTED_ROOT / f"{song_name}{CHANGELOG_SUFFIX}").exists():
            songs.append(song_name)
    return songs


def load_changelog(song_name: str):
    """corrected/<song>_changelog.csv -> list of rows with typed fields.
    Empty list for a song with no corrections (the file still exists,
    header-only)."""
    path = CORRECTED_ROOT / f"{song_name}{CHANGELOG_SUFFIX}"
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["onset_time"] = float(r["onset_time"])
        r["original_pitch"] = int(r["original_pitch"])
        r["corrected_pitch"] = int(r["corrected_pitch"])
        r["semitone_distance_moved"] = int(r["semitone_distance_moved"])
        r["chord_segment_pitchclasses"] = parse_pitch_classes(r["chord_segment_pitchclasses"])
    return sorted(rows, key=lambda r: r["onset_time"])


def find_changelog_row(sorted_rows: list, onset: float, pitch: int, tol: float = ONSET_MATCH_TOLERANCE):
    """The changelog row matching this note's (onset, original_pitch), or
    None. `sorted_rows` must already be sorted by onset_time (load_changelog
    does this) - changelog row order on disk is NOT chronological
    (auto_correct_pitches.py writes Pass 1 grouped by pitch value, not by
    time), so this can't just zip positionally like merged-vs-tags can."""
    onsets = [r["onset_time"] for r in sorted_rows]
    i = bisect.bisect_left(onsets, onset - tol)
    while i < len(sorted_rows) and sorted_rows[i]["onset_time"] <= onset + tol:
        if abs(sorted_rows[i]["onset_time"] - onset) <= tol and sorted_rows[i]["original_pitch"] == pitch:
            return sorted_rows[i]
        i += 1
    return None


def load_corrected_notes(corrected_path: Path, expected_count: int):
    """corrected/<song>.mid's "other"-track notes, in the same canonical
    order match_notes_to_tags() uses for merged/ (so index i lines up with
    pairs[i] from that function) - safe because auto_correct_pitches.py
    never adds, removes, or reorders notes, only changes pitch in place.
    Raises ValueError if the count doesn't match, rather than silently
    misaligning two lists."""
    import pretty_midi
    midi = pretty_midi.PrettyMIDI(str(corrected_path))
    other_tracks = [inst for inst in midi.instruments if inst.name == "other"]
    notes = [n for inst in other_tracks for n in sorted(inst.notes, key=lambda n: n.start)]
    notes.sort(key=lambda n: n.start)
    if len(notes) != expected_count:
        raise ValueError(f"{corrected_path} has {len(notes)} 'other' notes, expected {expected_count} "
                          f"(from merged/ + tags) - pipeline outputs look out of sync; re-run auto_correct_pitches.py")
    return notes


def build_records(song_name: str):
    """One dict per note in merged/<song>.mid's "other" track, in
    chronological order, joining: merged/ (original pitch), corrected/
    (corrected pitch, or None if that song had zero corrections and
    corrected/<song>.mid was never written), the changelog row (if any), and
    the chord-tone tag/segment from chords/<song>/other_note_tags.csv.

    Returns (records, changelog_rows).
    """
    import pretty_midi

    merged_path = MERGED_ROOT / f"{song_name}.mid"
    tags_path = CHORDS_ROOT / song_name / NOTE_TAGS_FILENAME
    corrected_path = CORRECTED_ROOT / f"{song_name}.mid"

    tag_rows = load_notes(song_name)
    merged_midi = pretty_midi.PrettyMIDI(str(merged_path))
    pairs = match_notes_to_tags(merged_midi, tag_rows)  # [(Note, tag_row)], true chronological order

    changelog_rows = load_changelog(song_name)

    corrected_notes = load_corrected_notes(corrected_path, len(pairs)) if corrected_path.exists() else None

    records = []
    for i, (note, row) in enumerate(pairs):
        original_pitch = note.pitch
        corrected_pitch = corrected_notes[i].pitch if corrected_notes is not None else original_pitch
        changed = corrected_pitch != original_pitch
        chord_pcs = parse_pitch_classes(row["chord_pitch_classes"]) if row["chord_pitch_classes"] else set()
        changelog_row = find_changelog_row(changelog_rows, note.start, original_pitch)

        chord_tone_now = (corrected_pitch % 12 in chord_pcs) if chord_pcs else None

        bugs = []
        if changed:
            if changelog_row is None:
                bugs.append("pitch differs from merged/ but no matching changelog row was found")
            elif changelog_row["corrected_pitch"] != corrected_pitch:
                bugs.append(f"changelog says corrected_pitch={changelog_row['corrected_pitch']} "
                            f"but corrected/ MIDI has {corrected_pitch}")
            if chord_pcs and chord_tone_now is False:
                bugs.append("corrected pitch is NOT a chord tone (should always be, by construction)")
        elif changelog_row is not None:
            bugs.append("changelog has a matching row at this onset/pitch, but corrected/ MIDI shows no pitch change")

        records.append({
            "onset": note.start, "offset": note.end,
            "original_pitch": original_pitch, "corrected_pitch": corrected_pitch, "changed": changed,
            "tag": row["tag"], "chord_pcs": chord_pcs,
            "segment_onset": row["segment_onset"], "segment_offset": row["segment_offset"],
            "correction_type": changelog_row["correction_type"] if changelog_row else None,
            "chord_tone_now": chord_tone_now, "bugs": bugs,
        })
    return records, changelog_rows


def find_densest_window(onsets: list, window_width: float):
    """(start, end, count) of the tightest span containing the most
    correction onsets within any window of width <= window_width. Two-pointer
    sweep over sorted onset times - a standard "max points in a sliding
    window" scan, not a fixed grid of bins, so the returned span is exactly
    bounded by the actual corrections in the cluster, not an arbitrary bin
    edge."""
    onsets = sorted(onsets)
    best_count, best_span = 1, (onsets[0], onsets[0])
    left = 0
    for right in range(len(onsets)):
        while onsets[right] - onsets[left] > window_width:
            left += 1
        count = right - left + 1
        if count > best_count:
            best_count = count
            best_span = (onsets[left], onsets[right])
    return best_span[0], best_span[1], best_count


def pick_default_range(records: list, changelog_rows: list, window: float, padding: float):
    """Returns (start, end, explanation_str)."""
    if changelog_rows:
        span_start, span_end, count = find_densest_window([r["onset_time"] for r in changelog_rows], window)
        start, end = max(0.0, span_start - padding), span_end + padding
        return start, end, (f"no --start/--end given - showing the densest correction cluster: "
                             f"{count} correction(s) spanning {span_end - span_start:.2f}s "
                             f"(search window {window:.0f}s), padded by {padding:.0f}s")
    end = min(DEFAULT_FALLBACK_S, records[-1]["onset"] + 1.0) if records else DEFAULT_FALLBACK_S
    return 0.0, end, (f"no --start/--end given, and this song has zero corrections - "
                       f"showing the first {end:.0f}s instead")


def format_pitch(pitch: int) -> str:
    return f"{pitch:>3}({pitch_name(pitch % 12):<2})"


def format_segment(record) -> str:
    if not record["chord_pcs"]:
        return "no chord data"
    return f"seg[{float(record['segment_onset']):7.2f}-{float(record['segment_offset']):7.2f}] {format_chord_set(record['chord_pcs'])}"


def format_chord_tone_now(record) -> str:
    if record["chord_tone_now"] is None:
        return "n/a"
    if record["chord_tone_now"]:
        return "yes"
    return "BUG:no" if record["changed"] else "no"  # "no" alone is normal for an untouched non_chord_tone


def print_full_listing(records: list):
    print(f"--- Full listing ({len(records)} note(s)) ---")
    print(f"{'':1} {'onset':>8}  {'original':<10} {'corrected':<19} {'segment / chord':<34} "
          f"{'type':<15} {'chord_tone_now'}")
    for r in records:
        mark = TAG_MARK.get(r["tag"], "?")
        corr_display = "unchanged" if not r["changed"] else format_pitch(r["corrected_pitch"])
        ctype = r["correction_type"] or "-"
        line = (f"{mark} {r['onset']:8.2f}  {format_pitch(r['original_pitch']):<10} {corr_display:<19} "
                f"{format_segment(r):<34} {ctype:<15} {format_chord_tone_now(r)}")
        print(line)
        for bug in r["bugs"]:
            print(f"      !!! BUG: {bug}")


def print_diff_view(records: list):
    changed = [r for r in records if r["changed"]]
    print(f"\n--- Diff view: {len(changed)} note(s) actually changed ---")
    if not changed:
        print("  (no corrections in this window)")
        return
    for r in changed:
        print(f"  {r['onset']:8.2f}s  {format_pitch(r['original_pitch'])} -> {format_pitch(r['corrected_pitch'])}  "
              f"[{r['correction_type'] or '?'}]  chord={format_chord_set(r['chord_pcs'])}  "
              f"chord_tone_now={format_chord_tone_now(r)}")


def print_bug_report(records: list, song_name: str):
    """Bug cross-check over the WHOLE song (not just the printed window) -
    a pipeline inconsistency matters regardless of where --start/--end
    happens to point."""
    flagged = [(r, bug) for r in records for bug in r["bugs"]]
    print(f"\n--- Consistency check across all {len(records)} note(s) of {song_name!r} ---")
    if not flagged:
        print("  No inconsistencies found between merged/, corrected/, and the changelog. Good.")
        return
    print(f"  !!! {len(flagged)} issue(s) found:")
    for r, bug in flagged:
        print(f"    at {r['onset']:.3f}s (pitch {r['original_pitch']}): {bug}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--song", type=str, help="Song name (matches, or uniquely substring-matches, a merged/<name>.mid)")
    parser.add_argument("--start", type=str, help="Range start - seconds, or M:SS (default: densest correction cluster)")
    parser.add_argument("--end", type=str, help="Range end - seconds, or M:SS (default: densest correction cluster)")
    parser.add_argument("--window", type=float, default=DEFAULT_WINDOW_S,
                         help=f"Sliding-window width in seconds used to find the densest correction cluster "
                              f"(default {DEFAULT_WINDOW_S:.0f})")
    parser.add_argument("--padding", type=float, default=DEFAULT_PADDING_S,
                         help=f"Context padding in seconds added around the densest cluster (default {DEFAULT_PADDING_S:.0f})")
    parser.add_argument("--diff-only", action="store_true", help="Skip the full listing, print only the diff view")
    parser.add_argument("--list", action="store_true", help="List songs available to compare and exit")
    args = parser.parse_args()

    songs = discover_songs()

    if args.list or not args.song:
        if not songs:
            print(f"No songs found with merged + note tags + a changelog. "
                  f"Run auto_correct_pitches.py first.")
            return
        print(f"{len(songs)} song(s) available to compare:")
        for name in songs:
            n_corrections = len(load_changelog(name))
            print(f"  {name}" + (f"   ({n_corrections} correction(s))" if n_corrections else "   (0 corrections)"))
        if not args.song:
            return

    song_name = resolve_song(args.song, songs)
    if song_name is None:
        print(f"No unique match for --song {args.song!r} among songs with a changelog. Available songs:", file=sys.stderr)
        for name in songs:
            print(f"  {name}", file=sys.stderr)
        sys.exit(1)
    if song_name != args.song:
        print(f"(matched --song {args.song!r} -> {song_name!r})")

    try:
        records, changelog_rows = build_records(song_name)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    if (args.start is None) != (args.end is None):
        print("Both --start and --end must be given together (or neither, for the default range).", file=sys.stderr)
        sys.exit(1)

    if args.start is not None:
        start, end = parse_time(args.start), parse_time(args.end)
        if start >= end:
            print(f"--start ({start}) must be before --end ({end}).", file=sys.stderr)
            sys.exit(1)
    else:
        start, end, explanation = pick_default_range(records, changelog_rows, args.window, args.padding)
        print(explanation)

    records_in_range = [r for r in records if start <= r["onset"] < end]
    print(f"\n{song_name}  —  range {start:.2f}-{end:.2f}s  ({len(records_in_range)} note(s) in range, "
          f"{len(changelog_rows)} correction(s) in the whole song)")
    print()

    if not records_in_range:
        print("  (nothing in this range)")
    else:
        if not args.diff_only:
            print_full_listing(records_in_range)
        print_diff_view(records_in_range)

    print_bug_report(records, song_name)


if __name__ == "__main__":
    main()
