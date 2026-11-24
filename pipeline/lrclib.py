"""Duration-checked LRCLIB synced lyrics and stable LRC timestamp parsing."""
import json
import math
from pathlib import Path
import re
from urllib.parse import urlencode

from .common import StageError, get, read, save


def parse_lrc(text):
    lines = []
    offset = 0.
    match = re.search(r"\[offset:([+-]?\d+)\]", text, re.I)
    if match:
        offset = int(match[1]) / 1000
    for line in text.splitlines():
        tags = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", line)
        content = re.sub(r"\[[^\]]*\]", "", line).strip()
        if not content:
            continue
        for minutes, seconds in tags:
            start = int(minutes) * 60 + float(seconds) + offset
            if start >= 0 and math.isfinite(start):
                lines.append({"text": content, "start_s": start})
    return sorted(lines, key=lambda x: x["start_s"])


def parse_paragraphs(plain_lyrics, synced_lines, duration):
    """Split plain lyrics on blank lines and align paragraph extents to LRC lines."""
    paragraphs, current = [], []
    for line in (plain_lyrics or "").splitlines():
        if line.strip():
            current.append(line.strip())
        elif current:
            paragraphs.append(current); current = []
    if current:
        paragraphs.append(current)
    if len(paragraphs) <= 1:
        return []
    from .line_alignment import align_lines
    plain = [line for paragraph in paragraphs for line in paragraph]
    matches = align_lines(plain, [line["text"] for line in synced_lines])
    by_source = {m["source_index"]: synced_lines[m["target_index"]]["start_s"] for m in matches}
    bounds, cursor = [], 0
    for paragraph in paragraphs:
        indices = range(cursor, cursor + len(paragraph))
        starts = [by_source[i] for i in indices if i in by_source]
        bounds.append((min(starts) if starts else None, len(paragraph)))
        cursor += len(paragraph)
    result = []
    for i, (start, count) in enumerate(bounds):
        if start is None:
            continue
        following = next((a for a, _ in bounds[i + 1:] if a is not None), duration)
        result.append({"index": len(result), "start_s": start, "end_s": max(start, following), "line_count": count})
    return result


def fetch(song, duration, output, force=False):
    path = Path(output) / "lrclib.json"
    request_path = Path(output) / "lrclib_request.json"
    request = {"artist": song["artist"], "title": song["title"],
               "duration_s": duration, "lrclib_id": song.get("lrclib_id")}
    same_source = request_path.exists() and read(request_path) == request
    if not path.exists() or force or not same_source:
        if song.get("lrclib_id") is not None:
            url = "https://lrclib.net/api/get/" + str(int(song["lrclib_id"]))
        else:
            url = "https://lrclib.net/api/get?" + urlencode({"artist_name": song["artist"],
                "track_name": song["title"], "duration": round(duration)})
        save(path, json.loads(get(url)))
        save(request_path, request)
    data = read(path)
    recorded = data.get("duration")
    if recorded is None or not math.isfinite(float(recorded)) or abs(float(recorded) - duration) > 2:
        raise StageError(f"LRCLIB duration {recorded} does not match audio {duration:.3f}s within 2s")
    # An ID override is explicit; artist/title still remain reviewable provenance.
    if not data.get("syncedLyrics"):
        raise StageError("LRCLIB result contains no synced lyrics")
    lines = parse_lrc(data["syncedLyrics"])
    if not lines or lines[-1]["start_s"] >= duration:
        raise StageError("LRCLIB line times are empty or outside the recording")
    return {"id": data.get("id"), "duration_s": float(recorded),
            "artist": data.get("artistName"), "title": data.get("trackName"), "lines": lines,
            "plain_lyrics": data.get("plainLyrics") or "",
            "lrclib_paragraphs": parse_paragraphs(data.get("plainLyrics"), lines, duration)}
