#!/usr/bin/env python3
"""
Computed pitch correction: uses the Chordino chord-tone tags
(chords/<song>/other_note_tags.csv, from tag_chord_tones.py) to snap
mis-transcribed pitches in merged/<song>.mid's "other" track, instead of a
hand-written corrections CSV (correct_notes.py, still available for manual
fixes - see the caveat on output paths below). Writes corrected/<song>.mid;
merged/<song>.mid is only ever opened for parsing, never written to.

CAVEAT, carried over from every earlier stage of this pipeline: Chordino's
chord segmentation is a heuristic (an HMM smoothing step over chroma
features), not verified ground truth, and a non_chord_tone tag is not
automatically "wrong" - passing tones, suspensions, and other intentional
non-chord-tone content are normal and look identical to a genuine
transcription slip at this layer (see tag_chord_tones.py, README "Harmonic
consistency check"). This script snaps notes anyway, on the theory that two
specific patterns below are high-enough-confidence to be worth it - that's a
hypothesis, not a given. Score corrected/ against the hand-labeled
ground_truth/ and ground_truth_anchor/ samples with evaluate_transcription.py
before treating this output as better than merged/'s original transcription.

Two-pass strategy, applied only to notes tagged non_chord_tone in the
"other" track. chord_tone and no_chord_data notes are always left untouched,
and nothing but pitch is ever changed (onset/offset/velocity untouched).

Pass 1 - stuck pitch (highest confidence): for each distinct raw pitch value
in the "other" track, walk its occurrences in time order. Once that pitch
has been legitimately a chord_tone at least once, every LATER occurrence of
that exact same pitch tagged non_chord_tone is corrected - the chord moved
on but the transcribed pitch didn't follow it. The first (legitimate)
occurrence itself is never touched.

Pass 2 - duration-filtered (lower confidence): every remaining
non_chord_tone note (not already corrected by Pass 1) whose duration
(offset - onset) exceeds --min-duration-ms (default 100ms) is corrected the
same way. Notes at or under the threshold are left alone - brief notes are
more likely genuine passing tones/ornaments than transcription errors.

Snapping (shared by both passes): the chord tone nearest the note's current
pitch by semitone distance, considering both octave directions from the
note's own pitch (not just its pitch class) so octave is preserved as
closely as possible. A tie (equidistant up/down, or between two different
chord tones) is broken by whichever target pitch is closer to the note's
immediate melodic neighbors (previous/next note in the "other" track, by
their original, uncorrected pitch).

Safety check before any snap is applied: if it would make the note overlap
in time with another note in the same "other" track that already has (or
was already corrected to) that exact pitch, the correction is SKIPPED and
the note is left exactly as transcribed. Standard MIDI note-on/note-off
encoding cannot reliably round-trip two overlapping notes of the identical
pitch - re-parsing the file can pair a note-off with the wrong note-on and
silently corrupt an unrelated, untouched note's duration. This was caught
by hand-verifying this script's own output against a written+reread MIDI
file before trusting it - see corrected/<song>_changelog.csv and the
skipped_pitch_collision count in the per-song summary for how often it fires.

Output per song (skipped entirely if nothing needs correcting):
    corrected/<song>.mid            - "other" track pitches snapped where
                                       flagged; every other track (bass,
                                       drums, vocals) passes through exactly
                                       as transcribed
    corrected/<song>_changelog.csv  - one row per corrected note: onset_time,
                                       original_pitch, corrected_pitch,
                                       correction_type (stuck_pitch or
                                       duration_filter), chord_segment_pitchclasses,
                                       semitone_distance_moved (signed:
                                       positive = moved up)

If corrected/<song>.mid already exists without a matching changelog next to
it, it likely came from correct_notes.py's hand-driven workflow rather than
this script - a warning is printed before it gets overwritten, so a human
correction is never silently clobbered by an automated guess.

Resumable like the rest of the pipeline: a song is skipped once its output
is newer than both merged/<song>.mid and chords/<song>/other_note_tags.csv.
Failures (missing/stale/mismatched inputs) are logged to
corrected/_auto_correct_failures.log instead of aborting the batch.

Usage:
    uv run auto_correct_pitches.py --limit 3          # smoke test 3 songs
    uv run auto_correct_pitches.py --dry-run           # preview, writes nothing
    uv run auto_correct_pitches.py --min-duration-ms 150
    uv run auto_correct_pitches.py                     # full corpus
    uv run auto_correct_pitches.py --retry-failed
"""
import argparse
import csv
import sys
import time
import traceback
from pathlib import Path

from inspect_range import load_notes

MERGED_ROOT = Path("merged")
CHORDS_ROOT = Path("chords")
OUTPUT_ROOT = Path("corrected")
FAILURE_LOG = OUTPUT_ROOT / "_auto_correct_failures.log"
NOTE_TAGS_FILENAME = "other_note_tags.csv"

DEFAULT_MIN_DURATION_MS = 100
ONSET_MATCH_TOLERANCE = 1e-4  # sanity check only - notes/tags come from the same source in order


def collect_jobs():
    """(song_name, merged_path, tags_path) for every song with both inputs."""
    jobs = []
    if not MERGED_ROOT.is_dir():
        return jobs
    for merged_path in sorted(MERGED_ROOT.glob("*.mid")):
        song_name = merged_path.stem
        tags_path = CHORDS_ROOT / song_name / NOTE_TAGS_FILENAME
        if tags_path.exists():
            jobs.append((song_name, merged_path, tags_path))
    return jobs


def find_missing_inputs():
    missing = []
    if not MERGED_ROOT.is_dir():
        return missing
    for merged_path in sorted(MERGED_ROOT.glob("*.mid")):
        song_name = merged_path.stem
        if not (CHORDS_ROOT / song_name / NOTE_TAGS_FILENAME).exists():
            missing.append((song_name, merged_path))
    return missing


def is_done(song_name: str, merged_path: Path, tags_path: Path) -> bool:
    # The changelog is written for every processed song, even an empty one
    # (0 corrections) - unlike corrected/<song>.mid, which only exists when
    # there was something to change. Checking the changelog is what makes a
    # legitimately-clean song "done" instead of being reprocessed forever.
    changelog_path = OUTPUT_ROOT / f"{song_name}_changelog.csv"
    if not changelog_path.exists():
        return False
    out_mtime = changelog_path.stat().st_mtime
    return out_mtime >= merged_path.stat().st_mtime and out_mtime >= tags_path.stat().st_mtime


def log_failure(song_name: str, error: str):
    FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with FAILURE_LOG.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{song_name}\t{error}\n")


def match_notes_to_tags(midi, tag_rows):
    """Zip merged/<song>.mid's "other"-track Note objects with
    other_note_tags.csv's rows, using the exact same traversal order
    tag_chord_tones.py used to generate that CSV (so row i <-> note i,
    positionally, without any onset/pitch fuzzy-matching). Then re-sorts
    both together by true onset time, for correct chronological analysis
    even in the rare case of more than one "other" instrument. Raises
    ValueError if counts or a spot-check of (onset, pitch) don't line up -
    a stale other_note_tags.csv should never be silently trusted."""
    other_tracks = [inst for inst in midi.instruments if inst.name == "other"]
    notes = [n for inst in other_tracks for n in sorted(inst.notes, key=lambda n: n.start)]

    if len(notes) != len(tag_rows):
        raise ValueError(f"{len(notes)} notes in merged MIDI's 'other' track vs "
                          f"{len(tag_rows)} rows in other_note_tags.csv - re-run tag_chord_tones.py")

    pairs = list(zip(notes, tag_rows))
    for note, row in pairs:
        if abs(note.start - row["onset"]) > ONSET_MATCH_TOLERANCE or note.pitch != row["pitch"]:
            raise ValueError(f"other_note_tags.csv looks stale: note at {note.start:.3f}/{note.pitch} "
                              f"doesn't match tag row {row['onset']:.3f}/{row['pitch']} - re-run tag_chord_tones.py")

    pairs.sort(key=lambda p: p[0].start)
    return pairs


def nearest_chord_tone(pitch: int, chord_pitch_classes: set, neighbor_pitches: list):
    """(target_pitch, signed_semitone_distance) - the chord tone closest to
    `pitch` by absolute semitone distance, considering both octave
    directions. Ties broken by whichever target is closer to the average of
    neighbor_pitches; falls back to the lower target if there are no
    neighbors either, for a stable, deterministic result."""
    candidates = []  # (abs_distance, target_pitch)
    for pc in chord_pitch_classes:
        up = (pc - pitch) % 12          # 1..11: semitones to move up to reach this pitch class
        down = up - 12                  # negative: semitones to move down instead
        candidates.append((up, pitch + up))
        candidates.append((-down, pitch + down))

    min_dist = min(d for d, _ in candidates)
    best = sorted({t for d, t in candidates if d == min_dist})
    if len(best) > 1 and neighbor_pitches:
        avg_neighbor = sum(neighbor_pitches) / len(neighbor_pitches)
        best.sort(key=lambda t: abs(t - avg_neighbor))
    target = best[0]
    return target, target - pitch


def plan_pass1(pairs):
    """Returns {index: chord_pitch_classes} for every note (index into
    `pairs`) selected as a Pass-1 stuck-pitch correction."""
    by_pitch = {}
    for i, (note, row) in enumerate(pairs):
        by_pitch.setdefault(note.pitch, []).append(i)

    selected = {}
    for pitch, indices in by_pitch.items():
        seen_legit = False
        for i in indices:  # `indices` is already in ascending (== chronological) order
            row = pairs[i][1]
            if row["tag"] == "chord_tone":
                seen_legit = True
            elif row["tag"] == "non_chord_tone" and seen_legit:
                selected[i] = parse_pitch_classes(row["chord_pitch_classes"])
            # no_chord_data: skip, doesn't change seen_legit either way
    return selected


def plan_pass2(pairs, pass1_indices: set, min_duration_s: float):
    """Returns {index: chord_pitch_classes} for every remaining
    non_chord_tone note above the duration threshold."""
    selected = {}
    for i, (note, row) in enumerate(pairs):
        if i in pass1_indices or row["tag"] != "non_chord_tone":
            continue
        if (note.end - note.start) > min_duration_s:
            selected[i] = parse_pitch_classes(row["chord_pitch_classes"])
    return selected


def parse_pitch_classes(raw: str) -> set:
    return {int(pc) for pc in raw.split(",") if pc != ""}


def would_collide(pairs, index: int, target_pitch: int) -> bool:
    """True if giving pairs[index]'s note `target_pitch` would make it
    overlap in time with another note in the same "other" track that
    already has (or was already corrected to) that same pitch. Standard MIDI
    note-on/note-off encoding cannot reliably round-trip two overlapping
    notes of the identical pitch - re-parsing can pair a note-off with the
    wrong note-on and silently corrupt an unrelated, untouched note's
    duration. Checked against notes' CURRENT pitch (reflecting any
    corrections already applied earlier in this same run), not their
    original one, since the collision is about the file actually written."""
    note = pairs[index][0]
    start, end = note.start, note.end
    for j, (other_note, _) in enumerate(pairs):
        if j == index or other_note.pitch != target_pitch:
            continue
        if other_note.start < end and start < other_note.end:  # strict overlap; back-to-back is fine
            return True
    return False


def correct_song(song_name: str, merged_path: Path, tags_path: Path, min_duration_s: float):
    """Returns (changelog_rows, midi_or_None, summary_dict). midi_or_None is
    the mutated pretty_midi object if >=1 correction applied, else None."""
    import pretty_midi

    tag_rows = load_notes(song_name)
    midi = pretty_midi.PrettyMIDI(str(merged_path))
    pairs = match_notes_to_tags(midi, tag_rows)

    original_pitches = [note.pitch for note, _ in pairs]  # neighbor lookups use pre-correction pitches

    pass1 = plan_pass1(pairs)
    pass2 = plan_pass2(pairs, set(pass1), min_duration_s)

    # Applied one at a time, not batch-planned-then-batch-applied: each note's
    # own target only ever depends on its own (never-mutated) original pitch,
    # but collision-checking must see any pitch already committed earlier in
    # this same loop, so mutation happens incrementally as we go.
    attempts = [(i, "stuck_pitch", chord_pcs) for i, chord_pcs in pass1.items()]
    attempts += [(i, "duration_filter", chord_pcs) for i, chord_pcs in pass2.items()]

    changelog_rows = []
    n_skipped_collision = 0
    for i, correction_type, chord_pcs in attempts:
        note, row = pairs[i]
        neighbors = [original_pitches[i - 1]] if i > 0 else []
        if i < len(pairs) - 1:
            neighbors.append(original_pitches[i + 1])
        target, distance = nearest_chord_tone(note.pitch, chord_pcs, neighbors)

        if would_collide(pairs, i, target):
            n_skipped_collision += 1
            continue  # leave this note's pitch exactly as transcribed - safer than a corrupted file

        changelog_rows.append({
            "onset_time": note.start, "original_pitch": note.pitch, "corrected_pitch": target,
            "correction_type": correction_type,
            "chord_segment_pitchclasses": ",".join(str(pc) for pc in sorted(chord_pcs)),
            "semitone_distance_moved": distance,
        })
        note.pitch = target

    applied_indices = {row_i for row_i in range(len(pairs))
                        if pairs[row_i][0].pitch != original_pitches[row_i]}
    n_pass1_applied = sum(1 for i, t, _ in attempts if t == "stuck_pitch" and i in applied_indices)
    n_pass2_applied = sum(1 for i, t, _ in attempts if t == "duration_filter" and i in applied_indices)

    n_chord_tone = sum(1 for _, row in pairs if row["tag"] == "chord_tone")
    n_no_data = sum(1 for _, row in pairs if row["tag"] == "no_chord_data")
    attempted_indices = {i for i, _, _ in attempts}
    n_left_brief = sum(1 for i, (_, row) in enumerate(pairs)
                        if row["tag"] == "non_chord_tone" and i not in attempted_indices)
    summary = {
        "total": len(pairs), "pass1": n_pass1_applied, "pass2": n_pass2_applied,
        "skipped_collision": n_skipped_collision,
        "left_brief": n_left_brief, "chord_tone": n_chord_tone, "no_chord_data": n_no_data,
    }
    return changelog_rows, (midi if changelog_rows else None), summary


CHANGELOG_HEADER = ["onset_time", "original_pitch", "corrected_pitch", "correction_type",
                    "chord_segment_pitchclasses", "semitone_distance_moved"]


def write_outputs(song_name: str, midi, changelog_rows, dry_run: bool):
    """Always writes the changelog (even empty, header-only, for a
    legitimately-clean song - see is_done()'s comment for why). Only writes
    corrected/<song>.mid when midi is not None (i.e. >=1 correction)."""
    out_path = OUTPUT_ROOT / f"{song_name}.mid"
    changelog_path = OUTPUT_ROOT / f"{song_name}_changelog.csv"

    if midi is not None and out_path.exists() and not changelog_path.exists():
        print(f"    WARNING: {out_path} exists with no matching changelog - it may be hand-corrected "
              f"output from correct_notes.py. {'Would overwrite' if dry_run else 'Overwriting'} it.")

    if dry_run:
        return

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    if midi is not None:
        midi.write(str(out_path))
    with changelog_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CHANGELOG_HEADER)
        writer.writeheader()
        writer.writerows(changelog_rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None,
                         help="Only process the first N unfinished songs (for smoke testing)")
    parser.add_argument("--dry-run", action="store_true",
                         help="Show what would be corrected, without writing corrected/ or the log")
    parser.add_argument("--retry-failed", action="store_true",
                         help="Clear the failure log first so previously-failed songs are attempted again")
    parser.add_argument("--min-duration-ms", type=float, default=DEFAULT_MIN_DURATION_MS,
                         help=f"Pass 2 duration threshold in milliseconds (default {DEFAULT_MIN_DURATION_MS})")
    args = parser.parse_args()
    min_duration_s = args.min_duration_ms / 1000.0

    if args.retry_failed and FAILURE_LOG.exists():
        FAILURE_LOG.unlink()

    jobs = collect_jobs()
    todo = [j for j in jobs if not is_done(*j)]
    print(f"{len(jobs)} songs with both a merged MIDI and note tags found, "
          f"{len(jobs) - len(todo)} already corrected, {len(todo)} to process. "
          f"min_duration={args.min_duration_ms:.0f}ms"
          + (" [DRY RUN]" if args.dry_run else ""))

    missing = find_missing_inputs()
    if missing:
        names = ", ".join(n for n, _ in missing[:5]) + (", ..." if len(missing) > 5 else "")
        print(f"{len(missing)} song(s) have a merged MIDI but no note tags yet "
              f"(run tag_chord_tones.py): {names}")

    if args.limit:
        todo = todo[:args.limit]

    if args.dry_run:
        for name, _, _ in todo:
            print(f"WOULD CORRECT: {name}")

    if not todo:
        print("Nothing to do.")
        return

    ok, failed, skipped_empty = 0, 0, 0
    for i, (song_name, merged_path, tags_path) in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {song_name} ...", flush=True)
        try:
            changelog_rows, midi, summary = correct_song(song_name, merged_path, tags_path, min_duration_s)
            print(f"    total={summary['total']}  chord_tone(unchanged)={summary['chord_tone']}  "
                  f"pass1_stuck_pitch={summary['pass1']}  pass2_duration_filter={summary['pass2']}  "
                  f"skipped_pitch_collision={summary['skipped_collision']}  "
                  f"left_as_is(brief non_chord_tone)={summary['left_brief']}  "
                  f"no_chord_data(unchanged)={summary['no_chord_data']}")
            write_outputs(song_name, midi, changelog_rows, args.dry_run)
            if midi is None:
                print(f"    nothing to correct - empty changelog written, no MIDI written"
                      + (" (dry run: neither written)" if args.dry_run else ""))
                skipped_empty += 1
            elif not args.dry_run:
                print(f"    -> {OUTPUT_ROOT / (song_name + '.mid')}, "
                      f"{OUTPUT_ROOT / (song_name + '_changelog.csv')}")
            ok += 1
        except Exception as e:
            log_failure(song_name, f"{type(e).__name__}: {e}")
            print(f"  FAILED (see {FAILURE_LOG})")
            traceback.print_exc(file=sys.stderr)
            failed += 1

    print(f"\nDone. {ok} succeeded ({skipped_empty} needed no correction), {failed} failed."
          + (" [DRY RUN - nothing written]" if args.dry_run else ""))
    if failed:
        print(f"See {FAILURE_LOG} for details. Re-run this script to retry (or pass --retry-failed).")


if __name__ == "__main__":
    main()
