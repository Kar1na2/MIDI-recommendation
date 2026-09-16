#!/usr/bin/env python3
"""
Octave-duplicate correction: a second, narrow auto-correction pass, run
after correct_notes.py, addressing the specific artifact found while
investigating why the frontend's melody-interval view showed implausible
semitone jumps (see docs/ melody-redesign brief / chat history for the
corpus-wide investigation this came out of).

The problem, established corpus-wide before writing this script (not
assumed): 18.6% of all note-to-note intervals in the "other" track exceed
18 semitones, present in all 75 songs (median 17.2% of a song's intervals).
95.8% of those occur with under 150ms between the notes (88.4% actually
overlap), and the interval-value distribution shows a large excess exactly
at multiples of 12 semitones (19.6% of flagged intervals vs. an 8.3% chance
baseline) - i.e. the same pitch class, different octave. Direct pitch
tracking (librosa pyin) against the real "other" stem audio on a sample of
these confirmed the pattern directly: the actual audio pitch was flat/
constant across the "leap" in 7 of 8 checkable cases, with one endpoint a
confirmed phantom note a clean 1-3 octaves off the real, held pitch.

Scope of the automated fix here (deliberately narrow, matching
correct_notes.py's own philosophy of only auto-applying the
highest-confidence pattern): two notes, ADJACENT in the "other" track's
onset order, whose pitches are an EXACT multiple of 12 semitones apart
(not "close to" - the distance-to-nearest-octave distribution shows only
the exact-multiple bucket is actually elevated over chance; +-1/+-2
semitones off are if anything *below* the random baseline, so widening the
band would dilute the signal, not sharpen it) AND separated by under
GAP_THRESHOLD_SECONDS. Treated as two detections of the same acoustic
event; the HIGHER-octave one is deleted, the lower kept as-is (onset/
offset/velocity unchanged) - every audio-confirmed case in the sample had
the lower octave matching reality, which also matches the general,
well-documented bias of polyphonic pitch detectors toward spurious
*upper*-harmonic false positives (a real 2nd/3rd harmonic really is present
in the signal; a spurious sub-harmonic below the true pitch is much rarer).

Deliberately NOT covered here (a separate, larger bucket - 34.9% of flagged
intervals sit near an octave-plus-a-fifth, i.e. fundamental-vs-3rd-harmonic
confusion rather than simple octave doubling; the audio sample showed the
real pitch matching *neither* transcribed endpoint cleanly for that
pattern, so there's no equally-confident automatic fix for it yet - left
as-is for now, flagged as a follow-up needing its own investigation).

Track identification, per-song safety assertion that no other track was
touched, and the corrected/<song>.mid / failure-log conventions all mirror
correct_notes.py directly (see that file's docstring) - this is the same
kind of narrow, explicit, auditable pass, just a different pattern.

Reads corrected/<song>.mid if correct_notes.py already produced one for
this song (chaining on top of its stuck-pitch fixes), else merged/<song>.mid
- same fallback export_note_events.py already uses. Always (re)writes
corrected/<song>.mid if >=1 duplicate was removed, whichever of the two it
read from.

Output per song:
    corrected/<song>.mid                        - only (re)written if >=1
                                                    octave-duplicate was
                                                    actually removed; every
                                                    other track passes
                                                    through byte-identical.
                                                    NOTE: this does NOT
                                                    rewrite correct_notes.py's
                                                    own _changelog.csv - a
                                                    deleted note simply no
                                                    longer appears as a row
                                                    in export_note_events.py's
                                                    output at all (the
                                                    correct outcome), it
                                                    doesn't need a
                                                    was_corrected flag the
                                                    way a *moved* note does.
    corrected/<song>_octave_duplicate_log.csv   - always written (even
                                                    empty/header-only):
                                                    removed_onset,
                                                    removed_pitch,
                                                    removed_pitch_name,
                                                    kept_onset, kept_pitch,
                                                    kept_pitch_name,
                                                    gap_seconds,
                                                    octaves_apart. Pure audit
                                                    trail, not read by any
                                                    later pipeline stage.
    corrected/_correct_octave_duplicates_review_candidates.csv
                                                 - always written: exact-
                                                    octave-multiple pairs
                                                    that were NOT auto-
                                                    applied because their
                                                    gap was >= threshold
                                                    (could be a genuine
                                                    octave jump between
                                                    phrases) - informational
                                                    only, same spirit as
                                                    correct_notes.py's
                                                    _proposals.csv.
    corrected/_correct_octave_duplicates_failures.log
                                                 - one line per skipped song.

Usage (from the repo root; ARGS is forwarded as CLI flags):
    make correct-octave-duplicates ARGS="--limit 3"           # smoke test
    make correct-octave-duplicates ARGS="--song 'Some Song'"  # just one song
    make correct-octave-duplicates ARGS="--dry-run"           # preview only
    make correct-octave-duplicates                            # full corpus
"""
import argparse
import csv
import sys
import time
from pathlib import Path

MERGED_ROOT = Path("merged")
CORRECTED_ROOT = Path("corrected")
FAILURE_LOG = CORRECTED_ROOT / "_correct_octave_duplicates_failures.log"
REVIEW_CANDIDATES_CSV = CORRECTED_ROOT / "_correct_octave_duplicates_review_candidates.csv"

GAP_THRESHOLD_SECONDS = 0.15  # see module docstring - 95.8% of the original flagged population fell under this

PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

DUPLICATE_LOG_HEADER = ["removed_onset", "removed_pitch", "removed_pitch_name",
                        "kept_onset", "kept_pitch", "kept_pitch_name",
                        "gap_seconds", "octaves_apart"]
REVIEW_CANDIDATES_HEADER = ["song", "note_a_onset", "note_a_pitch", "note_a_pitch_name",
                            "note_b_onset", "note_b_pitch", "note_b_pitch_name",
                            "gap_seconds", "octaves_apart"]


def pitch_name(pitch: int) -> str:
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
    """Same identification rule as correct_notes.py - see that file for the
    full rationale (exactly one track named "other", not a drum kit)."""
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
    return idx, inst


def snapshot_other_instruments(midi, other_idx: int):
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


def find_octave_duplicates(notes: list):
    """Single left-to-right pass over onset-sorted notes. `kept` always ends
    on the most recently retained note, so a chain of 3+ mutually-duplicate
    notes collapses correctly (each new note is compared against whichever
    one actually survived so far, not blindly against its raw predecessor).
    Returns (kept_notes, removed_log_rows, review_candidate_rows)."""
    if not notes:
        return [], [], []

    kept = [notes[0]]
    removed_rows = []
    review_rows = []

    for note in notes[1:]:
        prev = kept[-1]
        pitch_diff = note.pitch - prev.pitch
        gap = note.start - prev.end
        is_octave_multiple = pitch_diff != 0 and pitch_diff % 12 == 0

        if is_octave_multiple and gap < GAP_THRESHOLD_SECONDS:
            lower, higher = (prev, note) if prev.pitch < note.pitch else (note, prev)
            removed_rows.append({
                "removed_onset": higher.start, "removed_pitch": higher.pitch,
                "removed_pitch_name": pitch_name(higher.pitch),
                "kept_onset": lower.start, "kept_pitch": lower.pitch,
                "kept_pitch_name": pitch_name(lower.pitch),
                "gap_seconds": round(gap, 4),
                "octaves_apart": abs(pitch_diff) // 12,
            })
            kept[-1] = lower  # whichever one is lower survives, regardless of onset order
            continue

        if is_octave_multiple:  # exact octave multiple, but gap too large to auto-apply
            review_rows.append({
                "note_a_onset": prev.start, "note_a_pitch": prev.pitch,
                "note_a_pitch_name": pitch_name(prev.pitch),
                "note_b_onset": note.start, "note_b_pitch": note.pitch,
                "note_b_pitch_name": pitch_name(note.pitch),
                "gap_seconds": round(gap, 4),
                "octaves_apart": abs(pitch_diff) // 12,
            })

        kept.append(note)

    return kept, removed_rows, review_rows


def process_song(song_name: str, dry_run: bool):
    import pretty_midi

    print(f"\n{song_name}")
    corrected_path = CORRECTED_ROOT / f"{song_name}.mid"
    merged_path = MERGED_ROOT / f"{song_name}.mid"
    used_corrected = corrected_path.exists()
    midi_path = corrected_path if used_corrected else merged_path

    try:
        midi = pretty_midi.PrettyMIDI(str(midi_path))
    except Exception as e:
        msg = f"failed to load {midi_path}: {type(e).__name__}: {e}"
        print(f"  SKIP: {msg}")
        log_failure(song_name, msg)
        return None

    other_idx, other_inst = identify_other_track(midi, song_name)
    if other_inst is None:
        return None

    other_snapshot = snapshot_other_instruments(midi, other_idx)

    notes_sorted = sorted(other_inst.notes, key=lambda n: n.start)
    kept, removed_rows, review_rows = find_octave_duplicates(notes_sorted)

    print(f"  input={midi_path} ({'corrected' if used_corrected else 'merged'})  "
          f"notes={len(notes_sorted)}  removed={len(removed_rows)}  review_candidates={len(review_rows)}")

    if removed_rows:
        other_inst.notes = kept
        assert_only_other_track_changed(midi, other_idx, other_snapshot)

    if not dry_run:
        write_outputs(song_name, midi if removed_rows else None, removed_rows)
        if removed_rows:
            print(f"    -> {corrected_path}")
        print(f"    -> {CORRECTED_ROOT / (song_name + '_octave_duplicate_log.csv')}")
    else:
        print("    [DRY RUN] nothing written")

    return {"total": len(notes_sorted), "removed": len(removed_rows),
            "review_candidates": review_rows, "song": song_name}


def write_outputs(song_name: str, midi, removed_rows: list):
    CORRECTED_ROOT.mkdir(parents=True, exist_ok=True)
    if midi is not None:
        out_path = CORRECTED_ROOT / f"{song_name}.mid"
        assert out_path.resolve() != (MERGED_ROOT / f"{song_name}.mid").resolve(), \
            "refusing to write over merged/ - this should be unreachable"
        midi.write(str(out_path))

    with (CORRECTED_ROOT / f"{song_name}_octave_duplicate_log.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DUPLICATE_LOG_HEADER)
        writer.writeheader()
        writer.writerows(removed_rows)


def write_review_candidates(all_review_rows: list):
    REVIEW_CANDIDATES_CSV.parent.mkdir(parents=True, exist_ok=True)
    with REVIEW_CANDIDATES_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REVIEW_CANDIDATES_HEADER)
        writer.writeheader()
        writer.writerows(all_review_rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N songs (smoke test)")
    parser.add_argument("--song", type=str, default=None, help="Process only this one song")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change - writes nothing")
    args = parser.parse_args()

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

    print(f"{len(songs)} song(s) to process, gap_threshold={GAP_THRESHOLD_SECONDS*1000:.0f}ms"
          + (" [DRY RUN - nothing will be written]" if args.dry_run else ""))

    ok, skipped = 0, 0
    total_notes, total_removed = 0, 0
    all_review_rows = []
    songs_with_removals = []
    for song_name in songs:
        result = process_song(song_name, args.dry_run)
        if result is None:
            skipped += 1
            continue
        ok += 1
        total_notes += result["total"]
        total_removed += result["removed"]
        if result["removed"]:
            songs_with_removals.append((song_name, result["removed"]))
        for row in result["review_candidates"]:
            all_review_rows.append({"song": song_name, **row})

    if not args.dry_run:
        write_review_candidates(all_review_rows)

    print(f"\nDone. {ok} song(s) processed, {skipped} skipped (see {FAILURE_LOG} if any).")
    print(f"Across processed songs: {total_notes} notes seen, {total_removed} octave-duplicate note(s) removed "
          f"({100*total_removed/total_notes:.2f}% of all notes), {len(all_review_rows)} review candidate(s) "
          f"(exact-octave pairs with gap >= {GAP_THRESHOLD_SECONDS*1000:.0f}ms, not auto-applied)"
          + (f" -> {REVIEW_CANDIDATES_CSV}" if not args.dry_run else ""))
    print(f"{len(songs_with_removals)}/{ok} songs had at least one removal.")
    if songs_with_removals:
        print("Top songs by removals:")
        for name, n in sorted(songs_with_removals, key=lambda x: -x[1])[:10]:
            print(f"  {n:4d}  {name}")


if __name__ == "__main__":
    main()
