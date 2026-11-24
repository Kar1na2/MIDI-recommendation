"""Genius HTML cache, section headers and original-script lyric extraction."""
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import re
import os
import threading
import time
from urllib.parse import urlparse

from .common import StageError, atomic_write_bytes, read, save, fingerprint


LABELS = {"prechorus": "Pre-Chorus", "postchorus": "Post-Chorus",
          "chorus": "Chorus", "verse": "Verse", "intro": "Intro",
          "outro": "Outro", "bridge": "Bridge", "refrain": "Refrain",
          "interlude": "Interlude", "instrumental": "Instrumental"}
_REQUEST_LOCK = threading.Lock()
_LAST_PAGE_REQUEST = 0.0


def normalize_header(header):
    raw = header.strip().strip("[]").strip()
    name, _, people = raw.partition(":")
    name = re.sub(r"\s+\d+\s*$", "", name).strip()
    key = re.sub(r"[\s_-]+", "", name).casefold()
    label = LABELS.get(key, name)
    performers = [x.strip() for x in re.split(r",|&|\band\b", people) if x.strip()]
    return {"label": label, "performers": performers, "raw_header": header}


def parse_html(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    containers = soup.select('[data-lyrics-container="true"]')
    if not containers:
        raise StageError("Genius HTML contains no lyric containers; cannot invent section labels")
    sections, active = [], None
    for container in containers:
        for br in container.find_all("br"):
            br.replace_with("\n")
        # Inline anchors/spans belong to the same lyric line. Only <br> is a
        # line break; get_text('\n') would incorrectly split performer names.
        for line in container.get_text("", strip=False).splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            if not line:
                continue
            chunks = re.split(r"(\[[^\]]+\])", line)
            for chunk in chunks:
                chunk = chunk.strip()
                if not chunk:
                    continue
                if chunk.startswith("[") and chunk.endswith("]"):
                    header = normalize_header(chunk)
                    # Page titles (including bracketed song/title/lyrics blocks)
                    # are metadata. Only recognized bracketed musical headers
                    # can open a section; bare page headings cannot do so.
                    if header["label"] not in LABELS.values():
                        continue
                    active = {**header, "lines": [],
                              "genius_section_index": len(sections)}
                    sections.append(active)
                elif active is not None:
                    active["lines"].append(chunk)
    if not sections:
        raise StageError("Genius page has no bracketed section headers")
    counts = Counter()
    for section in sections:
        counts[section["label"]] += 1
        section["number"] = counts[section["label"]]
        section["display_label"] = f"{section['label']} {section['number']}"
    return sections


def _manual_paths(song):
    root = Path("data/genius_html")
    return [(song.get("genius_url"), root / f"{song['id']}.html", False),
            (song.get("genius_url_romanized"), root / f"{song['id']}.romanized.html", True)]


def _cache_path(song, romanized):
    root = Path("data/genius_html")
    return root / (f"{song['id']}.romanized.html" if romanized else f"{song['id']}.html")


def _access_token(env_file=Path(".env")):
    value = os.environ.get("GENIUS_ACCESS_TOKEN")
    if value is None and Path(env_file).is_file():
        for row in Path(env_file).read_text(encoding="utf-8-sig").splitlines():
            key, separator, candidate = row.partition("=")
            if separator and key.strip() == "GENIUS_ACCESS_TOKEN":
                value = candidate.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                break
    if not value:
        return None
    value = value.strip()
    if value.casefold().startswith("bearer "):
        value = value[7:].strip()
    return value or None


def _api_url(song, output=None):
    from .genius_matching import lookup
    result = lookup(song, _access_token(), Path(output) / "genius_search_cache" if output else None)
    if output:
        save(Path(output) / "genius_candidates.json", result)
    if not result["source_url"]:
        raise StageError(result["reason"])
    return result["source_url"]


def _request_page(url):
    global _LAST_PAGE_REQUEST
    import requests
    from requests.exceptions import Timeout
    from requests import HTTPError
    headers = {"User-Agent": "MIDI-Recommendation/0.1 (local music-analysis research project)"}
    last_error = None
    for attempt in range(3):
        with _REQUEST_LOCK:
            delay = 5 - (time.monotonic() - _LAST_PAGE_REQUEST)
            if delay > 0:
                time.sleep(delay)
            try:
                response = requests.get(url, headers=headers, timeout=20)
                _LAST_PAGE_REQUEST = time.monotonic()
            except Timeout as error:
                _LAST_PAGE_REQUEST = time.monotonic()
                last_error = error
                if attempt == 2:
                    break
                time.sleep(2 ** attempt)
                continue
        if response.status_code == 403:
            raise PermissionError("HTTP 403")
        if response.status_code == 429 or 500 <= response.status_code < 600:
            last_error = HTTPError(f"HTTP {response.status_code}", response=response)
            if attempt == 2:
                break
            retry_after = response.headers.get("Retry-After")
            wait = 2 ** attempt
            if retry_after:
                try:
                    wait = max(wait, float(retry_after))
                except ValueError:
                    try:
                        dt = parsedate_to_datetime(retry_after)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        wait = max(wait, (dt - datetime.now(timezone.utc)).total_seconds())
                    except (TypeError, ValueError, OverflowError):
                        pass
            time.sleep(max(0., wait))
            continue
        response.raise_for_status()
        return response.text
    raise StageError(f"Genius page retrieval failed after 3 transient attempts ({type(last_error).__name__})")


def _manual_instruction(url, path):
    return (f"Open {url} in your browser, save the page (Ctrl+S, 'Webpage, HTML only') "
            f"to {path.as_posix()}, then rerun with --force lyrics.")


def fetch(song, output, force=False):
    manual = _manual_paths(song)
    configured = song.get("genius_url") or song.get("genius_url_romanized")
    # User-saved HTML has no fetch metadata. A successful fetched cache is reused
    # only while the configured page URL remains the same.
    for url, path, romanized in manual:
        if url and path.is_file():
            metadata = path.with_suffix(".source.json")
            if metadata.exists():
                cached_source = read(metadata)
                if cached_source.get("configured_url") != configured:
                    continue
                html = path.read_text(encoding="utf-8-sig")
                return {"source_url": cached_source.get("source_url", url),
                        "romanized_only": romanized, "sections": parse_html(html),
                        "retrieval": "cached_fetch", "html_path": str(path)}
            html = path.read_text(encoding="utf-8-sig")
            return {"source_url": url, "romanized_only": romanized, "sections": parse_html(html),
                    "retrieval": "manual_html", "html_path": str(path)}

    if urlparse(configured).hostname not in {"genius.com", "www.genius.com"}:
        raise StageError("Genius source must use genius.com")
    if not _access_token():
        path = Path("data/genius_html") / f"{song['id']}.html"
        raise StageError("GENIUS_ACCESS_TOKEN is not set. " + _manual_instruction(configured, path))
    try:
        url = _api_url(song, output)
    except Exception as error:
        raise StageError(f"Genius API search failed ({type(error).__name__}); " +
                         _manual_instruction(configured, _cache_path(song, False))) from error
    if urlparse(url).hostname not in {"genius.com", "www.genius.com"}:
        raise StageError("Genius API returned a URL outside genius.com")
    denied = Path('data/genius_api/page_denials') / (fingerprint(url) + '.json')
    if denied.exists():
        raise StageError('Genius page previously returned HTTP 403; no retry. ' +
                         _manual_instruction(url, _cache_path(song, False)))
    romanized = "romaniz" in url.casefold()
    path = _cache_path(song, romanized)
    metadata = path.with_suffix(".source.json")
    same_source = (metadata.exists() and read(metadata).get("source_url") == url and
                   read(metadata).get("configured_url") == configured)
    if not path.exists() or not same_source:
        try:
            html = _request_page(url)
        except PermissionError:
            # No retries or alternate network path after an access denial.
            save(denied, {'url': url, 'http_status': 403,
                          'time_utc': datetime.now(timezone.utc).isoformat()})
            raise StageError("Genius returned HTTP 403; " + _manual_instruction(url, path))
        except Exception as error:
            raise StageError(f"Genius page retrieval failed ({type(error).__name__}); " +
                             _manual_instruction(url, path)) from error
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(path, html.encode("utf-8"))
        save(metadata, {"source_url": url, "configured_url": configured})
    sections = parse_html(path.read_text(encoding="utf-8-sig"))
    return {"source_url": url, "romanized_only": romanized, "sections": sections,
            "retrieval": "official_api_plus_page", "html_path": str(path)}
