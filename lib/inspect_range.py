#!/usr/bin/env python3
"""
Neutral side-by-side inspector for one song and one time range: prints the
Chordino chord segments active in that window (chords/<song>/other_chordnotes.csv)
interleaved in time order with the transcribed notes from the merged "other"
track in that same window, each carrying its chord_tone/non_chord_tone/
no_chord_data tag (chords/<song>/other_note_tags.csv, from tag_chord_tones.py).

This is a read-only spot-check tool, not a scoring tool. It prints exactly
what's in the data, in time order, and computes no new judgment of its own -
the chord_tone/non_chord_tone/no_chord_data tag shown for each note is
tag_chord_tones.py's, unchanged. It's meant to let a human eyeball a specific
window they're already suspicious of and compare the two independent signals
(Basic Pitch's transcription vs. Chordino's chord read) in context - e.g.
spotting that the same pitch keeps getting flagged non_chord_tone across
several consecutive, harmonically different chord segments. That's exactly
the kind of pattern rank_review_candidates.py's density ranking surfaces at
the corpus level but can't show you in context; this is the zoomed-in
follow-up once a candidate section catches your eye.

Reads only; never modifies merged/, chords/, or any other existing file.

Usage (from the repo root; ARGS is forwarded as CLI flags):
    make inspect-range ARGS='--song "Some Song" --start 60 --end 75'
    make inspect-range ARGS='--song "Some Song" --start 1:00 --end 1:15'
    make inspect-range ARGS="--list"                    # list songs available to inspect
"""
import argparse
import csv
import difflib
import sys
from pathlib import Path

CHORDS_ROOT = Path("chords")
CHORDNOTES_FILENAME = "other_chordnotes.csv"
NOTE_TAGS_FILENAME = "other_note_tags.csv"

PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Purely a visual aid for scanning - "x" mirrors the tag tag_chord_tones.py
# already computed, not a new judgment made here.
TAG_MARK = {"chord_tone": " ", "non_chord_tone": "x", "no_chord_data": "?"}


def pitch_name(pitch_class: int) -> str:
    return PITCH_NAMES[pitch_class % 12]


def parse_time(s: str) -> float:
    """Accepts plain seconds ("75", "75.5") or M:SS / MM:SS.mmm ("1:15", "1:15.5")."""
    s = s.strip()
    if ":" in s:
        minutes, _, seconds = s.partition(":")
        return int(minutes) * 60 + float(seconds)
    return float(s)


def discover_songs():
    """Songs with chord data at all (chordnotes present) - independent of
    whether they've been note-tagged yet, so --list can point out the gap."""
    if not CHORDS_ROOT.is_dir():
        return []
    return sorted(p.name for p in CHORDS_ROOT.iterdir()
                  if p.is_dir() and (p / CHORDNOTES_FILENAME).exists())


def resolve_song(query: str, songs: list) -> str:
    """Exact match first; then a unique case-insensitive substring match;
    then a unique close-spelling match (difflib) for typos - song folder
    names are long and easy to mistype/abbreviate. Returns None if there's
    no match or the match is ambiguous at whichever stage resolves it."""
    if query in songs:
        return query
    substring_matches = [s for s in songs if query.lower() in s.lower()]
    if len(substring_matches) == 1:
        return substring_matches[0]
    if len(substring_matches) > 1:
        return None
    close_matches = difflib.get_close_matches(query, songs, n=2, cutoff=0.6)
    return close_matches[0] if len(close_matches) == 1 else None


def load_segments(song_name: str):
    """chords/<song>/other_chordnotes.csv -> list of {onset, offset,
    pitch_classes} sorted by onset, one entry per distinct chord segment."""
    path = CHORDS_ROOT / song_name / CHORDNOTES_FILENAME
    by_segment = {}
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            seg = int(row["segment"])
            entry = by_segment.setdefault(seg, {
                "onset": float(row["onset"]),
                "offset": float(row["offset"]),
                "pitch_classes": set(),
            })
            entry["pitch_classes"].add(int(row["pitch_class"]))
    return sorted(by_segment.values(), key=lambda s: s["onset"])


def load_notes(song_name: str):
    """chords/<song>/other_note_tags.csv -> list of note dicts, sorted by onset."""
    path = CHORDS_ROOT / song_name / NOTE_TAGS_FILENAME
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["onset"] = float(r["onset"])
        r["pitch"] = int(r["pitch"])
        r["pitch_class"] = int(r["pitch_class"])
    return sorted(rows, key=lambda r: r["onset"])


def format_chord_set(pitch_classes) -> str:
    return "{" + ",".join(pitch_name(pc) for pc in sorted(pitch_classes)) + "}"


def print_range(song_name: str, start: float, end: float):
    segments = load_segments(song_name)
    notes = load_notes(song_name)

    # Segments *active* during the window (may extend outside it - printed
    # with their real, unclipped boundaries for full harmonic context).
    segs_in_range = [s for s in segments if s["offset"] > start and s["onset"] < end]
    # Notes are filtered strictly by onset falling inside the window.
    notes_in_range = [n for n in notes if start <= n["onset"] < end]

    print(f"{song_name}  —  range {start:.2f}–{end:.2f}s")
    print(f"({len(segs_in_range)} chord segment(s), {len(notes_in_range)} note(s) in range)")
    print()

    if not segs_in_range and not notes_in_range:
        print("  (nothing in this range)")
        return

    # Single chronological timeline: a segment header prints when the window
    # crosses into that segment's onset, notes print at their own onset.
    # Segments are non-overlapping and sort before a note landing at the same
    # instant, so this reads top-to-bottom exactly as it plays.
    timeline = [(s["onset"], 0, "segment", s) for s in segs_in_range]
    timeline += [(n["onset"], 1, "note", n) for n in notes_in_range]
    timeline.sort(key=lambda x: (x[0], x[1]))

    for _, _, kind, item in timeline:
        if kind == "segment":
            print(f"  [{item['onset']:7.2f} -{item['offset']:7.2f}]  CHORD {format_chord_set(item['pitch_classes'])}")
        else:
            mark = TAG_MARK.get(item["tag"], "?")
            print(f"     {mark} {item['onset']:7.2f}   note  pitch={item['pitch']:>3} "
                  f"({pitch_name(item['pitch_class']):<2})  {item['tag']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--song", type=str, help="Song name (matches, or uniquely substring-matches, a chords/<name>/ directory)")
    parser.add_argument("--start", type=str, help="Range start - seconds, or M:SS")
    parser.add_argument("--end", type=str, help="Range end - seconds, or M:SS")
    parser.add_argument("--list", action="store_true", help="List songs available to inspect and exit")
    args = parser.parse_args()

    songs = discover_songs()

    if args.list or not args.song:
        if not songs:
            print(f"No songs found under {CHORDS_ROOT}/ - run extract_chords.py first.")
            return
        print(f"{len(songs)} song(s) available under {CHORDS_ROOT}/:")
        for name in songs:
            has_tags = (CHORDS_ROOT / name / NOTE_TAGS_FILENAME).exists()
            print(f"  {name}" + ("" if has_tags else "   (no note tags yet - run tag_chord_tones.py)"))
        if not args.song:
            return

    song_name = resolve_song(args.song, songs)
    if song_name is None:
        print(f"No unique match for --song {args.song!r} under {CHORDS_ROOT}/. Available songs:", file=sys.stderr)
        for name in songs:
            print(f"  {name}", file=sys.stderr)
        sys.exit(1)
    if song_name != args.song:
        print(f"(matched --song {args.song!r} -> {song_name!r})")

    if args.start is None or args.end is None:
        print("Both --start and --end are required (unless using --list).", file=sys.stderr)
        sys.exit(1)

    start, end = parse_time(args.start), parse_time(args.end)
    if start >= end:
        print(f"--start ({start}) must be before --end ({end}).", file=sys.stderr)
        sys.exit(1)

    tags_path = CHORDS_ROOT / song_name / NOTE_TAGS_FILENAME
    if not tags_path.exists():
        print(f"No note tags for {song_name!r} yet - expected {tags_path}. "
              f"Run tag_chord_tones.py first (needs a merged MIDI too).", file=sys.stderr)
        sys.exit(1)

    print_range(song_name, start, end)


if __name__ == "__main__":
    main()
