"""Direct period comparisons, independent of greedy cluster identity."""
from .config import PATTERN_SIMILARITY_THRESHOLD, PERIOD_ACCEPTANCE_RATE, ONSET_TOLERANCE_STEPS
from .patterns import event_similarity, event_differences


def period_match(sequences, labels=None):
    labels = labels or [str(i+1) for i in range(len(sequences))]
    candidates = []
    for period in range(1, len(sequences) // 2 + 1):
        pairs = []
        for n in range(len(sequences) - period):
            a, b = sequences[n], sequences[n+period]
            # Compare every observed event sequence, including a single-note
            # unit. Empty/sustain units have no pitch evidence and count as
            # nonmatches rather than as identical empty patterns.
            eligible = bool(a) and bool(b)
            similarity = event_similarity(a, b) if eligible else None
            matched = eligible and similarity + 1e-12 >= PATTERN_SIMILARITY_THRESHOLD
            pairs.append({"a_unit": labels[n], "b_unit": labels[n+period],
                          "similarity": similarity, "matched": bool(matched),
                          "reason": None if eligible else "empty or sustain unit; counted as nonmatch",
                          "differences": event_differences(a, b) if eligible and not matched else []})
        count = sum(p["matched"] for p in pairs)
        candidates.append({"period": period, "matched_pairs": count, "comparison_count": len(pairs),
                           "match_rate": count / len(pairs), "pairs": pairs})
    # Prefer the shorter period on ties; do not choose by an external score.
    best = max(candidates, key=lambda r: (r["match_rate"], -r["period"]), default=None)
    return {"period": best["period"] if best else None,
            "match_rate": best["match_rate"] if best else 0.,
            "matched_pairs": best["matched_pairs"] if best else 0,
            "comparison_count": best["comparison_count"] if best else 0,
            "pairs": best["pairs"] if best else [], "candidates": candidates,
            "pass": bool(best) and best["match_rate"] >= PERIOD_ACCEPTANCE_RATE,
            "similarity_threshold": PATTERN_SIMILARITY_THRESHOLD,
            "acceptance_rate": PERIOD_ACCEPTANCE_RATE, "onset_tolerance_steps": ONSET_TOLERANCE_STEPS,
            "denominator": "N-p chronological comparisons; no wrapping; empty/sustain units count as nonmatches; single-event units are compared",
            "reason": None if best else "fewer than two eligible timeline units"}
