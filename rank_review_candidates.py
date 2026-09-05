#!/usr/bin/env python3
"""
Turns chords/<song_name>/other_note_tags.csv (from tag_chord_tones.py) into a
corpus-wide ranked list of chord segments with a high non-chord-tone density -
candidates for a human to go listen to, on the theory that a segment where
Basic Pitch's transcription and Chordino's independent chord read disagree a
lot is more likely to contain a transcription error than one where they agree.

This is purely a ranking/flagging tool: it reads chords/*/other_note_tags.csv
and writes review_candidates.csv and chord_review_candidates.csv (see below);
it never touches merged/ or either chords/ CSV (other_chordnotes.csv,
other_note_tags.csv) itself.

A "segment" here is one Chordino chord segment within one song (identified by
its onset/offset, from other_note_tags.csv's segment_onset/segment_offset
columns). Density = non_chord_tone notes / total notes in that segment
(no_chord_data notes are excluded - they have no segment to attribute to).
Segments with fewer than --min-notes notes are dropped from ranking (a single
non-chord-tone note out of one or two is 100% density but not a meaningful
signal).

Two output files, both written on every corpus-wide run:
  - review_candidates.csv: the original segment-only ranking (unchanged
    format) - one row per chord segment, sorted by density descending.
  - chord_review_candidates.csv: segment rows plus one additional row per
    song aggregating density across that whole song's tagged notes (same
    --min-notes floor, applied to the song's total note count), combined
    into a single list and re-sorted by density descending together, with a
    `scope` column ("segment" or "song") distinguishing the two. This is the
    file meant for later merging with a model-agreement score, once that
    pipeline exists - not part of this pass, just where the chord signal's
    own density scores are meant to live so that merge has something to
    read. A song's aggregate density is normally much lower than its hottest
    segment's, so segment rows dominate the top of the ranking - the point
    of including song rows in the same file/order is to see where a whole
    song lands in that same distribution, not to compete for the top spot.

Two modes:
  - Corpus-wide ranking (default): writes both CSVs above and prints the
    top --top segment candidates to the terminal.
  - Single-song investigation (--song): prints every segment of one song,
    in time order, with the actual note pitches involved - for checking a
    specific transcription result rather than browsing the whole corpus.

Usage:
    uv run rank_review_candidates.py                       # corpus-wide ranked list
    uv run rank_review_candidates.py --top 50               # show more in the terminal
    uv run rank_review_candidates.py --min-notes 4           # stricter noise floor
    uv run rank_review_candidates.py --song "Some Song"      # investigate one song
"""
import argparse
import csv
from collections import defaultdict
from pathlib import Path

CHORDS_ROOT = Path("chords")
NOTE_TAGS_FILENAME = "other_note_tags.csv"
OUTPUT_CSV = Path("review_candidates.csv")
CHORD_REVIEW_CSV = Path("chord_review_candidates.csv")

CSV_HEADER = ["song", "segment_onset", "segment_offset", "n_notes",
              "n_non_chord_tone", "density", "chord_pitch_classes"]
CHORD_REVIEW_HEADER = ["scope", "song", "segment_onset", "segment_offset",
                        "n_notes", "n_non_chord_tone", "density", "chord_pitch_classes"]

PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def pitch_name(pitch_class: int) -> str:
    return PITCH_NAMES[pitch_class % 12]


def load_tags(song_name: str):
    path = CHORDS_ROOT / song_name / NOTE_TAGS_FILENAME
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def discover_songs():
    if not CHORDS_ROOT.is_dir():
        return []
    return sorted(p.name for p in CHORDS_ROOT.iterdir()
                  if p.is_dir() and (p / NOTE_TAGS_FILENAME).exists())


def segments_for_song(song_name: str, rows=None):
    """Group one song's tag rows by chord segment. Returns a list of dicts:
    {segment_onset, segment_offset, notes: [row, ...], chord_pitch_classes}
    sorted by segment_onset. no_chord_data rows are skipped."""
    if rows is None:
        rows = load_tags(song_name)
    by_key = {}
    for row in rows:
        if row["tag"] == "no_chord_data" or not row["segment_onset"]:
            continue
        key = (row["segment_onset"], row["segment_offset"])
        entry = by_key.setdefault(key, {
            "segment_onset": float(row["segment_onset"]),
            "segment_offset": float(row["segment_offset"]),
            "chord_pitch_classes": row["chord_pitch_classes"],
            "notes": [],
        })
        entry["notes"].append(row)
    return sorted(by_key.values(), key=lambda s: s["segment_onset"])


def rank_corpus_combined(min_notes: int):
    """One pass over every song's tags: a `scope="segment"` row per chord
    segment (as rank_corpus, but with the scope field) plus one
    `scope="song"` row aggregating density across that song's whole set of
    tagged notes (no_chord_data excluded, same as segments). All rows -
    both scopes together - sorted by density descending."""
    combined = []
    for song_name in discover_songs():
        rows = load_tags(song_name)

        for seg in segments_for_song(song_name, rows):
            n_notes = len(seg["notes"])
            if n_notes < min_notes:
                continue
            n_nct = sum(1 for n in seg["notes"] if n["tag"] == "non_chord_tone")
            combined.append({
                "scope": "segment",
                "song": song_name,
                "segment_onset": seg["segment_onset"],
                "segment_offset": seg["segment_offset"],
                "n_notes": n_notes,
                "n_non_chord_tone": n_nct,
                "density": n_nct / n_notes,
                "chord_pitch_classes": seg["chord_pitch_classes"],
            })

        scored = [r for r in rows if r["tag"] != "no_chord_data"]
        if len(scored) >= min_notes:
            n_nct = sum(1 for r in scored if r["tag"] == "non_chord_tone")
            combined.append({
                "scope": "song",
                "song": song_name,
                "segment_onset": "",
                "segment_offset": "",
                "n_notes": len(scored),
                "n_non_chord_tone": n_nct,
                "density": n_nct / len(scored),
                "chord_pitch_classes": "",
            })

    combined.sort(key=lambda c: (-c["density"], -c["n_notes"]))
    return combined


def rank_corpus(min_notes: int):
    """Segment-only candidates, same shape review_candidates.csv has always
    had (no `scope` field) - derived from rank_corpus_combined() so the two
    outputs can never disagree on what counts as a segment or how density is
    computed."""
    return [{k: v for k, v in c.items() if k != "scope"}
            for c in rank_corpus_combined(min_notes) if c["scope"] == "segment"]


def write_csv(candidates, out_path: Path, header=CSV_HEADER):
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(candidates)


def print_investigation(song_name: str):
    rows = load_tags(song_name)
    segs = segments_for_song(song_name, rows)
    n_nodata = sum(1 for r in rows if r["tag"] == "no_chord_data")
    print(f"{song_name}: {len(rows)} notes, {len(segs)} chord segments, "
          f"{n_nodata} notes with no chord data")
    print()
    for seg in segs:
        n_notes = len(seg["notes"])
        n_nct = sum(1 for n in seg["notes"] if n["tag"] == "non_chord_tone")
        density = n_nct / n_notes if n_notes else 0.0
        chord_pcs = seg["chord_pitch_classes"]
        chord_names = ",".join(pitch_name(int(pc)) for pc in chord_pcs.split(",") if pc != "")
        flag = "  <-- " if density >= 0.5 and n_notes >= 3 else ""
        print(f"  [{seg['segment_onset']:7.2f} - {seg['segment_offset']:7.2f}]  "
              f"chord={{{chord_names}}}  notes={n_notes:3d}  non-chord={n_nct:3d} ({density:.0%}){flag}")
        for n in sorted(seg["notes"], key=lambda r: float(r["onset"])):
            mark = "x" if n["tag"] == "non_chord_tone" else " "
            print(f"      {mark} onset={float(n['onset']):7.3f} pitch={n['pitch']:>3} "
                  f"({pitch_name(int(n['pitch_class']))})")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--song", type=str, default=None,
                         help="Investigate one song in detail instead of ranking the corpus")
    parser.add_argument("--min-notes", type=int, default=3,
                         help="Drop segments with fewer than this many notes from the corpus ranking (default: 3)")
    parser.add_argument("--top", type=int, default=30,
                         help="How many top segment candidates to print to the terminal (default: 30). "
                              "The full ranking is always written to review_candidates.csv and "
                              "chord_review_candidates.csv")
    args = parser.parse_args()

    if not CHORDS_ROOT.is_dir() or not discover_songs():
        print(f"No tagged songs found under {CHORDS_ROOT}/ - run tag_chord_tones.py first.")
        return

    if args.song:
        if not (CHORDS_ROOT / args.song / NOTE_TAGS_FILENAME).exists():
            print(f"No tags found for {args.song!r} under {CHORDS_ROOT}/. Available songs:")
            for name in discover_songs():
                print(f"  {name}")
            return
        print_investigation(args.song)
        return

    combined = rank_corpus_combined(args.min_notes)
    candidates = [{k: v for k, v in c.items() if k != "scope"}
                  for c in combined if c["scope"] == "segment"]
    write_csv(candidates, OUTPUT_CSV)
    print(f"{len(candidates)} segments (>= {args.min_notes} notes) ranked, written to {OUTPUT_CSV}")

    write_csv(combined, CHORD_REVIEW_CSV, header=CHORD_REVIEW_HEADER)
    n_song_rows = sum(1 for c in combined if c["scope"] == "song")
    print(f"{len(combined)} rows ({len(candidates)} segment + {n_song_rows} per-song) written to "
          f"{CHORD_REVIEW_CSV} - segment and whole-song density combined via a `scope` column")
    print()
    for c in candidates[:args.top]:
        print(f"  {c['density']:5.0%}  {c['n_non_chord_tone']:3d}/{c['n_notes']:<3d} non-chord  "
              f"[{c['segment_onset']:7.2f}-{c['segment_offset']:7.2f}]  {c['song']}")


if __name__ == "__main__":
    main()
