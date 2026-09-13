#!/usr/bin/env python3
"""
Correction layer, rebuilt from scratch: identifies the "other" track in each
merged/<song>.mid explicitly (by instrument name + program/is_drum, never by
track index), then applies ONLY the highest-confidence automated correction
- "stuck pitch" across a chord change - and leaves every other non_chord_tone
note for human review instead of guessing.

Track identification (printed for every song, so it's always visible, not
just on failure):
    A track is a candidate "other" track if inst.name.strip().lower() ==
    "other". Exactly one candidate is required:
        - zero candidates  -> song is SKIPPED and logged (no 'other' track)
        - >1 candidate     -> song is SKIPPED and logged (ambiguous)
    The matched track's index, name, program, and is_drum are printed and
    logged. A candidate with is_drum=True is refused (an "other" track
    should never be a drum kit) rather than silently accepted. This script
    never reads, tags, or mutates notes in any track but the one identified
    this way - bass and drums are explicitly out of scope, and a snapshot
    of every other track's notes is asserted unchanged before anything is
    written (see _assert_only_other_track_changed below), so a bug that
    accidentally touched the wrong track would fail loudly instead of
    silently corrupting output.

Stuck-pitch correction (the only automatic edit this script makes): using
chords/<song>/other_note_tags.csv (from tag_chord_tones.py), chord segments
are ordered chronologically. A note tagged non_chord_tone in segment N is
"stuck" if that exact MIDI pitch (not just pitch class) was tagged
chord_tone by some note in the IMMEDIATELY PRECEDING segment N-1 - i.e. the
harmony moved on at the N-1 -> N chord change but this note's pitch didn't
follow it. Only this specific, narrow pattern is auto-corrected; a pitch
that was legitimately a chord tone several segments back but not in the
segment right before this one is left alone (see below).

Snapping: the stuck note is moved to the chord tone in segment N's own
pitch-class set nearest to its current pitch by semitone distance,
considering both octave directions from the note's current pitch (so octave
is preserved as closely as possible, not collapsed to a fixed octave). Ties
are broken by whichever candidate pitch is closer to the note's immediate
melodic neighbors (previous/next note in the "other" track, by their
original, pre-correction pitch); with no neighbors either, the lower
candidate wins for a stable, deterministic result.

Safety check before any snap is applied: if it would make the note overlap
in time with another note in the "other" track that already has (or was
already corrected to) that exact pitch, the correction is SKIPPED and the
note is left exactly as transcribed - standard MIDI note-on/note-off
encoding can't reliably round-trip two overlapping same-pitch notes, and a
mis-paired note-off on re-parse can silently corrupt an unrelated note's
duration.

Every other non_chord_tone note (not the stuck-pitch pattern above; a
no_chord_data note is a different tag entirely and never appears here since
there's no chord to check it against) is NOT touched automatically. It's
written instead to corrected/<song>_proposals.csv for a human to review by
ear against the audio, alongside the same nearest-chord-tone suggestion the
stuck-pitch pass uses - see write_proposals below for the exact columns.
This script never applies those rows on its own; a later step (not part of
this file) is expected to read back whichever rows still say "correct" in
the action column and apply just those.

Note durations below --min-duration-ms (default 100ms) are included in the
proposals file too (so nothing is invisible), but default to action="keep"
rather than "correct" - a brief note is more likely a genuine passing tone
or ornament than a transcription slip, so the file starts biased toward
leaving them alone; a human can still flip one to "correct" by hand.

Output per song:
    corrected/<song>.mid                    - only written if >=1 stuck-pitch
                                               correction was actually applied;
                                               every other track (bass, drums)
                                               passes through byte-identical
    corrected/<song>_changelog.csv          - always written (even empty/
                                               header-only for a clean song):
                                               onset_time, original_pitch,
                                               corrected_pitch, correction_type
                                               (always "stuck_pitch"),
                                               chord_segment_pitchclasses,
                                               semitone_distance_moved (signed,
                                               + = moved up)
    corrected/<song>_proposals.csv          - always written (even empty/
                                               header-only): one row per
                                               remaining non_chord_tone note,
                                               sorted by onset_time. Columns:
                                               onset_time, offset_time,
                                               duration_ms, current_pitch (post
                                               stuck-pitch correction - moot
                                               for these rows since none of
                                               them were touched by it),
                                               current_pitch_name (e.g. "C#4"),
                                               chord_segment_start,
                                               chord_segment_end,
                                               chord_pitchclasses (e.g. "0,4,7"),
                                               nearest_chord_tone,
                                               nearest_chord_tone_name,
                                               semitone_distance, action
                                               (pre-filled "correct" or "keep"
                                               per the duration rule above -
                                               edit this column by hand).
                                               No chord_name/chord-label
                                               column: extract_chords.py's
                                               Chordino output is raw
                                               pitch-class membership only, it
                                               never produces a chord-name
                                               label to derive one from.
    corrected/_correct_notes_failures.log   - one line per skipped song
                                               (ambiguous/missing track,
                                               missing/stale note tags)

Nothing here is part of the automated pipeline (separate_stems.py ->
transcribe_*.py -> merge_stems.py -> tag_chord_tones.py); it's a separate,
opt-in step run after tagging. merged/ is read-only to this script - only
ever parsed, never written to.

Usage (from the repo root; ARGS is forwarded as CLI flags):
    make correct-notes ARGS="--limit 3"               # smoke test the first 3 songs
    make correct-notes ARGS="--song 'Some Song'"      # just one song
    make correct-notes ARGS="--dry-run"               # preview only, writes nothing
    make correct-notes ARGS="--min-duration-ms 150"   # proposals-file duration threshold
    make correct-notes                                # full corpus
"""
import argparse
import csv
import sys
import time
from pathlib import Path

MERGED_ROOT = Path("merged")
CHORDS_ROOT = Path("chords")
OUTPUT_ROOT = Path("corrected")
NOTE_TAGS_FILENAME = "other_note_tags.csv"
FAILURE_LOG = OUTPUT_ROOT / "_correct_notes_failures.log"

ONSET_MATCH_TOLERANCE = 1e-4  # sanity check only - notes/tags come from the same source in order
DEFAULT_MIN_DURATION_MS = 100

PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

CHANGELOG_HEADER = ["onset_time", "original_pitch", "corrected_pitch", "correction_type",
                    "chord_segment_pitchclasses", "semitone_distance_moved"]
PROPOSALS_HEADER = ["onset_time", "offset_time", "duration_ms", "current_pitch",
                    "current_pitch_name", "chord_segment_start", "chord_segment_end",
                    "chord_pitchclasses", "nearest_chord_tone", "nearest_chord_tone_name",
                    "semitone_distance", "action"]

ACTION_CORRECT = "correct"
ACTION_KEEP = "keep"


def pitch_name(pitch: int) -> str:
    """MIDI pitch -> scientific pitch notation, e.g. 61 -> "C#4" (MIDI 60 ==
    middle C == C4, the standard convention pretty_midi/most DAWs use)."""
    return f"{PITCH_NAMES[pitch % 12]}{pitch // 12 - 1}"


def log_failure(song_name: str, error: str):
    FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with FAILURE_LOG.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{song_name}\t{error}\n")


def collect_songs():
    if not MERGED_ROOT.is_dir():
        return []
    return [p.stem for p in sorted(MERGED_ROOT.glob("*.mid"))]


def identify_other_track(midi, song_name: str):
    """Prints every track's index/name/program/is_drum/note-count, then
    returns (index, instrument) for the single track named "other", or
    (None, None) after printing+logging why (none found, ambiguous, or a
    same-named track that's actually a drum kit)."""
    for idx, inst in enumerate(midi.instruments):
        kind = "drum kit" if inst.is_drum else f"program={inst.program}"
        print(f"    idx={idx} name={inst.name!r} {kind} notes={len(inst.notes)}")

    candidates = [(idx, inst) for idx, inst in enumerate(midi.instruments)
                  if inst.name.strip().lower() == "other"]

    if not candidates:
        msg = "no track named 'other' found"
        print(f"  SKIP: {msg}")
        log_failure(song_name, msg)
        return None, None

    if len(candidates) > 1:
        msg = f"{len(candidates)} tracks named 'other' (ambiguous): indices {[i for i, _ in candidates]}"
        print(f"  SKIP: {msg}")
        log_failure(song_name, msg)
        return None, None

    idx, inst = candidates[0]
    if inst.is_drum:
        msg = f"track idx={idx} is named 'other' but is_drum=True - refusing to treat it as the melodic 'other' track"
        print(f"  SKIP: {msg}")
        log_failure(song_name, msg)
        return None, None

    # Belt-and-suspenders: the candidate filter above already guarantees
    # this, but assert it explicitly rather than relying on that filter
    # alone never having a bug.
    assert inst.name.strip().lower() == "other" and inst.name.strip().lower() not in ("bass", "drums"), \
        "identify_other_track must only ever return a track literally named 'other'"

    print(f"  MATCHED: idx={idx} name={inst.name!r} program={inst.program} "
          f"is_drum={inst.is_drum} notes={len(inst.notes)}")
    return idx, inst


def snapshot_other_instruments(midi, other_idx: int):
    """(pitch, start, end, velocity) tuples for every track that is NOT the
    identified 'other' track, keyed by index - used to assert afterward that
    bass/drums/anything else was never touched."""
    return {
        idx: [(n.pitch, n.start, n.end, n.velocity) for n in inst.notes]
        for idx, inst in enumerate(midi.instruments) if idx != other_idx
    }


def assert_only_other_track_changed(midi, other_idx: int, snapshot: dict):
    for idx, inst in enumerate(midi.instruments):
        if idx == other_idx:
            continue
        current = [(n.pitch, n.start, n.end, n.velocity) for n in inst.notes]
        assert current == snapshot[idx], \
            f"BUG: track idx={idx} ({inst.name!r}) changed - only the identified 'other' track may be modified"


def load_note_tags(tags_path: Path):
    rows = []
    with tags_path.open(newline="") as f:
        for row in csv.DictReader(f):
            row["onset"] = float(row["onset"])
            row["pitch"] = int(row["pitch"])
            rows.append(row)
    return rows


def match_notes_to_tags(other_inst, tag_rows: list):
    """Zip the 'other' track's Note objects (sorted by onset, the same
    traversal order tag_chord_tones.py used) with other_note_tags.csv's rows
    positionally, then spot-check every pair's (onset, pitch) actually
    agree. Raises ValueError if counts or values don't line up - a stale
    other_note_tags.csv should never be silently trusted."""
    notes = sorted(other_inst.notes, key=lambda n: n.start)
    if len(notes) != len(tag_rows):
        raise ValueError(f"{len(notes)} notes in 'other' track vs {len(tag_rows)} rows in "
                          f"{NOTE_TAGS_FILENAME} - stale? re-run tag_chord_tones.py")
    pairs = list(zip(notes, tag_rows))
    for note, row in pairs:
        if abs(note.start - row["onset"]) > ONSET_MATCH_TOLERANCE or note.pitch != row["pitch"]:
            raise ValueError(f"{NOTE_TAGS_FILENAME} looks stale: note at {note.start:.3f}/{note.pitch} "
                              f"doesn't match tag row {row['onset']:.3f}/{row['pitch']} - re-run tag_chord_tones.py")
    return pairs


def parse_pitch_classes(raw: str) -> set:
    return {int(pc) for pc in raw.split(",") if pc != ""}


def build_segments(tag_rows: list):
    """Ordered list of distinct chord segments (chronological, derived from
    rows that carry chord data - no_chord_data rows have no segment_onset
    and don't participate), each {onset, chord_tone_pitches: set of raw
    MIDI pitches (not pitch classes) tagged chord_tone in that segment}.
    Also returns a (segment_onset, segment_offset) raw-string -> index map
    for O(1) lookup from a tag row."""
    by_key = {}
    for row in tag_rows:
        if row["segment_onset"] == "":
            continue
        key = (row["segment_onset"], row["segment_offset"])
        entry = by_key.setdefault(key, {"onset": float(row["segment_onset"]), "chord_tone_pitches": set()})
        if row["tag"] == "chord_tone":
            entry["chord_tone_pitches"].add(row["pitch"])
    ordered_keys = sorted(by_key, key=lambda k: by_key[k]["onset"])
    segments = [by_key[k] for k in ordered_keys]
    index_by_key = {k: i for i, k in enumerate(ordered_keys)}
    return segments, index_by_key


def plan_stuck_pitches(pairs: list, segments: list, index_by_key: dict):
    """{index into `pairs`: chord_pitch_classes} for every non_chord_tone
    note whose exact pitch was a chord_tone in the immediately preceding
    chord segment - the narrow "stuck pitch across a chord change" pattern.
    A pitch that was legit further back, but not in the segment right
    before this one, is NOT selected here (it goes to review proposals
    instead - see plan_review_proposals)."""
    selected = {}
    for i, (note, row) in enumerate(pairs):
        if row["tag"] != "non_chord_tone" or row["segment_onset"] == "":
            continue
        seg_idx = index_by_key[(row["segment_onset"], row["segment_offset"])]
        if seg_idx == 0:
            continue
        prev_segment = segments[seg_idx - 1]
        if note.pitch in prev_segment["chord_tone_pitches"]:
            selected[i] = parse_pitch_classes(row["chord_pitch_classes"])
    return selected


def nearest_chord_tone(pitch: int, chord_pitch_classes: set, neighbor_pitches: list):
    """(target_pitch, signed_semitone_distance) - the chord tone closest to
    `pitch` by absolute semitone distance, considering both octave
    directions. Ties broken by whichever target is closer to the average of
    neighbor_pitches; falls back to the lower target if there are no
    neighbors either, for a stable, deterministic result."""
    candidates = []  # (abs_distance, target_pitch)
    for pc in chord_pitch_classes:
        up = (pc - pitch) % 12          # 0..11: semitones to move up to reach this pitch class
        down = up - 12                  # negative (or 0): semitones to move down instead
        candidates.append((up, pitch + up))
        candidates.append((-down, pitch + down))

    min_dist = min(d for d, _ in candidates)
    best = sorted({t for d, t in candidates if d == min_dist})
    if len(best) > 1 and neighbor_pitches:
        avg_neighbor = sum(neighbor_pitches) / len(neighbor_pitches)
        best.sort(key=lambda t: abs(t - avg_neighbor))
    target = best[0]
    return target, target - pitch


def would_collide(pairs: list, index: int, target_pitch: int) -> bool:
    """True if giving pairs[index]'s note `target_pitch` would overlap in
    time with another note in the same 'other' track that already has (or
    was already corrected to) that same pitch."""
    note = pairs[index][0]
    start, end = note.start, note.end
    for j, (other_note, _) in enumerate(pairs):
        if j == index or other_note.pitch != target_pitch:
            continue
        if other_note.start < end and start < other_note.end:  # strict overlap; back-to-back is fine
            return True
    return False


def plan_proposals(pairs: list, applied_indices: set, min_duration_s: float):
    """One row per non_chord_tone note NOT already applied as a stuck-pitch
    correction (a no_chord_data note is a different tag and never appears
    here - there's no chord to check it against or snap it to). Columns
    reflect the note's CURRENT state (post stuck-pitch correction, though
    none of these rows were touched by it, by construction) plus a
    same-algorithm nearest_chord_tone suggestion that's informational only -
    nothing here is written back to the MIDI by this script. `action` is
    pre-filled "correct" for notes longer than min_duration_s, "keep" for
    notes at/under it (see module docstring) - a human edits this column by
    hand before some later step applies whatever still says "correct".
    Returned sorted by onset_time."""
    rows = []
    for i, (note, row) in enumerate(pairs):
        if row["tag"] != "non_chord_tone" or i in applied_indices:
            continue

        chord_pcs = parse_pitch_classes(row["chord_pitch_classes"])
        neighbors = [pairs[i - 1][0].pitch] if i > 0 else []
        if i < len(pairs) - 1:
            neighbors.append(pairs[i + 1][0].pitch)
        target, distance = nearest_chord_tone(note.pitch, chord_pcs, neighbors)

        duration_s = note.end - note.start
        action = ACTION_CORRECT if duration_s > min_duration_s else ACTION_KEEP

        rows.append({
            "onset_time": note.start, "offset_time": note.end,
            "duration_ms": round(duration_s * 1000, 1),
            "current_pitch": note.pitch, "current_pitch_name": pitch_name(note.pitch),
            "chord_segment_start": row["segment_onset"], "chord_segment_end": row["segment_offset"],
            "chord_pitchclasses": row["chord_pitch_classes"],
            "nearest_chord_tone": target, "nearest_chord_tone_name": pitch_name(target),
            "semitone_distance": distance, "action": action,
        })
    rows.sort(key=lambda r: r["onset_time"])
    return rows


def process_song(song_name: str, dry_run: bool, min_duration_s: float):
    import pretty_midi

    print(f"\n{song_name}")
    merged_path = MERGED_ROOT / f"{song_name}.mid"
    try:
        midi = pretty_midi.PrettyMIDI(str(merged_path))
    except Exception as e:
        msg = f"failed to load {merged_path}: {type(e).__name__}: {e}"
        print(f"  SKIP: {msg}")
        log_failure(song_name, msg)
        return None

    other_idx, other_inst = identify_other_track(midi, song_name)
    if other_inst is None:
        return None

    tags_path = CHORDS_ROOT / song_name / NOTE_TAGS_FILENAME
    if not tags_path.exists():
        msg = f"no {NOTE_TAGS_FILENAME} found at {tags_path} (run tag_chord_tones.py first)"
        print(f"  SKIP: {msg}")
        log_failure(song_name, msg)
        return None

    tag_rows = load_note_tags(tags_path)
    try:
        pairs = match_notes_to_tags(other_inst, tag_rows)
    except ValueError as e:
        print(f"  SKIP: {e}")
        log_failure(song_name, str(e))
        return None

    other_snapshot = snapshot_other_instruments(midi, other_idx)

    segments, index_by_key = build_segments(tag_rows)
    stuck = plan_stuck_pitches(pairs, segments, index_by_key)

    original_pitches = [note.pitch for note, _ in pairs]  # neighbor lookups use pre-correction pitches
    changelog_rows = []
    n_collision_skipped = 0
    for i, chord_pcs in stuck.items():
        note, row = pairs[i]
        neighbors = [original_pitches[i - 1]] if i > 0 else []
        if i < len(pairs) - 1:
            neighbors.append(original_pitches[i + 1])
        target, distance = nearest_chord_tone(note.pitch, chord_pcs, neighbors)

        if would_collide(pairs, i, target):
            n_collision_skipped += 1
            continue

        changelog_rows.append({
            "onset_time": note.start, "original_pitch": note.pitch, "corrected_pitch": target,
            "correction_type": "stuck_pitch",
            "chord_segment_pitchclasses": ",".join(str(pc) for pc in sorted(chord_pcs)),
            "semitone_distance_moved": distance,
        })
        note.pitch = target

    applied_indices = {i for i in stuck if pairs[i][0].pitch != original_pitches[i]}
    proposal_rows = plan_proposals(pairs, applied_indices, min_duration_s)

    assert_only_other_track_changed(midi, other_idx, other_snapshot)

    n_chord_tone = sum(1 for _, row in pairs if row["tag"] == "chord_tone")
    n_non_chord_tone = sum(1 for _, row in pairs if row["tag"] == "non_chord_tone")
    n_no_data = sum(1 for _, row in pairs if row["tag"] == "no_chord_data")
    n_default_correct = sum(1 for r in proposal_rows if r["action"] == ACTION_CORRECT)
    n_default_keep = len(proposal_rows) - n_default_correct
    print(f"  total={len(pairs)}  chord_tone={n_chord_tone}  non_chord_tone={n_non_chord_tone}  "
          f"no_chord_data={n_no_data}")
    print(f"  stuck_pitch corrected={len(changelog_rows)}  skipped_pitch_collision={n_collision_skipped}  "
          f"proposals={len(proposal_rows)} (default correct={n_default_correct}, default keep={n_default_keep})")

    if not dry_run:
        write_outputs(song_name, midi if changelog_rows else None, changelog_rows, proposal_rows)
        if changelog_rows:
            print(f"    -> {OUTPUT_ROOT / (song_name + '.mid')}")
        print(f"    -> {OUTPUT_ROOT / (song_name + '_changelog.csv')}, "
              f"{OUTPUT_ROOT / (song_name + '_proposals.csv')}")
    else:
        print("    [DRY RUN] nothing written")

    return {
        "total": len(pairs), "corrected": len(changelog_rows),
        "collisions": n_collision_skipped, "proposals": len(proposal_rows),
        "default_correct": n_default_correct, "default_keep": n_default_keep,
        "semitone_distances": [r["semitone_distance"] for r in proposal_rows],
    }


def write_outputs(song_name: str, midi, changelog_rows: list, proposal_rows: list):
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    if midi is not None:
        out_path = OUTPUT_ROOT / f"{song_name}.mid"
        assert out_path.resolve() != (MERGED_ROOT / f"{song_name}.mid").resolve(), \
            "refusing to write over merged/ - this should be unreachable"
        midi.write(str(out_path))

    with (OUTPUT_ROOT / f"{song_name}_changelog.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CHANGELOG_HEADER)
        writer.writeheader()
        writer.writerows(changelog_rows)

    with (OUTPUT_ROOT / f"{song_name}_proposals.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PROPOSALS_HEADER)
        writer.writeheader()
        writer.writerows(proposal_rows)


def print_distance_distribution(all_distances: list):
    if not all_distances:
        return
    from collections import Counter
    counts = Counter(all_distances)
    print(f"\nSemitone-distance distribution across all proposals (n={len(all_distances)}):")
    for distance in sorted(counts):
        n = counts[distance]
        bar = "#" * min(n, 50)
        sign = "+" if distance > 0 else ""
        print(f"  {sign}{distance:>3d}: {n:5d}  {bar}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None,
                         help="Only process the first N songs (for smoke testing)")
    parser.add_argument("--song", type=str, default=None,
                         help="Process only this one song (exact merged/<song>.mid stem)")
    parser.add_argument("--dry-run", action="store_true",
                         help="Show what would be matched/corrected - writes nothing")
    parser.add_argument("--min-duration-ms", type=float, default=DEFAULT_MIN_DURATION_MS,
                         help=f"Proposals-file duration threshold in milliseconds (default {DEFAULT_MIN_DURATION_MS}) "
                              f"- notes at/under this default to action=keep instead of action=correct")
    args = parser.parse_args()
    min_duration_s = args.min_duration_ms / 1000.0

    songs = collect_songs()
    if not songs:
        print(f"No merged/*.mid found under {MERGED_ROOT}/.")
        return

    if args.song:
        songs = [s for s in songs if s == args.song]
        if not songs:
            print(f"ERROR: no merged/{args.song}.mid found.", file=sys.stderr)
            sys.exit(1)
    if args.limit:
        songs = songs[:args.limit]

    print(f"{len(songs)} song(s) to process, min_duration={args.min_duration_ms:.0f}ms"
          + (" [DRY RUN - nothing will be written]" if args.dry_run else ""))

    ok, skipped = 0, 0
    totals = {"corrected": 0, "collisions": 0, "proposals": 0, "default_correct": 0, "default_keep": 0}
    all_distances = []
    for song_name in songs:
        result = process_song(song_name, args.dry_run, min_duration_s)
        if result is None:
            skipped += 1
            continue
        ok += 1
        for k in totals:
            totals[k] += result[k]
        all_distances.extend(result["semitone_distances"])

    print(f"\nDone. {ok} song(s) processed, {skipped} skipped (see {FAILURE_LOG} if any).")
    print(f"Across processed songs: {totals['corrected']} stuck-pitch correction(s) applied, "
          f"{totals['collisions']} skipped for pitch collision, "
          f"{totals['proposals']} note(s) sent to proposals CSVs "
          f"(default correct={totals['default_correct']}, default keep={totals['default_keep']}).")
    print_distance_distribution(all_distances)


if __name__ == "__main__":
    main()
