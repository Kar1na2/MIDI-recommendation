"""Three independent rings on the original MP3 timeline; no pattern-cluster input."""
from collections import defaultdict
import math

import numpy as np

from .config import (TEXTURE_WINDOW_BARS, TEXTURE_MIN_BARS, TEXTURE_NOVELTY_THRESHOLD,
                     TEXTURE_MERGE_DISTANCE, LYRIC_ALIGNMENT_CONFIDENCE)


def coverage(notes, start, end):
    intervals = sorted((max(start, n["onset_s"]), min(end, n["offset_s"]))
                       for n in notes if n["onset_s"] < end and n["offset_s"] > start)
    total, previous = 0., start
    for a, b in intervals:
        total += max(0., b - max(a, previous))
        previous = max(previous, b)
    return total / max(1e-9, end-start)


def bar_features(notes, bars, audio_path):
    import soundfile as sf
    waveform, sr = sf.read(audio_path, dtype="float32", always_2d=True)
    instruments = sorted({n["instrument"] for n in notes})
    channels = sorted({n["instrument"] + "__" + n.get("layer", "line") for n in notes})
    vectors, records = [], []
    for bar in bars:
        a, b = bar["start_s"], bar["end_s"]
        beat_s = (b-a) / bar["beats_per_bar"]
        overlapping = [n for n in notes if n["onset_s"] < b and n["offset_s"] > a]
        attacks = [n for n in overlapping if a <= n["onset_s"] < b]
        vector, layers, active = [], {}, []
        for key in channels:
            source, layer = key.rsplit("__", 1)
            selected = [n for n in overlapping if n["instrument"] == source and n.get("layer", "line") == layer]
            onsets = [n for n in selected if a <= n["onset_s"] < b]
            duration = float(np.mean([(n["offset_s"]-n["onset_s"]) / beat_s for n in onsets])) if onsets else 0.
            sustained = coverage(selected, a, b)
            layers[key] = {"onsets": len(onsets), "mean_duration_beats": duration,
                           "sustained_coverage": sustained}
            vector.extend([min(2., len(onsets)/16), min(2., duration/4), sustained])
        for instrument in instruments:
            selected = [n for n in overlapping if n["instrument"] == instrument]
            is_active = sum(a <= n["onset_s"] < b for n in selected) >= 2 or coverage(selected, a, b) >= .5
            vector.append(float(is_active))
            if is_active:
                active.append(instrument)
        pitch = float(np.median([n["pitch"] for n in attacks])) if attacks else 0.
        chunk = waveform[max(0, int(a*sr)):min(len(waveform), int(b*sr))]
        rms = float(np.sqrt(np.mean(chunk**2))) if chunk.size else 0.
        db = 20 * math.log10(max(1e-6, rms))
        vector.extend([pitch / 127, np.clip((db+60)/60, 0, 1)])
        vectors.append(vector)
        records.append({"bar": bar["bar"], "start_s": a, "end_s": b,
                        "layers": layers, "active_instruments": active, "median_pitch": pitch,
                        "rms": rms, "rms_db": db})
    return np.array(vectors), records


def distance(a, b):
    return float(np.sqrt(np.mean((a-b)**2)))


def major_sections(vectors):
    n, window = len(vectors), TEXTURE_WINDOW_BARS
    novelty = {i: distance(vectors[max(0,i-window):i].mean(axis=0),
                            vectors[i:min(n,i+window)].mean(axis=0)) for i in range(1, n)}
    peaks = [i for i, v in novelty.items() if v >= TEXTURE_NOVELTY_THRESHOLD
             and v > novelty.get(i-1, -1) and v >= novelty.get(i+1, -1)]
    cuts = [0, n]
    for i in sorted(peaks, key=lambda i: (-novelty[i], i)):
        if min(abs(i-j) for j in cuts) >= TEXTURE_MIN_BARS:
            cuts.append(i)
    cuts.sort()
    merged = True
    while merged and len(cuts) > 2:
        merged = False
        for index in range(1, len(cuts)-1):
            a, b, c = cuts[index-1:index+2]
            if distance(vectors[a:b].mean(axis=0), vectors[b:c].mean(axis=0)) <= TEXTURE_MERGE_DISTANCE:
                cuts.pop(index)
                merged = True
                break
    return list(zip(cuts, cuts[1:])), novelty


def section_label(records):
    active = sorted({i for r in records for i in r["active_instruments"]})
    features = defaultdict(list)
    for r in records:
        for key, value in r["layers"].items():
            features[key].append(value)
    piano_line = [v for k, values in features.items() if "piano__line" in k for v in values]
    chords = [v for k, values in features.items() if k.endswith("__chord") for v in values]
    if piano_line and np.mean([v["onsets"] for v in piano_line]) >= 6:
        return "arpeggiated piano"
    if chords and np.mean([v["sustained_coverage"] for v in chords]) >= .5:
        return "sustained chords"
    if len(active) >= 4:
        return "full band"
    if "drums" in active and any("bass" in i for i in active):
        return "drums + bass groove"
    return " · ".join(active) if active else "sparse / quiet"


def lyric_ring(duration, grid, lyric_data, instrumental, reason=None):
    if instrumental:
        return [{"start": 0., "end": duration, "label": "Instrumental", "index": 0}], "instrumental", None
    if (lyric_data or {}).get("ring1_segments"):
        return lyric_data["ring1_segments"], lyric_data.get("source", "genius_per_section"), reason
    sections = (lyric_data or {}).get("sections", [])
    verified = bool(sections) and (lyric_data or {}).get("validated", False)
    if verified:
        downbeats = grid["downbeat_times"]
        starts = [min(downbeats, key=lambda t: abs(t-s["raw_start_s"])) for s in sections]
        if any(b <= a for a, b in zip(starts, starts[1:])):
            verified, reason = False, "distinct lyric boundaries collapse onto the same downbeat"
        else:
            starts[0] = 0.
            segments = [{"start": a, "end": starts[i+1] if i+1 < len(starts) else duration,
                         "label": s.get("display_label", s["label"]), "index": i,
                         "alignment_confidence": s["match_confidence"]}
                        for i, (s, a) in enumerate(zip(sections, starts))]
            return segments, "genius_forced_alignment", None
    reason = reason or (lyric_data or {}).get("reason") or "every Genius section did not pass vocal-stem forced alignment"
    return [{"start": 0., "end": duration, "label": "Lyric sections unavailable", "index": 0}], "unavailable", reason


def build(track_id, audio_path, grid, layer_notes, lyric_data=None, instrumental=False, reason=None,
          grid_flags=None):
    from .texture import analyze
    texture_data = analyze(track_id, audio_path, grid, layer_notes, grid_flags)
    duration = grid["duration_s"]
    ring1, source, reason = lyric_ring(duration, grid, lyric_data, instrumental, reason)
    ring2, ring3 = texture_data["ring2"], texture_data["ring3"]
    layers = [{"depth": 1, "name": "Lyric sections", "source": source, "segments": ring1},
              {"depth": 2, "name": "Major musical sections", "source": "texture_novelty_v2", "segments": ring2},
              {"depth": 3, "name": "Instrument entries / exits", "source": "persistent_instrument_activity", "segments": ring3}]
    for layer in layers:
        layer["new_boundaries"] = len(layer["segments"])-1
        for segment in layer["segments"]:
            segment["description"] = segment["label"]
    return {"method": "onion-v2", "track_id": track_id, "duration": duration, "layers": layers,
            "grid_basis": texture_data["grid_basis"],
            "meta": {"original_audio_path": str(audio_path), "ring_1_reason": reason,
                     "pattern_clusters_used_by_rings_2_3": False,
                     "flags": ["more_than_12_major_sections"] if len(ring2)>12 else []},
            "feature_records": texture_data["feature_records"], "novelty": texture_data["novelty"],
            "boundary_strength": texture_data["boundary_strength"], "policy": texture_data["policy"]}
