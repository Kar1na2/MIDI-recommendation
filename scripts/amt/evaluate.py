"""Compare candidate MIDIs against the same reviewed, bounded reference CSV.

Onset-only labels cannot establish pitch accuracy. No reference is generated
from predictions. Every MIDI must use the reference audio's absolute clock.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import mir_eval
import numpy as np
import pretty_midi

from lib.gt_format import load_ground_truth


def frame_score(reference, notes, start, end):
    """Micro pitch-frame F1 on a 10 ms grid, including carried-in notes."""
    times = np.arange(start, end, .01)
    ref = np.zeros((len(times), 128), dtype=bool)
    est = np.zeros_like(ref)
    for r in reference:
        if r["offset"] is not None and r["pitch"] is not None:
            first, last = np.searchsorted(times, [r["onset"], r["offset"]])
            ref[first:last, int(round(r["pitch"]))] = True
    for n in notes:
        first, last = np.searchsorted(times, [n.start, n.end])
        est[first:last, n.pitch] = True
    tp = int(np.count_nonzero(ref & est))
    nref, nest = int(ref.sum()), int(est.sum())
    return {"precision": tp / nest if nest else 0., "recall": tp / nref if nref else 0.,
            "f1": 2 * tp / (nref + nest) if nref + nest else 0., "frame_step_seconds": .01}


def score(reference, notes, start, end, *, drums=False):
    if any(not math.isfinite(r["onset"]) or any(r[k] is not None and not math.isfinite(r[k])
                                              for k in ("offset", "pitch")) for r in reference):
        raise ValueError("Reference contains nonfinite annotations")
    rows = [r for r in reference if start <= r["onset"] < end]
    estimates = [n for n in notes if start <= n.start < end]
    if not rows:
        return {"status": "no_reviewed_reference", "reference_notes": 0, "estimated_notes": len(estimates)}
    f, p, r = mir_eval.onset.f_measure(np.array(sorted(r["onset"] for r in rows)),
                                      np.array(sorted(n.start for n in estimates)), window=.05)
    result = {"status": "scored", "reference_notes": len(rows), "estimated_notes": len(estimates),
              "attack_onset": {"precision": p, "recall": r, "f1": f}, "tolerance_seconds": .05}
    if any(r["offset"] is None or r["pitch"] is None for r in rows):
        result["note_metrics_status"] = "requires_complete_pitch_and_offset_labels_for_this_window"
        return result
    if any(not 0 <= r["pitch"] <= 127 for r in rows):
        raise ValueError("Reference MIDI pitches must be in [0, 127]")
    ref_intervals = np.array([[r["onset"], r["offset"]] for r in rows])
    est_intervals = np.array([[n.start, n.end] for n in estimates]).reshape(-1, 2)
    ref_hz = mir_eval.util.midi_to_hz(np.array([r["pitch"] for r in rows]))
    est_hz = mir_eval.util.midi_to_hz(np.array([n.pitch for n in estimates]))
    for name, ratio in (("note_onset", None), ("note_onset_offset", .2)):
        if drums and ratio is not None:
            continue
        p, r, f, _ = mir_eval.transcription.precision_recall_f1_overlap(
            ref_intervals, ref_hz, est_intervals, est_hz,
            onset_tolerance=.05, pitch_tolerance=50., offset_ratio=ratio)
        result[name] = {"precision": p, "recall": r, "f1": f}
    active_reference = [r for r in reference if r["onset"] < end and
                        (r["offset"] is None or r["offset"] > start)]
    if not drums and all(r["pitch"] is not None and r["offset"] is not None for r in active_reference):
        if any(not 0 <= r["pitch"] <= 127 for r in active_reference):
            raise ValueError("Reference MIDI pitches must be in [0, 127]")
        result["pitch_frame"] = frame_score(active_reference, notes, start, end)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--estimate", type=Path, action="append", required=True)
    parser.add_argument("--start", type=float, required=True)
    parser.add_argument("--end", type=float, required=True)
    parser.add_argument("--track", type=int, help="Select the same track index in each MIDI; otherwise pool pitched tracks")
    parser.add_argument("--drums", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not all(math.isfinite(x) for x in (args.start, args.end)) or not 0 <= args.start < args.end:
        parser.error("Use a finite interval with 0 <= start < end")
    if args.output.exists():
        parser.error("Output already exists")
    reference = load_ground_truth(args.reference)
    results = []
    for path in args.estimate:
        midi = pretty_midi.PrettyMIDI(str(path))
        if args.track is not None and not 0 <= args.track < len(midi.instruments):
            parser.error(f"Invalid track index for {path}")
        instruments = [midi.instruments[args.track]] if args.track is not None else [
            i for i in midi.instruments if i.is_drum == args.drums]
        result = score(reference, [n for i in instruments for n in i.notes], args.start, args.end, drums=args.drums)
        results.append({"midi": str(path.resolve()), **result})
    report = {"reference": str(args.reference.resolve()), "window": [args.start, args.end],
              "instrument_matching": "selected_track" if args.track is not None else "instrument_agnostic",
              "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
