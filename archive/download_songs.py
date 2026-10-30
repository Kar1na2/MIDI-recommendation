"""Download a Spotify metadata list as a resumable, deduplicated MP3 corpus.

The input is the flat JSON array written by ``fetch_liked_songs.py``. Audio
is resolved from YouTube with yt-dlp because Spotify's Web API does not expose
full track audio. Every file is keyed by Spotify track ID, failures are logged,
and exact duplicate audio is retained only once.

Usage (from the repository root):
    python archive/download_songs.py archive/liked_songs.json liked-audio
    python archive/download_songs.py archive/liked_songs.json liked-audio --limit 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import yt_dlp


MANIFEST_NAME = "download_manifest.json"
FAILURE_LOG_NAME = "_download_failures.tsv"
SEARCH_RESULTS = 5
BAD_VARIANTS = {
    "cover", "karaoke", "nightcore", "slowed", "sped up", "instrumental",
    "live", "remix", "8d", "bass boosted",
}


def load_songs(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("songs", [])
    if not isinstance(data, list):
        raise ValueError("input JSON must be a song array or contain a 'songs' array")

    unique: dict[str, dict] = {}
    for song in data:
        track_id = song.get("track_id")
        if not track_id or not song.get("title") or not song.get("artist"):
            continue
        unique.setdefault(str(track_id), song)
    return list(unique.values())


def safe_component(value: str, max_length: int = 120) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value or "untitled")[:max_length].rstrip(" .")


def output_name(song: dict) -> str:
    label = safe_component(f"{song['artist']} - {song['title']}")
    return f"{label} [{song['track_id']}].mp3"


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    value = "".join(ch if ch.isalnum() else " " for ch in value)
    return " ".join(value.split())


IDENTITY_STOPWORDS = {
    "official", "video", "music", "audio", "mv", "lyrics", "lyric",
    "feat", "featuring", "color", "coded", "eng", "rom", "han",
    "live", "clip",
}


def identity_normalized(value: str) -> str:
    return " ".join(
        token for token in normalized(value).split()
        if token not in IDENTITY_STOPWORDS
    )


def candidate_score(song: dict, entry: dict) -> float:
    expected = normalized(f"{song['artist']} {song['title']}")
    actual = normalized(f"{entry.get('title', '')} {entry.get('uploader', '')}")
    text_score = SequenceMatcher(None, expected, actual).ratio()

    expected_duration = (song.get("duration_ms") or 0) / 1000
    candidate_duration = entry.get("duration") or 0
    if expected_duration and candidate_duration:
        delta = abs(expected_duration - candidate_duration)
        duration_score = max(0.0, 1.0 - delta / max(30.0, expected_duration * 0.25))
    else:
        duration_score = 0.5

    expected_words = set(normalized(song["title"]).split())
    actual_text = normalized(entry.get("title", ""))
    variant_penalty = sum(
        0.12 for word in BAD_VARIANTS
        if word in actual_text and word not in expected_words
    )
    official_bonus = 0.05 if any(
        marker in normalized(f"{entry.get('title', '')} {entry.get('uploader', '')}")
        for marker in ("official", "topic", "vevo")
    ) else 0.0
    return 0.65 * text_score + 0.35 * duration_score + official_bonus - variant_penalty


def candidate_is_plausible(song: dict, entry: dict) -> bool:
    expected_title = normalized(song["title"])
    actual_title = normalized(entry.get("title", ""))
    title_score = SequenceMatcher(None, expected_title, actual_title).ratio()
    expected_tokens = set(expected_title.split())
    actual_tokens = set(actual_title.split())
    overlap = len(expected_tokens & actual_tokens) / max(1, len(expected_tokens | actual_tokens))
    expected_artist = normalized(song["artist"])
    candidate_text = normalized(f"{entry.get('title', '')} {entry.get('uploader', '')}")
    artist_score = SequenceMatcher(None, expected_artist, candidate_text).ratio()
    artist_tokens = set(expected_artist.split()) & set(candidate_text.split())
    artist_match = bool(artist_tokens) or artist_score >= 0.55
    # A title-only match is accepted only when the title is nearly exact;
    # generic titles otherwise tend to select a different artist's recording.
    return (title_score >= 0.55 or overlap >= 0.45) and (artist_match or title_score >= 0.88)


def search_candidate(song: dict) -> tuple[dict | None, float]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "noplaylist": True,
    }
    queries = [
        f"{song['artist']} - {song['title']} official audio",
        f"{song['title']} {song['artist']}",
        song["title"],
    ]
    with yt_dlp.YoutubeDL(opts) as ydl:
        for query in queries:
            info = ydl.extract_info(f"ytsearch{SEARCH_RESULTS}:{query}", download=False)
            entries = [e for e in (info or {}).get("entries", []) if e and e.get("id")]
            if entries:
                ranked = sorted(
                    ((candidate_score(song, entry), entry) for entry in entries
                     if candidate_is_plausible(song, entry)),
                    reverse=True,
                    key=lambda pair: pair[0],
                )
                if not ranked:
                    continue
                score, candidate = ranked[0]
                return candidate, score
    return None, 0.0


def download_candidate(candidate: dict, work_dir: Path) -> Path:
    video_id = candidate["id"]
    outtmpl = str(work_dir / f"{video_id}.%(ext)s")
    opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
        "fragment_retries": 5,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "0",
        }],
    }
    url = f"https://www.youtube.com/watch?v={video_id}"
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    result = work_dir / f"{video_id}.mp3"
    if not result.exists():
        raise RuntimeError("yt-dlp completed without producing an MP3")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_hash_index(directories: list[Path]) -> dict[str, str]:
    index: dict[str, str] = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.mp3")):
            index.setdefault(sha256(path), str(path))
    return index


def probe_duration(path: Path) -> float | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=True,
        )
        return float(result.stdout.strip())
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None


def existing_metadata_matches(songs: list[dict]) -> dict[str, str]:
    """Return high-confidence Spotify-ID -> existing corpus-file matches.

    The original corpus predates Spotify IDs, so its filenames are the only
    available identity. Require both artist and title agreement when both are
    present in a filename; title-only files additionally require duration
    agreement. This deliberately favors a second download over a false merge.
    """
    candidates = []
    for directory in (Path("audio"), Path("k-indie")):
        if directory.is_dir():
            candidates.extend(sorted(directory.glob("*.mp3")))
    parsed = []
    for path in candidates:
        label = path.stem
        if " - " in label:
            artist_text, title_text = label.split(" - ", 1)
        else:
            artist_text, title_text = "", label
        parsed.append((path, identity_normalized(artist_text), identity_normalized(title_text), probe_duration(path)))

    matches = {}
    for song in songs:
        artist = identity_normalized(song["artist"])
        title = identity_normalized(song["title"])
        expected_duration = (song.get("duration_ms") or 0) / 1000
        best = None
        for path, existing_artist, existing_title, actual_duration in parsed:
            title_score = SequenceMatcher(None, title, existing_title).ratio()
            if existing_artist:
                artist_score = SequenceMatcher(None, artist, existing_artist).ratio()
                title_tokens = set(title.split())
                existing_tokens = set(existing_title.split())
                overlap = len(title_tokens & existing_tokens) / max(1, len(title_tokens | existing_tokens))
                if not (artist_score >= 0.78 and (title_score >= 0.68 or overlap >= 0.55)):
                    continue
                score = 0.6 * artist_score + 0.4 * max(title_score, overlap)
            else:
                if title_score < 0.88 or not expected_duration or actual_duration is None:
                    continue
                if abs(expected_duration - actual_duration) > 2.0:
                    continue
                score = title_score
            if best is None or score > best[0]:
                best = (score, path)
        if best is not None:
            matches[str(song["track_id"])] = str(best[1])
    return matches


def write_json_atomic(path: Path, data) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_file", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--retry-low-score", action="store_true",
                        help="Retry downloaded/duplicate records whose search score is below 0.35")
    parser.add_argument(
        "--dedupe-against", type=Path, action="append", default=[],
        help="Additional MP3 directory whose exact audio should be skipped",
    )
    args = parser.parse_args()

    songs = load_songs(args.json_file)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / MANIFEST_NAME
    failure_path = args.output_dir / FAILURE_LOG_NAME
    work_dir = args.output_dir / ".partial"
    work_dir.mkdir(exist_ok=True)

    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if args.retry_failed:
        manifest = {key: value for key, value in manifest.items() if value.get("status") != "failed"}
        failure_path.unlink(missing_ok=True)

    metadata_matches = existing_metadata_matches(songs)
    hash_index = build_hash_index([
        Path("audio"), Path("k-indie"), args.output_dir, *args.dedupe_against,
    ])
    todo = []
    for song in songs:
        track_id = str(song["track_id"])
        final_path = args.output_dir / output_name(song)
        record = manifest.get(track_id, {})
        existing_path = metadata_matches.get(track_id)
        if existing_path:
            # A previous run may have downloaded this same logical track
            # before metadata matching was enabled; remove that local copy.
            final_path.unlink(missing_ok=True)
            manifest[track_id] = {
                "track_id": track_id,
                "title": song["title"],
                "artist": song["artist"],
                "status": "existing",
                "duplicate_of": existing_path,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            continue
        if args.retry_low_score and record.get("match_score", 1) < 0.35:
            final_path.unlink(missing_ok=True)
            manifest.pop(track_id, None)
            record = {}
        if final_path.exists() or record.get("status") in {"downloaded", "duplicate"}:
            continue
        if record.get("status") == "failed" and not args.retry_failed:
            continue
        todo.append(song)
    resolved_count = len(songs) - len(todo)
    write_json_atomic(manifest_path, manifest)
    if args.limit is not None:
        todo = todo[:args.limit]

    print(
        f"{len(songs)} unique Spotify tracks; {resolved_count} already "
        f"resolved; {len(todo)} to process"
    )
    if args.dry_run:
        for song in todo:
            print(f"WOULD DOWNLOAD {song['artist']} - {song['title']} [{song['track_id']}]")
        return

    ok = duplicates = failed = 0
    for index, song in enumerate(todo, 1):
        track_id = str(song["track_id"])
        label = f"{song['artist']} - {song['title']}"
        print(f"[{index}/{len(todo)}] {label}", flush=True)
        record = {
            "track_id": track_id,
            "title": song["title"],
            "artist": song["artist"],
            "status": "failed",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        try:
            candidate, score = search_candidate(song)
            if candidate is None:
                raise RuntimeError("no YouTube search results")
            downloaded = download_candidate(candidate, work_dir)
            digest = sha256(downloaded)
            record.update({
                "youtube_id": candidate["id"],
                "youtube_title": candidate.get("title"),
                "match_score": round(score, 4),
                "sha256": digest,
            })
            if digest in hash_index:
                record.update({"status": "duplicate", "duplicate_of": hash_index[digest]})
                downloaded.unlink()
                duplicates += 1
                print(f"  duplicate of {hash_index[digest]}")
            else:
                final_path = args.output_dir / output_name(song)
                shutil.move(str(downloaded), final_path)
                hash_index[digest] = str(final_path)
                record.update({"status": "downloaded", "path": str(final_path)})
                ok += 1
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            with failure_path.open("a", encoding="utf-8") as f:
                f.write(f"{track_id}\t{label}\t{record['error']}\n")
            failed += 1
            print(f"  FAILED: {record['error']}")
        finally:
            manifest[track_id] = record
            write_json_atomic(manifest_path, manifest)

    shutil.rmtree(work_dir, ignore_errors=True)
    print(f"Done: {ok} downloaded, {duplicates} exact duplicates, {failed} failed")


if __name__ == "__main__":
    main()
