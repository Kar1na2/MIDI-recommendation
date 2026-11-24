"""Stage 1: measured full-mix beats, downbeats and variable-tempo bar geometry."""
from bisect import bisect_right
from collections import Counter
import math
from statistics import median

from .common import StageError, atomic_write_audio


def build_grid(beats, downbeats, duration):
    beats = [float(t) for t in beats]
    downbeats = [float(t) for t in downbeats]
    if (len(beats) < 3 or len(downbeats) < 2 or not math.isfinite(duration)
            or duration <= 0 or any(not math.isfinite(t) or t < 0 or t > duration
                                   for t in beats + downbeats)
            or any(b <= a for a, b in zip(beats, beats[1:]))
            or any(b <= a for a, b in zip(downbeats, downbeats[1:]))):
        raise StageError("beat_this did not produce a usable ordered beat/downbeat grid")
    indices = [min(range(len(beats)), key=lambda i: abs(beats[i] - t)) for t in downbeats]
    if any(abs(beats[i] - t) > .08 for i, t in zip(indices, downbeats)):
        raise StageError("Downbeats do not coincide with beat predictions within 80 ms")
    meters = [b - a for a, b in zip(indices, indices[1:])]
    if not meters or min(meters) < 1:
        raise StageError("No complete bar between measured downbeats")
    meter = Counter(meters).most_common(1)[0][0]
    flags = [] if len(set(meters)) == 1 else ["nonconstant_beats_per_bar"]
    bars = []
    for number, (a, b, ia, ib) in enumerate(zip(downbeats, downbeats[1:], indices, indices[1:])):
        bars.append({"bar": number, "start_s": a, "end_s": b,
                     "beat_times": [a] + beats[ia + 1:ib] + [b],
                     "beats_per_bar": ib - ia, "complete": True})
    # A trailing partial bar has no predicted closing downbeat. Extrapolate only
    # its endpoint, record that provenance, and never call it a measured boundary.
    last = downbeats[-1]
    if last < duration:
        interval = median([b - a for a, b in zip(beats, beats[1:])][-8:])
        following = [t for t in beats if t > last]
        tail = [last] + following
        while len(tail) <= meter:
            tail.append(tail[-1] + interval)
        endpoint = tail[meter]
        if endpoint < duration:
            flags.append("untracked_tail_after_predicted_final_bar")
            endpoint = duration
            tail[-1] = endpoint
        bars.append({"bar": len(bars), "start_s": last, "end_s": endpoint,
                     "beat_times": tail[:meter] + [endpoint],
                     "beats_per_bar": meter, "complete": False,
                     "endpoint_source": "extrapolated_from_last_measured_beats"})
    return {"model": "beat_this", "checkpoint": "final0", "input": "full_mix",
            "beat_times": beats, "downbeat_times": downbeats,
            "estimated_bpm": 60 / median([b - a for a, b in zip(beats, beats[1:])]),
            "beats_per_bar": meter if len(set(meters)) == 1 else None,
            "observed_beats_per_bar": meters,
            "beat_bar_indices": [bisect_right(downbeats, t + 1e-7) - 1 for t in beats],
            "bars": bars, "duration_s": duration, "flags": flags,
            "pickup_end_s": downbeats[0],
            "tempo_octave_check": "pending_stage4"}


def compare_bar_grids(raw_grid, dbn_grid, tolerance_s=.03):
    raw_bars, dbn_bars = raw_grid["bars"], dbn_grid["bars"]
    differences = []
    raw_covered_dbn = set()
    for index, raw in enumerate(raw_bars):
        duration = raw["end_s"] - raw["start_s"]
        min_overlap = max(.10, duration * .05)
        overlaps = [(dbn_index, dbn) for dbn_index, dbn in enumerate(dbn_bars)
                    if min(raw["end_s"], dbn["end_s"]) -
                       max(raw["start_s"], dbn["start_s"]) > min_overlap]
        raw_covered_dbn.update(dbn_index for dbn_index, _ in overlaps)
        first_dbn = overlaps[0][1] if overlaps else None
        last_dbn = overlaps[-1][1] if overlaps else None
        dbn_start = first_dbn["start_s"] if first_dbn else None
        dbn_end = last_dbn["end_s"] if last_dbn else None
        start_delta = (dbn_start - raw["start_s"]) if first_dbn else None
        end_delta = (dbn_end - raw["end_s"]) if last_dbn else None
        dbn_beats = sum(dbn["beats_per_bar"] for _, dbn in overlaps)
        same = bool(len(overlaps) == 1 and dbn_beats == raw["beats_per_bar"]
                    and abs(start_delta) <= tolerance_s and abs(end_delta) <= tolerance_s)
        if not same:
            reasons = []
            if len(overlaps) != 1:
                reasons.append("bar_split_or_merge")
            if dbn_beats != raw["beats_per_bar"]:
                reasons.append("beat_count_differs")
            if first_dbn and abs(start_delta) > tolerance_s:
                reasons.append("start_boundary_shift")
            if last_dbn and abs(end_delta) > tolerance_s:
                reasons.append("end_boundary_shift")
            differences.append({"raw_bar": index + 1, "bar": index + 1,
                "raw_start_s": raw["start_s"], "raw_end_s": raw["end_s"],
                "raw_start": _clock(raw["start_s"]), "raw_end": _clock(raw["end_s"]),
                "raw_beats_per_bar": raw["beats_per_bar"],
                "dbn_bars": [dbn_index + 1 for dbn_index, _ in overlaps],
                "dbn_start_s": dbn_start, "dbn_end_s": dbn_end,
                "dbn_start": _clock(dbn_start) if dbn_start is not None else None,
                "dbn_end": _clock(dbn_end) if dbn_end is not None else None,
                "dbn_beats_per_bar": dbn_beats if overlaps else None,
                "start_delta_ms": start_delta * 1000 if start_delta is not None else None,
                "end_delta_ms": end_delta * 1000 if end_delta is not None else None,
                "reasons": reasons})
    for index, dbn in enumerate(dbn_bars):
        if index not in raw_covered_dbn:
            differences.append({"raw_bar": None, "bar": None, "raw_start_s": None,
                "raw_end_s": None, "raw_start": None, "raw_end": None,
                "raw_beats_per_bar": None, "dbn_bars": [index + 1],
                "dbn_start_s": dbn["start_s"], "dbn_end_s": dbn["end_s"],
                "dbn_start": _clock(dbn["start_s"]), "dbn_end": _clock(dbn["end_s"]),
                "dbn_beats_per_bar": dbn["beats_per_bar"],
                "start_delta_ms": None, "end_delta_ms": None,
                "reasons": ["dbn_only_bar"]})
    return {"raw_bar_count": len(raw_bars), "dbn_bar_count": len(dbn_bars),
            "different_bar_count": len(differences), "timestamp_tolerance_ms": tolerance_s * 1000,
            "different_bars": differences}


def run(audio_path, duration, device="cpu"):
    from beat_this.inference import File2Beats
    tracker = File2Beats(checkpoint_path="final0", device=device, dbn=False)
    # CPJKU returns (beats, downbeats); retain these predictions verbatim.
    beats, downbeats = tracker(str(audio_path))
    raw_grid = build_grid(beats, downbeats, duration)
    dbn = {"available": False, "error": None, "irregular_interval_count": None}
    dbn_grid = None
    try:
        dbn_tracker = File2Beats(checkpoint_path="final0", device=device, dbn=True)
        dbn_beats, dbn_downbeats = dbn_tracker(str(audio_path))
        dbn_grid = build_grid(dbn_beats, dbn_downbeats, duration)
        dbn = {"available": True, "irregular_interval_count": sum(
            count != 4 for count in dbn_grid["observed_beats_per_bar"]),
            "beats_per_bar_intervals": dbn_grid["observed_beats_per_bar"],
            "bar_comparison": compare_bar_grids(raw_grid, dbn_grid)}
    except Exception as error:
        raise StageError(f"DBN grid failed; both grids are required: {type(error).__name__}: {error}") from error
    return {"raw": {"beat_times": list(raw_grid["beat_times"]),
                     "downbeat_times": list(raw_grid["downbeat_times"]),
                     "observed_beats_per_bar": list(raw_grid["observed_beats_per_bar"]),
                     "dbn_comparison": dbn},
            "raw_grid": raw_grid, "dbn_grid": dbn_grid}


def auto_choice(raw_grid, dbn_grid):
    raw_odd = sum(bar["beats_per_bar"] != 4 for bar in raw_grid["bars"] if bar.get("complete", True))
    dbn_odd = sum(bar["beats_per_bar"] != 4 for bar in dbn_grid["bars"] if bar.get("complete", True))
    regular = [bar["start_s"] for bar in raw_grid["bars"]
               if bar["beats_per_bar"] == 4 and bar.get("complete", True)]
    aligned = sum(any(abs(t - d) <= .070 + 1e-9 for d in dbn_grid["downbeat_times"]) for t in regular)
    share = aligned / len(regular) if regular else None
    choose_dbn = dbn_odd < raw_odd and share is not None and share >= .90
    return {"chosen_grid": "dbn" if choose_dbn else "raw", "raw_irregular_bars": raw_odd,
            "dbn_irregular_bars": dbn_odd, "raw_regular_downbeats": len(regular),
            "matched_downbeats": aligned, "downbeat_agreement_share": share,
            "tolerance_ms": 70, "required_share": .90,
            "reason": "DBN has fewer irregular bars and >=90% regular raw downbeats agree within 70 ms"
                      if choose_dbn else "raw retained: DBN did not satisfy both irregular-bar and downbeat-agreement conditions"}


def select_grid(detected, policy="strict", beats_per_bar=4):
    raw, dbn = detected["raw_grid"], detected["dbn_grid"]
    if dbn is None:
        raise StageError("Both raw and DBN grids are required; no substitute grid was used")
    decision = auto_choice(raw, dbn)
    selected = decision["chosen_grid"] if policy == "auto" else "dbn" if policy == "dbn" else "raw"
    mode = "as_detected" if policy == "auto" else policy
    grid = regularize_grid(dbn if selected == "dbn" else raw, mode, beats_per_bar)
    grid.update(meter_policy=policy, chosen_grid=selected, auto_choice=decision,
                raw_grid=raw, dbn_grid=dbn, raw=detected.get("raw", raw.get("raw", {})),
                raw_dbn_bar_comparison=compare_bar_grids(raw, dbn))
    return grid


def regularize_grid(raw_grid, meter_policy="strict", beats_per_bar=4):
    """Regularize only irregular runs whose total beat count supports exact bars."""
    if meter_policy not in {"strict", "as_detected", "dbn"}:
        raise StageError(f"Unknown beats.meter_policy: {meter_policy}")
    beats = raw_grid["beat_times"]
    raw_downbeats = raw_grid["downbeat_times"]
    raw_indices = [min(range(len(beats)), key=lambda i: abs(beats[i] - t)) for t in raw_downbeats]
    meters = [b - a for a, b in zip(raw_indices, raw_indices[1:])]
    irregular = []
    for i, (a, b, count) in enumerate(zip(raw_downbeats, raw_downbeats[1:], meters)):
        if count != beats_per_bar:
            irregular.append({"bar_index": i, "start_s": a, "end_s": b,
                              "start_mmss_ms": _clock(a), "end_mmss_ms": _clock(b),
                              "detected_beat_count": count})
    corrected_indices = list(raw_indices)
    corrections, unresolved = [], []
    if meter_policy == "strict":
        i = 0
        regions = []
        while i < len(meters):
            if meters[i] == beats_per_bar:
                i += 1
                continue
            start = i
            while i < len(meters) and meters[i] != beats_per_bar:
                i += 1
            regions.append((start, i))
        # Work right-to-left so changing the number of detections in one region
        # cannot shift the original indices of a region to its left.
        for start, end in reversed(regions):
            total = raw_indices[end] - raw_indices[start]
            if total <= 0 or total % beats_per_bar:
                unresolved.append({"start_bar_index": start, "end_bar_index": end - 1,
                                   "detected_beats": total,
                                   "reason": "irregular_region_total_not_divisible_by_expected_meter"})
                continue
            replacement = list(range(raw_indices[start], raw_indices[end] + 1, beats_per_bar))
            if not replacement or replacement[-1] != raw_indices[end]:
                unresolved.append({"start_bar_index": start, "end_bar_index": end - 1,
                                   "detected_beats": total,
                                   "reason": "region_end_does_not_agree_with_regular_meter_phase"})
                continue
            corrected_indices[start:end + 1] = replacement
            old_inside = raw_indices[start + 1:end]
            for offset, after in enumerate(replacement[1:-1], 1):
                before = min(old_inside, key=lambda index: abs(index - after)) if old_inside else None
                if before != after:
                    corrections.append({"bar_index": start + offset - 1,
                                        "before_downbeat_s": beats[before] if before is not None else None,
                                        "after_downbeat_s": beats[after],
                                        "before_beat_index": before, "after_beat_index": after})
    corrected_downbeats = [beats[index] for index in corrected_indices]
    grid = build_grid(beats, corrected_downbeats, raw_grid["duration_s"])
    after_irregular = []
    after_meters = grid["observed_beats_per_bar"]
    for i, (count, a, b) in enumerate(zip(after_meters, corrected_downbeats, corrected_downbeats[1:])):
        if count != beats_per_bar:
            after_irregular.append({"bar_index": i, "start_s": a, "end_s": b,
                                    "start_mmss_ms": _clock(a), "end_mmss_ms": _clock(b),
                                    "detected_beat_count": count,
                                    "status": "unresolved" if meter_policy == "strict" else "as_detected"})
    grid.update(meter_policy=meter_policy, expected_beats_per_bar=beats_per_bar,
                irregular_bars_before=irregular,
                irregular_bars_after=after_irregular,
                corrected_bars=corrections, unresolved_irregular_regions=unresolved,
                raw_downbeat_times=list(raw_downbeats),
                bars_before_regularization=meters,
                bars_after_regularization=grid["observed_beats_per_bar"])
    grid["raw"] = raw_grid.get("raw", {"beat_times": list(beats),
                                        "downbeat_times": list(raw_downbeats),
                                        "observed_beats_per_bar": list(meters)})
    grid["model"] = "beat_this_dbn" if meter_policy == "dbn" else raw_grid.get("model", "beat_this")
    return grid


def _clock(value):
    minutes, seconds = divmod(value, 60)
    return f"{int(minutes):02d}:{seconds:06.3f}"


def write_click(audio_path, grid, output_path):
    """Overlay auditable high downbeat and lower beat clicks on the full mix."""
    import numpy as np
    import soundfile as sf
    audio, rate = sf.read(audio_path, always_2d=True, dtype="float32")
    audio *= .82
    length = max(1, round(rate * .035))
    phase = np.arange(length, dtype=np.float32) / rate
    envelope = np.exp(-phase * 90).astype(np.float32)
    for index, beat in enumerate(grid["beat_times"]):
        sample = round(beat * rate)
        if sample >= len(audio):
            continue
        downbeat = any(abs(beat - t) < .03 for t in grid["downbeat_times"])
        frequency, amplitude = (1760., .16) if downbeat else (880., .09)
        tone = amplitude * envelope * np.sin(2 * np.pi * frequency * phase)
        end = min(len(audio), sample + length)
        audio[sample:end, :] += tone[:end - sample, None]
    peak = float(np.max(np.abs(audio)))
    atomic_write_audio(output_path, audio, rate, subtype="FLOAT")
    return {"path": str(output_path), "sample_rate": rate,
            "overlay_gain": 0.82, "peak_after_overlay": peak,
            "downbeat_click_hz": 1760, "beat_click_hz": 880}


def grid_steps(grid, steps=16):
    """Interpolate subdivisions through each measured beat, preserving rubato."""
    if steps < 1:
        raise ValueError("steps must be positive")
    result = []
    for bar in grid["bars"]:
        times = bar["beat_times"]
        count = len(times) - 1
        for step in range(steps):
            position = step * count / steps
            beat = min(int(position), count - 1)
            fraction = position - beat
            t = times[beat] + fraction * (times[beat + 1] - times[beat])
            width = (times[beat + 1] - times[beat]) * count / steps
            result.append((t, bar["bar"], step, width))
    return result


def bar_position(grid, time):
    """Fractional bar coordinate, including pickups/end, for joins and distances."""
    bars = grid["bars"]
    i = min(max(bisect_right([b["start_s"] for b in bars], time) - 1, 0), len(bars) - 1)
    b = bars[i]
    return b["bar"] + (time - b["start_s"]) / (b["end_s"] - b["start_s"])


def snap(grid, time):
    return min(grid["downbeat_times"], key=lambda t: abs(t - time))
