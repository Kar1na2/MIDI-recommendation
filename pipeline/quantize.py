"""Stage 4: measured bar/step coordinates, residuals and grid-quality evidence."""
from bisect import bisect_left
from collections import defaultdict
from statistics import median

from .beats import grid_steps


def nearest_index(times, time):
    i = bisect_left(times, time)
    return min((max(0, i - 1), min(i, len(times) - 1)), key=lambda j: abs(times[j] - time))


def map_notes(notes, grid, steps):
    lattice = grid_steps(grid, steps)
    times = [x[0] for x in lattice]
    if not times:
        raise ValueError("Empty step grid")
    records, excluded = [], []
    for n in notes:
        if not grid["bars"][0]["start_s"] <= n["onset_s"] < grid["duration_s"]:
            excluded.append({**n, "reason": "pickup_or_outside_audio"})
            continue
        t, bar, step, width = lattice[nearest_index(times, n["onset_s"])]
        error = (n["onset_s"] - t) * 1000
        row = {**n, "bar": bar, "step": step, "steps_per_bar": steps, "grid_time_s": t,
               "quantization_error_ms": error, "step_duration_ms": width * 1000,
               "error_step_fraction": abs(error) / (width * 1000)}
        records.append(row)
    return records, excluded


def collision_stats(rows):
    """Fraction of occupied instrument/bar/step cells spanning >40 ms."""
    cells = defaultdict(list)
    for row in rows:
        cells[row["bar"], row["step"]].append(row["onset_s"])
    collisions = sum(len(times) >= 2 and max(times) - min(times) > .04 + 1e-9
                     for times in cells.values())
    return {"occupied_step_count": len(cells), "collision_step_count": collisions,
            "collision_rate": collisions / len(cells) if cells else 0.}


def quantize(notes, grid, steps=16, step_candidates=None, fit_tolerance_ms=5.0,
             collision_limit=.10):
    candidates = sorted(set(step_candidates if step_candidates is not None else
                            (16, 24, 32, 48) if steps == 16 else ()))
    if candidates and (any(value not in (16, 24, 32, 48) for value in candidates)
                       or fit_tolerance_ms < 0 or not 0 < collision_limit < 1):
        raise ValueError("Grid family must use 16/24/32/48 steps and nonnegative fit tolerance")
    initial, excluded = map_notes(notes, grid, candidates[0] if candidates else steps)
    by_instrument = defaultdict(list)
    for row in initial:
        by_instrument[row["instrument"]].append(row)
    records, stats, adjustments, resolutions = [], [], [], {}
    debug = []
    for instrument, rows in sorted(by_instrument.items()):
        before = collision_stats(rows)
        candidate_rows = {}
        if candidates:
            candidate_stats = []
            for resolution in candidates:
                mapped = map_notes(rows, grid, resolution)[0]
                collisions = collision_stats(mapped)
                errors = [abs(row["quantization_error_ms"]) for row in mapped]
                candidate_rows[resolution] = mapped
                candidate_stats.append({"steps_per_bar": resolution,
                    "median_abs_error_ms": median(errors) if errors else None,
                    "within_30ms_share": sum(error <= 30 for error in errors) / len(errors) if errors else None,
                    "collision_rate": collisions["collision_rate"],
                    "collision_step_count": collisions["collision_step_count"],
                    "occupied_step_count": collisions["occupied_step_count"]})
            acceptable = [item for item in candidate_stats if item["collision_rate"] <= collision_limit
                          and item["within_30ms_share"] >= .90]
            selected = (min(acceptable, key=lambda item: item["steps_per_bar"]) if acceptable else
                        min(candidate_stats, key=lambda item: (-item["within_30ms_share"], item["steps_per_bar"])))
            chosen = selected["steps_per_bar"]
            rows = candidate_rows[chosen]
            at16 = next((item for item in candidate_stats if item["steps_per_bar"] == 16), candidate_stats[0])
            after_collision = selected["collision_rate"]
            upgrade = chosen > 16 and next((item["collision_rate"] for item in candidate_stats
                                            if item["steps_per_bar"] == 16), 0.) > collision_limit
            debug.append({"instrument": instrument, "selection_rule":
                          "coarsest grid with >=90% onsets within 30 ms and <=10% collisions; otherwise highest within-30-ms share, coarsest tie",
                          "fit_tolerance_ms": fit_tolerance_ms, "collision_limit": collision_limit,
                          "candidates": candidate_stats, "selected_steps_per_bar": chosen,
                          "no_qualifying_grid": not bool(acceptable),
                          "all_candidates_exceeded_collision_limit": all(
                              item['collision_rate'] > collision_limit for item in candidate_stats)})
            if chosen != candidates[0]:
                adjustments.append({"instrument": instrument, "from_steps_per_bar": candidates[0],
                                    "to_steps_per_bar": chosen,
                                    "reason": "grid_family_selection_by_onset_error_and_collision",
                                    "collision_rate_16": at16["collision_rate"]})
        else:
            at16 = before if steps == 16 else collision_stats(map_notes(rows, grid, 16)[0])
            upgrade = steps == 16 and at16["collision_rate"] > .10
            chosen = 32 if upgrade else steps
            after_collision = None
            if upgrade:
                rows = map_notes(rows, grid, 32)[0]
                adjustments.append({"instrument": instrument, "from_steps_per_bar": 16,
                                    "to_steps_per_bar": 32,
                                    "reason": "collision_rate_16_exceeds_10_percent",
                                    "collision_rate_16": at16["collision_rate"]})
        after = collision_stats(rows)
        if after_collision is None:
            after_collision = after["collision_rate"]
        records.extend(rows)
        resolutions[instrument] = chosen
        frac = median([r["error_step_fraction"] for r in rows])
        stats.append({"instrument": instrument, "note_count": len(rows),
                      "median_abs_error_ms": median([abs(r["quantization_error_ms"]) for r in rows]),
                      "median_error_step_fraction": frac,
                      "within_30ms_share": sum(abs(r["quantization_error_ms"]) <= 30 for r in rows) / len(rows),
                      "grid_suspect": frac > .25,
                      "requested_steps_per_bar": steps, "steps_per_bar": chosen,
                      "requantized_to_32": chosen == 32 and steps == 16 and upgrade,
                      "collision_rate_16": at16["collision_rate"],
                      "collision_rate_32": (after["collision_rate"] if chosen == 32 else
                                             candidate_rows.get(32) and collision_stats(candidate_rows[32])["collision_rate"]),
                      "initial_collision_rate": before["collision_rate"], **after})
    records.sort(key=lambda n: (n["onset_s"], n["instrument"], n["pitch"]))
    # Half/double hypotheses use the SAME measured beats and bar origins. Finer
    # grids always fit at least as well: evidence is ambiguity, never auto-fixing.
    hypotheses = []
    for factor in (.5, 1., 2.):
        candidate = grid_steps(grid, max(1, round(steps * factor)))
        tt = [x[0] for x in candidate]
        residuals = [abs(n["onset_s"] - tt[nearest_index(tt, n["onset_s"])]) * 1000 for n in records]
        hypotheses.append({"tempo_factor": factor,
                           "median_abs_error_ms": median(residuals) if residuals else None,
                           "within_30ms_share": sum(e <= 30 for e in residuals) / len(residuals) if residuals else None})
    flags = list(grid.get("flags", []))
    if any(row.get("no_qualifying_grid") for row in debug):
        flags.append("no_grid_meets_90pct_within30ms_and_10pct_collision")
    if any(s["grid_suspect"] for s in stats):
        flags.append("median_quantization_error_exceeds_quarter_step")
    if not records:
        flags.append("no_quantizable_onsets")
    if records:
        half, base, double = hypotheses
        if double["median_abs_error_ms"] < base["median_abs_error_ms"] * .7 and base["median_abs_error_ms"] > 15:
            flags.append("possible_half_tempo_or_finer_subdivision")
        if half["within_30ms_share"] >= base["within_30ms_share"] * .95:
            flags.append("possible_double_tempo_or_coarser_subdivision")
    return {"steps_per_bar": steps, "instrument_steps_per_bar": resolutions,
            "resolution_adjustments": adjustments,
            "collision_policy": {"denominator": "occupied instrument/bar/step cells",
                                 "collision_minimum_raw_onset_span_ms": 40,
                                 "upgrade_if_16_step_collision_rate_exceeds": .10,
                                 "upgrade_to_steps_per_bar": 32,
                                 "grid_family_candidates": candidates,
                                 "grid_family_fit_tolerance_ms": fit_tolerance_ms if candidates else None,
                                 "grid_family_collision_limit": collision_limit if candidates else None,
                                 "grid_family_debug": debug},
            "notes": records, "excluded_notes": excluded,
            "instrument_stats": stats, "flags": flags, "grid_suspect": bool(flags),
            "tempo_hypotheses": hypotheses,
            "tempo_check_limit": "Onset fit cannot distinguish tempo octaves from subdivision feel; no automatic tempo correction."}
