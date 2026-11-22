"""Reference-window inclusion, texture diagnosis and multi-scale acceptance."""
from collections import Counter, defaultdict
from statistics import median

from .note_metrics import onset_texture
from .period import period_match


ALIASES = {
    "piano": ("acoustic_piano", "electric_piano", "organ"),
    "keys": ("acoustic_piano", "electric_piano", "organ"),
    "drums": ("drums",),
    "strings": ("synth_strings", "string_ensemble", "violin", "viola", "cello"),
    "synth": ("synth_lead", "synth_pad", "synth_strings"),
    "bass": ("acoustic_bass", "electric_bass"),
    "electric bass": ("electric_bass",),
    "guitar": ("acoustic_guitar", "clean_electric_guitar", "distorted_electric_guitar"),
}


def group_key(value):
    key = value.strip().casefold()
    for family in ("piano", "drum", "string", "synth", "guitar"):
        if family in key:
            return {"drum": "drums", "string": "strings"}.get(family, family)
    if "bass" in key:
        return "electric bass" if "electric" in key else "bass"
    return "piano" if key == "keys" else key


def source_and_layer(value):
    if "__" in value:
        return tuple(value.split("__", 1))
    return value, None


def overlap_share(start, end, window_start, window_end):
    return max(0., min(end, window_end) - max(start, window_start)) / (end - start)


def instrument_mapping(requested, actual, notes, start, end):
    mappings = []
    active = [n for n in notes if n["onset_s"] < end and n["offset_s"] > start]
    for name in requested:
        key = group_key(name)
        matched = sorted(set(ALIASES.get(key, (name,))) & set(actual))
        reason = "instrument-family labels"
        evidence = []
        if key == "strings" and notes:
            candidates = [instrument for instrument in actual
                          if instrument in ALIASES["strings"] or instrument.startswith("synth_")]
            for instrument in candidates:
                rows = [n for n in active if n["instrument"] == instrument]
                duration = sum(min(end, n["offset_s"]) - max(start, n["onset_s"]) for n in rows)
                if rows:
                    evidence.append({"instrument": instrument, "note_count": len(rows),
                                     "summed_note_overlap_s": duration})
            evidence.sort(key=lambda row: (-row["summed_note_overlap_s"], -row["note_count"], row["instrument"]))
            matched = [evidence[0]["instrument"]] if evidence else []
            reason = ("emitted synth/string label with greatest summed note overlap in the exact window; "
                      "data-based accompaniment mapping, not an auditory identification")
        mappings.append({"requested_instrument": name, "matched_instruments": matched,
                         "mapping_reason": reason, "mapping_candidates": evidence})
    return mappings


def score_level(name, units, rows, definitions, mappings, primary_name, threshold):
    by_unit = defaultdict(dict)
    for row in rows:
        by_unit[row["unit_id"]][row["instrument"]] = row
    results = []
    for mapping in mappings:
        counts = Counter()
        for unit in units:
            for instrument in mapping["matched_instruments"]:
                row = by_unit[unit["unit_id"]].get(instrument, {})
                exact_id = row.get("exact_id", row.get("pitch_pattern_id"))
                if exact_id:
                    counts[instrument, exact_id] += 1
        dominant, count = counts.most_common(1)[0] if counts else (None, 0)
        share = count / len(units) if units else 0.
        per_instrument_periods = {}
        for instrument in mapping["matched_instruments"]:
            sequences = [by_unit[u["unit_id"]].get(instrument, {}).get("events", []) for u in units]
            labels = [f"b{u['bar']}{'ab'[u['half']-1]}" if "half" in u else
                      f"b{u['bar']}" if "bar" in u else "–".join(map(str, u["bars"])) for u in units]
            per_instrument_periods[instrument] = period_match(sequences, labels)
        period_instrument = max(per_instrument_periods,
            key=lambda inst: per_instrument_periods[inst]["match_rate"], default=None)
        period_result = per_instrument_periods.get(period_instrument, period_match([]))
        results.append({
            **mapping,
            "dominant_pitch_pattern_cluster": f"{dominant[0]}:{dominant[1]}" if dominant else None,
            "dominant_instrument": dominant[0] if dominant else None,
            "dominant_pitch_pattern_id": dominant[1] if dominant else None,
            "dominant_cluster_unit_count": count, "dominant_cluster_bar_count": count,
            "dominant_cluster_share": share,
            "simplified_mini_notation": definitions.get(dominant[1], {}).get("mini_notation") if dominant else None,
            "old_dominant_cluster_pass": bool(units) and share >= threshold,
            "period_match": period_result, "period_instrument": period_instrument,
            "per_instrument_periods": per_instrument_periods,
            "pass": period_result["pass"],
        })
    primary = next((row for row in results if group_key(row["requested_instrument"]) == primary_name), results[0])
    unit_rows = []
    for unit in units:
        matches, clusters = [], {}
        for instrument, row in sorted(by_unit[unit["unit_id"]].items()):
            pid = row.get("exact_id", row.get("pitch_pattern_id"))
            cluster = f"{instrument}:{pid}" if pid else row.get("pattern_type", "sustain")
            clusters[instrument] = cluster
            matches.append({"instrument": instrument, "pattern_id": row.get("pattern_id"),
                            "pitch_pattern_id": pid,
                            "exact_id": pid, "shape_id": row.get("shape_id"),
                            "pitch_pattern_cluster_id": f"{instrument}:{pid}" if pid else None,
                            "pattern_type": row.get("pattern_type", "regular_bar"),
                            "contour": row.get("contour")})
        unit_rows.append({**unit, "pitch_pattern_cluster_ids": clusters,
                          "instrument_patterns": matches})
    return {"level": name, "unit_count": len(units), "units": unit_rows,
            "floor": 1 / len(units) if units else None,
            "acceptance_id": "exact_id",
            "acceptance_metric": "period_match_rate",
            "period_match": primary["period_match"],
            "per_instrument_group": results,
            "primary_instrument": primary["requested_instrument"],
            "dominant_pitch_pattern_cluster": primary["dominant_pitch_pattern_cluster"],
            "dominant_cluster_instrument": primary["dominant_instrument"],
            "dominant_pitch_pattern_id": primary["dominant_pitch_pattern_id"],
            "dominant_cluster_unit_count": primary["dominant_cluster_unit_count"],
            "dominant_cluster_bar_count": primary["dominant_cluster_bar_count"],
            "dominant_cluster_share": primary["dominant_cluster_share"],
            "simplified_mini_notation": primary["simplified_mini_notation"],
            "acceptance_threshold": threshold, "pass": primary["pass"]}


def additional_levels(grid, quantized, selected, start, end, mappings, primary, threshold,
                      onset_threshold, drum_distance, contour_threshold=1):
    from .patterns import sequence_clusters, encode_bar, onset_count
    instrument_notes = defaultdict(lambda: defaultdict(list))
    for note in quantized["notes"]:
        instrument_notes[note["instrument"]][note["bar"]].append(note)
    bars = grid["bars"]
    regular = {b["bar"]: b for b in bars if b["beats_per_bar"] == 4}
    units_by_level = {"half_bar": {}, "two_bar_phrase": {}}
    encodings = {name: defaultdict(dict) for name in units_by_level}
    for bar_id, bar in regular.items():
        midpoint = bar["beat_times"][2]
        for half in (0, 1):
            unit_id = f"{bar_id}:{half}"
            units_by_level["half_bar"][unit_id] = {
                "unit_id": unit_id, "bar": bar_id + 1, "half": half + 1,
                "start_s": bar["start_s"] if half == 0 else midpoint,
                "end_s": midpoint if half == 0 else bar["end_s"]}
        if bar_id + 1 in regular:
            next_bar = regular[bar_id + 1]
            unit_id = f"{bar_id}:{bar_id + 1}"
            units_by_level["two_bar_phrase"][unit_id] = {
                "unit_id": unit_id, "bars": [bar_id + 1, bar_id + 2],
                "start_s": bar["start_s"], "end_s": next_bar["end_s"]}
    for instrument, grouped in sorted(instrument_notes.items()):
        steps = quantized.get("instrument_steps_per_bar", {}).get(instrument, quantized["steps_per_bar"])
        for bar_id in regular:
            for half in (0, 1):
                chosen = [{**n, "step": n["step"] - half * (steps // 2)}
                          for n in grouped[bar_id]
                          if half * (steps // 2) <= n["step"] < (half + 1) * (steps // 2)]
                encodings["half_bar"][instrument][f"{bar_id}:{half}"] = encode_bar(
                    chosen, steps // 2, instrument == "drums")
            if bar_id + 1 in regular:
                chosen = [*grouped[bar_id], *[{**n, "step": n["step"] + steps}
                                             for n in grouped[bar_id + 1]]]
                encodings["two_bar_phrase"][instrument][f"{bar_id}:{bar_id + 1}"] = encode_bar(
                    chosen, steps * 2, instrument == "drums")
    selected_ids = {b["bar"] for b in selected}
    selected_half = [unit for unit in units_by_level["half_bar"].values()
                     if unit["bar"] - 1 in selected_ids
                     and overlap_share(unit["start_s"], unit["end_s"], start, end) >= .5 - 1e-9]
    selected_pairs = []
    ordered = sorted(selected_ids)
    index = 0
    while index + 1 < len(ordered):
        first, second = ordered[index:index + 2]
        if second == first + 1:
            selected_pairs.append(units_by_level["two_bar_phrase"][f"{first}:{second}"])
            index += 2
        else:
            index += 1
    scored = []
    for level, chosen_units in (("half_bar", selected_half), ("two_bar_phrase", selected_pairs)):
        rows, definitions = [], {}
        for instrument, examples in encodings[level].items():
            active = {unit: e for unit, e in examples.items() if onset_count(e["onset_pattern"]) > 1}
            prefix = instrument + ("_h" if level == "half_bar" else "_p2")
            assignments = sequence_clusters(active, prefix)
            shapes = sequence_clusters(active, prefix + "s", shape=True)
            by_cluster = defaultdict(list)
            for unit, encoding in examples.items():
                pid = assignments[unit][0] if unit in assignments else None
                rows.append({"unit_id": unit, "instrument": instrument, "pitch_pattern_id": pid,
                             "events": encoding["events"],
                             "exact_id": pid, "shape_id": shapes[unit][0] if pid else None,
                             "pattern_id": pid or "sustain", "contour": encoding["contour"],
                             "pattern_type": "regular_bar" if pid else "sustain"})
                if pid:
                    by_cluster[pid].append(encoding)
            for pid, choices in by_cluster.items():
                signatures = Counter(e["mini_notation"] for e in choices)
                best = max(choices, key=lambda e: signatures[e["mini_notation"]])
                definitions[pid] = {**best, "clustering_representative_events": choices[0]["events"]}
        result = score_level(level, chosen_units, rows, definitions, mappings, primary, threshold)
        result["unit_definition"] = (
            "Each measured four-beat bar split at beat 3; units require >=50% window overlap."
            if level == "half_bar" else
            "Consecutive selected regular bars paired without overlap, anchored at the first eligible bar; "
            "all sliding pairs in the song supply clustering examples, but only disjoint pairs count in acceptance.")
        scored.append(result)
    return scored


def evaluate(reference, grid, pattern_data, threshold=.75, quantized=None,
             transcription_notes=None, layer_data=None):
    requested = reference.get("instruments", reference.get("instrument"))
    if isinstance(requested, str):
        requested = [part.strip() for part in requested.replace("&", "and").split("and") if part.strip()]
    if not requested:
        raise ValueError("Arpeggio reference requires at least one instrument group")
    start, end = float(reference["start_s"]), float(reference["end_s"])
    selected = [bar for bar in grid["bars"] if bar["beats_per_bar"] == 4
                and overlap_share(bar["start_s"], bar["end_s"], start, end) >= .5 - 1e-9]
    actual = sorted({source_and_layer(row["instrument"])[0] for row in pattern_data["bars"]})
    notes = transcription_notes if transcription_notes is not None else (quantized or {}).get("notes", [])
    mappings = instrument_mapping(requested, actual, notes, start, end)
    primary = group_key(reference.get("primary_instrument", requested[0]))
    acceptance_layer = reference.get("acceptance_layer", "line" if layer_data else None)
    pattern_rows = pattern_data["bars"]
    if acceptance_layer:
        pattern_rows = [{**row, "instrument": source_and_layer(row["instrument"])[0]}
                        for row in pattern_rows
                        if source_and_layer(row["instrument"])[1] == acceptance_layer]
    units = [{"unit_id": str(bar["bar"]), "bar": bar["bar"] + 1,
              "start_s": bar["start_s"], "end_s": bar["end_s"],
              "window_overlap_share": overlap_share(bar["start_s"], bar["end_s"], start, end)}
             for bar in selected]
    rows = [{**row, "unit_id": str(row["bar"])} for row in pattern_rows]
    definitions = {row["pitch_pattern_id"]: row for row in pattern_data.get("pitch_pattern_definitions", [])}
    one = score_level("one_bar", units, rows, definitions, mappings, primary, threshold)
    levels = [one]
    acceptance_quantized = quantized
    if layer_data and quantized is not None:
        acceptance_quantized = {**quantized,
            "notes": [row for row in layer_data["notes"] if row["layer"] == acceptance_layer]}
    if acceptance_quantized is not None:
        policy = pattern_data.get("clustering", {})
        levels.extend(additional_levels(grid, acceptance_quantized, selected, start, end, mappings, primary, threshold,
                                        policy.get("onset_normalized_symmetric_difference_threshold", .25),
                                        policy.get("pitch_edit_threshold", 2),
                                        policy.get("contour_edit_threshold", 1)))
    primary_mapping = next((m for m in mappings if group_key(m["requested_instrument"]) == primary), mappings[0])
    primary_labels = set(primary_mapping["matched_instruments"])
    if layer_data:
        half_bars = [unit for unit in layer_data["half_bar_units"]
                     if unit["instrument"] in primary_labels
                     and overlap_share(unit["start_s"], unit["end_s"], start, end) >= .5 - 1e-9]
        half_rows = {(row["unit_id"], row["layer"]): row for row in layer_data["half_bar_layers"]}
        line_rows = [half_rows.get((unit["unit_id"], "line"), {"note_count": 0, "mini_notation": "~"})
                     for unit in half_bars]
        counts = [row["note_count"] for row in line_rows]
        deficient = sum(count < 3 for count in counts)
        deficient_share = deficient / len(counts) if counts else 1.
        texture = {"layer": acceptance_layer, "half_bar_minimum_notes": 3,
                   "eligible_half_bar_count": len(half_bars),
                   "line_note_counts_by_half_bar": [
                       {"bar": unit["bar"], "half": unit["half"], "note_count": row["note_count"],
                        "mini_notation": row["mini_notation"]}
                       for unit, row in zip(half_bars, line_rows)],
                   "half_bars_below_minimum": deficient,
                   "share_half_bars_below_minimum": deficient_share,
                   "median_line_notes_per_half_bar": median(counts) if counts else 0,
                   "failure_classification": "transcription" if deficient_share > .5 or not counts else "matching",
                   "definition": "Transcription failure when more than half of the eligible reference half-bars contain fewer than three line-layer notes."}
    else:
        primary_notes = [n for n in notes if n["instrument"] in primary_labels
                         and start <= n["onset_s"] < end]
        texture = onset_texture(primary_notes) if notes else None
    passed = any(level["pass"] for level in levels)
    return {**{k: v for k, v in one.items() if k not in ("level", "units", "unit_count")},
            "reference": reference, "regular_section_bar_count": len(selected),
            "cluster_ids_per_bar": one["units"], "instrument_mapping": mappings,
            "bar_inclusion_minimum_overlap": .5,
            "levels": {level["level"]: level for level in levels},
            "acceptance_rule": "For each scale, maximize sim(n,n+p)>=0.85 over p=1..floor(N/2); pass if >=75% of N-p comparisons match at any scale. Empty/sustain comparisons are nonmatches.",
            "primary_onset_texture": texture,
            "acceptance_layer": acceptance_layer,
            "failure_classification": texture["failure_classification"] if texture and not passed else None,
            "pass": passed}
