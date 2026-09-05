#!/usr/bin/env python3
"""
Pick two "anchor" excerpts for hand-labeled ground truth, chosen by the
Chordino harmonic-consistency signal instead of at random:

  Anchor A ("hot")  - the song with the HIGHEST corpus-wide non-chord-tone
                       density (chord_review_candidates.csv, scope=song) -
                       the pipeline's most-likely-to-have-errors case.
  Anchor B ("clean") - the song with the LOWEST such density - the
                       pipeline's best-case output, where the transcription
                       largely agrees with an independent chord read.

Within each anchor song, scans contiguous runs of Chordino chord segments
(chords/<song>/other_chordnotes.csv) for the 20-30s span that maximizes
(Anchor A) or minimizes (Anchor B) non-chord-tone density among its tagged
notes (chords/<song>/other_note_tags.csv), among spans with at least
--min-notes notes so a short, noisy span can't win by chance. Segment-aligned
rather than an arbitrary time slice, so the excerpt starts/ends on an actual
chord change rather than mid-note.

For each anchor's chosen window, writes:
    ground_truth_anchor/<song>_excerpt/{other,bass,drums}.wav   - the
        matching slice of stems/<song>/{other,bass,drums}.wav, cropped and
        re-zeroed to the excerpt's own timeline (regenerated every run -
        these are derived audio, not hand-authored)
    ground_truth_anchor/<song>_excerpt/merged_excerpt.mid       - the
        matching slice of merged/<song>.mid's other/bass/drums tracks, same
        re-zeroed timeline (also regenerated every run)
    ground_truth_anchor/<song>_excerpt/{other,bass,drums}.csv   - EMPTY
        ground-truth annotation templates (gt_format.py's schema), for hand
        labeling against the .wav files above. Like select_eval_sample.py,
        never overwrites a template that already exists, so labeling work
        already done is never touched.
    ground_truth_anchor/_anchors.txt                            - a record
        of which song/window was picked, why, and when - append-only.

This only ever reads stems/, merged/, chords/, and chord_review_candidates.csv;
it never modifies any of them. Ground-truth CSVs are scaffolded empty, same
as select_eval_sample.py - this script picks *where* to label, not what the
labels should be.

Usage:
    uv run select_anchor_excerpts.py
    uv run select_anchor_excerpts.py --min-notes 20    # stricter noise floor
"""
import argparse
import csv
import sys
import time
from pathlib import Path

from gt_format import write_template
from inspect_range import load_segments, load_notes, CHORDS_ROOT

MERGED_ROOT = Path("merged")
STEMS_ROOT = Path("stems")
CHORD_REVIEW_CSV = Path("chord_review_candidates.csv")
GT_ANCHOR_ROOT = Path("ground_truth_anchor")
ANCHOR_MANIFEST = GT_ANCHOR_ROOT / "_anchors.txt"

STEM_ORDER = ("other", "bass", "drums")
MIN_WINDOW = 20.0
MAX_WINDOW = 30.0
DEFAULT_MIN_NOTES = 15


def load_song_density_rows():
    """scope="song" rows from chord_review_candidates.csv, in whatever order
    they're already in (the file is sorted by density descending, and
    filtering to one scope preserves that relative order)."""
    if not CHORD_REVIEW_CSV.exists():
        return []
    rows = list(csv.DictReader(CHORD_REVIEW_CSV.open(newline="")))
    return [r for r in rows if r["scope"] == "song"]


def pick_anchors(song_rows):
    """(anchor_a, anchor_b, note) - highest- and lowest-density song rows.
    `note` is a caveat string when the corpus is thin enough that this is a
    weaker pick than it will be later (e.g. only a handful of songs to
    choose extremes from)."""
    if len(song_rows) < 2:
        return None, None, "fewer than 2 tagged songs available - need at least 2 to pick a top and bottom anchor"
    note = None
    if len(song_rows) < 10:
        note = (f"only {len(song_rows)} song(s) have chord-tagged data right now (merged/ hasn't "
                f"caught up to the rest of the corpus yet) - these anchors will get more meaningful "
                f"as more songs come through transcribe_harmonic.py/transcribe_drums.py/merge_stems.py")
    return song_rows[0], song_rows[-1], note


def find_best_window(song_name: str, mode: str, min_notes: int):
    """mode="max" (Anchor A) or "min" (Anchor B). Scans every contiguous run
    of chord segments whose total span is in [MIN_WINDOW, MAX_WINDOW],
    scores it by non-chord-tone density among its tagged notes (no_chord_data
    excluded), and returns the best-scoring one with >= min_notes notes.
    Falls back to ignoring min_notes (with a caveat) if nothing meets it."""
    segments = load_segments(song_name)
    notes = load_notes(song_name)

    def score_windows(floor):
        out = []
        for i in range(len(segments)):
            start = segments[i]["onset"]
            for j in range(i, len(segments)):
                end = segments[j]["offset"]
                span = end - start
                if span > MAX_WINDOW:
                    break
                if span < MIN_WINDOW:
                    continue
                window_notes = [n for n in notes if start <= n["onset"] < end and n["tag"] != "no_chord_data"]
                if len(window_notes) < floor:
                    continue
                nct = sum(1 for n in window_notes if n["tag"] == "non_chord_tone")
                out.append({"start": start, "end": end, "span": span,
                            "n_notes": len(window_notes), "n_non_chord_tone": nct,
                            "density": nct / len(window_notes)})
        return out

    candidates = score_windows(min_notes)
    caveat = None
    if not candidates:
        candidates = score_windows(0)
        caveat = f"no {MIN_WINDOW:.0f}-{MAX_WINDOW:.0f}s window had >= {min_notes} notes - picked from all windows regardless of count"
    if not candidates:
        return None, "no chord segments long enough to form a 20-30s window at all"

    key = (lambda c: (-c["density"], -c["n_notes"])) if mode == "max" else (lambda c: (c["density"], -c["n_notes"]))
    best = min(candidates, key=key)
    return best, caveat


def slice_audio(song_name: str, start: float, end: float, out_dir: Path):
    import soundfile as sf
    for stem in STEM_ORDER:
        src = STEMS_ROOT / song_name / f"{stem}.wav"
        if not src.exists():
            print(f"    WARNING: {src} missing, skipping audio slice for {stem}", file=sys.stderr)
            continue
        audio, sr = sf.read(str(src))
        i0, i1 = int(start * sr), int(end * sr)
        sf.write(str(out_dir / f"{stem}.wav"), audio[i0:i1], sr)


def slice_midi(song_name: str, start: float, end: float, out_path: Path):
    import pretty_midi
    merged_path = MERGED_ROOT / f"{song_name}.mid"
    src = pretty_midi.PrettyMIDI(str(merged_path))
    out = pretty_midi.PrettyMIDI()
    for inst in src.instruments:
        if inst.name not in STEM_ORDER:
            continue
        new_inst = pretty_midi.Instrument(program=inst.program, is_drum=inst.is_drum, name=inst.name)
        for note in inst.notes:
            if not (start <= note.start < end):
                continue
            new_inst.notes.append(pretty_midi.Note(
                velocity=note.velocity, pitch=note.pitch,
                start=note.start - start, end=min(note.end, end) - start,
            ))
        new_inst.notes.sort(key=lambda n: n.start)
        out.instruments.append(new_inst)
    out.write(str(out_path))


def process_anchor(label: str, row: dict, min_notes: int):
    song_name = row["song"]
    print(f"Anchor {label}: {song_name}  (song-wide density {float(row['density']):.1%}, "
          f"{row['n_non_chord_tone']}/{row['n_notes']} notes)")

    mode = "max" if label == "A" else "min"
    window, caveat = find_best_window(song_name, mode, min_notes)
    if window is None:
        print(f"  SKIPPED: {caveat}", file=sys.stderr)
        return None
    if caveat:
        print(f"  NOTE: {caveat}")

    start, end = window["start"], window["end"]
    print(f"  window [{start:7.2f} - {end:7.2f}]  ({window['span']:.1f}s)  "
          f"density={window['density']:.1%}  ({window['n_non_chord_tone']}/{window['n_notes']} notes)")

    excerpt_dir = GT_ANCHOR_ROOT / f"{song_name}_excerpt"
    excerpt_dir.mkdir(parents=True, exist_ok=True)

    slice_audio(song_name, start, end, excerpt_dir)
    slice_midi(song_name, start, end, excerpt_dir / "merged_excerpt.mid")
    for stem in STEM_ORDER:
        write_template(excerpt_dir / f"{stem}.csv")

    print(f"  -> {excerpt_dir}/  ({', '.join(f'{s}.wav' for s in STEM_ORDER)}, merged_excerpt.mid, "
          f"{', '.join(f'{s}.csv' for s in STEM_ORDER)})")

    return {"label": label, "song": song_name, "start": start, "end": end,
            "density": window["density"], "n_notes": window["n_notes"],
            "n_non_chord_tone": window["n_non_chord_tone"]}


def write_manifest(results, corpus_note):
    GT_ANCHOR_ROOT.mkdir(parents=True, exist_ok=True)
    with ANCHOR_MANIFEST.open("a") as f:
        f.write(f"# {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        if corpus_note:
            f.write(f"# {corpus_note}\n")
        for r in results:
            f.write(f"Anchor {r['label']}: {r['song']}  [{r['start']:.3f}-{r['end']:.3f}]  "
                     f"density={r['density']:.1%} ({r['n_non_chord_tone']}/{r['n_notes']} notes)\n")
        f.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-notes", type=int, default=DEFAULT_MIN_NOTES,
                         help=f"Minimum tagged notes a candidate window must have to be considered "
                              f"(default {DEFAULT_MIN_NOTES}) - guards against a short, noisy span winning by chance")
    args = parser.parse_args()

    song_rows = load_song_density_rows()
    anchor_a, anchor_b, corpus_note = pick_anchors(song_rows)
    if anchor_a is None:
        print(f"Can't pick anchors: {corpus_note}", file=sys.stderr)
        print("Run extract_chords.py, tag_chord_tones.py, and rank_review_candidates.py first.", file=sys.stderr)
        sys.exit(1)
    if corpus_note:
        print(f"NOTE: {corpus_note}\n")

    if anchor_a["song"] == anchor_b["song"]:
        print("WARNING: top and bottom song are the same (only one tagged song available) - "
              "both anchors will point at the same song.", file=sys.stderr)

    results = []
    for label, row in (("A", anchor_a), ("B", anchor_b)):
        r = process_anchor(label, row, args.min_notes)
        if r:
            results.append(r)
        print()

    if results:
        write_manifest(results, corpus_note)
        print(f"Recorded in {ANCHOR_MANIFEST}")
    print("\nNext: hand-label the ground truth CSVs, then see the README for the Sonic Visualiser workflow.")


if __name__ == "__main__":
    main()
