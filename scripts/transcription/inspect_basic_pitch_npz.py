#!/usr/bin/env python3
"""
inspect_basic_pitch_npz.py

Inspect the raw model output saved by `basic-pitch --save-model-outputs`.

Basic Pitch's final MIDI is the result of thresholding + note-formation logic
(onset/frame linking, minimum note length, melodia trick) applied on top of
three raw activation grids: note, onset, and contour. A lot gets thrown away
in that process. This script shows you what the model actually "considered"
in a given time window, before any of that happens -- useful for telling
apart "the model never saw this note" from "the model saw it but it didn't
clear the threshold."

Usage:
    make inspect-basic-pitch-npz ARGS="path/to/song_basic_pitch_model_output.npz --start 12.30 --end 14.10"
    # or directly:
    uv run python -m scripts.transcription.inspect_basic_pitch_npz \
        path/to/song_basic_pitch_model_output.npz --start 12.30 --end 14.10

Run this inside the same environment basic-pitch is installed in (the main
`.venv`) -- it imports basic_pitch.constants / basic_pitch.note_creation
directly so the frame-to-time conversion exactly matches what produced your
MIDI file, instead of re-deriving it by hand and risking drift. Standalone
diagnostic tool, not part of the resumable pipeline (no `--dry-run`/
`--limit`/failure log) - moved here from the repo root (was `tmp.py`).

Requires re-running Basic Pitch with `--save-model-outputs` to produce the
`.npz` file this reads; transcribe_harmonic.py does not pass that flag by
default (the .npz files are large and only useful for this kind of ad hoc
debugging), so there is no ready-made corpus of them - point this at one you
generate yourself for a specific song/window you're investigating.
"""

import argparse
import numpy as np

from basic_pitch.constants import FREQ_BINS_CONTOURS
from basic_pitch.note_creation import model_frames_to_time, MIDI_OFFSET

# Mirrors basic_pitch.inference.DEFAULT_ONSET_THRESHOLD / DEFAULT_FRAME_THRESHOLD.
# Hardcoded here (rather than imported) to avoid pulling in the heavier
# inference.py module (and its optional TF/ONNX/CoreML backends) just to
# read two float constants.
DEFAULT_ONSET_THRESHOLD = 0.5
DEFAULT_FRAME_THRESHOLD = 0.3

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_to_name(midi_num: float) -> str:
    n = int(round(midi_num))
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


def freq_to_midi(freq: float) -> float:
    return 69 + 12 * np.log2(freq / 440.0)


def load_model_output(npz_path: str):
    data = np.load(npz_path, allow_pickle=True)
    model_output = data["basic_pitch_model_output"].item()
    return model_output["note"], model_output["onset"], model_output["contour"]


def active_bins(activations, times, start, end, min_activation):
    """For a (n_frames, n_bins) grid, return every bin whose activation
    crosses min_activation at least once inside [start, end]."""
    frame_mask = (times >= start) & (times <= end)
    if not frame_mask.any():
        return [], frame_mask
    windowed = activations[frame_mask]
    windowed_times = times[frame_mask]

    results = []
    for bin_idx in range(activations.shape[1]):
        col = windowed[:, bin_idx]
        peak = float(col.max())
        if peak < min_activation:
            continue
        active = np.where(col >= min_activation)[0]
        peak_idx = int(col.argmax())
        results.append(
            {
                "bin_idx": bin_idx,
                "peak": peak,
                "peak_t": float(windowed_times[peak_idx]),
                "first_t": float(windowed_times[active[0]]),
                "last_t": float(windowed_times[active[-1]]),
            }
        )
    return results, frame_mask


def main():
    parser = argparse.ArgumentParser(
        description="Inspect raw Basic Pitch model output within a time window."
    )
    parser.add_argument("npz_path", help="Path to the *_basic_pitch_model_output.npz file")
    parser.add_argument("--start", type=float, required=True, help="Start time in seconds")
    parser.add_argument("--end", type=float, required=True, help="End time in seconds")
    parser.add_argument(
        "--min-activation",
        type=float,
        default=0.05,
        help="Minimum activation to report a bin as 'considered' (default: 0.05)",
    )
    parser.add_argument(
        "--timeline",
        action="store_true",
        help="Also print a per-frame activation strip for every notable NOTE bin, "
        "so you can see exactly when each pitch turns on/off inside the window "
        "instead of just its first/last-seen span.",
    )
    args = parser.parse_args()

    note, onset, contour = load_model_output(args.npz_path)
    times = model_frames_to_time(note.shape[0])

    print(f"NPZ: {args.npz_path}")
    print(f"Window: {args.start}s - {args.end}s")
    print(
        f"Activation floor: {args.min_activation}  |  "
        f"Default onset thresh: {DEFAULT_ONSET_THRESHOLD}  |  "
        f"Default frame thresh: {DEFAULT_FRAME_THRESHOLD}"
    )
    print()

    print("=== NOTE activation ('is this pitch sounding') -- semitone resolution ===")
    print(
        f"{'MIDI':>5} {'Note':>5} {'Peak':>6} {'Passes default':>16} {'Peak time':>10} "
        f"{'First seen':>11} {'Last seen':>11}"
    )
    note_rows, mask = active_bins(note, times, args.start, args.end, args.min_activation)
    if not mask.any():
        print("  (no frames fall inside this window -- check --start/--end)")
    for r in sorted(note_rows, key=lambda x: -x["peak"]):
        midi_num = MIDI_OFFSET + r["bin_idx"]
        passes = "YES" if r["peak"] >= DEFAULT_FRAME_THRESHOLD else "no"
        print(
            f"{midi_num:5d} {midi_to_name(midi_num):>5} {r['peak']:6.3f} {passes:>16} "
            f"{r['peak_t']:9.3f}s {r['first_t']:10.3f}s {r['last_t']:10.3f}s"
        )

    print()
    print("=== ONSET activation ('is a new note starting here') -- semitone resolution ===")
    print(
        f"{'MIDI':>5} {'Note':>5} {'Peak':>6} {'Passes default':>16} {'Peak time':>10} "
        f"{'First seen':>11} {'Last seen':>11}"
    )
    onset_rows, _ = active_bins(onset, times, args.start, args.end, args.min_activation)
    for r in sorted(onset_rows, key=lambda x: -x["peak"]):
        midi_num = MIDI_OFFSET + r["bin_idx"]
        passes = "YES" if r["peak"] >= DEFAULT_ONSET_THRESHOLD else "no"
        print(
            f"{midi_num:5d} {midi_to_name(midi_num):>5} {r['peak']:6.3f} {passes:>16} "
            f"{r['peak_t']:9.3f}s {r['first_t']:10.3f}s {r['last_t']:10.3f}s"
        )

    print()
    print("=== CONTOUR activation (finer: 3 bins/semitone -- catches near-misses & pitch drift) ===")
    print(
        f"{'Freq(Hz)':>9} {'~Note':>6} {'Cents off':>10} {'Peak':>6} {'Peak time':>10} "
        f"{'First seen':>11} {'Last seen':>11}"
    )
    contour_rows, _ = active_bins(contour, times, args.start, args.end, args.min_activation)
    for r in sorted(contour_rows, key=lambda x: -x["peak"]):
        freq = FREQ_BINS_CONTOURS[r["bin_idx"]]
        midi_est = freq_to_midi(freq)
        cents_off = (midi_est - round(midi_est)) * 100
        print(
            f"{freq:9.1f} {midi_to_name(midi_est):>6} {cents_off:10.1f} {r['peak']:6.3f} "
            f"{r['peak_t']:9.3f}s {r['first_t']:10.3f}s {r['last_t']:10.3f}s"
        )

    if args.timeline and note_rows:
        print()
        print("=== TIMELINE (NOTE activation, per-frame) ===")
        print("Chars: '.'<0.15  '-'<0.30  '='<0.50 (frame threshold)  '#'>=0.50")
        frame_mask = (times >= args.start) & (times <= args.end)
        windowed = note[frame_mask]
        windowed_times = times[frame_mask]
        header = "".join(
            str(int(t) % 10) if abs(t - round(t)) < 0.02 else " " for t in windowed_times
        )
        print(f"{'':>10} {header}")
        for r in sorted(note_rows, key=lambda x: x["bin_idx"]):
            midi_num = MIDI_OFFSET + r["bin_idx"]
            col = windowed[:, r["bin_idx"]]
            strip = "".join(
                "#" if v >= 0.5 else "=" if v >= DEFAULT_FRAME_THRESHOLD else "-" if v >= 0.15 else "."
                for v in col
            )
            print(f"{midi_num:4d} {midi_to_name(midi_num):>5} {strip}")

    print()
    print("Note: 'Passes default' only checks raw activation against Basic Pitch's default")
    print("thresholds -- it does NOT replicate the full note-formation logic (onset/frame")
    print("linking, minimum note length, melodia trick). A 'no' here is a tuning candidate,")
    print("not a guaranteed miss -- and a bin that never appears above --min-activation at")
    print("all is your real signal that the model never registered the pitch in this window.")


if __name__ == "__main__":
    main()