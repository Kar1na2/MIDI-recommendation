"""Official artist-first Genius lookup and per-section vocal-stem validation."""
from pathlib import Path
import subprocess

from . import genius, genius_matching
from .common import read, save, StageError
from .config import LYRIC_ALIGNMENT_CONFIDENCE
from .lyric_validation import diagnose_sections, per_section_ring


def lookup(song, output):
    output = Path(output)
    if song.get("cached_reference") == "aespa-attitude":
        return genius.fetch({**song, "id": "aespa-attitude",
            "genius_url": "https://genius.com/Aespa-attitude-lyrics"}, output)
    result = genius_matching.lookup(song, genius._access_token(),
                                    Path("pilot/lyrics_rework/search_cache"))
    save(output / "genius_candidates.json", result)
    if not result["source_url"]:
        raise StageError(result["reason"])
    url = result["source_url"]
    cached = output / "genius_sections.json"
    if cached.is_file() and read(cached).get("source_url") == url:
        return read(cached)
    sections = genius.parse_html(genius._request_page(url))
    parsed = {"sections": sections, "source_url": url}
    save(cached, parsed)
    return parsed


def run(song, output, grid, stems, whisperx_python="/home/usr/venvs/whisperx/bin/python",
        device="cpu"):
    output = Path(output)
    lookup_record = output / "pilot_genius.json"
    reason, parsed = None, None
    try:
        if lookup_record.is_file() and read(lookup_record).get("status") != "failed":
            parsed = read(lookup_record)
        else:
            parsed = {"status": "ok", **lookup(song, output)}
            save(lookup_record, parsed)
    except Exception as error:
        reason = f"{type(error).__name__}: {error}"
        save(lookup_record, {"status": "failed", "reason": reason})
    sections = []
    if parsed:
        flat = [line for section in parsed["sections"] for line in section["lines"]]
        request, target = output / "pilot_whisperx_request.json", output / "pilot_whisperx.json"
        payload = {"vocals": stems["vocals"], "lines": flat,
                   "language": song["language"], "device": device}
        previous = read(request) if request.is_file() else {}
        same_source = all(previous.get(key) == payload[key] for key in ("vocals", "lines", "language"))
        try:
            if not target.is_file() or not same_source:
                save(request, payload)
                process = subprocess.run([whisperx_python, "-m", "pipeline.whisperx_runner",
                    "--request", str(request), "--output", str(target)],
                    capture_output=True, text=True, timeout=900)
                if process.returncode:
                    raise StageError("Vocal-stem alignment failed: " + process.stderr[-700:])
            sections = diagnose_sections(parsed["sections"], read(target)["lines"],
                                         grid["duration_s"], LYRIC_ALIGNMENT_CONFIDENCE)
        except Exception as error:
            reason = f"{type(error).__name__}: {error}"
    ring = per_section_ring(sections, grid["duration_s"], stems["vocals"])
    valid = sum(section["validated"] for section in sections)
    return {**ring, "sections": sections,
            "genius_section_count": len(parsed["sections"]) if parsed else 0,
            "validated_section_count": valid, "validated": bool(sections) and valid == len(sections),
            "source_url": parsed.get("source_url") if parsed else None,
            "confidence_threshold": LYRIC_ALIGNMENT_CONFIDENCE,
            "labels": "per_section" if valid else "activity_only", "reason": reason}
