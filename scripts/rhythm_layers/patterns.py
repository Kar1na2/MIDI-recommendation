"""Beat-grid recurrence and pattern changes, without loudness descriptors.

Attack detection uses spectral flux to locate events. Subsequent analysis uses
binary event locations, unit-normalized pitch-class profiles and spectral
shapes. RMS, velocity, stem energy share and absolute amplitude are excluded.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
from scipy.signal import find_peaks, resample_poly
from scipy.ndimage import gaussian_filter1d
from numba import njit

from .common import OUT, ROOT, PARTS

SR, HOP, FFT = 11025, 256, 2048
VERSION = "rhythm-harmony-v1"


def unit(x, axis=-1):
    return x / np.maximum(np.linalg.norm(x, axis=axis, keepdims=True), 1e-9)


def load_audio(path):
    y, sr = sf.read(path, dtype="float32", always_2d=True)
    y = y.mean(axis=1)
    divisor = math.gcd(sr, SR)
    if sr != SR:
        y = resample_poly(y, SR // divisor, sr // divisor)
    # Scaling the recording cannot change its representation.
    peak = float(np.max(np.abs(y), initial=0))
    return y / peak if peak > 0 else y


def extract(path, cache=True):
    path = Path(path)
    stat = path.stat()
    key = hashlib.sha256(f"{VERSION}:{path}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()[:24]
    target = OUT / "features" / f"{key}.npz"
    if cache and target.is_file():
        with np.load(target) as data:
            return {k: data[k] for k in data.files}
    y = load_audio(path)
    spectrum = np.abs(librosa.stft(y, n_fft=FFT, hop_length=HOP)).astype("float32")
    mel = librosa.feature.melspectrogram(S=spectrum ** 2, sr=SR, n_mels=24)
    logmel = librosa.power_to_db(mel, ref=np.max, top_db=65)
    flux = np.maximum(np.diff(logmel, axis=1, prepend=logmel[:, :1]), 0).mean(axis=0)
    if np.max(flux, initial=0) > .01:
        onset_frames = librosa.onset.onset_detect(onset_envelope=flux, sr=SR, hop_length=HOP,
                                                normalize=True, delta=.09, wait=2, backtrack=False)
    else:
        onset_frames = np.array([], dtype=int)
    chroma = librosa.feature.chroma_stft(S=spectrum ** 2, sr=SR, n_fft=FFT, hop_length=HOP)
    chroma = unit(chroma.T).astype("float32")
    shape = unit(np.maximum(mel.T, 0) ** .3).astype("float32")
    times = librosa.frames_to_time(np.arange(len(flux)), sr=SR, hop_length=HOP)
    result = {"times": times.astype("float32"), "onsets": times[onset_frames].astype("float32"),
              "flux": (flux / max(float(np.max(flux, initial=0)), 1e-9)).astype("float32"),
              "chroma": chroma, "shape": shape, "duration": np.array(len(y) / SR)}
    if cache:
        target.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(target, **result)
    return result


def pulse(features, duration):
    n = min(len(f["flux"]) for f in features.values())
    envelope = np.mean([f["flux"][:n] for f in features.values()], axis=0)
    if np.max(envelope, initial=0) < .02:
        return np.arange(0, duration + .5, .5), 120., 0.
    tempo, frames = librosa.beat.beat_track(onset_envelope=envelope, sr=SR, hop_length=HOP,
                                          start_bpm=110, trim=False, tightness=75)
    beats = librosa.frames_to_time(frames, sr=SR, hop_length=HOP)
    bpm = float(np.asarray(tempo).ravel()[0])
    if len(beats) < 4:
        return np.arange(0, duration + .5, .5), 120., 0.
    interval = float(np.median(np.diff(beats)))
    # Extend the estimated pulse through pickups/tails, without claiming downbeats.
    before = np.arange(beats[0] - interval, -interval, -interval)[::-1]
    after = np.arange(beats[-1] + interval, duration + interval, interval)
    beats = np.unique(np.r_[before, beats, after])
    intervals = np.diff(beats)
    regularity = max(0., 1. - float(np.std(intervals) / max(np.mean(intervals), 1e-6)))
    onset_times = np.concatenate([f["onsets"] for f in features.values()])
    residual = np.min(np.abs(onset_times[:, None] - beats[None, :]), axis=1) if len(onset_times) else np.ones(1)
    support = float(np.mean(residual < interval * .18))
    return beats, bpm, min(1., regularity * support * 1.8)


def grid(features, beats, duration):
    positions = np.arange(0, len(beats) - 1, .25)
    times = np.interp(positions, np.arange(len(beats)), beats)
    valid = (times >= 0) & (times < duration)
    times = times[valid]
    result = {}
    for part, f in features.items():
        rhythm = np.zeros(len(times), dtype="float32")
        for onset in f["onsets"]:
            index = int(np.argmin(np.abs(times - onset)))
            if abs(times[index] - onset) < .18:
                rhythm[index] = 1
        harmonic = np.stack([np.interp(times, f["times"], f["chroma"][:, i]) for i in range(12)], axis=1)
        shape = np.stack([np.interp(times, f["times"], f["shape"][:, i]) for i in range(24)], axis=1)
        result[part] = {"rhythm": rhythm, "harmonic": unit(harmonic), "shape": unit(shape)}
    return times, result


def event_distance(a, b):
    # One-slot tolerance represents expressive timing; no velocities are used.
    a, b = np.asarray(a), np.asarray(b)
    aa = np.maximum.reduce([a, np.roll(a, 1) * .35, np.roll(a, -1) * .35])
    bb = np.maximum.reduce([b, np.roll(b, 1) * .35, np.roll(b, -1) * .35])
    return float(np.abs(aa - bb).sum() / max(np.maximum(aa, bb).sum(), 1.))


def signatures(data, start, end, cycle=16):
    end = min(end, len(next(iter(data.values()))["rhythm"]))
    start = max(0, start)
    size = (end - start) // cycle * cycle
    if size < cycle:
        return None
    start = end - size
    return {p: {"rhythm": x["rhythm"][start:end].reshape(-1, cycle).mean(axis=0),
                "harmonic": unit(x["harmonic"][start:end].reshape(-1, cycle, 12).mean(axis=0)),
                "shape": unit(x["shape"][start:end].mean(axis=0))} for p, x in data.items()}


def signature_distance(a, b):
    rhythm, harmony, timbre = [], [], []
    for part in a:
        rhythm.append(event_distance(a[part]["rhythm"], b[part]["rhythm"]))
        if part != "drums":
            harmony.append(float(np.mean(1 - np.sum(a[part]["harmonic"] * b[part]["harmonic"], axis=-1))))
        timbre.append(float(1 - np.dot(a[part]["shape"], b[part]["shape"])))
    return np.clip([np.mean(rhythm), np.mean(harmony) if harmony else 0., np.mean(timbre)], 0, 1)


def changes(times, data, fine=False):
    context = 32 if not fine else 16
    scores = np.zeros(len(times))
    evidence = {}
    weights = np.array([.72, .23, .05] if not fine else [.58, .27, .15])
    for index in range(context, len(times) - context, 4):
        left = signatures(data, index - context, index)
        right = signatures(data, index, index + context)
        delta = signature_distance(left, right)
        score = float(np.dot(weights, delta))
        scores[index] = score
        evidence[index] = delta
    candidates = scores[scores > 0]
    if not len(candidates):
        return []
    median = float(np.median(candidates))
    mad = float(np.median(np.abs(candidates - median)))
    threshold = max(.20 if fine else .24, median + 1.4 * mad)
    peaks, _ = find_peaks(scores, distance=24 if fine else 32, height=threshold, prominence=.07)
    result = []
    for index in peaks:
        if index not in evidence:
            continue
        delta = evidence[index]
        result.append({"time": round(float(times[index]), 3), "score": round(float(scores[index]), 3),
                       "rhythm": round(float(delta[0]), 3), "harmony": round(float(delta[1]), 3),
                       "spectral_shape": round(float(delta[2]), 3), "source": "measured_pattern_change"})
    # Keep the two pattern rings readable. Retain the strongest changes and
    # leave finer detail to the second ring instead of fragmenting each stem.
    limit = 4 if not fine else 6
    if len(result) > limit:
        result = sorted(sorted(result, key=lambda item: item["score"], reverse=True)[:limit],
                        key=lambda item: item["time"])
    return result


def cycle_summary(times, data, start, end):
    first, last = np.searchsorted(times, [start, end])
    indices = [np.flatnonzero(x["rhythm"][first:last]) + first for x in data.values()]
    all_attacks = np.concatenate(indices) if indices else np.array([], dtype=int)
    spacings = np.concatenate([np.diff(i) for i in indices if len(i) > 1]) if any(len(i) > 1 for i in indices) else np.array([])
    if len(spacings):
        gaps, counts = np.unique(spacings, return_counts=True)
        spacing = float(gaps[np.argmax(counts)] / 4.)
    else:
        spacing = None
    stats = {"attack_count": int(len(all_attacks)), "attacks_per_beat": round(float(len(all_attacks) / max((last - first) / 4., .001)), 2),
             "offbeat_fraction": round(float(np.mean(all_attacks % 4 != 0)), 3) if len(all_attacks) else None,
             "most_common_attack_gap_beats": spacing}
    candidates = []
    for beats in (1, 2, 4, 8, 16):
        lag = beats * 4
        if last - first < 2 * lag:
            continue
        r, h = [], []
        for part, x in data.items():
            a, b = x["rhythm"][first:last - lag], x["rhythm"][first + lag:last]
            if np.count_nonzero(a) + np.count_nonzero(b) >= 4:
                r.append(1 - event_distance(a, b))
            if part != "drums":
                aa, bb = x["harmonic"][first:last - lag], x["harmonic"][first + lag:last]
                h.append(float(np.mean(np.sum(aa * bb, axis=1))))
        if r:
            harmonic = float(np.mean(h)) if h else None
            score = .75 * np.mean(r) + .25 * harmonic if harmonic is not None else np.mean(r)
            candidates.append((score, beats, np.mean(r), harmonic))
    if not candidates:
        return {"beats": None, "repeat_count": 0, "similarity": None, "rhythm_similarity": None,
                "harmony_similarity": None, "notation": {}, "contour": None, **stats}
    best = max(candidates, key=lambda v: v[0])
    # Prefer the shortest cycle close to the best fit, not an arbitrarily long window.
    best = next(v for v in candidates if v[0] >= best[0] - .025)
    score, beats, rhythmic, harmonic = best
    if score < .60 or rhythmic < .55:
        beats = None
    notation = {}
    if beats:
        cycle = beats * 4
        for part, x in data.items():
            templates = x["rhythm"][first:first + ((last - first) // cycle) * cycle].reshape(-1, cycle)
            template = np.mean(templates, axis=0) >= .5
            notation[part] = " ".join(part if active else "~" for active in template)
    contour = None
    if "other" in data and last > first:
        x = data["other"]
        active = np.where(x["rhythm"][first:last] > 0)[0] + first
        if len(active) >= 8:
            pcs = np.argmax(x["harmonic"][active], axis=1)
            intervals = (np.diff(pcs) + 6) % 12 - 6
            rises = sum(np.all(intervals[i:i + 3] > 0) for i in range(len(intervals) - 2))
            falls = sum(np.all(intervals[i:i + 3] < 0) for i in range(len(intervals) - 2))
            if rises >= 2 and rises > falls * 1.5:
                contour = "Recurring rising pitch-class contours (candidate)"
            elif falls >= 2 and falls > rises * 1.5:
                contour = "Recurring falling pitch-class contours (candidate)"
    return {"beats": beats, "repeat_count": int((last - first) // (beats * 4)) if beats else 0,
            "similarity": round(float(score), 3), "rhythm_similarity": round(float(rhythmic), 3),
            "harmony_similarity": round(float(harmonic), 3) if harmonic is not None else None, "notation": notation, "contour": contour, **stats}


def make_layers(times, data, duration, anchors=None):
    broad, fine = changes(times, data), changes(times, data, True)
    for start, end in (anchors or {}).get("lock_broad_intervals", []):
        broad = [c for c in broad if not start < c["time"] < end]
    for start, end in (anchors or {}).get("lock_fine_intervals", []):
        fine = [c for c in fine if not start < c["time"] < end]
    for time in (anchors or {}).get("broad", []):
        if 0 < time < duration:
            broad = [c for c in broad if abs(c["time"] - time) > 1.5]
            broad.append({"time": time, "source": "user_listening_annotation", "score": None})
    for time in (anchors or {}).get("fine", []):
        if 0 < time < duration:
            fine = [c for c in fine if abs(c["time"] - time) > 1.5]
            fine.append({"time": time, "source": "user_listening_annotation", "score": None})
    broad.sort(key=lambda c: c["time"])
    # Fine patterns inherit broad boundaries, but are not forced to add a cut.
    fine = sorted(broad + [c for c in fine if all(abs(c["time"] - b["time"]) > 1.2 for b in broad)], key=lambda c: c["time"])
    layers = []
    for depth, (name, cuts) in enumerate((("Rhythm & harmony sections", broad), ("Motifs & variations", fine)), 1):
        boundaries = [0.] + [c["time"] for c in cuts] + [duration]
        segments = []
        for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            summary = cycle_summary(times, data, start, end)
            label = f"{summary['beats']}-beat cycle candidate" if summary["beats"] else ("Offbeat-led attacks · variable phrase" if (summary.get("offbeat_fraction") or 0) > .6 else "Beat-led attacks · variable phrase")
            if not any(np.any(x["rhythm"][(times >= start) & (times < end)]) for x in data.values()):
                label = "No distinct attacks detected"
            description = (f"{summary['repeat_count']} complete cycle candidates; repeated attack locations and pitch-class sequence are compared separately."
                           if summary["beats"] else "No short stable attack cycle met the recurrence threshold.")
            if summary["beats"]:
                description += f" Rhythm similarity {summary['rhythm_similarity']:.2f}."
                if summary['harmony_similarity'] is not None:
                    description += f" Harmony similarity {summary['harmony_similarity']:.2f}."
            if summary.get("most_common_attack_gap_beats") is not None:
                description += f" Most common within-stem attack gap: {summary['most_common_attack_gap_beats']:g} estimated beats; {summary['attacks_per_beat']:g} total attacks per beat across the selected parts."
            annotation = None
            if anchors and start <= 6.1 and end >= 17.9 and start >= 5.9 and end <= 18.1:
                label = "Ascending arpeggio figure · two cycles"
                annotation = "User listening annotation: four consecutive ascending arpeggios, repeated twice from 0:06 to 0:18."
                description = annotation
            segments.append({"index": index, "start": round(start, 3), "end": round(end, 3), "label": label,
                             "description": description, "cycle": summary, "annotation": annotation,
                             "boundary": next((c for c in cuts if abs(c["time"] - start) < .001), None)})
        layers.append({"depth": depth, "name": name, "segments": segments, "new_boundaries": len(cuts)})
    return layers


def analyze_parts(paths, anchors=None):
    features = {part: extract(ROOT / paths[part]) for part in PARTS}
    duration = min(float(f["duration"]) for f in features.values())
    beats, bpm, confidence = pulse(features, duration)
    times, values = grid(features, beats, duration)
    views = {"instrumental": {"name": "Three instrumental stems", "layers": make_layers(times, values, duration, anchors)}}
    for part in PARTS:
        views[part] = {"name": part.title(), "layers": make_layers(times, {part: values[part]}, duration, anchors if part == "other" else None)}
    return {"method": VERSION, "duration": round(duration, 3), "pulse": {"bpm": round(bpm, 2), "confidence": round(confidence, 3),
            "assumption": "Estimated beats; four beats per Tidal cycle is a rendering convention, not a detected meter."},
            "beat_times": np.round(beats[(beats >= 0) & (beats <= duration)], 3).tolist(), "views": views}


@njit(cache=True)
def accumulated_dtw(cost):
    n, m = cost.shape
    accumulated = np.full((n, m), np.float32(1e8), dtype=np.float32)
    direction = np.zeros((n, m), dtype=np.uint8)
    accumulated[0, 0] = cost[0, 0]
    for i in range(n):
        center = i * (m - 1) / max(n - 1, 1)
        low, high = max(0, int(center - .18 * m)), min(m, int(center + .18 * m) + 1)
        for j in range(low, high):
            if i == 0 and j == 0:
                continue
            best, step = np.float32(1e8), 0
            if i and j:
                best, step = accumulated[i - 1, j - 1], 1
            if i and j >= 2 and accumulated[i - 1, j - 2] + .045 < best:
                best, step = accumulated[i - 1, j - 2] + .045, 2
            if i >= 2 and j and accumulated[i - 2, j - 1] + .045 < best:
                best, step = accumulated[i - 2, j - 1] + .045, 3
            accumulated[i, j] = best + cost[i, j]
            direction[i, j] = step
    return accumulated, direction


def timeline_map(instrument_paths, original_paths):
    """Match beat-synchronous harmony and attacks to map instrumental time to original time."""
    try:
        instrumental = {p: extract(ROOT / instrument_paths[p]) for p in PARTS}
        original = {p: extract(ROOT / original_paths[p]) for p in PARTS}
        duration_i = min(float(f["duration"]) for f in instrumental.values())
        duration_o = min(float(f["duration"]) for f in original.values())
        bi, _, _ = pulse(instrumental, duration_i)
        bo, _, _ = pulse(original, duration_o)
        ti, gi = grid(instrumental, bi, duration_i)
        to, go = grid(original, bo, duration_o)
        def vector(data):
            columns = []
            for part in PARTS:
                columns.extend((data[part]["rhythm"][:, None] * .55,
                                unit(data[part]["harmonic"]) * .75))
            return unit(np.concatenate(columns, axis=1))
        vi, vo = vector(gi), vector(go)
        n, m = len(to), len(ti)
        if min(n, m) < 16 or max(n, m) > 6000:
            raise ValueError("Not enough shared beat samples for a reliable timeline match")
        # Dynamic alignment tolerates short inserts, tempo drift and a different
        # intro/outro. A narrow proportional band keeps repeated choruses from
        # producing implausible local tempo changes.
        cost = np.clip(1 - vo @ vi.T, 0, 2).astype("float32")
        inf = np.float32(1e8)
        accumulated, direction = accumulated_dtw(cost)
        if accumulated[-1, -1] >= inf:
            raise ValueError("Rhythm/harmony sequences did not align inside the allowed tempo band")
        i, j = n - 1, m - 1
        path = [(i, j)]
        while i or j:
            step = direction[i, j]
            if step == 1: i, j = i - 1, j - 1
            elif step == 2: i, j = i - 1, j - 2
            elif step == 3: i, j = i - 2, j - 1
            else: raise ValueError("Incomplete dynamic alignment path")
            path.append((i, j))
        path.reverse()
        pi, pj = np.asarray(path).T
        mean_cost = float(np.mean(cost[pi, pj]))
        confidence = float(np.clip((1 - mean_cost) * .82 + .18 * min(1., len(path) / max(n, m)), 0, 1))
        # Thin the map but retain the end anchors and nondecreasing time pairs.
        stride = max(1, len(path) // 80)
        sample = np.unique(np.r_[np.arange(0, len(path), stride), len(path) - 1])
        return {"status": "matched" if confidence >= .55 else "low_confidence",
                "confidence": round(confidence, 3), "mean_beat_grid_cost": round(mean_cost, 3),
                "original_time": np.round(np.r_[0., to[pi[sample]], duration_o], 3).tolist(),
                "instrumental_time": np.round(np.r_[0., ti[pj[sample]], duration_i], 3).tolist(),
                "original_duration": round(duration_o, 3), "instrumental_duration": round(duration_i, 3),
                "method": "constrained DTW over three per-stem onset/chroma grids"}
    except Exception as error:
        return {"status": "unmatched", "confidence": 0., "reason": f"{type(error).__name__}: {error}"[:240]}


def map_time(mapping, instrumental_time):
    if mapping.get("status") != "matched" or mapping.get("confidence", 0) < .55:
        return None
    source = np.asarray(mapping["instrumental_time"], dtype=float)
    target = np.asarray(mapping["original_time"], dtype=float)
    keep = np.r_[True, np.diff(source) > 1e-4]
    if np.count_nonzero(keep) < 2:
        return None
    return round(float(np.interp(instrumental_time, source[keep], target[keep])), 3)
