"""Stage 6: Genius labels with manual, synced-line or vocal-aligned timings."""
from collections import Counter
from difflib import SequenceMatcher
import csv
import math
from pathlib import Path
import re
import subprocess
import sys

from . import genius, lrclib
from .beats import bar_position, snap
from .common import StageError, read, save, warning
from .line_alignment import align_lines


def paragraph_groups(plain, paragraphs):
    """Return stable A/B/... similarity groups for repeated lyric paragraphs."""
    raw = [x.strip() for x in re.split(r"\n\s*\n", plain or "") if x.strip()]
    normalized = [re.sub(r"[^\w]", "", x.casefold()) for x in raw]
    groups, assignments = [], []
    for text in normalized:
        found = next((i for i, exemplar in enumerate(groups)
                      if SequenceMatcher(None, text, exemplar).ratio() >= .82), None)
        if found is None:
            found = len(groups); groups.append(text)
        assignments.append(found)
    counts = Counter(assignments)
    chorus = max(counts, key=lambda x: (counts[x], -x)) if counts else None
    result = []
    for i, paragraph in enumerate(paragraphs):
        group = assignments[i] if i < len(assignments) else i
        result.append({**paragraph, "group": chr(65 + group) if group < 26 else f"G{group+1}",
                       "likely_chorus": group == chorus, "text": raw[i] if i < len(raw) else ""})
    return result


def paragraph_agreement(sections, paragraphs, lines):
    """Annotate Genius section boundaries without altering their timestamps."""
    starts = [p["start_s"] for p in paragraphs[1:]]
    lrc_starts = [line["start_s"] for line in lines]
    for section in sections:
        boundary = section.get("raw_start_s")
        nearest = min(range(len(lrc_starts)), key=lambda i: abs(lrc_starts[i] - boundary)) if boundary is not None and lrc_starts else None
        section["paragraph_agreement"] = bool(nearest is not None and any(
            abs(i - nearest) <= 1 for i, start in enumerate(lrc_starts) if any(abs(start-p) < .001 for p in starts)))
    total = len(sections)
    matched = sum(s["paragraph_agreement"] for s in sections)
    return {"agreements": matched, "boundaries": total,
            "rate": matched / total if total else None}


def manual_sections(path, source, duration):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not {"label", "start_s", "end_s"}.issubset(reader.fieldnames or []):
            raise StageError("Manual CSV requires label,start_s,end_s")
        rows = list(reader)
    if not rows:
        raise StageError("Manual CSV is empty")
    result, counts, previous_end, source_cursor = [], Counter(), 0., 0
    for row in rows:
        a, b = float(row["start_s"]), float(row["end_s"])
        if not all(math.isfinite(x) for x in (a, b)) or not 0 <= a < b <= duration or a < previous_end:
            raise StageError("Manual sections must be ordered, nonoverlapping and inside the audio")
        header = genius.normalize_header(row["label"])
        counts[header["label"]] += 1
        item = {**header, "number": counts[header["label"]], "raw_start_s": a,
                "raw_end_s": b, "timing_source": "manual_sections_csv", "match_confidence": 1.,
                "flags": [], "genius_section_index": None}
        if header["label"] != "Instrumental" and source is not None:
            if source_cursor >= len(source) or source[source_cursor]["label"] != header["label"]:
                raise StageError("Manual CSV labels must preserve every Genius section in order (Instrumental rows may be added)")
            item.update({k: source[source_cursor][k] for k in ("performers", "number", "genius_section_index")})
            source_cursor += 1
        elif header["label"] != "Instrumental" and source is None:
            item["flags"].append("genius_labels_unverified")
        result.append(item)
        previous_end = b
    if source is not None and source_cursor != len(source):
        raise StageError("Manual CSV omits Genius sections; correct it rather than borrowing model times")
    return result


def timed_sections(source, lines, matches, timing_source, duration, confidence_threshold=.65):
    flattened = [(i, line) for i, s in enumerate(source) for line in s["lines"]]
    by_source = {m["source_index"]: m for m in matches}
    result = []
    for i, section in enumerate(source):
        indices = [j for j, (si, _) in enumerate(flattened) if si == i]
        found = [(j, by_source[j]) for j in indices if j in by_source and
                 lines[by_source[j]["target_index"]].get("start_s") is not None]
        confidence = sum(m["similarity"] for _, m in found) / max(1, len(indices))
        first = found[0] if found else None
        # First matched line is used, but a missing actual first line is flagged.
        start = lines[first[1]["target_index"]]["start_s"] if first else None
        flags = []
        if not found:
            flags.append("no_matched_lines")
        elif first[0] != indices[0]:
            flags.append("first_source_line_unmatched")
        if confidence < confidence_threshold:
            flags.append("low_match_confidence")
        result.append({k: section[k] for k in ("label", "number", "performers", "genius_section_index")} |
                      {"raw_start_s": start, "raw_end_s": None,
                       "timing_source": timing_source, "match_confidence": confidence,
                       "match_method": ("romanized" if any(m.get("match_method") == "romanized"
                                        for _, m in found) else "direct") if found else None,
                       "matched_lines": len(found), "source_line_count": len(indices), "flags": flags})
    for i, section in enumerate(result):
        next_start = result[i + 1]["raw_start_s"] if i + 1 < len(result) else duration
        if section["raw_start_s"] is not None and next_start is not None and next_start > section["raw_start_s"]:
            section["raw_end_s"] = next_start
        else:
            section["flags"].append("missing_or_nonincreasing_section_boundary")
    return result


def vocal_silences(vocals, grid):
    """Conservative long near-silence spans, measured on the vocal stem.

    LRC provides starts only. It cannot by itself establish vocal-free gaps.
    Use <= -45 dB relative frame RMS as silence evidence, with 100 ms margins.
    """
    import numpy as np
    import soundfile as sf
    from statistics import median
    y, sr = sf.read(vocals, always_2d=True, dtype="float32")
    y = y.mean(axis=1)
    width = max(1, round(sr * .05))
    usable = len(y) // width * width
    if not usable:
        return []
    rms = np.sqrt(np.mean(y[:usable].reshape(-1, width) ** 2, axis=1))
    peak = float(np.max(rms))
    if peak <= 1e-8:
        raise StageError("Vocal stem is effectively silent; lyric timing/source recording needs review")
    quiet = rms < peak * 10 ** (-45 / 20)
    changes = np.diff(np.r_[False, quiet, False].astype(int))
    starts, ends = np.where(changes == 1)[0], np.where(changes == -1)[0]
    bar_seconds = median([b["end_s"] - b["start_s"] for b in grid["bars"]])
    return [(max(0., a * .05 + .1), min(grid["duration_s"], b * .05 - .1))
            for a, b in zip(starts, ends) if (b - a) * .05 - .2 > 2 * bar_seconds]


def add_instrumentals(sections, gaps):
    """Split timed lyric regions at measured long vocal silences; retain source IDs."""
    result = []
    for section in sections:
        a, b = section["raw_start_s"], section["raw_end_s"]
        if a is None or b is None:
            result.append(section)
            continue
        cuts = [(max(a, x), min(b, y)) for x, y in gaps if x < b and y > a]
        cursor, fragment = a, 0
        for x, y in cuts:
            if x > cursor:
                result.append({**section, "raw_start_s": cursor, "raw_end_s": x, "fragment": fragment})
                fragment += 1
            result.append({"label": "Instrumental", "number": 0, "performers": [],
                           "raw_start_s": x, "raw_end_s": y, "timing_source": "vocal_stem_silence",
                           "match_confidence": None, "flags": ["vocal_stem_silence_proxy"],
                           "genius_section_index": None})
            cursor = y
        if cursor < b or not cuts:
            result.append({**section, "raw_start_s": cursor, "fragment": fragment})
        elif not any(s.get("genius_section_index") == section.get("genius_section_index") and s["label"] != "Instrumental" for s in result):
            # Do not erase a Genius header whose entire estimated span is silent.
            result.append({**section, "flags": section["flags"] + ["timed_section_is_vocally_silent"]})
    counts = Counter()
    for s in result:
        if s["label"] == "Instrumental":
            counts["Instrumental"] += 1
            s["number"] = counts["Instrumental"]
    return result


def decorate(sections, grid):
    for section in sections:
        a, b = section["raw_start_s"], section["raw_end_s"]
        if a is None or b is None:
            section.update(snapped_start_s=None, snapped_end_s=None, start_bar=None, end_bar=None, status="failed")
            continue
        sa, sb = snap(grid, a), snap(grid, b)
        section.update(snapped_start_s=sa, snapped_end_s=sb,
                       start_bar=round(bar_position(grid, sa)), end_bar=round(bar_position(grid, sb)))
        if sb <= sa:
            section["flags"].append("collapsed_after_downbeat_snap")
        section["status"] = "review" if section["flags"] else "complete"
        section["display_label"] = f"{section['label']} {section['number']}"
    return sections


def run_legacy(song, output, grid, separation, force=False, device="cpu", whisperx_python=None, confidence_threshold=.65):
    warnings = []
    duration = grid["duration_s"]
    if song.get("manual_sections_csv"):
        # Invalid manual truth is an explicit error, never replaced with a model.
        sections = manual_sections(song["manual_sections_csv"], None, duration)
        try:
            parsed = genius.fetch(song, output, force)
        except Exception as error:
            warning(f"Genius unavailable ({error}); manual CSV timings remain authoritative, source-label coverage is unverified.", warnings)
            parsed = {"source_url": song.get("genius_url"), "sections": None}
        source = parsed["sections"]
        if source is not None:
            sections = manual_sections(song["manual_sections_csv"], source, duration)
        timing_source = "manual_sections_csv"
    else:
        try:
            parsed = genius.fetch(song, output, force)
        except Exception as error:
            parsed = {"source_url": song.get("genius_url"), "sections": None, "romanized_only": False}
            warning(f"Genius unavailable ({type(error).__name__}: {error}); using unverified LRCLIB paragraph segments.", warnings)
            lrc = lrclib.fetch(song, duration, output, force)
            paragraphs = paragraph_groups(lrc["plain_lyrics"], lrc["lrclib_paragraphs"])
            sections = [{"label": "Unlabeled", "number": i + 1, "performers": [],
                         "raw_start_s": p["start_s"], "raw_end_s": p["end_s"],
                         "timing_source": "lrclib_paragraphs", "match_confidence": None,
                         "flags": ["labels_unverified"], "genius_section_index": None,
                         "group": p["group"], "likely_chorus": p["likely_chorus"],
                         "line_count": p["line_count"]} for i, p in enumerate(paragraphs)]
            decorated = decorate(sections, grid)
            save(Path(output) / "lyrics.json", {"status": "complete", "labels": "unverified",
                 "lrclib_paragraphs": lrc["lrclib_paragraphs"], "paragraph_groups": paragraphs,
                 "paragraph_agreement": None, "sections": decorated, "warnings": warnings})
            return {"source_url": parsed["source_url"], "genius_section_count": None,
                    "timing_source": "lrclib_paragraphs", "labels": "unverified",
                    "lrclib_paragraphs": lrc["lrclib_paragraphs"], "paragraph_groups": paragraphs,
                    "paragraph_agreement": None, "warnings": warnings, "sections": decorated}
        source = parsed["sections"]
        flat = [line for section in source for line in section["lines"]]
        try:
            lrc = lrclib.fetch(song, duration, output, force)
            paragraphs = lrc["lrclib_paragraphs"]
            lines = lrc["lines"]
            agreement = None
            mismatch = parsed["romanized_only"] and any(re.search(r"[가-힣ぁ-ゟ゠-ヿ一-龯]", l["text"]) for l in lines)
            if mismatch:
                warning(f"Romanized-only Genius source: language-aware {song.get('language')} romanization used for ordered matching.", warnings)
            matches = align_lines(flat, [l["text"] for l in lines], romanize=mismatch,
                                  language=song.get("language"))
            candidate = timed_sections(source, lines, matches, "lrclib", duration, confidence_threshold)
            agreement = paragraph_agreement(candidate, paragraphs, lines)
            if any(s["raw_start_s"] is None or s["raw_end_s"] is None or
                   s["match_confidence"] < confidence_threshold or "first_source_line_unmatched" in s["flags"] for s in candidate):
                raise StageError("LRCLIB sequence alignment cannot confidently time every section")
            sections, timing_source = candidate, "lrclib"
        except Exception as error:
            warning(f"LRCLIB rejected/unavailable ({type(error).__name__}: {error}); falling back to WhisperX vocal forced alignment.", warnings)
            save(Path(output) / "lyric_attempts.json", {"warnings": warnings,
                 "rejected_source": "lrclib", "reason": str(error), "next_source": "whisperx"})
            # Preserve a failed LRCLIB alignment for review if there was one.
            if "candidate" in locals():
                save(Path(output) / "lrclib_alignment.json", {"sections": candidate, "matches": matches,
                     "lrclib_paragraphs": paragraphs, "paragraph_agreement": agreement})
            if parsed["romanized_only"]:
                raise StageError("WhisperX requires original-script Genius lyrics; romanized-only CTC alignment is untrustworthy") from error
            language = song.get("language")
            if not language:
                text = " ".join(flat)
                language = "ko" if re.search(r"[가-힣]", text) else "ja" if re.search(r"[ぁ-ゟ゠-ヿ]", text) else None
            if not language:
                raise StageError("Set manifest language for the WhisperX language-specific alignment model")
            if not separation or not separation.get("stems", {}).get("vocals"):
                raise StageError("WhisperX timing fallback requires successful source separation and its vocals stem")
            request = Path(output) / "whisperx_request.json"
            target = Path(output) / "whisperx.json"
            payload = {"vocals": separation["stems"]["vocals"], "lines": flat,
                       "language": language, "device": device,
                       "vocal_sha256": separation.get("stem_sha256", {}).get("vocals")}
            same_request = request.exists() and read(request) == payload
            if not target.exists() or force or not same_request:
                save(request, payload)
                command = [whisperx_python or sys.executable, "-m", "pipeline.whisperx_runner",
                           "--request", str(request.resolve()), "--output", str(target.resolve())]
                result = subprocess.run(command, capture_output=True, text=True)
                if result.returncode:
                    raise StageError("WhisperX forced alignment failed; install the separately pinned aligner and pass --whisperx-python. " + result.stderr[-800:])
            aligned = read(target)
            lines = aligned["lines"]
            matches = [{"source_index": i, "target_index": i, "similarity": l["confidence"]} for i, l in enumerate(lines)]
            sections = timed_sections(source, lines, matches, "whisperx", duration, confidence_threshold)
            timing_source = "whisperx"
        gaps = (vocal_silences(separation["stems"]["vocals"], grid)
                if separation and separation.get("stems", {}).get("vocals") else [])
        # Prefix/suffix without any aligned lyric is included only when measured silent.
        if sections and sections[0]["raw_start_s"] is not None:
            first = sections[0]["raw_start_s"]
            for a, b in gaps:
                if a < first and b <= first:
                    sections.insert(0, {"label": "Instrumental", "number": 0, "performers": [],
                        "raw_start_s": a, "raw_end_s": b, "timing_source": "vocal_stem_silence",
                        "match_confidence": None, "flags": ["vocal_stem_silence_proxy"], "genius_section_index": None})
                    break
        sections = add_instrumentals(sections, gaps)
    if source is not None and "lrc" in locals():
        genius_sections = [section for section in sections if section.get("genius_section_index") is not None]
        agreement = paragraph_agreement(genius_sections, lrc["lrclib_paragraphs"], lrc["lines"])
    decorated = decorate(sections, grid)
    if source is not None and "agreement" not in locals():
        agreement = paragraph_agreement(decorated, [], [])
    for section in decorated:
        if section["flags"]:
            warning(f"{section['label']} {section['number']}: {', '.join(section['flags'])}", warnings)
    result = {"source_url": parsed["source_url"], "genius_section_count": len(source) if source is not None else None,
            "timing_source": timing_source, "warnings": warnings,
            "confidence_threshold": confidence_threshold, "sections": decorated}
    if "lrc" in locals():
        result.update(labels="verified", lrclib_paragraphs=lrc.get("lrclib_paragraphs", []),
                      paragraph_agreement=agreement)
        save(Path(output) / "lyrics.json", {"status": "complete", **result})
    elif source is not None:
        result["paragraph_agreement"] = agreement
    return result


def run(song, output, grid, separation, force=False, device="cpu", whisperx_python=None, confidence_threshold=.65):
    if confidence_threshold != .65:
        raise StageError("Core lyric alignment confidence is fixed at 0.65")
    from .lyric_backend import run as run_core
    return run_core(song, output, grid, separation)