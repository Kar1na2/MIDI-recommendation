#!/usr/bin/env python3
"""
Score merged/<song>.mid against hand-labeled ground truth in
ground_truth/<song_name>/{other,bass,drums}.csv, using mir_eval's onset and
note-transcription metrics, broken down per stem so you can see which
transcription stage (Basic Pitch for other/bass, ADTLib for drums) is the
weak point - plus a summary table averaged across the whole sample.

Run select_eval_sample.py first to pick a sample and scaffold the CSVs to
fill in by hand.

Two metrics per stem, per song:

  Onset F-measure (mir_eval.onset.f_measure): did we find a note attack at
  roughly the right time? Uses every annotated ground-truth row, including
  onset-only ones (offset/pitch left blank) - the cheapest annotation to do
  and the metric available for every stem regardless of how much hand-labeling
  effort went in.

  Note transcription P/R/F (mir_eval.transcription.precision_recall_f1_overlap):
  did we find a note at the right time AND the right pitch? Only uses
  ground-truth rows with both offset and pitch filled in; a song/stem with
  none of those is reported as "insufficient note-level ground truth" rather
  than silently scored as 0. Reported two ways:
    - note_offset:  onset + pitch + offset all have to match (the standard
                     mir_eval note-transcription metric)
    - note_noffset: onset + pitch only, offset ignored
  The offset-sensitive number isn't a meaningful signal for the drums stem:
  transcribe_drums.py writes a fixed 50ms note length, not a measured
  duration, so note_noffset is the one to trust there. For bass/other,
  Basic Pitch estimates real durations, so both are meaningful.

Usage:
    uv run evaluate_transcription.py                       # score the whole ground_truth/ sample
    uv run evaluate_transcription.py --songs "SongA,SongB"  # score just these songs
    uv run evaluate_transcription.py --onset-window 0.05 --pitch-tolerance 50
    uv run evaluate_transcription.py --output-csv eval_results.csv
"""
import argparse
import csv
import sys
import warnings
from pathlib import Path

import numpy as np
import pretty_midi
import mir_eval

from gt_format import load_ground_truth, full_note_rows, GroundTruthError

MERGED_ROOT = Path("merged")
GT_ROOT = Path("ground_truth")
STEMS = ("other", "bass", "drums")
METRIC_COLUMNS = ["onset_p", "onset_r", "onset_f",
                   "note_offset_p", "note_offset_r", "note_offset_f",
                   "note_noffset_p", "note_noffset_r", "note_noffset_f"]


def load_sample_songs():
    manifest = GT_ROOT / "_sample.txt"
    if manifest.exists():
        songs = [l.strip() for l in manifest.read_text().splitlines() if l.strip() and not l.startswith("#")]
        return [s for s in songs if (GT_ROOT / s).is_dir()]
    if GT_ROOT.is_dir():
        return sorted(p.name for p in GT_ROOT.iterdir() if p.is_dir())
    return []


def get_estimated_notes(song_name: str, stem: str):
    """(onsets, intervals[n,2], pitches_midi) for the `stem`-named instrument
    track in merged/<song>.mid, or None if that track/file isn't there."""
    merged_path = MERGED_ROOT / f"{song_name}.mid"
    if not merged_path.exists():
        return None
    pm = pretty_midi.PrettyMIDI(str(merged_path))
    inst = next((i for i in pm.instruments if i.name == stem), None)
    if inst is None:
        return None
    notes = sorted(inst.notes, key=lambda n: n.start)
    onsets = np.array([n.start for n in notes])
    intervals = np.array([[n.start, n.end] for n in notes]) if notes else np.zeros((0, 2))
    pitches = np.array([float(n.pitch) for n in notes])
    return onsets, intervals, pitches


def score_stem(song_name: str, stem: str, onset_window: float, pitch_tolerance: float, offset_ratio: float):
    gt_path = GT_ROOT / song_name / f"{stem}.csv"
    try:
        gt_rows = load_ground_truth(gt_path)
    except GroundTruthError as e:
        return {"error": str(e)}
    if not gt_rows:
        return {"error": "no ground truth annotated yet"}

    est = get_estimated_notes(song_name, stem)
    if est is None:
        return {"error": f"no '{stem}' track in merged/{song_name}.mid"}
    est_onsets, est_intervals, est_pitches_midi = est

    result = {"n_gt_onsets": len(gt_rows), "n_est_onsets": len(est_onsets)}

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # mir_eval warns on empty ref/est - we report n_gt/n_est instead

        ref_onsets = np.array([r["onset"] for r in gt_rows])
        # NB: mir_eval.onset.f_measure returns (F, P, R) - unlike transcription's (P, R, F, overlap) below
        f, p, r = mir_eval.onset.f_measure(ref_onsets, est_onsets, window=onset_window)
        result["onset_p"], result["onset_r"], result["onset_f"] = p, r, f

        full_rows = full_note_rows(gt_rows)
        result["n_gt_full_notes"] = len(full_rows)
        if not full_rows:
            result["note_error"] = "insufficient note-level ground truth (need offset+pitch filled in)"
        else:
            ref_intervals = np.array([[r["onset"], r["offset"]] for r in full_rows])
            ref_pitches_hz = mir_eval.util.midi_to_hz(np.array([r["pitch"] for r in full_rows]))
            est_pitches_hz = mir_eval.util.midi_to_hz(est_pitches_midi) if len(est_pitches_midi) else np.array([])

            for label, ratio in (("note_offset", offset_ratio), ("note_noffset", None)):
                p, r, f, _ = mir_eval.transcription.precision_recall_f1_overlap(
                    ref_intervals, ref_pitches_hz, est_intervals, est_pitches_hz,
                    onset_tolerance=onset_window, pitch_tolerance=pitch_tolerance, offset_ratio=ratio,
                )
                result[f"{label}_p"], result[f"{label}_r"], result[f"{label}_f"] = p, r, f

    return result


def fmt(x):
    return f"{x:.2f}" if isinstance(x, float) else "  - "


def print_song_report(song_name: str, stem_results: dict):
    print(f"\n{song_name}")
    for stem in STEMS:
        res = stem_results[stem]
        if "error" in res and "n_gt_onsets" not in res:
            print(f"  {stem:6s}  SKIPPED: {res['error']}")
            continue
        onset_line = (f"onset P/R/F={fmt(res['onset_p'])}/{fmt(res['onset_r'])}/{fmt(res['onset_f'])} "
                       f"(gt={res['n_gt_onsets']}, est={res['n_est_onsets']})")
        if "note_error" in res:
            note_line = res["note_error"]
        else:
            note_line = (f"note[+offset] F={fmt(res['note_offset_f'])}  "
                         f"note[no offset] F={fmt(res['note_noffset_f'])}  (gt full notes={res['n_gt_full_notes']})")
        print(f"  {stem:6s}  {onset_line}")
        print(f"          {note_line}")


def print_summary_table(all_results: dict):
    print("\n" + "=" * 78)
    print("SUMMARY (mean across sample, per stem)")
    print("=" * 78)
    header = f"{'stem':7s} {'n':>3s} {'onset_P':>8s} {'onset_R':>8s} {'onset_F':>8s} {'noteF+off':>10s} {'noteF noff':>11s}"
    print(header)
    for stem in STEMS:
        rows = [r[stem] for r in all_results.values() if "onset_f" in r[stem]]
        n = len(rows)
        if n == 0:
            print(f"{stem:7s} {0:3d}   (no scored songs for this stem - nothing annotated, or no merged track)")
            continue
        mean_onset_p = np.mean([r["onset_p"] for r in rows])
        mean_onset_r = np.mean([r["onset_r"] for r in rows])
        mean_onset_f = np.mean([r["onset_f"] for r in rows])
        note_rows = [r for r in rows if "note_offset_f" in r]
        mean_note_off = np.mean([r["note_offset_f"] for r in note_rows]) if note_rows else None
        mean_note_noff = np.mean([r["note_noffset_f"] for r in note_rows]) if note_rows else None
        print(f"{stem:7s} {n:3d} {mean_onset_p:8.2f} {mean_onset_r:8.2f} {mean_onset_f:8.2f} "
              f"{fmt(mean_note_off):>10s} {fmt(mean_note_noff):>11s}"
              + (f"   ({len(note_rows)}/{n} songs had note-level gt)" if note_rows else "   (no note-level gt yet)"))
    print("=" * 78)


def write_csv(path: Path, all_results: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["song", "stem"] + METRIC_COLUMNS + ["n_gt_onsets", "n_est_onsets", "n_gt_full_notes", "note"])
        for song, stem_results in all_results.items():
            for stem in STEMS:
                r = stem_results[stem]
                if "onset_f" not in r:
                    writer.writerow([song, stem] + [""] * len(METRIC_COLUMNS) + ["", "", "", r.get("error", "")])
                    continue
                row = [r.get(c, "") for c in METRIC_COLUMNS]
                writer.writerow([song, stem] + row + [r["n_gt_onsets"], r["n_est_onsets"],
                                                        r.get("n_gt_full_notes", 0), r.get("note_error", "")])
    print(f"\nWrote per-song, per-stem results to {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--songs", type=str, default=None,
                         help="Comma-separated song names to score (default: everything in ground_truth/)")
    parser.add_argument("--onset-window", type=float, default=0.05, help="Onset match tolerance, seconds (default 0.05)")
    parser.add_argument("--pitch-tolerance", type=float, default=50.0, help="Pitch match tolerance, cents (default 50)")
    parser.add_argument("--offset-ratio", type=float, default=0.2,
                         help="Offset match tolerance as a fraction of note duration (default 0.2)")
    parser.add_argument("--output-csv", type=str, default=None, help="Also write per-song, per-stem results to this CSV")
    args = parser.parse_args()

    songs = [s.strip() for s in args.songs.split(",")] if args.songs else load_sample_songs()
    if not songs:
        print("No songs to score. Run select_eval_sample.py first, or pass --songs.")
        return

    all_results = {}
    for song in songs:
        stem_results = {stem: score_stem(song, stem, args.onset_window, args.pitch_tolerance, args.offset_ratio)
                         for stem in STEMS}
        all_results[song] = stem_results
        print_song_report(song, stem_results)

    print_summary_table(all_results)

    if args.output_csv:
        write_csv(Path(args.output_csv), all_results)


if __name__ == "__main__":
    main()
