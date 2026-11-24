"""Stage 7: section overlaps, dominant patterns and honest boundary agreement."""
from collections import Counter
import random
from statistics import mean, pstdev

from .beats import bar_position


def run(lyrics, patterns, grid, quantized_notes=None, baseline_shuffles=1000, seed=0):
    definitions = {p["pattern_id"]: p for p in patterns["patterns"]}
    rhythm = patterns["rhythmic_sections"]
    # Only measured downbeats qualify for boundary agreement; an extrapolated
    # final endpoint must not make a lyric ending look artificially accurate.
    boundaries = sorted({s[k] for s in rhythm for k in ("start_s", "end_s")
                         if s[k] in grid["downbeat_times"]})
    boundary_bars = [bar_position(grid, t) for t in boundaries]
    records, overlaps, dominant = [], [], []
    for i, section in enumerate(lyrics["sections"]):
        item = {**section, "lyric_section_id": f"l{i + 1}"}
        a, b = section["raw_start_s"], section["raw_end_s"]
        if a is None or b is None or b <= a:
            item.update(covered_bars=[], compression_ratio=None,
                        start_boundary_distance_bars=None, end_boundary_distance_bars=None)
            records.append(item)
            continue
        bars = [bar for bar in grid["bars"] if bar["start_s"] < b and bar["end_s"] > a]
        covered = [bar["bar"] for bar in bars]
        odd_bars = [bar for bar in bars if bar["beats_per_bar"] != 4]
        item["irregular_bars"] = [{"bar": bar["bar"], "token": f"odd[{bar['beats_per_bar']}]",
                                   "beat_count": bar["beats_per_bar"], "start_s": bar["start_s"],
                                   "end_s": bar["end_s"]} for bar in odd_bars]
        item["contains_irregular_bars"] = bool(odd_bars)
        item["covered_bars"] = covered
        item["bar_overlap_policy"] = "raw section interval intersects bar; half-open intervals"
        for r in rhythm:
            seconds = max(0., min(b, r["end_s"]) - max(a, r["start_s"]))
            if seconds:
                overlaps.append({"lyric_section_id": item["lyric_section_id"],
                                 "rhythmic_section_id": r["rhythmic_section_id"],
                                 "overlap_s": seconds, "overlap_percent": seconds / (b - a) * 100})
        run_count = 0
        regular_covered = [bar["bar"] for bar in bars if bar["beats_per_bar"] == 4]
        for instrument in sorted({r["instrument"] for r in patterns["runs"]}):
            active_rows = [r for r in patterns["bars"] if r["instrument"] == instrument and
                           r["bar"] in regular_covered and r["active"] and not r.get("irregular_bar") and
                           r.get("pattern_type") == "regular_bar"]
            counts = Counter(r["pattern_id"] for r in active_rows)
            runs = [r for r in patterns["runs"] if r["instrument"] == instrument and
                    any(r["start_bar"] <= bar < r["end_bar"] for bar in regular_covered)]
            run_count += len(runs)
            for pid, count in counts.most_common():
                dominant.append({"lyric_section_id": item["lyric_section_id"], "instrument": instrument,
                                 "pattern_id": pid, "bar_count": count,
                                 "active_bar_share": count / len(active_rows),
                                 "rank": list(counts.most_common()).index((pid, count)) + 1,
                                 "mini_notation": definitions[pid]["mini_notation"],
                                 "compression_ratio": len(covered) / len(runs) if runs else None})
        instrument_count = len({r["instrument"] for r in patterns["runs"]})
        item["regular_bars"] = len(regular_covered)
        item["compression_ratio"] = len(regular_covered) * instrument_count / run_count if run_count and regular_covered else None
        voice = [n for n in (quantized_notes or []) if n["instrument"] == "voice"]
        if a is not None and b is not None and b > a:
            vocal_overlap = [(n, max(0., min(b, n["offset_s"]) - max(a, n["onset_s"])))
                             for n in voice if n["onset_s"] < b and n["offset_s"] > a]
            active_voice_bars = {n["bar"] for n, overlap_s in vocal_overlap
                                 if overlap_s > 0 and n["bar"] in regular_covered}
            item["voice_note_coverage"] = {"note_count": len(vocal_overlap),
                "regular_bars_with_voice_notes": len(active_voice_bars),
                "regular_bar_coverage": len(active_voice_bars) / len(regular_covered) if regular_covered else None,
                "note_duration_overlap_s": sum(overlap_s for _, overlap_s in vocal_overlap),
                "lyric_section_duration_s": b - a,
                "note_duration_share_of_section": min(1., sum(overlap_s for _, overlap_s in vocal_overlap) / (b - a))}
        else:
            item["voice_note_coverage"] = {"note_count": None, "regular_bars_with_voice_notes": None,
                "regular_bar_coverage": None, "note_duration_overlap_s": None,
                "lyric_section_duration_s": None, "note_duration_share_of_section": None}
        for edge in ("start", "end"):
            raw = section[f"raw_{edge}_s"]
            item[f"{edge}_boundary_distance_bars"] = min(
                (abs(bar_position(grid, raw) - t) for t in boundary_bars), default=None)
        records.append(item)
    lyric_edges = sorted({t for s in records if s["label"] != "Instrumental"
                          for t in (s.get("raw_start_s"), s.get("raw_end_s")) if t is not None})
    rhythm_boundaries = sorted(set(boundaries))
    rng = random.Random(seed)
    downbeats = list(grid["downbeat_times"])
    random_shares = []
    sample_size = min(len(downbeats), len(rhythm_boundaries))
    for _ in range(baseline_shuffles):
        sampled = rng.sample(downbeats, sample_size) if sample_size else []
        random_shares.append(sum(min((abs(bar_position(grid, t) - bar_position(grid, b))
                                      for b in sampled), default=float("inf")) <= 1
                                 for t in lyric_edges) / len(lyric_edges) if lyric_edges else 0.)
    baseline_mean = mean(random_shares) if random_shares else None
    observed_share = sum(min((abs(bar_position(grid, t) - b) for b in boundary_bars),
                             default=float("inf")) <= 1 for t in lyric_edges) / len(lyric_edges) if lyric_edges else None
    agreement = observed_share
    result_baseline = {"observed_share_within_one_bar": observed_share,
        "baseline_mean_share": baseline_mean,
        "baseline_std_share": pstdev(random_shares) if random_shares else None,
        "baseline_95th_percentile_share": sorted(random_shares)[min(len(random_shares) - 1,
            round(.95 * (len(random_shares) - 1)))] if random_shares else None,
        "observed_minus_baseline": observed_share - baseline_mean if observed_share is not None and baseline_mean is not None else None,
        "one_sided_empirical_p": (1 + sum(v >= observed_share for v in random_shares)) /
            (1 + len(random_shares)) if observed_share is not None and random_shares else None,
        "observed_percentile": 100 * (sum(v < observed_share for v in random_shares)
            + .5 * sum(v == observed_share for v in random_shares)) / len(random_shares)
            if observed_share is not None and random_shares else None,
        "percentile_definition": "midrank: 100 * (shuffles below observed + half tied) / shuffles",
        "shuffle_distribution": random_shares,
        "boundaries_compared": len(lyric_edges), "rhythmic_boundaries": len(rhythm_boundaries),
        "shuffles": len(random_shares), "seed": seed,
        "comparison": "unique raw non-instrumental lyric start/end times vs nearest rhythmic boundary within one bar; random sets sampled without replacement from measured downbeats"}
    return {"lyric_sections": records, "section_overlaps": overlaps,
            "dominant_patterns": dominant,
            "boundary_agreement_within_one_bar_share": agreement,
            "boundary_agreement_baseline": result_baseline,
            "most_boundaries_within_one_bar": agreement > .5 if agreement is not None else None,
            "boundary_agreement_clock": "raw lyric times versus measured rhythmic downbeat boundaries"}
