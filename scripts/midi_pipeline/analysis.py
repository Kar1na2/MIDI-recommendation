"""Symbolic rhythm/melody analysis. No audio features, model calls or legacy readers."""
import argparse
import json
import math
from pathlib import Path

import numpy as np

from .store import ANALYSIS_VERSION, load_run, save

GROUP_TOLERANCE = .03
MAX_GROUPS = 24
MATCH_COST = .22


def groups(notes):
    result = []
    for n in sorted(notes, key=lambda n: (n["onset"], n["pitch"])):
        # Anchor to the first attack; do not chain close attacks into a chord.
        if result and n["onset"] - result[-1][0]["onset"] <= GROUP_TOLERANCE:
            result[-1].append(n)
        else:
            result.append([n])
    return result


def features(sequence, mode):
    times = np.array([g[0]["onset"] for g in sequence])
    gaps = np.diff(times)
    scale = float(np.median(gaps[gaps > 0])) if np.any(gaps > 0) else 1.
    durations = np.array([np.median([n["duration"] for n in g]) for g in sequence])
    rhythm = np.column_stack((np.log1p(np.r_[gaps, gaps[-1] if len(gaps) else 0] / scale),
                              np.log1p(durations / scale)))
    if mode == "rhythm":
        return rhythm
    centers = np.array([np.mean([n["pitch"] for n in g]) for g in sequence])
    if mode == "contour":
        return np.column_stack((rhythm, np.r_[0, np.sign(np.diff(centers))]))
    # Absolute register is removed, preserving intervals and simultaneous voicing.
    widths = np.array([max(n["pitch"] for n in g) - min(n["pitch"] for n in g) for g in sequence])
    return np.column_stack((rhythm, (centers - centers[0]) / 6., widths / 12.,
                            np.array([len(g) for g in sequence]) / 3.))


def dtw(a, b):
    """Global DTW for candidate subsequences; length/tempo may differ. Return path."""
    costs = np.abs(a[:, None, :] - b[None, :, :]).mean(axis=2)
    table = np.full((len(a) + 1, len(b) + 1), np.inf)
    table[0, 0] = 0
    previous = {}
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            options = [(table[i - 1, j - 1], i - 1, j - 1),
                       (table[i - 1, j] + .08, i - 1, j),
                       (table[i, j - 1] + .08, i, j - 1)]
            value, pi, pj = min(options)
            table[i, j] = costs[i - 1, j - 1] + value
            previous[i, j] = (pi, pj)
    path, i, j = [], len(a), len(b)
    while i and j:
        path.append([i - 1, j - 1])
        i, j = previous[i, j]
    path.reverse()
    return {"cost": float(table[-1, -1] / max(len(path), 1)), "alignment": path}


def recurrence(sequence, mode, max_groups=MAX_GROUPS):
    """All attack offsets and lengths 3..max_groups, approximate shortlist then DTW.

    Phrase seconds/beat lengths are unrestricted. This bounded search is deliberately
    candidate generation, not exhaustive discovery or evidence of a verified motif.
    """
    count = len(sequence)
    # A 24-attack overlap preserves every supported phrase while bounding memory.
    if count > 240:
        stride = 240 - max_groups + 1
        matches = [match for first in range(0, count, stride)
                   for match in recurrence(sequence[first:first + 240], mode, max_groups)]
        retained = []
        for match in matches:
            if not any(all(abs(match["spans"][k][0] - item["spans"][k][0]) < .3 for k in (0, 1))
                       for item in retained):
                retained.append(match)
        return retained[:12]
    shortlist = []
    def windows(length):
        items = [features(sequence[i:i + length], mode) for i in range(count - length + 1)]
        descriptors = [np.column_stack([np.interp(np.linspace(0, 1, 8),
                        np.linspace(0, 1, length), f[:, k]) for k in range(f.shape[1])]).ravel()
                       for f in items]
        return items, np.array(descriptors)
    for length in range(3, min(max_groups, count // 2) + 1):
        aa, av = windows(length)
        for other_length in (length, length + 1):
            if other_length > max_groups or other_length >= count:
                continue
            bb, bv = windows(other_length)
            # Vectorize per query to bound temporary memory on long contexts.
            candidates = []
            for i in range(len(aa)):
                first_j = i + length
                if first_j >= len(bb):
                    break
                scores = np.abs(bv[first_j:] - av[i]).mean(axis=1)
                j = first_j + int(np.argmin(scores))
                candidates.append((float(scores[j - first_j]), i, j, length, other_length))
            shortlist.extend(sorted(candidates)[:6])
    matches = []
    for _, i, j, length, other_length in sorted(shortlist)[:120]:
        a, b = sequence[i:i + length], sequence[j:j + other_length]
        match = dtw(features(a, mode), features(b, mode))
        if match["cost"] > MATCH_COST:
            continue
        spans = [[g[0]["onset"], max(n["offset"] for n in h[-1])] for g, h in ((a[0], a), (b[0], b))]
        # Above expression uses first note group's first onset and final group's offsets.
        matches.append({"kind": mode, "status": "candidate_from_predictions", **match,
                        "spans": spans, "attack_counts": [length, other_length],
                        "note_ids": [[n["id"] for g in h for n in g] for h in (a, b)],
                        "transposition_semitones": float(np.mean([n["pitch"] for n in b[0]]) -
                                                          np.mean([n["pitch"] for n in a[0]])),
                        "tempo_ratio": (b[-1][0]["onset"] - b[0][0]["onset"]) /
                                       (a[-1][0]["onset"] - a[0][0]["onset"])})
    # Prefer long low-cost candidates, suppress near-identical nested reports.
    matches.sort(key=lambda m: (-min(m["attack_counts"]), m["cost"]))
    retained = []
    for m in matches:
        if not any(all(abs(m["spans"][k][0] - r["spans"][k][0]) < .3 for k in (0, 1)) for r in retained):
            retained.append(m)
        if len(retained) == 12:
            break
    return retained


CHORDS = {"major": {0, 4, 7}, "minor": {0, 3, 7}, "diminished": {0, 3, 6},
          "augmented": {0, 4, 8}, "sus2": {0, 2, 7}, "sus4": {0, 5, 7},
          "dominant7": {0, 4, 7, 10}, "major7": {0, 4, 7, 11}, "minor7": {0, 3, 7, 10}}


def chord_candidates(notes):
    pitched = [n for n in notes if not n["is_drum"]]
    boundaries = sorted({t for n in pitched for t in (n["onset"], n["offset"])})
    changes = {}
    for n in pitched:
        changes.setdefault(n["onset"], []).append((1, n))
        changes.setdefault(n["offset"], []).append((-1, n))
    result = []
    active = {}
    for start, end in zip(boundaries, boundaries[1:]):
        for direction, note in changes[start]:
            if direction == 1:
                active[note["id"]] = note
            else:
                active.pop(note["id"], None)
        selected = list(active.values())
        pcs = {n["pitch"] % 12 for n in selected}
        if len(pcs) < 3 or end - start < .08:
            continue
        labels = [{"root_pitch_class": root, "quality": name}
                  for root in sorted(pcs) for name, template in CHORDS.items()
                  if {(p + root) % 12 for p in template} == pcs]
        candidate = {"start": start, "end": end, "pitch_classes": sorted(pcs),
                     "labels": labels, "note_ids": [n["id"] for n in selected],
                     "status": "candidate_simultaneity_not_verified_harmony"}
        if result and result[-1]["end"] == start and result[-1]["pitch_classes"] == candidate["pitch_classes"] and result[-1]["note_ids"] == candidate["note_ids"]:
            result[-1]["end"] = end
        else:
            result.append(candidate)
    return result


def arpeggios(sequence):
    result = []
    for i in range(len(sequence) - 2):
        for size in range(3, min(8, len(sequence) - i) + 1):
            sub = sequence[i:i + size]
            if any(len(g) != 1 for g in sub):
                break
            pitches = [g[0]["pitch"] for g in sub]
            delta = np.diff(pitches)
            pcs = {p % 12 for p in pitches}
            triads = [{"root_pitch_class": root, "quality": name} for root in sorted(pcs)
                      for name, template in CHORDS.items() if len(template) == 3 and
                      {(p + root) % 12 for p in template} == pcs]
            if triads and (np.all(delta > 0) or np.all(delta < 0)):
                result.append({"span": [sub[0][0]["onset"], sub[-1][0]["offset"]],
                               "note_ids": [g[0]["id"] for g in sub], "pitches": pitches,
                               "triad_candidates": triads, "status": "candidate_not_verified"})
    return result


def analyze(document, bpm=None, beat_origin=0.):
    start, end = document["source_window"]
    notes = [n for n in document["notes"] if n["in_source_window"]]
    tracks = []
    for track in document["tracks"]:
        selected = [n for n in notes if n["track"] == track["track"]]
        sequence = groups(selected)
        times = [g[0]["onset"] for g in sequence]
        gaps = np.diff(times)
        median = float(np.median(gaps)) if len(gaps) else None
        events = [{"note_id": n["id"], "onset": n["onset"], "duration": n["duration"],
                   **({"beat": (n["onset"] - beat_origin) * bpm / 60,
                       "duration_beats": n["duration"] * bpm / 60,
                       "nearest_quarter_beat": round((n["onset"] - beat_origin) * bpm / 60 * 4) / 4,
                       "subdivision_residual_beats": (n["onset"] - beat_origin) * bpm / 60 -
                          round((n["onset"] - beat_origin) * bpm / 60 * 4) / 4} if bpm else {})} for n in selected]
        mono = all(len(g) == 1 for g in sequence)
        overlaps = []
        active = []
        for current in sorted(selected, key=lambda n: (n["onset"], n["offset"])):
            active = [n for n in active if n["offset"] > current["onset"]]
            overlaps.extend({"note_ids": [n["id"], current["id"]],
                             "seconds": min(n["offset"], current["offset"]) - current["onset"]}
                            for n in active)
            active.append(current)
        drum = track["is_drum"]
        rhythm = recurrence(sequence, "rhythm") if len(sequence) >= 6 else []
        full = recurrence(sequence, "notes") if not drum and len(sequence) >= 6 else []
        boundaries = ([{"time": times[0], "kind": "first_observed_attack"}] if times else [])
        boundaries += [{"time": times[i + 1], "kind": "gap_candidate", "gap_seconds": float(gap)}
                       for i, gap in enumerate(gaps) if median and gap > max(.5, median * 3)]
        if selected:
            boundaries.append({"time": max(n["offset"] for n in selected), "kind": "last_observed_offset"})
        tracks.append({**track, "note_count": len(selected), "attack_count": len(sequence),
                       "ioi_seconds": gaps.tolist(), "duration_seconds": [n["duration"] for n in selected],
                       "relative_ioi_subdivisions": (gaps / median).tolist() if median else [],
                       "pitch_intervals": np.diff([g[0]["pitch"] for g in sequence]).tolist() if mono and not drum else None,
                       "contour": np.sign(np.diff([g[0]["pitch"] for g in sequence])).tolist() if mono and not drum else None,
                       "polyphonic_overlap": overlaps, "analytical_events": events,
                       "rhythm_candidates": rhythm, "note_sequence_candidates": full,
                       "contour_candidates": recurrence(sequence, "contour") if not drum and len(sequence) >= 6 else [],
                       "arpeggio_candidates": arpeggios(sequence) if not drum else [],
                       "phrase_boundary_candidates": boundaries,
                       "persistent_figure_candidates": [m for m in full if min(m["attack_counts"]) >= 6],
                       "overlay_candidate": {"time": times[0], "reason": "first observed track attack after window start"}
                                              if times and times[0] > start + 1 else None,
                       "percussion_semantics": "MIDI keys are drum classes; no pitch contours/chords" if drum else None})
    return {"version": ANALYSIS_VERSION, "run_id": document["run_id"], "status": "candidates_require_review",
            "source_window": [start, end], "beat_grid": {"bpm": bpm, "origin": beat_origin,
            "source": "explicit_user_grid" if bpm else "unavailable_seconds_only"},
            "method": {"group_tolerance_seconds": GROUP_TOLERANCE, "max_phrase_groups": MAX_GROUPS,
                       "match_cost_max": MATCH_COST, "search": "all attack offsets; lengths 3..24; nearest descriptor shortlist then DTW; adjacent lengths permitted",
                       "limitations": "bounded approximate search; centroid/width voicing summary can collide; polyphonic voice leading unresolved"},
            "tracks": tracks, "chord_progression_candidates": chord_candidates(notes),
            "unresolved": ["Predicted note accuracy is unverified on this recording",
                           "Instrument programs are predictions; velocity is a writer constant",
                           "No inferred meter/downbeat; beat grid requires external reviewed tempo",
                           "Gap/overlay candidates are observations, not verified musical phrase or section labels",
                           "Chord templates describe simultaneous MIDI pitch classes, not confirmed harmony",
                           "Drum offsets and sustain-pedal sounding durations are not measured"]}


def compare_windows(document, a, b):
    result = []
    for track in document["tracks"]:
        sequences = [groups([n for n in document["notes"] if n["track"] == track["track"]
                            and interval[0] <= n["onset"] < interval[1]]) for interval in (a, b)]
        entry = {"track": track["track"], "program": track["program"], "is_drum": track["is_drum"],
                 "attack_counts": [len(s) for s in sequences]}
        if all(sequences):
            for mode in (("rhythm",) if track["is_drum"] else ("rhythm", "contour", "notes")):
                entry[mode] = dtw(*(features(s, mode) for s in sequences))
        else:
            entry["status"] = "insufficient_predicted_attacks"
        result.append(entry)
    return {"windows": [list(a), list(b)], "metric": "normalized DTW cost, lower is closer; not a confidence or acoustic similarity",
            "tracks": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bpm", type=float)
    parser.add_argument("--beat-origin", type=float, default=0.)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output file")
    if args.bpm is not None and (not math.isfinite(args.bpm) or args.bpm <= 0):
        parser.error("Positive finite tempo required")
    _, document = load_run(args.run)
    save(args.output, analyze(document, args.bpm, args.beat_origin))


if __name__ == "__main__":
    main()
