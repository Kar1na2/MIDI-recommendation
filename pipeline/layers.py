"""Split each instrument's quantized half-bar events into bass, chord, and line layers."""
from collections import Counter, defaultdict
from statistics import median

from .patterns import simplify_tokens


PITCH_NAMES = ("c", "c#", "d", "d#", "e", "f", "f#", "g", "g#", "a", "a#", "b")
LAYERS = ("bass", "chord", "line")


def pitch_name(pitch):
    return f"{PITCH_NAMES[int(pitch) % 12]}{int(pitch) // 12 - 1}"


def _components(rows, tolerance):
    groups = []
    for row in sorted(rows, key=lambda item: (item["onset_s"], item["pitch"])):
        if groups and row["onset_s"] - groups[-1][-1]["onset_s"] <= tolerance + 1e-9:
            groups[-1].append(row)
        else:
            groups.append([row])
    return groups


def _mini_notation(rows, steps, half, tolerance=.03):
    cells = defaultdict(list)
    shift = half * steps
    for row in rows:
        cells[row["step"] - shift].append(int(row["pitch"]))
    tokens = []
    for step in range(steps):
        pitches = sorted(set(cells.get(step, [])))
        if not pitches:
            tokens.append("~")
        elif len(pitches) == 1:
            tokens.append(pitch_name(pitches[0]))
        else:
            tokens.append("[" + ",".join(pitch_name(p) for p in pitches) + "]")
    return simplify_tokens(tokens)


def split_notes(notes, grid, instrument_steps_per_bar, coincidence_tolerance_s=.03,
                bass_sustain_share=.5):
    """Return note layers and per-half-bar counts/notation for every instrument."""
    by_instrument_bar = defaultdict(list)
    for index, row in enumerate(notes):
        by_instrument_bar[(row["instrument"], row["bar"])].append({**row, "_layer_note_index": index})
    pattern_notes, public_notes, units, summaries = [], [], [], []
    totals = defaultdict(Counter)
    for bar in grid["bars"]:
        if bar["beats_per_bar"] != 4:
            continue
        middle = bar["beat_times"][2]
        for half, (start, end) in enumerate(((bar["start_s"], middle), (middle, bar["end_s"]))):
            duration = end - start
            for instrument in sorted({key[0] for key in by_instrument_bar if key[1] == bar["bar"]}):
                steps = instrument_steps_per_bar.get(instrument, 16)
                half_steps = steps // 2
                rows = [row for row in by_instrument_bar[instrument, bar["bar"]]
                        if half * half_steps <= row["step"] < (half + 1) * half_steps]
                assigned = {}
                lowest = min(rows, key=lambda row: (row["pitch"], row["onset_s"])) if rows else None
                # Estimate the prospective skyline before extracting bass. This
                # keeps a short low attack out of an otherwise clean arpeggio.
                skyline = [max(group, key=lambda row: row["pitch"])
                           for group in _components(rows, coincidence_tolerance_s)]
                upper = [row["pitch"] for row in skyline if row is not lowest]
                separated = bool(upper) and lowest["pitch"] <= median(upper) - 12
                sustained = lowest is not None and max(0., min(lowest["offset_s"], end)
                    - max(lowest["onset_s"], start)) >= duration * bass_sustain_share - 1e-9
                bass = lowest if sustained or separated else None
                if bass is not None:
                    assigned[bass["_layer_note_index"]] = "bass"
                chord_candidates = []
                for index, row in enumerate(rows):
                    if bass is not None and row["_layer_note_index"] == bass["_layer_note_index"]:
                        continue
                    neighbours = sum(1 for other in rows if other["_layer_note_index"] != row["_layer_note_index"]
                                    and abs(other["onset_s"] - row["onset_s"]) <= coincidence_tolerance_s + 1e-9)
                    if neighbours >= 2:
                        chord_candidates.append(row)
                for row in chord_candidates:
                    assigned[row["_layer_note_index"]] = "chord"
                remaining = [row for row in rows if row["_layer_note_index"] not in assigned]
                line = []
                for group in _components(remaining, coincidence_tolerance_s):
                    line.append(max(group, key=lambda row: (row["pitch"], -row["onset_s"])))
                line_indices = {row["_layer_note_index"] for row in line}
                for row in line:
                    assigned[row["_layer_note_index"]] = "line"
                per_layer = {name: [row for row in rows if assigned.get(row["_layer_note_index"]) == name]
                             for name in LAYERS}
                unit_id = f"{instrument}:{bar['bar']}:{half}"
                units.append({"unit_id": unit_id, "instrument": instrument,
                    "bar": bar["bar"] + 1, "half": half + 1, "start_s": start, "end_s": end,
                    "duration_s": duration, "note_count": len(rows),
                    "layer_note_counts": {name: len(per_layer[name]) for name in LAYERS},
                    "layer_mini_notation": {name: _mini_notation(per_layer[name], half_steps, half)
                                             for name in LAYERS},
                    "line_meets_minimum": len(per_layer["line"]) >= 3})
                for layer in LAYERS:
                    selected = per_layer[layer]
                    totals[instrument][layer] += len(selected)
                    decorated = []
                    for row in selected:
                        clean = {key: value for key, value in row.items() if key != "_layer_note_index"}
                        clean.update(layer=layer, source_instrument=instrument,
                                     unit_root_pitch=bass["pitch"] if bass else min(r["pitch"] for r in rows),
                                     instrument=f"{instrument}__{layer}", layer_unit_id=unit_id)
                        pattern_notes.append(clean)
                        public = {key: value for key, value in clean.items()
                                  if key not in ("instrument", "source_instrument")}
                        public["instrument"] = instrument
                        public_notes.append(public)
                        decorated.append(public)
                    summaries.append({"unit_id": unit_id, "instrument": instrument, "layer": layer,
                        "bar": bar["bar"] + 1, "half": half + 1,
                        "start_s": start, "end_s": end, "note_count": len(selected),
                        "mini_notation": _mini_notation(selected, half_steps, half)})
    regular_bars = {bar["bar"] for bar in grid["bars"] if bar["beats_per_bar"] == 4}
    for (instrument, bar), rows in sorted(by_instrument_bar.items()):
        if bar in regular_bars:
            continue
        for row in rows:
            clean = {key: value for key, value in row.items() if key != "_layer_note_index"}
            clean.update(layer="irregular", source_instrument=instrument,
                         instrument=f"{instrument}__irregular", layer_unit_id=f"{instrument}:{bar}:odd")
            pattern_notes.append(clean)
            public = {key: value for key, value in clean.items()
                      if key not in ("instrument", "source_instrument")}
            public["instrument"] = instrument
            public_notes.append(public)
            totals[instrument]["irregular"] += 1
    public_notes.sort(key=lambda row: (row["onset_s"], row["instrument"], row["layer"], row["pitch"]))
    pattern_notes.sort(key=lambda row: (row["onset_s"], row["instrument"], row["pitch"]))
    summary = [{"instrument": instrument, "layer_note_counts": dict(counts)}
               for instrument, counts in sorted(totals.items())]
    return {"policy": {"unit": "measured four-beat half-bar", "layers": [*LAYERS, "irregular"],
                        "bass_rule": "lowest-register note if >=50% half-bar overlap OR >=12 semitones below median prospective skyline pitch (excluding that note)",
                        "chord_rule": "each note onset within 30ms of at least two other same-instrument notes",
                        "line_rule": "highest note in each remaining onset group within 30ms",
                        "line_acceptance_minimum_notes_per_half_bar": 3,
                        "irregular_bars": "kept as per-instrument odd-meter tokens; no half-bar layer assignment",
                        "coincidence_tolerance_ms": coincidence_tolerance_s * 1000},
            "notes": public_notes, "pattern_notes": pattern_notes,
            "half_bar_layers": summaries, "half_bar_units": units,
            "instrument_summary": summary,
            "discarded_note_count": len(notes) - len(public_notes)}
