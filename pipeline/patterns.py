"""Tolerant instrument patterns, repeated phrases and novelty sectioning."""
from collections import Counter, defaultdict
from statistics import median

from .config import PATTERN_SIMILARITY_THRESHOLD, ONSET_TOLERANCE_STEPS


def smallest_period(sequence):
    """Exact primitive period, keeping rests and simultaneous note groups."""
    if not sequence:
        return (), 0
    for length in range(1, len(sequence) + 1):
        if len(sequence) % length == 0 and all(sequence[i] == sequence[i % length] for i in range(len(sequence))):
            return sequence[:length], len(sequence) // length
    return sequence, 1


def simplify_tokens(tokens, repeats=1, rest="~"):
    """Drop phase-only outer rests and preserve internal rest durations compactly."""
    tokens = list(tokens)
    while tokens and tokens[0] == rest:
        tokens.pop(0)
    while tokens and tokens[-1] == rest:
        tokens.pop()
    if not tokens:
        return rest
    period, cycle_count = shortest_tiling(tuple(tokens))
    count = repeats * cycle_count
    compact, index = [], 0
    while index < len(period):
        token = period[index]
        if token != rest:
            compact.append(str(token))
            index += 1
            continue
        end = index + 1
        while end < len(period) and period[end] == rest:
            end += 1
        compact.append(rest if end - index == 1 else f"{rest}{end - index}")
        index = end
    if count == 1 and len(compact) == 1:
        return compact[0]
    if count == 1:
        return " ".join(compact)
    return "[" + " ".join(compact) + f"]*{count}"


def shortest_tiling(sequence):
    """Shortest repeating cycle, allowing the final cycle's trailing rests to be omitted."""
    if not sequence:
        return (), 0
    for length in range(1, len(sequence) + 1):
        if all(sequence[i] == sequence[i % length] for i in range(len(sequence))):
            return sequence[:length], (len(sequence) + length - 1) // length
    return sequence, 1


def format_pattern_tokens(tokens, repeats=1, rest="~"):
    """Compact a complete step grid without discarding its phase or length."""
    tokens = tuple(tokens)
    if not tokens or not isinstance(repeats, int) or repeats < 1:
        raise ValueError("Pattern notation requires a nonempty grid and positive repetitions")
    period, cycles = smallest_period(tokens)
    compact, index = [], 0
    while index < len(period):
        if period[index] != rest:
            compact.append(str(period[index]))
            index += 1
            continue
        end = index + 1
        while end < len(period) and period[end] == rest:
            end += 1
        compact.append(rest if end - index == 1 else f"{rest}{end - index}")
        index = end
    body = " ".join(compact)
    count = repeats * cycles
    return f"[{body}]*{count}" if count > 1 else body


def expand_onset_notation(notation, steps_per_bar=None):
    """Expand step-based onset notation; reject incomplete or ambiguous bars."""
    import re
    tokens, index = [], 0
    while index < len(notation):
        if notation[index].isspace():
            index += 1
        elif notation[index] == "x":
            tokens.append("x")
            index += 1
        elif notation[index] == "~":
            match = re.match(r"~([0-9]*)", notation[index:])
            count = int(match.group(1) or 1)
            if count < 1:
                raise ValueError("A rest must occupy at least one step")
            tokens.extend("." * count)
            index += len(match.group(0))
        elif notation[index] == "[":
            match = re.match(r"\[([^\[\]]+)\]\*([1-9][0-9]*)", notation[index:])
            if not match:
                raise ValueError("Invalid repeated onset pattern")
            tokens.extend(expand_onset_notation(match.group(1)) * int(match.group(2)))
            index += len(match.group(0))
        else:
            raise ValueError(f"Unexpected onset notation at position {index}")
    result = "".join(tokens)
    if steps_per_bar is not None and len(result) != steps_per_bar:
        raise ValueError(f"Notation describes {len(result)} steps, expected {steps_per_bar}")
    return result


def normalized_onset_distance(pattern_a, pattern_b):
    onsets_a = {i for i, token in enumerate(pattern_a) if token == "x"}
    onsets_b = {i for i, token in enumerate(pattern_b) if token == "x"}
    union = onsets_a | onsets_b
    return len(onsets_a ^ onsets_b) / len(union) if union else 0.


def onset_count(pattern):
    return pattern.count("x")


def encode_bar(notes, steps=16, percussion=False):
    ordered = sorted(notes, key=lambda n: (n["step"], n["pitch"], n["onset_s"]))
    shape_root = min((n.get("unit_root_pitch", n["pitch"]) for n in ordered), default=0)
    events = [(n["step"], n["pitch"]) for n in ordered]
    origin = ordered[0]["pitch"] if ordered and not percussion else 0
    cells = [tuple(sorted(n["pitch"] - origin for n in ordered if n["step"] == step)) for step in range(steps)]
    onset = "".join("x" if cell else "." for cell in cells)
    # Event shape includes time slots and voicing; absolute roots may change.
    # Local roots are removed for each repeated subfigure, so rising arpeggios
    # over changing chord roots still compress within a bar.
    figure, repeats = tuple(cells), 1
    offsets = [0]
    for length in range(1, steps + 1):
        if steps % length:
            continue
        chunks, roots = [], []
        for first in range(0, steps, length):
            chunk = cells[first:first + length]
            # One note has no melodic shape; normalizing it would compress any
            # melody into repetitions of a single transposed note.
            can_transpose = not percussion and sum(bool(c) for c in chunk) >= 3
            root = next((c[0] for c in chunk if c), 0) if can_transpose else 0
            chunks.append(tuple(tuple(p - root for p in c) for c in chunk))
            roots.append(root)
        if all(c == chunks[0] for c in chunks):
            figure, repeats, offsets = chunks[0], len(chunks), roots
            break
    tokens = ["~" if not c else str(c[0]) if len(c) == 1
              else "[" + ",".join(map(str, c)) + "]" for c in figure]
    pitch_notation = format_pattern_tokens(tokens, repeats)
    onset_tokens = ["x" if c == "x" else "~" for c in onset]
    onset_notation = format_pattern_tokens(onset_tokens)
    _, onset_repeats = smallest_period(tuple(onset))
    centers = [median(cell) for cell in cells if cell]
    contour = "".join("U" if right > left else "D" if right < left else "R"
                      for left, right in zip(centers, centers[1:]))
    return {"onset_pattern": onset, "pitch_pattern": [list(c) for c in cells],
            "events": events, "shape_events": [(step, pitch - shape_root) for step, pitch in events],
            "shape_root_pitch": shape_root,
            "pitch_intervals": [b["pitch"] - a["pitch"] for a, b in zip(ordered, ordered[1:])],
            "contour": contour,
            "contour_basis": "direction between median pitches of consecutive occupied quantized steps; rests skipped",
            "drum_classes": [n["pitch"] for n in ordered] if percussion else None,
            "within_bar": {"steps": len(figure), "repeats": repeats,
                           "figure": [list(c) for c in figure], "root_offsets": offsets},
            "onset_within_bar_repeats": onset_repeats,
            "mini_notation": pitch_notation, "onset_mini_notation": onset_notation}


def edit_distance(a, b):
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def event_similarity(a, b):
    """Order-preserving, one-to-one edit alignment with global onset tolerance."""
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            match = x[1] == y[1] and abs(x[0] - y[0]) <= ONSET_TOLERANCE_STEPS
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j-1] + (not match)))
        previous = current
    return 1 - previous[-1] / max(len(a), len(b)) if a or b else 1.


def event_differences(a, b):
    """One optimal monotonic edit alignment; tolerated timing shifts cost zero."""
    costs = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    def matches(x, y):
        return x[1] == y[1] and abs(x[0] - y[0]) <= ONSET_TOLERANCE_STEPS
    for i in range(len(a) + 1):
        costs[i][0] = i
    for j in range(len(b) + 1):
        costs[0][j] = j
    for i, x in enumerate(a, 1):
        for j, y in enumerate(b, 1):
            costs[i][j] = min(costs[i-1][j] + 1, costs[i][j-1] + 1,
                              costs[i-1][j-1] + (not matches(x, y)))
    changes, i, j = [], len(a), len(b)
    while i or j:
        if i and j and costs[i][j] == costs[i-1][j-1] + (not matches(a[i-1], b[j-1])):
            if not matches(a[i-1], b[j-1]):
                changes.append({"operation": "substitute", "a": a[i-1], "b": b[j-1]})
            i, j = i-1, j-1
        elif i and costs[i][j] == costs[i-1][j] + 1:
            changes.append({"operation": "delete", "a": a[i-1], "b": None})
            i -= 1
        else:
            changes.append({"operation": "insert", "a": None, "b": b[j-1]})
            j -= 1
    return list(reversed(changes))


def sequence_clusters(encodings, prefix, threshold=PATTERN_SIMILARITY_THRESHOLD, shape=False):
    """Chronological greedy clustering against the first member of each cluster.

    Callers supply units in time order. Do not frequency-sort, pick a nearest
    seed, or update representatives: those would change the specified policy.
    """
    if not 0 <= threshold <= 1:
        raise ValueError("Pattern similarity threshold must be between 0 and 1")
    representatives, assignments = [], {}
    key = "shape_events" if shape else "events"
    for unit, encoding in encodings.items():
        value = [tuple(event) for event in encoding[key]]
        for index, representative in enumerate(representatives):
            similarity = event_similarity(value, representative)
            if bool(value) == bool(representative) and similarity + 1e-12 >= threshold:
                break
        else:
            index, similarity = len(representatives), 1.
            representatives.append(value)
        assignments[unit] = (f"{prefix}{index + 1:03d}", 1 - similarity)
    return assignments


def cluster(values, distance, threshold, prefix):
    """Frequency-first representative radius; no transitive single-link chains."""
    counts = Counter(values)
    representatives, assignments = [], {}
    for value in sorted(counts, key=lambda v: (-counts[v], v)):
        choices = [(distance(value, rep), i) for i, rep in enumerate(representatives)
                   if value[0] == rep[0]]  # never merge active patterns with silence
        best = min(choices, default=(threshold + 1, 0))
        if best[0] > threshold:
            best = (0, len(representatives))
            representatives.append(value)
        assignments[value] = (f"{prefix}{best[1] + 1:03d}", best[0])
    return assignments


def contour_clusters(encodings, onset_threshold, prefix, percussion=False, drum_distance=2,
                     contour_threshold=1):
    """Primary match: tolerant rhythm + near contour; interval edit breaks ties.

    An interval difference never splits pitched patterns with the same rhythm
    and contour. Absolute drum classes retain their existing edit radius,
    because GM percussion numbers are classes rather than melodic pitches.
    """
    if percussion:
        keys = {unit: (True, tuple(e["drum_classes"])) for unit, e in encodings.items()}
        assignments = cluster(list(keys.values()), lambda a, b: edit_distance(a[1], b[1]),
                              drum_distance, prefix)
        return {unit: assignments[key] for unit, key in keys.items()}
    keys = {unit: (e["onset_pattern"], e["contour"], tuple(e["pitch_intervals"]))
            for unit, e in encodings.items()}
    counts = Counter(keys.values())
    seeds, assignments = [], {}
    for value in sorted(counts, key=lambda key: (-counts[key], key)):
        candidates = []
        for index, seed in enumerate(seeds):
            rhythm_distance = normalized_onset_distance(value[0], seed[0])
            contour_distance = edit_distance(value[1], seed[1])
            if contour_distance <= contour_threshold and rhythm_distance <= onset_threshold:
                candidates.append((rhythm_distance, contour_distance,
                                   edit_distance(value[2], seed[2]), index))
        if candidates:
            _, _, interval_distance, index = min(candidates)
        else:
            index, interval_distance = len(seeds), 0
            seeds.append(value)
        assignments[value] = (f"{prefix}{index + 1:03d}", interval_distance)
    return {unit: assignments[key] for unit, key in keys.items()}


def ranges(bars):
    """Human-readable, one-based inclusive bar ranges."""
    groups = []
    for bar in sorted(bars):
        if groups and bar == groups[-1][-1] + 1:
            groups[-1].append(bar)
        else:
            groups.append([bar])
    return ", ".join(str(g[0] + 1) if len(g) == 1 else f"{g[0] + 1}–{g[-1] + 1}" for g in groups)


def repeating_phrases(rows, definitions):
    phrases = []
    for length in (2, 4):
        for kind, key in (("combined", "pattern_id"), ("onset", "onset_pattern_id"), ("pitch", "pitch_pattern_id")):
            candidates = defaultdict(list)
            for first in range(len(rows) - length + 1):
                selected = rows[first:first + length]
                if any(r["pattern_type"] != "regular_bar" for r in selected) or not any(r["active"] for r in selected):
                    continue
                candidates[tuple(r[key] for r in selected)].append(first)
            for sequence, starts in candidates.items():
                # Overlapping sliding windows are not independent repetitions.
                independent = []
                for first in starts:
                    if not independent or first >= independent[-1] + length:
                        independent.append(first)
                if len(independent) < 2:
                    continue
                phrases.append({"instrument": rows[0]["instrument"], "length_bars": length,
                    "kind": kind, "pattern_ids": list(sequence), "occurrence_count": len(independent),
                    "occurrences": [{"start_bar": rows[s]["bar"], "end_bar": rows[s]["bar"] + length}
                                    for s in independent],
                    "mini_notation": " | ".join(definitions[r["pattern_id"]][
                        "onset_mini_notation" if kind == "onset" else "mini_notation"]
                        for r in rows[independent[0]:independent[0] + length])})
    return phrases


def novelty_sections(grid, bar_features, irregular_by_bar, window, minimum, threshold):
    n = len(grid["bars"])
    cuts = {0, n}
    for i in irregular_by_bar:
        cuts.update((i, i + 1))
    mandatory = sorted(cuts)
    curve = []
    for first, last in zip(mandatory, mandatory[1:]):
        if first in irregular_by_bar:
            continue
        values = {}
        for i in range(first + 1, last):
            left, right = Counter(), Counter()
            for j in range(max(first, i - window), i):
                left.update(bar_features[j])
            for j in range(i, min(last, i + window)):
                right.update(bar_features[j])
            # Normalize edge windows so shorter windows do not create novelty.
            nl, nr = min(window, i - first), min(window, last - i)
            tokens = left.keys() | right.keys()
            intersection = sum(min(left[t] / nl, right[t] / nr) for t in tokens)
            union = sum(max(left[t] / nl, right[t] / nr) for t in tokens)
            values[i] = 1 - intersection / union if union else 0.
            curve.append({"bar": i, "time_s": grid["bars"][i]["start_s"], "novelty": values[i]})
        peaks = [i for i, value in values.items() if value >= threshold and value > 0
                 and value > values.get(i - 1, -1) and value >= values.get(i + 1, -1)]
        # Strongest local peaks first, with spacing from both neighbours. Regions
        # bounded by odd bars may themselves be shorter than the minimum.
        selected = [first, last]
        for i in sorted(peaks, key=lambda i: (-values[i], i)):
            if min(abs(i - j) for j in selected) >= minimum:
                selected.append(i)
                cuts.add(i)
    sections = []
    cuts = sorted(cuts)
    scores = {r["bar"]: r["novelty"] for r in curve}
    for first, last in zip(cuts, cuts[1:]):
        odd = [irregular_by_bar[i] for i in range(first, last) if i in irregular_by_bar]
        source = "irregular_bar_boundary" if first in irregular_by_bar or (first - 1) in irregular_by_bar else "novelty_peak"
        if first == 0:
            source = "recording_start"
        sections.append({"rhythmic_section_id": f"r{len(sections) + 1}",
            "start_bar": first, "end_bar": last, "start_s": grid["bars"][first]["start_s"],
            "end_s": grid["bars"][last - 1]["end_s"], "boundary_novelty": scores.get(first),
            "boundary_jaccard": 1 - scores[first] if first in scores else None,
            "boundary_source": source, "irregular_bars": odd, "contains_irregular_bars": bool(odd)})
    return sections, curve


def arpeggio_acceptance(reference, grid, pattern_data, minimum_share=.75,
                        quantized=None, transcription_notes=None, layer_data=None):
    from .reference import evaluate
    return evaluate(reference, grid, pattern_data, minimum_share, quantized,
                    transcription_notes, layer_data)

def run(quantized, grid, threshold=.5, onset_distance=.25, pitch_distance=2, window=2,
        min_section_bars=4, contour_distance=1, layer_notes=None):
    if not 0 <= threshold <= 1:
        raise ValueError("Similarity threshold must be between 0 and 1")
    if not 0 <= onset_distance <= 1 or pitch_distance < 0 or min(window, min_section_bars) < 1:
        raise ValueError("Distances must be nonnegative and window/minimum section length positive")
    base_steps = quantized["steps_per_bar"]
    analysis_notes = layer_notes if layer_notes is not None else quantized["notes"]
    grouped = defaultdict(list)
    instruments = sorted({n["instrument"] for n in analysis_notes})
    for note in analysis_notes:
        grouped[note["instrument"], note["bar"]].append(note)
    definitions, pitch_definitions, bar_rows, runs, metrics = {}, {}, [], [], []
    sets = {b["bar"]: set() for b in grid["bars"]}
    features = {b["bar"]: set() for b in grid["bars"]}
    top_patterns, phrases = [], []
    irregular_bars = [{"bar": bar["bar"], "token": f"odd[{bar['beats_per_bar']}]",
                       "beat_count": bar["beats_per_bar"], "start_s": bar["start_s"],
                       "end_s": bar["end_s"]}
                      for bar in grid["bars"] if bar["beats_per_bar"] != 4]
    irregular_by_bar = {row["bar"]: row for row in irregular_bars}
    regular_bar_count = sum(bar["beats_per_bar"] == 4 for bar in grid["bars"])
    for instrument in instruments:
        source_instrument = instrument.split("__", 1)[0]
        steps = quantized.get("instrument_steps_per_bar", {}).get(source_instrument, base_steps)
        encodings = {b["bar"]: encode_bar(grouped[instrument, b["bar"]], steps, source_instrument == "drums")
                     for b in grid["bars"] if b["bar"] not in irregular_by_bar}
        onset_keys = {b: (onset_count(e["onset_pattern"]) > 1, e["onset_pattern"])
                      for b, e in encodings.items() if onset_count(e["onset_pattern"]) > 1}
        onset_sustain_bars = {b for b, e in encodings.items() if onset_count(e["onset_pattern"]) <= 1}
        onset_clusters = cluster(list(onset_keys.values()), lambda a, b: normalized_onset_distance(a[1], b[1]),
                                 onset_distance, instrument + "_o")
        active_encodings = {b: e for b, e in encodings.items() if b in onset_keys}
        pitch_clusters = sequence_clusters(active_encodings, instrument + "_m")
        shape_clusters = sequence_clusters(active_encodings, instrument + "_s", shape=True)
        current = None
        instrument_runs = []
        instrument_rows = []
        for bar in grid["bars"]:
            selected = grouped[instrument, bar["bar"]]
            if bar["bar"] in irregular_by_bar:
                odd = irregular_by_bar[bar["bar"]]
                instrument_rows.append({"instrument": instrument, "bar": bar["bar"],
                                 "pattern_id": odd["token"], "token": odd["token"],
                                 "pattern_type": "irregular_bar", "irregular_bar": True,
                                 "exact_id": None, "shape_id": None,
                                 "steps_per_bar": steps, "contour": None,
                                 "sustain": False, "onset_count": len({n["step"] for n in selected}),
                                 "active": bool(selected), "note_count": len(selected),
                                 "bar_root_pitch": min(selected, key=lambda n: (n["step"], n["onset_s"], n["pitch"]))["pitch"] if selected else None})
                current = None
                continue
            encoding = encodings[bar["bar"]]
            if bar["bar"] in onset_sustain_bars:
                row = {"instrument": instrument, "bar": bar["bar"], "pattern_id": "sustain",
                       "onset_pattern_id": None, "pitch_pattern_id": None,
                       "exact_id": None, "shape_id": None,
                       "onset_distance_to_representative": None,
                       "pitch_distance_to_representative": None,
                       "pattern_type": "sustain", "sustain": True, "onset_count": onset_count(encoding["onset_pattern"]),
                       "steps_per_bar": steps, "contour": encoding["contour"],
                       "active": bool(selected), "note_count": len(selected), "irregular_bar": False,
                       "bar_root_pitch": min(selected, key=lambda n: (n["step"], n["onset_s"], n["pitch"]))["pitch"] if selected else None}
                instrument_rows.append(row)
                if current and current["pattern_id"] == "sustain":
                    current["end_bar"] = bar["bar"] + 1
                    current["bar_count"] += 1
                else:
                    current = {"instrument": instrument, "pattern_id": "sustain",
                               "start_bar": bar["bar"], "end_bar": bar["bar"] + 1,
                               "bar_count": 1, "mini_notation": encoding["mini_notation"]}
                    instrument_runs.append(current)
                continue
            oid, od = onset_clusters[onset_keys[bar["bar"]]]
            mid, md = pitch_clusters[bar["bar"]]
            sid, sd = shape_clusters[bar["bar"]]
            pid = oid + ":" + mid
            definitions.setdefault(pid, {"pattern_id": pid, "instrument": instrument, **encoding})
            row = {"instrument": instrument, "bar": bar["bar"], "pattern_id": pid,
                   "events": encoding["events"],
                   "onset_pattern_id": oid, "pitch_pattern_id": mid,
                   "exact_id": mid, "shape_id": sid,
                   "shape_distance_to_representative": sd,
                   "onset_distance_to_representative": od, "pitch_distance_to_representative": md,
                   "steps_per_bar": steps, "contour": encoding["contour"],
                   "onset_count": onset_count(encoding["onset_pattern"]), "sustain": False,
                   "active": bool(selected), "note_count": len(selected),
                   "pattern_type": "regular_bar", "irregular_bar": False,
                   "bar_root_pitch": min(selected, key=lambda n: (n["step"], n["onset_s"], n["pitch"]))["pitch"] if selected else None}
            instrument_rows.append(row)
            features[bar["bar"]].update((oid, mid))
            if selected:
                sets[bar["bar"]].add(pid)
            if current and current["pattern_id"] == pid:
                current["end_bar"] = bar["bar"] + 1
                current["bar_count"] += 1
            else:
                current = {"instrument": instrument, "pattern_id": pid,
                           "start_bar": bar["bar"], "end_bar": bar["bar"] + 1,
                           "bar_count": 1, "mini_notation": encoding["mini_notation"]}
                instrument_runs.append(current)
        # Print the modal observed encoding for each combined cluster ID. The
        # first chronological member can be an allowed noisy variant.
        examples = defaultdict(list)
        for row in instrument_rows:
            if row["pattern_type"] == "regular_bar":
                examples[row["pattern_id"]].append((row["bar"], encodings[row["bar"]]))
        for pid, candidates in examples.items():
            counts = Counter((tuple(tuple(c) for c in e["pitch_pattern"]), e["onset_pattern"])
                             for _, e in candidates)
            best_signature = min(counts, key=lambda signature: (-counts[signature],
                next(bar for bar, e in candidates
                     if (tuple(tuple(c) for c in e["pitch_pattern"]), e["onset_pattern"]) == signature)))
            representative_bar, representative = next(
                (bar, e) for bar, e in candidates
                if (tuple(tuple(c) for c in e["pitch_pattern"]), e["onset_pattern"]) == best_signature)
            definitions[pid].update(representative)
            definitions[pid]["representative_bar"] = representative_bar
        pitch_examples = defaultdict(list)
        for row in instrument_rows:
            if row.get("pitch_pattern_id") is not None:
                pitch_examples[row["pitch_pattern_id"]].append((row["bar"], encodings[row["bar"]]))
        for pid, candidates in pitch_examples.items():
            counts = Counter((tuple(tuple(c) for c in e["pitch_pattern"]), e["onset_pattern"])
                             for _, e in candidates)
            best_signature = min(counts, key=lambda signature: (-counts[signature],
                next(bar for bar, e in candidates
                     if (tuple(tuple(c) for c in e["pitch_pattern"]), e["onset_pattern"]) == signature)))
            representative_bar, representative = next(
                (bar, e) for bar, e in candidates
                if (tuple(tuple(c) for c in e["pitch_pattern"]), e["onset_pattern"]) == best_signature)
            pitch_definitions[pid] = {"pitch_pattern_id": pid, "instrument": instrument,
                                      **representative, "representative_bar": representative_bar,
                                      "notation_exemplar_bar": representative_bar,
                                      "clustering_representative_bar": candidates[0][0],
                                      "clustering_representative_events": candidates[0][1]["events"]}
        for item in instrument_runs:
            item["run_mini_notation"] = item["mini_notation"] + (f" × {item['bar_count']} bars" if item["bar_count"] > 1 else "")
        runs.extend(instrument_runs)
        bar_rows.extend(instrument_rows)
        counts = Counter(r["pattern_id"] for r in instrument_rows
                         if r["active"] and not r["irregular_bar"] and r["pattern_type"] == "regular_bar")
        for pid, count in counts.most_common(10):
            if count < 2:
                continue
            matching = [r["bar"] for r in instrument_rows if r["pattern_id"] == pid
                        and r["pattern_type"] == "regular_bar"]
            top_patterns.append({"instrument": instrument, "pattern_id": pid, "bar_count": count,
                "bars": matching, "bar_ranges": ranges(matching),
                "mini_notation": definitions[pid]["mini_notation"],
                "onset_mini_notation": definitions[pid]["onset_mini_notation"]})
        phrases.extend(repeating_phrases(instrument_rows, definitions))
        metrics.append({"instrument": instrument, "raw_bars": len(grid["bars"]),
                        "regular_bars": regular_bar_count,
                        "irregular_bars": len(irregular_bars),
                        "sustain_bars": sum(r["pattern_type"] == "sustain" for r in instrument_rows),
                        "pattern_runs": len(instrument_runs),
                        "compression_ratio": regular_bar_count / len(instrument_runs) if instrument_runs else None})
    sections, novelty = novelty_sections(grid, features, irregular_by_bar, window, min_section_bars, 1 - threshold)
    for section in sections:
        section["active_pattern_ids"] = sorted(set().union(*(sets[i] for i in range(section["start_bar"], section["end_bar"]))))
        section["irregular_bars"] = [irregular_by_bar[i] for i in range(section["start_bar"], section["end_bar"])
                                     if i in irregular_by_bar]
        section["contains_irregular_bars"] = bool(section["irregular_bars"])
        section["instrument_compression"] = []
        for instrument in instruments:
            regular_bars = sum(grid["bars"][i]["beats_per_bar"] == 4
                               for i in range(section["start_bar"], section["end_bar"]))
            count = sum(r["instrument"] == instrument and r["start_bar"] < section["end_bar"] and r["end_bar"] > section["start_bar"] for r in runs)
            section["instrument_compression"].append({"instrument": instrument,
                "raw_bars": section["end_bar"] - section["start_bar"], "regular_bars": regular_bars,
                "irregular_bars": len(section["irregular_bars"]), "pattern_runs": count,
                "compression_ratio": regular_bars / count if count and regular_bars else None})
        if section["end_s"] not in grid["downbeat_times"]:
            section["end_boundary_source"] = "extrapolated_tail"
    repeat_check = []
    for instrument in instruments:
        by_bar = {row["bar"]: row for row in bar_rows if row["instrument"] == instrument
                  and row["pattern_type"] == "regular_bar"}
        adjacent, offset_two, cycles = [], [], []
        for bar in sorted(by_bar):
            current = by_bar[bar].get("pattern_id")
            next_row, two_row = by_bar.get(bar + 1), by_bar.get(bar + 2)
            if current and next_row and next_row.get("pattern_id"):
                if current != "sustain" and current == next_row["pattern_id"]:
                    adjacent.append(bar + 1)
            if current and two_row and two_row.get("pattern_id"):
                if current != "sustain" and current == two_row["pattern_id"]:
                    offset_two.append(bar + 1)
            third, fourth = by_bar.get(bar + 2), by_bar.get(bar + 3)
            following = by_bar.get(bar + 1)
            if (following and third and fourth and current and following.get("pattern_id")
                    and current != "sustain" and following["pattern_id"] != "sustain"
                    and (current, following["pattern_id"]) ==
                    (third.get("pattern_id"), fourth.get("pattern_id"))):
                cycles.append(bar + 1)
        source, layer = instrument.split("__", 1) if "__" in instrument else (instrument, None)
        repeat_check.append({"instrument": source, "layer": layer,
            "adjacent_bar_matches_n_to_n_plus_1": adjacent,
            "adjacent_bar_match_count": len(adjacent),
            "offset_two_bar_matches_n_to_n_plus_2": offset_two,
            "offset_two_bar_match_count": len(offset_two),
            "repeating_two_bar_chord_cycle_starts": cycles,
            "repeating_two_bar_chord_cycle_count": len(cycles),
            "definition": "Uses layer-specific cluster IDs. Bar n versus n+1, bar n versus n+2, and the two-bar pair (n,n+1) versus (n+2,n+3) are counted separately."})
    return {"jaccard_threshold": threshold, "patterns": list(definitions.values()),
            "pitch_pattern_definitions": list(pitch_definitions.values()),
            "clustering": {"onset_normalized_symmetric_difference_threshold": onset_distance, "pitch_edit_threshold": pitch_distance,
                "method": "chronological greedy event-sequence clustering; first eligible fixed representative",
                "pattern_similarity_threshold": PATTERN_SIMILARITY_THRESHOLD,
                "onset_tolerance_steps": ONSET_TOLERANCE_STEPS,
                "onset_distance": "symmetric difference of onset steps / union of onset steps; bars with <=1 onset tagged sustain and excluded from ranking",
                "pitch_primary_key": ["quantized_onset_step", "absolute_pitch"],
                "contour_edit_threshold": None,
                "pitched_interval_edit_threshold_applied": False,
                "interval_edit_role": "not used for clustering; retained for notation",
                "pitch_sequence": "ordered (quantized step, pitch) events; insert/delete/substitute cost 1; normalize by longer sequence",
                "exact_id": "absolute pitch; used for acceptance",
                "shape_id": "pitch relative to lowest bass/root in unit; separate exploratory ID"},
            "sectioning": {"window_bars": window, "minimum_section_bars": min_section_bars,
                "novelty_threshold": 1 - threshold, "similarity": "mean-normalized multiset Jaccard of onset/pitch cluster IDs"},
            "novelty_curve": novelty, "top_patterns": top_patterns, "repeating_phrases": phrases,
            "bars": bar_rows, "runs": runs, "instrument_compression": metrics,
            "irregular_bars": irregular_bars,
            "rhythmic_sections": sections,
            "two_bar_repeat_unit_check": repeat_check,
            "compression_definition": "regular 4-beat bars divided by consecutive regular-bar pattern runs; irregular bars are standalone tokens and excluded"}
