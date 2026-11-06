"""Resumably resolve public Genius section headings and local alignment text.

Lyrics are kept only in ignored local working data. Website exports contain
source links, section labels and timestamps, never complete copyrighted lyrics.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from rapidfuzz.fuzz import ratio, token_set_ratio

from .common import PRIVATE, inventory, read_json, write_json

OVERRIDES = read_json(__file__.replace("fetch_lyrics.py", "source_overrides.json"), {})
HEADERS = {"User-Agent": "bepop-pattern-research/1.0 (public lyric section lookup)"}


def get(url, attempts=3):
    last = None
    for attempt in range(attempts):
        try:
            with urlopen(Request(url, headers=HEADERS), timeout=35) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as error:
            last = error
            if error.code == 429:
                delay = min(60, int(error.headers.get("Retry-After", 5)))
            elif error.code in (401, 403, 404):
                raise RuntimeError(f"Public page unavailable (HTTP {error.code}); no access controls were bypassed") from error
            else:
                delay = 2 ** (attempt + 1)
        except (URLError, TimeoutError) as error:
            last = error
            delay = 2 ** (attempt + 1)
        if attempt + 1 < attempts:
            time.sleep(delay)
    raise RuntimeError(f"Public Genius request failed: {last}")


def search_song(artist, title):
    query = quote(f"{artist} {title}")
    data = json.loads(get(f"https://genius.com/api/search/multi?q={query}"))
    candidates = []
    for section in data.get("response", {}).get("sections", []):
        if section.get("type") != "song":
            continue
        for item in section.get("hits", []):
            song = item.get("result", {})
            match_title = song.get("title", "")
            match_artist = (song.get("primary_artist") or {}).get("name", "")
            title_score = max(ratio(title.casefold(), match_title.casefold()),
                              token_set_ratio(title.casefold(), match_title.casefold()) - 6)
            artist_score = token_set_ratio(artist.casefold(), match_artist.casefold())
            candidates.append((.58 * title_score + .42 * artist_score, title_score, artist_score,
                               song.get("url"), match_title, match_artist))
    return max(candidates, default=(0, 0, 0, None, "", ""))


def parse_page(html, source_url):
    soup = BeautifulSoup(html, "html.parser")
    containers = soup.select('div[data-lyrics-container="true"]')
    if not containers:
        raise RuntimeError("Genius page loaded, but no public lyrics section container was found")
    sections = []
    active = None
    pending_heading = None

    def finish_section():
        if active and active["lines"]:
            sections.append(active)

    def start_section(raw_heading):
        nonlocal active
        finish_section()
        label = raw_heading.split(":", 1)[0].strip()
        active = {"name": label, "lines": []}

    for container in containers:
        for br in container.find_all("br"):
            br.replace_with("\n")
        text = container.get_text("\n", strip=True)
        for line in text.splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            if not line:
                continue
            if pending_heading is not None:
                pending_heading += " " + line
                if "]" in line:
                    start_section(pending_heading.split("]", 1)[0].lstrip("["))
                    pending_heading = None
                continue
            match = re.fullmatch(r"\[([^\]]+)\]", line)
            if match:
                start_section(match[1])
            elif line.startswith("[") and "]" not in line:
                # Genius may split performer credits across inline markup nodes.
                pending_heading = line[1:].strip()
            elif active is not None:
                active["lines"].append(line)
    finish_section()
    sections = [s for s in sections if s["lines"]]
    if not sections:
        raise RuntimeError("No section headings and lyric lines were available to align")
    return {"source_url": source_url, "sections": sections,
            "section_count": len(sections),
            "lyric_line_count": sum(len(s["lines"]) for s in sections)}


def fetch_one(track, force=False):
    tid = track["track_id"]
    out = PRIVATE / "lyrics" / f"{tid}.json"
    if out.is_file() and not force:
        return read_json(out)
    override = OVERRIDES.get(tid, {})
    if track.get("instrumental_recording"):
        result = {"track_id": tid, "status": "recording_has_no_lead_vocal_source", "sections": [],
                  "source_url": override.get("genius_url"),
                  "reason": override.get("recording_mismatch") or "Instrumental source title"}
        write_json(out, result)
        return result
    url = override.get("genius_url")
    matched = None
    if not url:
        score, title_score, artist_score, url, matched_title, matched_artist = search_song(track["artist"], track["title"])
        if not url or score < 78 or title_score < 72 or artist_score < 55:
            result = {"track_id": tid, "status": "candidate_unverified", "match_score": round(score, 1),
                      "matched_title": matched_title, "matched_artist": matched_artist, "sections": []}
            write_json(out, result)
            return result
        matched = {"title": matched_title, "artist": matched_artist, "score": round(score, 1)}
    if urlparse(url).netloc.lower() not in ("genius.com", "www.genius.com"):
        return {"track_id": tid, "status": "invalid_source_domain", "sections": []}
    parsed = parse_page(get(url), url)
    result = {"track_id": tid, "status": "lyrics_ready", "matched_song": matched,
              "language": override.get("language"), "recording_mismatch": override.get("recording_mismatch"),
              **parsed}
    write_json(out, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--track-id")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--pause", type=float, default=.4)
    args = parser.parse_args()
    rows = inventory()
    if args.track_id:
        rows = [row for row in rows if row["track_id"] == args.track_id]
        if not rows:
            parser.error("Spotify track ID is not in the saved library")
    if args.limit:
        rows = rows[:args.limit]
    log = read_json(PRIVATE / "lyrics-index.json", {})
    for index, track in enumerate(rows, 1):
        if track["track_id"] in log and not args.force:
            continue
        try:
            record = fetch_one(track, force=args.force)
            log[track["track_id"]] = {k: record.get(k) for k in
                                      ("status", "source_url", "matched_song", "match_score", "language")}
            print(f"[{index}/{len(rows)}] {track['artist']} — {track['title']}: {record['status']}", flush=True)
        except Exception as error:
            log[track["track_id"]] = {"status": "source_fetch_failed", "reason": str(error)[:220]}
            print(f"[{index}/{len(rows)}] {track['artist']} — {track['title']}: {type(error).__name__}: {error}", flush=True)
        write_json(PRIVATE / "lyrics-index.json", log)
        time.sleep(max(0, args.pause))
    counts = {}
    for value in log.values():
        counts[value["status"]] = counts.get(value["status"], 0) + 1
    print(f"Lyrics catalog: {counts}", flush=True)


if __name__ == "__main__":
    main()
