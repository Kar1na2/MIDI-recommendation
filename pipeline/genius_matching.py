"""Official artist-first Genius candidate retrieval and explicit title matching."""
from functools import lru_cache
from pathlib import Path
import re
import threading
import time
import unicodedata

from .common import StageError, fingerprint, read, save
from .config import GENIUS_TITLE_SIMILARITY

ARTIST_SIMILARITY = .85
_LOCK = threading.Lock()
_LAST = 0.0


def normalized(value):
    return "".join(character for character in unicodedata.normalize("NFKC", value).casefold()
                   if character.isalnum())


@lru_cache(maxsize=8192)
def variants(value):
    value = unicodedata.normalize("NFKC", value or "").strip()
    clean = re.sub(r"\s*[-–—]\s*(?:from the |from |instrumental|remaster|reverb).*", "", value, flags=re.I)
    clean = re.sub(r"\s*\(?\b(?:feat\.?|featuring|ft\.?)\s+[^)]*\)?", "", clean, flags=re.I)
    parts = [clean, re.sub(r"\([^)]*\)", "", clean)]
    parts += re.findall(r"\(([^)]+)\)", clean)
    result = set()
    for part in parts:
        if normalized(part):
            result.add(part.strip())
        if re.search(r"[가-힣]", part):
            try:
                from .line_alignment import romanize_hangul
                result.add(romanize_hangul(part))
            except ImportError:
                pass
        if re.search(r"[ぁ-ゟ゠-ヿ]", part):
            from .line_alignment import japanese_readings
            result.update(japanese_readings(part))
        try:
            from text_unidecode import unidecode
            result.add(unidecode(part))
        except ImportError:
            pass
    return sorted({normalized(part) for part in result if normalized(part)})


def similarity(left, right):
    from rapidfuzz.fuzz import ratio
    return max((ratio(a, b) / 100 for a in variants(left) for b in variants(right)), default=0.0)


def score_candidate(song, candidate):
    primary = candidate.get("primary_artist", {})
    artists = [primary.get("name", "")]
    artists += [row.get("name", "") for row in candidate.get("primary_artists", [])]
    artist_score = max((similarity(song["artist"], value) for value in artists), default=0.0)
    title_score = similarity(song["title"], candidate.get("title", ""))
    accepted = artist_score >= ARTIST_SIMILARITY and title_score >= GENIUS_TITLE_SIMILARITY
    return {"id": candidate.get("id"), "title": candidate.get("title", ""),
            "artist": primary.get("name", ""), "url": candidate.get("url"),
            "artist_similarity": artist_score, "title_similarity": title_score,
            "title_variants": variants(candidate.get("title", "")), "eligible": accepted,
            "reason": "eligible" if accepted else
                      "artist_mismatch" if artist_score < ARTIST_SIMILARITY else "title_below_threshold"}


def _api_get(endpoint, params, token, cache):
    from .genius_queue import request
    return request(endpoint, params, token, cache)


def lookup(song, token, cache=None):
    from .genius_queue import GeniusPending
    from .config import GENIUS_CATALOG_MAX_PAGES
    if not token:
        raise StageError("GENIUS_ACCESS_TOKEN is missing")
    candidates, queries, artist_ids = {}, [], set()
    pending = None

    def add(result):
        key = str(result.get("id") or result.get("url"))
        if key != "None": candidates[key] = result
        primary = result.get("primary_artist", {})
        if primary.get("id") and similarity(song["artist"], primary.get("name", "")) >= ARTIST_SIMILARITY:
            artist_ids.add(primary["id"])

    def eligible():
        rows = [score_candidate(song, row) for row in candidates.values()]
        return [row for row in rows if row["eligible"] and row.get("url")]

    try:
        # Targeted title variants first; no unbounded catalog enumeration.
        titles = [song['title']] + re.findall(r'\(([^)]+)\)', song['title'])
        titles += variants(song['title'])
        seen = set()
        for title in titles:
            if normalized(title) in seen: continue
            seen.add(normalized(title))
            query = f"{song['artist']} {title}"
            queries.append({'endpoint':'/search','q':query})
            for hit in _api_get('/search',{'q':query},token,cache).get('hits',[]):
                add(hit.get('result',{}))
            if eligible(): break
        if not eligible() and not artist_ids:
            query = song['artist']; queries.append({'endpoint':'/search','q':query})
            for hit in _api_get('/search',{'q':query},token,cache).get('hits',[]):
                add(hit.get('result',{}))
        if not eligible():
            for artist_id in sorted(artist_ids):
                page = 1
                while page is not None and page <= GENIUS_CATALOG_MAX_PAGES:
                    endpoint=f'/artists/{artist_id}/songs'
                    queries.append({'endpoint':endpoint,'page':page})
                    payload=_api_get(endpoint,{'sort':'title','per_page':50,'page':page},token,cache)
                    for result in payload.get('songs',[]): add(result)
                    if eligible(): break
                    page=payload.get('next_page')
                if eligible(): break
    except GeniusPending as error:
        pending={'request_key':error.key,'retry_at':error.retry_at,'reason':str(error)}
    considered=[score_candidate(song,row) for _,row in sorted(candidates.items())]
    accepted=eligible()
    accepted.sort(key=lambda row:(-row['title_similarity'],-row['artist_similarity'],
                                  'romaniz' in row['url'].casefold(),row['url']))
    selected=accepted[0] if accepted else None
    return {'artist':song['artist'],'title':song['title'],
        'requested_title_variants':variants(song['title']),'artist_threshold':ARTIST_SIMILARITY,
        'title_threshold':GENIUS_TITLE_SIMILARITY,'queries':queries,
        'catalog_page_limit':GENIUS_CATALOG_MAX_PAGES,'candidate_count':len(considered),
        'candidates':considered,'selected':selected,'source_url':selected['url'] if selected else None,
        'status':'pending_genius' if pending and not selected else 'matched' if selected else 'no_match',
        'pending':pending,'reason':pending['reason'] if pending else None if selected else
        'No candidate passed both artist and title requirements'}