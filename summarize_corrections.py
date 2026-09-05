#!/usr/bin/env python3
"""
Corpus-wide summary of auto_correct_pitches.py's output: reads every
eligible song's chords/<song>/other_note_tags.csv (for total note counts and
the chord-tone/non-chord-tone/no-chord-data breakdown) and
corrected/<song>_changelog.csv (for what was actually corrected), and
reports:

  - total notes across the corpus, and total corrections by type
    (stuck_pitch vs duration_filter)
  - the distribution of semitone distances moved (bucketed 1/2/3/4/5+,
    plus mean/median/max) - a corpus dominated by 1-2 semitone moves is the
    expected/healthy case; a lot of 5+ semitone moves is a flag to go check
    whether the chord detection or the snapping logic itself is off
  - the 10 songs with the highest correction RATE (corrections / total
    notes, not raw count, so it's not just picking the longest songs) -
    these are worth spot-checking first
  - every song with zero corrections, cross-checked against its own tag
    data: a song that's "clean" because the transcription genuinely agrees
    with the chord read looks the same, from auto_correct_pitches.py's
    output alone, as a song where chord extraction silently produced
    nothing useful (all no_chord_data) - this cross-check tells them apart

Read-only: only reads chords/*/other_note_tags.csv,
corrected/*_changelog.csv, and corrected/_auto_correct_failures.log; writes
only corrected/_correction_summary.csv (plus the terminal report).

A song only shows up here if auto_correct_pitches.py has actually run on
it - i.e. it has a changelog (present, possibly empty, for every
successfully-processed song since that script's resumability fix) or an
entry in its failure log. Run auto_correct_pitches.py across the corpus
first; this won't run it for you.

Usage:
    uv run summarize_corrections.py
"""
import csv
import statistics
from pathlib import Path

from inspect_range import load_notes

MERGED_ROOT = Path("merged")
CHORDS_ROOT = Path("chords")
CORRECTED_ROOT = Path("corrected")
FAILURE_LOG = CORRECTED_ROOT / "_auto_correct_failures.log"
NOTE_TAGS_FILENAME = "other_note_tags.csv"
SUMMARY_CSV = CORRECTED_ROOT / "_correction_summary.csv"

SUMMARY_HEADER = [
    "song", "total_notes", "chord_tone", "non_chord_tone", "no_chord_data",
    "stuck_pitch_corrections", "duration_filter_corrections", "total_corrections",
    "correction_rate", "avg_abs_semitone_distance",
    "dist_1_2_semitones", "dist_3_4_semitones", "dist_5plus_semitones", "status",
]


def eligible_songs():
    """Songs auto_correct_pitches.py would consider - same universe as its
    own collect_jobs(): a merged MIDI plus note tags."""
    if not MERGED_ROOT.is_dir():
        return []
    return sorted(p.stem for p in MERGED_ROOT.glob("*.mid")
                  if (CHORDS_ROOT / p.stem / NOTE_TAGS_FILENAME).exists())


def failed_songs():
    if not FAILURE_LOG.exists():
        return set()
    names = set()
    for line in FAILURE_LOG.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            names.add(parts[1])
    return names


def summarize_song(song_name: str, failed: set):
    tag_rows = load_notes(song_name)
    n_chord_tone = sum(1 for r in tag_rows if r["tag"] == "chord_tone")
    n_non_chord_tone = sum(1 for r in tag_rows if r["tag"] == "non_chord_tone")
    n_no_chord_data = sum(1 for r in tag_rows if r["tag"] == "no_chord_data")

    changelog_path = CORRECTED_ROOT / f"{song_name}_changelog.csv"
    if not changelog_path.exists():
        status = "failed" if song_name in failed else "not_processed"
        return {
            "song": song_name, "total_notes": len(tag_rows),
            "chord_tone": n_chord_tone, "non_chord_tone": n_non_chord_tone,
            "no_chord_data": n_no_chord_data,
            "stuck_pitch_corrections": 0, "duration_filter_corrections": 0,
            "total_corrections": 0, "correction_rate": 0.0, "avg_abs_semitone_distance": 0.0,
            "dist_1_2_semitones": 0, "dist_3_4_semitones": 0, "dist_5plus_semitones": 0,
            "status": status,
        }, []

    changelog_rows = list(csv.DictReader(changelog_path.open(newline="")))
    distances = [abs(int(r["semitone_distance_moved"])) for r in changelog_rows]
    n_stuck = sum(1 for r in changelog_rows if r["correction_type"] == "stuck_pitch")
    n_dur = sum(1 for r in changelog_rows if r["correction_type"] == "duration_filter")
    total_corrections = len(changelog_rows)

    return {
        "song": song_name, "total_notes": len(tag_rows),
        "chord_tone": n_chord_tone, "non_chord_tone": n_non_chord_tone, "no_chord_data": n_no_chord_data,
        "stuck_pitch_corrections": n_stuck, "duration_filter_corrections": n_dur,
        "total_corrections": total_corrections,
        "correction_rate": (total_corrections / len(tag_rows)) if tag_rows else 0.0,
        "avg_abs_semitone_distance": (sum(distances) / len(distances)) if distances else 0.0,
        "dist_1_2_semitones": sum(1 for d in distances if d in (1, 2)),
        "dist_3_4_semitones": sum(1 for d in distances if d in (3, 4)),
        "dist_5plus_semitones": sum(1 for d in distances if d >= 5),
        "status": "ok",
    }, distances


def print_report(rows, all_distances):
    processed = [r for r in rows if r["status"] == "ok"]
    failed = [r for r in rows if r["status"] == "failed"]
    not_processed = [r for r in rows if r["status"] == "not_processed"]

    total_notes = sum(r["total_notes"] for r in rows)
    total_stuck = sum(r["stuck_pitch_corrections"] for r in rows)
    total_dur = sum(r["duration_filter_corrections"] for r in rows)
    total_corrections = total_stuck + total_dur

    print(f"=== Corpus-wide correction summary ===")
    print(f"{len(rows)} eligible song(s): {len(processed)} processed, {len(failed)} failed, "
          f"{len(not_processed)} not yet processed")
    if len(rows) < 10:
        print(f"NOTE: only {len(rows)} song(s) in the corpus right now (merged/ hasn't caught up to "
              f"the rest of the 75-song corpus yet) - every ranking below is over a small pool.")
    print()

    print(f"Total notes across all songs (in the 'other' track):       {total_notes}")
    print(f"Total corrections applied:                                  {total_corrections}")
    print(f"  stuck_pitch:      {total_stuck}")
    print(f"  duration_filter:  {total_dur}")
    print()

    print(f"=== Semitone distance distribution ({len(all_distances)} corrections) ===")
    if all_distances:
        buckets = {1: 0, 2: 0, 3: 0, 4: 0}
        five_plus = 0
        for d in all_distances:
            if d >= 5:
                five_plus += 1
            else:
                buckets[d] = buckets.get(d, 0) + 1
        for b in (1, 2, 3, 4):
            n = buckets.get(b, 0)
            print(f"  {b} semitone{'s' if b != 1 else ' '}:  {n:5d}  ({100*n/len(all_distances):5.1f}%)")
        print(f"  5+ semitones:  {five_plus:5d}  ({100*five_plus/len(all_distances):5.1f}%)")
        print(f"  mean={statistics.mean(all_distances):.2f}  median={statistics.median(all_distances):.1f}  "
              f"max={max(all_distances)}")
        if five_plus / len(all_distances) > 0.10:
            print(f"  FLAG: {100*five_plus/len(all_distances):.1f}% of corrections moved 5+ semitones - "
                  f"worth checking whether chord detection or the snapping logic is behaving as expected "
                  f"on those specific notes, rather than trusting them by default.")
        else:
            print(f"  Mostly small moves (1-2 semitones dominate) - the expected/healthy pattern.")
    else:
        print("  (no corrections applied yet)")
    print()

    print(f"=== Top 10 songs by correction RATE (corrections / total notes) ===")
    ranked = sorted(processed, key=lambda r: -r["correction_rate"])
    for r in ranked[:10]:
        print(f"  {r['correction_rate']:5.1%}  ({r['total_corrections']:4d}/{r['total_notes']:<4d} notes)  "
              f"stuck={r['stuck_pitch_corrections']:3d}  duration={r['duration_filter_corrections']:3d}  "
              f"{r['song']}")
    if len(ranked) < 10:
        print(f"  (only {len(ranked)} processed song(s) - 'top 10' is just all of them)")
    print()

    print(f"=== Songs with zero corrections ===")
    zero = [r for r in processed if r["total_corrections"] == 0]
    if not zero:
        print("  (none - every processed song had at least one correction)")
    for r in zero:
        has_chord_data = r["chord_tone"] + r["non_chord_tone"] > 0
        flag = "" if has_chord_data else "  <-- SUSPECT: 100% no_chord_data, chord extraction may have silently failed"
        print(f"  {r['song']}: {r['total_notes']} notes, {r['chord_tone']} chord_tone, "
              f"{r['non_chord_tone']} non_chord_tone, {r['no_chord_data']} no_chord_data{flag}")
    print()

    if failed:
        print(f"=== Failed songs (see {FAILURE_LOG}) ===")
        for r in failed:
            print(f"  {r['song']}")
        print()
    if not_processed:
        print(f"=== Not yet processed (run auto_correct_pitches.py) ===")
        for r in not_processed:
            print(f"  {r['song']}")


def main():
    songs = eligible_songs()
    if not songs:
        print(f"No eligible songs found (need both merged/<song>.mid and "
              f"chords/<song>/{NOTE_TAGS_FILENAME}). Run the pipeline first.")
        return

    failed = failed_songs()
    rows, all_distances = [], []
    for song_name in songs:
        row, distances = summarize_song(song_name, failed)
        rows.append(row)
        all_distances.extend(distances)

    print_report(rows, all_distances)

    CORRECTED_ROOT.mkdir(parents=True, exist_ok=True)
    with SUMMARY_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_HEADER)
        writer.writeheader()
        for r in rows:
            out = dict(r)
            out["correction_rate"] = f"{r['correction_rate']:.4f}"
            out["avg_abs_semitone_distance"] = f"{r['avg_abs_semitone_distance']:.2f}"
            writer.writerow(out)
    print(f"\nWritten to {SUMMARY_CSV}")


if __name__ == "__main__":
    main()
