"""Analyze predicted or reference MIDI without using spectrogram attack peaks.

Retains raw note timings and register. Repeated phrases are candidates from
quantized, contiguous attack-group sequences, not verified musical labels.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path

import numpy as np
import pretty_midi


def attack_groups(notes, tolerance=.03):
    """Group near-simultaneous notes relative to the first onset, not a chain."""
    groups = []
    for note in sorted(notes, key=lambda n: (n.start, n.pitch)):
        if not groups or note.start - groups[-1][0].start > tolerance:
            groups.append([note])
        else:
            groups[-1].append(note)
    return groups


def phrase_key(groups, transpose=False, rhythm_only=False):
    starts = np.array([g[0].start for g in groups])
    gaps = np.diff(starts)
    if len(gaps) == 0 or np.any(gaps <= 0):
        return None
    unit = float(np.median(gaps))
    # Tempo-normalized IOIs; adjacent buckets can still miss similar phrases.
    rhythm = tuple(np.rint(gaps / unit * 10).astype(int))
    if rhythm_only:
        return rhythm
    origin = min(n.pitch for n in groups[0]) if transpose else 0
    pitches = tuple(tuple(sorted(n.pitch - origin for n in g)) for g in groups)
    durations = tuple(tuple(sorted((n.pitch - origin, int(round((n.end - n.start) / unit * 10)))
                                   for n in g)) for g in groups)
    return rhythm, pitches, durations


def repeated_phrases(groups, *, transpose=False, rhythm_only=False, max_results=30):
    candidates = []
    for length in (16, 12, 8, 6, 4):
        lookup = defaultdict(list)
        for index in range(len(groups) - length + 1):
            window = groups[index:index + length]
            # Do not join figures across long empty passages.
            gaps = np.diff([g[0].start for g in window])
            if max(gaps, default=0) > 2.:
                continue
            key = phrase_key(window, transpose, rhythm_only)
            if key is not None:
                lookup[key].append(index)
        for indices in lookup.values():
            disjoint = []
            for index in indices:
                if not disjoint or index >= disjoint[-1] + length:
                    disjoint.append(index)
            if len(disjoint) < 2:
                continue
            occurrences = [{"start": groups[i][0].start,
                            "end": max(n.end for g in groups[i:i + length] for n in g)} for i in disjoint]
            candidates.append({"attack_groups": length, "occurrences": occurrences,
                               "status": "midi_sequence_candidate"})
    candidates.sort(key=lambda x: (-x["attack_groups"], -len(x["occurrences"]), x["occurrences"][0]["start"]))
    return candidates[:max_results]


def analyze(midi, *, start=0., end=None, bpm=None, beat_origin=0., transpose=False):
    if end is None:
        end = midi.get_end_time()
    if not all(math.isfinite(x) for x in (start, end, beat_origin)) or start < 0 or end <= start:
        raise ValueError("Use a finite interval with 0 <= start < end")
    if bpm is not None and (not math.isfinite(bpm) or bpm <= 0):
        raise ValueError("BPM must be finite and positive")
    tracks = []
    for index, inst in enumerate(midi.instruments):
        # Attacks belong to [start, end). Do not manufacture an attack for a
        # carried-in sustained note. Durations remain raw key-off durations.
        notes = [n for n in inst.notes if start <= n.start < end]
        groups = attack_groups(notes)
        onsets = [g[0].start for g in groups]
        gaps = np.diff(onsets)
        events = [{"onset": n.start, "offset": n.end, "pitch": n.pitch,
                   "duration_seconds": n.end - n.start,
                   **({"beat": (n.start - beat_origin) * bpm / 60,
                       "duration_beats": (n.end - n.start) * bpm / 60} if bpm else {})}
                  for n in sorted(notes, key=lambda n: (n.start, n.pitch))]
        # Only explicitly monophonic attack groups get a melodic contour.
        contour = [int(np.sign(b[0].pitch - a[0].pitch)) if len(a) == len(b) == 1 else None
                   for a, b in zip(groups, groups[1:])] if not inst.is_drum else None
        tracks.append({"track": index, "name": inst.name, "program": int(inst.program), "is_drum": bool(inst.is_drum),
                       "note_count": len(notes), "attack_group_count": len(groups),
                       "pitch_range": [min(n.pitch for n in notes), max(n.pitch for n in notes)] if notes else None,
                       "median_ioi_seconds": float(np.median(gaps)) if len(gaps) else None,
                       "monophonic_contour": contour, "events": events,
                       "rhythm_repeats": repeated_phrases(groups, rhythm_only=True),
                       "note_repeats": repeated_phrases(groups, transpose=transpose) if not inst.is_drum else [],
                       # Drum MIDI keys are drum classes, never scale degrees.
                       "drum_class_repeats": repeated_phrases(groups) if inst.is_drum else []})
    return {"schema_version": 1, "representation": "midi_note_sequences",
            "status": "depends_on_input_transcription_accuracy", "window": [start, end],
            "clock": "input_midi_seconds", "bpm": bpm, "beat_origin": beat_origin,
            "beat_grid_source": "user_supplied" if bpm else "none_seconds_only",
            "transposition_invariant": transpose, "attack_group_tolerance_seconds": .03,
            "repetition_method": "contiguous 4/6/8/12/16 attack groups; IOI and duration buckets at 0.1 median IOI; nonoverlapping occurrences",
            "limitations": ["No inferred meter, downbeat, chord name, instrument identity verification, or section boundary",
                            "Note ends are MIDI key-off times; sustain-pedal sounding duration is not inferred",
                            "Exact buckets can miss timing variants; rhythm-only matches can have different pitches"],
            "tracks": tracks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--midi", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=float, default=0.)
    parser.add_argument("--end", type=float)
    parser.add_argument("--bpm", type=float, help="Reviewed tempo; model MIDI may contain placeholder tempo")
    parser.add_argument("--beat-origin", type=float, default=0.)
    parser.add_argument("--transpose", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new path")
    try:
        result = analyze(pretty_midi.PrettyMIDI(str(args.midi)), start=args.start, end=args.end,
                         bpm=args.bpm, beat_origin=args.beat_origin, transpose=args.transpose)
    except ValueError as error:
        parser.error(str(error))
    result["input_midi"] = str(args.midi.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps([{k: t[k] for k in ["track", "name", "note_count", "attack_group_count", "pitch_range"]}
                      for t in result["tracks"]], indent=2))


if __name__ == "__main__":
    main()
