"""Per-section forced-alignment validation and measured vocal-activity fallback."""
import math
from collections import Counter

import numpy as np

from .config import LYRIC_ALIGNMENT_CONFIDENCE


def _timed(line, duration):
    a, b = line.get("start_s"), line.get("end_s")
    return (a is not None and b is not None and math.isfinite(a) and math.isfinite(b)
            and 0 <= a < b <= duration)


def diagnose_sections(source, lines, duration, confidence=LYRIC_ALIGNMENT_CONFIDENCE):
    """Known text maps one-to-one to WhisperX lines; no guessed timestamps."""
    sections, cursor, counts = [], 0, Counter()
    for source_index, section in enumerate(source):
        count = len(section.get("lines", []))
        selected = lines[cursor:cursor+count]
        timed = [(i, line) for i, line in enumerate(selected) if _timed(line, duration)]
        reasons = []
        if not count:
            reasons.append("section_has_no_lyric_lines")
        if count and not timed:
            reasons.append("no_timed_evidence_recording_presence_undetermined")
        if timed and timed[0][0] != 0:
            reasons.append("first_source_line_unmatched")
        score = sum(float(line.get("confidence") or 0) for _, line in timed) / max(1, count)
        if score < confidence:
            reasons.append("low_confidence")
        starts = [line["start_s"] for _, line in timed]
        if any(b < a for a, b in zip(starts, starts[1:])):
            reasons.append("out_of_order_times")
        if any(line.get("start_s") is not None and not _timed(line, duration) for line in selected):
            reasons.append("invalid_line_interval")
        counts[section["label"]] += 1
        sections.append({"label": section["label"], "number": section.get("number", counts[section["label"]]),
            "display_label": section.get("display_label", f"{section['label']} {counts[section['label']]}"),
            "genius_section_index": source_index, "source_line_count": count,
            "matched_lines": len(timed), "match_confidence": score,
            "raw_start_s": timed[0][1]["start_s"] if timed else None,
            "last_aligned_line_end_s": max((line["end_s"] for _, line in timed), default=None),
            "raw_end_s": None, "timing_source": "whisperx_vocal_stem",
            "failure_reasons": reasons, "source_lines": section.get("lines", []),
            "line_diagnostics": [{"index": cursor+i, **line} for i, line in enumerate(selected)]})
        cursor += count
    previous_occurrences = {}
    for index, section in enumerate(sections):
        start, own_end = section["raw_start_s"], section["last_aligned_line_end_s"]
        next_start = sections[index+1]["raw_start_s"] if index+1 < len(sections) else duration
        # A missing following section must not erase this section's measured end.
        if start is not None and own_end is not None:
            section["raw_end_s"] = next_start if next_start is not None and next_start > start else own_end
            if next_start is not None and next_start < start:
                section["failure_reasons"].append("out_of_order_section_times")
            if next_start is not None and own_end > next_start + 1e-6:
                section["failure_reasons"].append("overlapping_sections")
        if start is None or section["raw_end_s"] is None:
            section["failure_reasons"].append("missing_section_interval")
        text_key = tuple(section["source_lines"])
        prior = previous_occurrences.get(text_key)
        if text_key and prior and start is not None and prior["raw_start_s"] is not None:
            if start <= prior["raw_start_s"] or start < (prior["last_aligned_line_end_s"] or 0):
                section["failure_reasons"].append("repeated_section_aligned_to_wrong_occurrence")
        if text_key:
            previous_occurrences[text_key] = section
        section["failure_reasons"] = list(dict.fromkeys(section["failure_reasons"]))
        section["validated"] = not section["failure_reasons"]
        section["flags"] = list(section["failure_reasons"])
    return sections


def vocal_activity(vocals_path, duration, minimum_s=2.0, relative_db=-35.0):
    import soundfile as sf
    audio, sample_rate = sf.read(vocals_path, dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    frame_s = .1
    width = max(1, round(sample_rate * frame_s))
    rms = np.array([float(np.sqrt(np.mean(mono[i:i+width] ** 2)))
                    for i in range(0, len(mono), width)])
    threshold = max(1e-5, float(np.max(rms, initial=0)) * 10 ** (relative_db/20))
    active = rms >= threshold
    runs = []
    for index, value in enumerate(active):
        a, b = index * frame_s, min(duration, (index+1) * frame_s)
        if a >= duration:
            break
        if runs and runs[-1]["active"] == bool(value):
            runs[-1]["end"] = b
        else:
            runs.append({"start": a, "end": b, "active": bool(value)})
    if not runs:
        runs = [{"start": 0.0, "end": duration, "active": False}]
    runs[-1]["end"] = duration
    while len(runs) > 1:
        short = next((i for i, row in enumerate(runs) if row["end"]-row["start"] < minimum_s-1e-9), None)
        if short is None:
            break
        if 0 < short < len(runs)-1 and runs[short-1]["active"] == runs[short+1]["active"]:
            runs[short-1]["end"] = runs[short+1]["end"]
            del runs[short:short+2]
        elif short == 0:
            runs[1]["start"] = runs[0]["start"]; del runs[0]
        else:
            runs[short-1]["end"] = runs[short]["end"]; del runs[short]
    for index, row in enumerate(runs):
        row.update(index=index, label="vocals" if row.pop("active") else "no vocals",
                   source="vocal_activity")
    return {"segments": runs, "source": "vocal_activity", "minimum_segment_s": minimum_s,
            "rms_frame_s": frame_s, "relative_threshold_db": relative_db,
            "absolute_rms_threshold": threshold, "labels": "activity_only"}


def per_section_ring(sections, duration, vocals_path):
    valid = [section for section in sections if section.get("validated")]
    if not valid:
        fallback = vocal_activity(vocals_path, duration)
        return {"ring1_segments": fallback["segments"], "source": "vocal_activity",
                "activity_policy": {key: value for key, value in fallback.items() if key != "segments"}}
    result, cursor = [], 0.0
    for section in sorted(valid, key=lambda row: row["raw_start_s"]):
        a, b = section["raw_start_s"], section["raw_end_s"]
        if a < cursor - 1e-6:
            continue
        if a > cursor + 1e-6:
            result.append({"start": cursor, "end": a, "label": "unaligned", "source": "unaligned"})
        result.append({"start": a, "end": b, "label": section["display_label"],
            "source": "genius_per_section", "genius_section_index": section["genius_section_index"],
            "alignment_confidence": section["match_confidence"]})
        cursor = b
    if cursor < duration:
        result.append({"start": cursor, "end": duration, "label": "unaligned", "source": "unaligned"})
    for index, segment in enumerate(result):
        segment["index"] = index
    return {"ring1_segments": result, "source": "genius_per_section"}
