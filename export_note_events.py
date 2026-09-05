#!/usr/bin/env python3
"""
Analysis-ready export stage: flattens everything upstream (the corrected
transcription, Chordino's chord segments, and the auto-correction changelog)
into two flat CSVs for downstream pattern-recognition work on harmonic
content. Nothing else in the pipeline reads these back in - this is a leaf
node, not another stage other scripts depend on.

    note_events_other.csv - one row per note in the "other" track only
    (bass and drums are untouched by correction and are out of scope for
    this export entirely - see README, "Analysis-ready export"):

        song, onset, offset, pitch, velocity  - the note itself, from
            corrected/<song>.mid's "other" track if that file exists, else
            merged/<song>.mid's (bass/drums notes are never read by this
            script at all, correction or no)
        chord_onset, chord_offset, chord_pitchclasses  - the Chordino chord
            segment active at the note's onset (chords/<song>/other_chordnotes.csv,
            matched the same way tag_chord_tones.py does). Blank if the onset
            falls in a gap between segments, or the song has no chord data.
        was_corrected, correction_type, original_pitch  - from
            corrected/<song>_changelog.csv (auto_correct_pitches.py's output),
            matched by (onset_time, corrected_pitch) - exact match, not
            tolerance-based, since a correction only ever changes pitch and
            pretty_midi round-trips onset times exactly for files this size.
            was_corrected is False and the other two columns blank for any
            note the changelog doesn't mention (chord_tone, no_chord_data, or
            a non_chord_tone left uncorrected because it was under
            auto_correct_pitches.py's --min-duration-ms threshold - see
            README).

    A song whose corrected/<song>.mid exists with no matching changelog next
    to it (correct_notes.py's hand-correction path writes a single shared
    corrected/correction_log.csv instead, a different format) still has its
    corrected notes exported, but was_corrected/correction_type/original_pitch
    are left blank for all of them and the song is called out in the summary
    printed at the end - this script doesn't guess at hand-correction
    provenance rather than risk mislabeling it.

    chord_segments.csv - one row per distinct chord segment across every
    song under chords/, independent of how far merged/corrected coverage has
    reached (the chord stage runs corpus-wide well ahead of transcription):
        song, start, end, pitch_class_set

Both pitch values in note_events_other.csv reflect Chordino-guided
auto-correction where it has been applied, not raw Basic Pitch output - see
README, "Analysis-ready export".

Read-only except for the two output CSVs (written at the repo root, next to
review_candidates.csv): reads merged/, corrected/, and chords/ only, and
writes nothing back into any of them.

Usage:
    uv run export_note_events.py
    uv run export_note_events.py --note-events-out my_notes.csv --chord-segments-out my_chords.csv
    uv run export_note_events.py --skip-note-events       # chord_segments.csv only
    uv run export_note_events.py --skip-chord-segments    # note_events_other.csv only
"""
import argparse
import csv
from pathlib import Path

from tag_chord_tones import load_chord_segments, find_segment

MERGED_ROOT = Path("merged")
CORRECTED_ROOT = Path("corrected")
CHORDS_ROOT = Path("chords")
CHORDNOTES_FILENAME = "other_chordnotes.csv"

NOTE_EVENTS_CSV = Path("note_events_other.csv")
CHORD_SEGMENTS_CSV = Path("chord_segments.csv")

NOTE_EVENTS_HEADER = [
    "song", "onset", "offset", "pitch", "velocity",
    "chord_onset", "chord_offset", "chord_pitchclasses",
    "was_corrected", "correction_type", "original_pitch",
]
CHORD_SEGMENTS_HEADER = ["song", "start", "end", "pitch_class_set"]


def other_track_notes(midi):
    """Every Note in `midi`'s "other" track(s), sorted by onset - same
    convention as tag_chord_tones.py/auto_correct_pitches.py (there may be
    more than one instrument literally named "other")."""
    other_tracks = [inst for inst in midi.instruments if inst.name == "other"]
    return sorted((n for inst in other_tracks for n in inst.notes), key=lambda n: n.start)


def load_changelog(song_name: str):
    """corrected/<song>_changelog.csv -> {(onset_time, corrected_pitch): row}.
    Returns None if the file doesn't exist at all, distinct from an existing
    file with zero data rows (a real "nothing needed correcting" result,
    which returns an empty dict) - callers use None to detect the
    no-changelog-at-all case (see module docstring)."""
    path = CORRECTED_ROOT / f"{song_name}_changelog.csv"
    if not path.exists():
        return None
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    return {(float(row["onset_time"]), int(row["corrected_pitch"])): row for row in rows}


def collect_note_event_songs():
    """(song_name, midi_path, used_corrected) for every song with a merged
    MIDI - corrected/<song>.mid preferred when it exists, merged/<song>.mid
    otherwise."""
    if not MERGED_ROOT.is_dir():
        return []
    jobs = []
    for merged_path in sorted(MERGED_ROOT.glob("*.mid")):
        song_name = merged_path.stem
        corrected_path = CORRECTED_ROOT / f"{song_name}.mid"
        if corrected_path.exists():
            jobs.append((song_name, corrected_path, True))
        else:
            jobs.append((song_name, merged_path, False))
    return jobs


def export_note_events(out_path: Path):
    import pretty_midi

    jobs = collect_note_event_songs()
    if not jobs:
        print(f"No songs found under {MERGED_ROOT}/ - {out_path} not written.")
        return

    rows = []
    n_corrected_notes = 0
    songs_missing_changelog = []
    for song_name, midi_path, used_corrected in jobs:
        midi = pretty_midi.PrettyMIDI(str(midi_path))
        notes = other_track_notes(midi)

        chordnotes_path = CHORDS_ROOT / song_name / CHORDNOTES_FILENAME
        segments = load_chord_segments(chordnotes_path) if chordnotes_path.exists() else []
        onsets = [s["onset"] for s in segments]

        changelog = load_changelog(song_name) if used_corrected else None
        if used_corrected and changelog is None:
            songs_missing_changelog.append(song_name)

        for note in notes:
            seg = find_segment(segments, onsets, note.start) if segments else None
            row = {
                "song": song_name, "onset": note.start, "offset": note.end,
                "pitch": note.pitch, "velocity": note.velocity,
                "chord_onset": seg["onset"] if seg else "",
                "chord_offset": seg["offset"] if seg else "",
                "chord_pitchclasses": ",".join(str(pc) for pc in sorted(seg["pitch_classes"])) if seg else "",
                "was_corrected": False, "correction_type": "", "original_pitch": "",
            }
            match = changelog.get((note.start, note.pitch)) if changelog else None
            if match:
                row["was_corrected"] = True
                row["correction_type"] = match["correction_type"]
                row["original_pitch"] = int(match["original_pitch"])
                n_corrected_notes += 1
            rows.append(row)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=NOTE_EVENTS_HEADER)
        writer.writeheader()
        writer.writerows(rows)

    print(f"{len(rows)} note(s) across {len(jobs)} song(s) -> {out_path} "
          f"({n_corrected_notes} flagged was_corrected)")
    if songs_missing_changelog:
        names = ", ".join(songs_missing_changelog[:5]) + (", ..." if len(songs_missing_changelog) > 5 else "")
        print(f"  NOTE: {len(songs_missing_changelog)} song(s) have corrected/<song>.mid but no matching "
              f"_changelog.csv (likely hand-corrected via correct_notes.py) - their notes are exported, "
              f"but was_corrected/correction_type/original_pitch are left blank for all of them: {names}")


def collect_chord_segment_songs():
    """(song_name, chordnotes_path) for every song under chords/ with a
    other_chordnotes.csv - the full chord-detection corpus, independent of
    how far merged/corrected coverage has reached."""
    if not CHORDS_ROOT.is_dir():
        return []
    songs = []
    for song_dir in sorted(p for p in CHORDS_ROOT.iterdir() if p.is_dir()):
        chordnotes_path = song_dir / CHORDNOTES_FILENAME
        if chordnotes_path.exists():
            songs.append((song_dir.name, chordnotes_path))
    return songs


def export_chord_segments(out_path: Path):
    songs = collect_chord_segment_songs()
    if not songs:
        print(f"No songs found under {CHORDS_ROOT}/ - {out_path} not written.")
        return

    rows = []
    for song_name, chordnotes_path in songs:
        for seg in load_chord_segments(chordnotes_path):
            rows.append({
                "song": song_name, "start": seg["onset"], "end": seg["offset"],
                "pitch_class_set": ",".join(str(pc) for pc in sorted(seg["pitch_classes"])),
            })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CHORD_SEGMENTS_HEADER)
        writer.writeheader()
        writer.writerows(rows)

    print(f"{len(rows)} chord segment(s) across {len(songs)} song(s) -> {out_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--note-events-out", type=Path, default=NOTE_EVENTS_CSV,
                         help=f"Output path for the note-events CSV (default {NOTE_EVENTS_CSV})")
    parser.add_argument("--chord-segments-out", type=Path, default=CHORD_SEGMENTS_CSV,
                         help=f"Output path for the chord-segments CSV (default {CHORD_SEGMENTS_CSV})")
    parser.add_argument("--skip-note-events", action="store_true", help="Don't write note_events_other.csv")
    parser.add_argument("--skip-chord-segments", action="store_true", help="Don't write chord_segments.csv")
    args = parser.parse_args()

    if not args.skip_note_events:
        export_note_events(args.note_events_out)
    if not args.skip_chord_segments:
        export_chord_segments(args.chord_segments_out)


if __name__ == "__main__":
    main()
