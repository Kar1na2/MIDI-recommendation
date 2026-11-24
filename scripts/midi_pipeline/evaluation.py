"""One-to-one event evaluation against independent, complete synthetic labels."""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

from .store import load_run, save


def match(reference, predicted, pitch=True, tolerance=.05):
    if not reference or not predicted:
        return []
    distance = np.array([[abs(a["onset"] - b["onset"]) for b in predicted] for a in reference])
    allowed = distance <= tolerance
    if pitch:
        allowed &= np.array([[a["pitch"] == b["pitch"] and a.get("is_drum", False) == b.get("is_drum", False)
                              and ("program" not in a or a["program"] == b.get("program"))
                              for b in predicted] for a in reference])
    # A forbidden edge outweighs all allowed edges combined: maximize match count first.
    cost = np.where(allowed, distance, 1e6)
    ii, jj = linear_sum_assignment(cost)
    return [(int(i), int(j)) for i, j in zip(ii, jj) if allowed[i, j]]


def rates(hits, reference_count, prediction_count):
    if not reference_count and not prediction_count:
        return {"precision": None, "recall": None, "f1": None, "true_positive": 0,
                "false_positive": 0, "missing": 0, "status": "no_positive_events"}
    precision = hits / prediction_count if prediction_count else None
    recall = hits / reference_count if reference_count else None
    return {"precision": precision, "recall": recall,
            "f1": (2 * precision * recall / (precision + recall)
                   if precision is not None and recall is not None and precision + recall else 0.),
            "true_positive": hits, "false_positive": prediction_count - hits, "missing": reference_count - hits}


def score(reference, prediction):
    if reference.get("label_source") != "synthetic_midi_render" or not reference.get("complete_notes"):
        raise ValueError("This evaluator requires complete independent synthetic labels")
    ref = reference["notes"]
    pred = [n for n in prediction["notes"] if n["in_source_window"]]
    pairs = match(ref, pred)
    attacks = match(ref, pred, pitch=False)
    onset_errors = [abs(ref[i]["onset"] - pred[j]["onset"]) for i, j in pairs]
    offset_errors = [abs(ref[i]["offset"] - pred[j]["offset"]) for i, j in pairs]
    def mean(values):
        return float(np.mean(values)) if values else None
    offset_hits = sum(abs(ref[i]["offset"] - pred[j]["offset"]) <= .05 for i, j in pairs)
    event_rate = rates(len(pairs), len(ref), len(pred))
    return {"note_onset_pitch_program": event_rate, "note_onset": event_rate,
            "attack_onset": rates(len(attacks), len(ref), len(pred)),
            "note_offset_after_matched_onset_pitch_program": rates(offset_hits, len(ref), len(pred)),
            "pitch_correctness_given_onset_match": mean([float(ref[i]["pitch"] == pred[j]["pitch"]) for i, j in attacks]),
            "program_correctness_given_onset_match": mean([float(ref[i].get("program") == pred[j].get("program"))
                                                           for i, j in attacks if "program" in ref[i]]),
            "mean_absolute_onset_error_seconds": mean(onset_errors),
            "mean_absolute_offset_error_seconds": mean(offset_errors),
            "onset_tolerance_seconds": .05,
            "offset_tolerance_seconds": .05,
            "offset_metric_scope": "only pitch+onset matched notes; does not hide missing notes",
            "matches": [{"reference_index": i, "prediction_note_id": pred[j]["id"]} for i, j in pairs]}


def motif_score(reference, analysis, kind):
    expected = reference["target_motif_occurrence_start_pairs"]
    key = "rhythm_candidates" if kind == "rhythm" else "note_sequence_candidates"
    # Cross-track instrumental repeats may be absent; disclose rather than pool identities.
    predictions = sorted({tuple(round(span[0], 2) for span in candidate["spans"])
                          for track in analysis["tracks"] for candidate in track[key]})
    # Collapse near-identical start pairs for a target-level, rather than subphrase-level metric.
    unique = []
    for pair in predictions:
        if not any(max(abs(a - b) for a, b in zip(pair, other)) <= .2 for other in unique):
            unique.append(pair)
    remaining = list(expected)
    hits = 0
    for pair in unique:
        for item in remaining:
            if max(abs(a - b) for a, b in zip(pair, item)) <= .2:
                remaining.remove(item)
                hits += 1
                break
    return {**rates(hits, len(expected), len(unique)), "start_tolerance_seconds": .2,
            "scope": "target six-note motif start pairs, per predicted track; other discovered submotifs count as off-target",
            "predicted_start_pairs": unique, "reference_start_pairs": expected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output path")
    _, notes = load_run(args.run)
    reference = json.loads(args.reference.read_text())
    result = score(reference, notes)
    analysis = json.loads((args.run / "analysis.json").read_text())
    result["rhythm_pattern"] = motif_score(reference, analysis, "rhythm")
    result["motif_recurrence"] = motif_score(reference, analysis, "notes")
    save(args.output, result)


if __name__ == "__main__":
    main()
