"""Texture-only major sections and independent instrument activity for ring 3."""
from collections import Counter
import math

import numpy as np

from .config import (TEXTURE_WINDOW_BARS, TEXTURE_V2_MIN_BARS,
    TEXTURE_NOVELTY_MAD_K, TEXTURE_FORM_DISTANCE, TEXTURE_FORM_PRIME_DISTANCE,
    TEXTURE_GRID_FLAG_LIMIT, TEXTURE_TIME_FRAME_S, TEXTURE_MIN_SECONDS,
    TEXTURE_SUSTAIN_SECONDS)

FEATURE_NAMES = ("line_note_density", "mean_note_duration_s", "sustained_coverage",
                 "chord_onset_rate", "median_pitch", "mix_loudness_db")


def coverage(notes, start, end):
    intervals = sorted((max(start, row["onset_s"]), min(end, row["offset_s"]))
                       for row in notes if row["onset_s"] < end and row["offset_s"] > start)
    total, previous = 0.0, start
    for a, b in intervals:
        total += max(0.0, b - max(a, previous))
        previous = max(previous, b)
    return total / max(1e-9, end - start)


def analysis_units(grid, flags):
    duration = float(grid["duration_s"])
    flagged = sorted(set(flags or []))
    time_based = len(flagged) > TEXTURE_GRID_FLAG_LIMIT
    if time_based:
        units = [{"unit": i, "start_s": float(a), "end_s": min(duration, float(a) + TEXTURE_TIME_FRAME_S)}
                 for i, a in enumerate(np.arange(0.0, duration, TEXTURE_TIME_FRAME_S))]
        return units, "time", max(1, math.ceil(TEXTURE_MIN_SECONDS / TEXTURE_TIME_FRAME_S))
    units = []
    for bar in grid["bars"]:
        a = 0.0 if not units else float(bar["start_s"])
        b = min(duration, float(bar["end_s"]))
        if b > a:
            units.append({"unit": len(units), "bar": bar["bar"], "start_s": a, "end_s": b})
    return units, "bars", TEXTURE_V2_MIN_BARS


def features(notes, units, audio_path):
    import soundfile as sf
    waveform, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
    instruments = sorted({row["instrument"] for row in notes})
    vectors, records = [], []
    for unit in units:
        a, b = unit["start_s"], unit["end_s"]
        duration = max(1e-9, b - a)
        overlap = [row for row in notes if row["onset_s"] < b and row["offset_s"] > a]
        attacks = [row for row in overlap if a <= row["onset_s"] < b]
        line = [row for row in attacks if row.get("layer", "line") == "line"]
        chord = [row for row in attacks if row.get("layer") == "chord"]
        chord_groups = []
        for onset in sorted(row["onset_s"] for row in chord):
            if not chord_groups or onset - chord_groups[-1] > .03:
                chord_groups.append(onset)
        sustained = [row for row in overlap
                     if row["offset_s"] - row["onset_s"] >= TEXTURE_SUSTAIN_SECONDS]
        mean_duration = float(np.mean([row["offset_s"] - row["onset_s"] for row in attacks])) if attacks else 0.0
        pitches = [row["pitch"] for row in (attacks or overlap)]
        pitch = float(np.median(pitches)) if pitches else 0.0
        chunk = waveform[max(0, int(a * sample_rate)):min(len(waveform), int(b * sample_rate))]
        rms = float(np.sqrt(np.mean(chunk ** 2))) if chunk.size else 0.0
        loudness = 20 * math.log10(max(1e-6, rms))
        raw = [len(line) / duration, mean_duration, coverage(sustained, a, b),
               len(chord_groups) / duration, pitch, loudness]
        # Fixed physical scales, shared by all songs. No instrument-set dimensions.
        vector = [min(2.0, raw[0] / 8), min(2.0, raw[1] / 2), raw[2],
                  min(2.0, raw[3] / 4), raw[4] / 127, float(np.clip((raw[5] + 60) / 60, 0, 1))]
        active = [instrument for instrument in instruments
                  if sum(row["instrument"] == instrument for row in attacks) >= 2
                  or coverage([row for row in overlap if row["instrument"] == instrument], a, b) >= .5]
        records.append({**unit, **dict(zip(FEATURE_NAMES, raw)),
            "chord_coverage": coverage([row for row in overlap if row.get("layer") == "chord"], a, b),
            "line_instrument_counts": dict(Counter(row["instrument"] for row in line)),
            "active_instruments": active, "feature_vector": vector})
        vectors.append(vector)
    return np.asarray(vectors, dtype=float), records


def distance(left, right):
    return float(np.sqrt(np.mean((np.asarray(left) - np.asarray(right)) ** 2)))


def novelty_sections(vectors, minimum_units):
    n, window = len(vectors), TEXTURE_WINDOW_BARS
    if not n:
        return [], {}, {"median": 0.0, "mad": 0.0, "threshold": 0.0}
    novelty = {i: distance(vectors[max(0, i-window):i].mean(axis=0),
                           vectors[i:min(n, i+window)].mean(axis=0)) for i in range(1, n)}
    values = list(novelty.values())
    median = float(np.median(values)) if values else 0.0
    mad = float(np.median(np.abs(np.asarray(values) - median))) if values else 0.0
    threshold = median + TEXTURE_NOVELTY_MAD_K * mad
    peaks = [i for i, value in novelty.items() if value > threshold
             and value > novelty.get(i-1, -1) and value >= novelty.get(i+1, -1)]
    cuts = [0, n]
    for index in sorted(peaks, key=lambda item: (-novelty[item], item)):
        if min(abs(index - cut) for cut in cuts) >= minimum_units:
            cuts.append(index)
    cuts.sort()
    return list(zip(cuts, cuts[1:])), novelty, {"median": median, "mad": mad,
        "k": TEXTURE_NOVELTY_MAD_K, "threshold": threshold, "candidate_peak_count": len(peaks)}


def _letter(index):
    result = ""
    while True:
        result = chr(65 + index % 26) + result
        index = index // 26 - 1
        if index < 0:
            return result


def descriptor(records):
    density = float(np.mean([row["line_note_density"] for row in records]))
    duration = float(np.mean([row["mean_note_duration_s"] for row in records]))
    chords = float(np.mean([row["chord_coverage"] for row in records]))
    chord_rate = float(np.mean([row["chord_onset_rate"] for row in records]))
    instruments = Counter()
    for row in records:
        instruments.update(row["line_instrument_counts"])
    lead = instruments.most_common(1)[0][0] if instruments else ""
    if density >= 2.5:
        return "arpeggiated piano" if "piano" in lead else "rapid melodic line"
    if chords >= .4 and duration >= .35:
        return "sustained chords"
    if chord_rate >= 1.0:
        return "short chord pulses"
    if duration >= .8:
        return "sustained texture"
    if density < 1.0:
        return "sparse / quiet" if not instruments else "sparse melodic line"
    return "melodic line"


def form_sections(ranges, vectors, records, duration):
    representatives, assigned = [], []
    for first, last in ranges:
        mean = vectors[first:last].mean(axis=0)
        eligible = [(distance(mean, exemplar), index) for index, exemplar in enumerate(representatives)
                    if distance(mean, exemplar) <= TEXTURE_FORM_DISTANCE]
        if eligible:
            join_distance, cluster = min(eligible, key=lambda item: (item[0], item[1]))
        else:
            cluster, join_distance = len(representatives), 0.0
            representatives.append(mean)
        if assigned and assigned[-1]["cluster"] == cluster:
            assigned[-1]["last"] = last
        else:
            assigned.append({"first": first, "last": last, "cluster": cluster})
    result = []
    for item in assigned:
        first, last, cluster = item["first"], item["last"], item["cluster"]
        mean = vectors[first:last].mean(axis=0)
        join_distance = distance(mean, representatives[cluster])
        base = _letter(cluster)
        form = base + ("'" if join_distance > TEXTURE_FORM_PRIME_DISTANCE else "")
        label = descriptor(records[first:last])
        result.append({"index": len(result), "start": 0.0 if first == 0 else records[first]["start_s"],
            "end": duration if last == len(records) else records[last]["start_s"],
            "first_unit": first, "last_unit_exclusive": last,
            "form_letter": form, "base_letter": base, "texture_descriptor": label,
            "label": f"{form} · {label}", "cluster_distance": join_distance,
            "mean_feature_vector": mean.tolist()})
    return result


def instrument_sections(major, records):
    result = []
    for parent in major:
        first, last = parent["first_unit"], parent["last_unit_exclusive"]
        sets = [tuple(row["active_instruments"]) for row in records[first:last]]
        if not sets:
            continue
        cuts, current, i = [0], sets[0], 1
        while i < len(sets):
            j = i + 1
            while j < len(sets) and sets[j] == sets[i]:
                j += 1
            if sets[i] != current and j - i >= 2:
                cuts.append(i)
                current = sets[i]
            i = j
        cuts.append(len(sets))
        previous = set()
        for a, b in zip(cuts, cuts[1:]):
            active = set(sets[a])
            result.append({"index": len(result), "parent_index": parent["index"],
                "start": parent["start"] if not a else records[first+a]["start_s"],
                "end": parent["end"] if b == len(sets) else records[first+b]["start_s"],
                "label": " · ".join(sorted(active)) or "sparse / quiet",
                "active_instruments": sorted(active), "added": sorted(active - previous),
                "removed": sorted(previous - active)})
            previous = active
    return result


def analyze(track_id, audio_path, grid, layer_notes, grid_flags=None):
    flags = sorted(set(grid_flags if grid_flags is not None else grid.get("flags", [])))
    units, basis, minimum = analysis_units(grid, flags)
    vectors, records = features(layer_notes, units, audio_path)
    ranges, novelty, strength = novelty_sections(vectors, minimum)
    if basis == "time" and len(ranges) > 1:
        first, last = ranges[-1]
        if float(grid["duration_s"]) - records[first]["start_s"] < TEXTURE_MIN_SECONDS:
            ranges[-2:] = [(ranges[-2][0], last)]
    major = form_sections(ranges, vectors, records, float(grid["duration_s"]))
    instruments = instrument_sections(major, records)
    return {"schema_version": "texture/2", "track_id": track_id, "grid_basis": basis,
        "grid_flags": flags, "ring2": major, "ring3": instruments,
        "feature_names": list(FEATURE_NAMES), "feature_records": records,
        "novelty": novelty, "boundary_strength": strength,
        "policy": {"instrument_set_features_in_ring2": False,
            "minimum_section_bars": TEXTURE_V2_MIN_BARS, "minimum_section_seconds": TEXTURE_MIN_SECONDS,
            "frame_seconds_for_unreliable_grid": TEXTURE_TIME_FRAME_S,
            "grid_flag_limit": TEXTURE_GRID_FLAG_LIMIT, "novelty_mad_k": TEXTURE_NOVELTY_MAD_K,
            "form_distance_threshold": TEXTURE_FORM_DISTANCE,
            "prime_distance_threshold": TEXTURE_FORM_PRIME_DISTANCE,
            "form_representative": "fixed first section mean; nearest qualifying cluster; chronological deterministic letters",
            "adjacent_same_base_letter": "merged", "ring3_activity_persistence_units": 2}}
