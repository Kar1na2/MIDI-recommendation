"""Validated manifests, atomic JSON and explicit stage failures."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Protocol
from urllib.request import Request, urlopen


class StageError(RuntimeError):
    pass


class ModelUnavailable(StageError):
    """A model cannot run; never select a different model automatically."""


@dataclass(frozen=True)
class Note:
    onset_s: float
    offset_s: float
    pitch: int
    velocity: int | None
    instrument: str

    def __post_init__(self):
        if (not all(math.isfinite(t) for t in (self.onset_s, self.offset_s))
                or self.onset_s < 0 or self.offset_s <= self.onset_s
                or not 0 <= self.pitch <= 127
                or (self.velocity is not None and not 0 <= self.velocity <= 127)
                or not self.instrument):
            raise ValueError(f"Invalid note: {self}")


class TranscriptionBackend(Protocol):
    def transcribe(self, audio_path: Path, hints: list[str] | None) -> list[Note]: ...


def save(path: Path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    atomic_write_bytes(path, payload.encode("utf-8"))


def atomic_path(path: Path) -> Path:
    """Return a unique sibling temp path that retains the target extension."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix=f".{path.stem}.", suffix=path.suffix,
                                         dir=path.parent, delete=False)
    temporary = Path(handle.name)
    handle.close()
    return temporary


def atomic_write_bytes(path: Path, payload: bytes):
    path = Path(path)
    temporary = atomic_path(path)
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_audio(path: Path, audio, sample_rate: int, subtype="FLOAT"):
    """Write audio to a same-directory temporary WAV and atomically publish it."""
    import soundfile as sf

    path = Path(path)
    temporary = atomic_path(path)
    try:
        sf.write(temporary, audio, sample_rate, subtype=subtype, format="WAV")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(path):
    with Path(path).open("rb") as stream:
        if hasattr(hashlib, "file_digest"):
            return hashlib.file_digest(stream, "sha256").hexdigest()
        # hashlib.file_digest was added in Python 3.11; retain identical
        # streaming SHA-256 behavior for compatible user environments on 3.10.
        hasher = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
        return hasher.hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                     default=str).encode()).hexdigest()


def get(url):
    request = Request(url, headers={"User-Agent": "rhythm-lyric-research/1.0"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def warning(message, log):
    print(f"WARNING: {message}", flush=True)
    if message not in log:
        log.append(message)


def load_songs(manifest):
    import yaml
    manifest = Path(manifest).resolve()
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    rows = data.get("songs") if isinstance(data, dict) else data
    if not isinstance(rows, list) or not rows:
        raise StageError("Manifest must contain a nonempty songs list")
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise StageError("Every manifest entry must be a mapping")
        for key in ("id", "audio_path", "artist", "title"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise StageError(f"Manifest entry requires nonempty {key}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", row["id"]) or row["id"] in seen:
            raise StageError(f"Unsafe or duplicate song id: {row['id']}")
        seen.add(row["id"])
        for key in ("audio_path", "manual_sections_csv"):
            if row.get(key):
                row[key] = str((manifest.parent / row[key]).resolve())
        hints = row.get("instrument_hints")
        if hints is not None and (not isinstance(hints, list) or
                                   any(not isinstance(x, str) for x in hints)):
            raise StageError("instrument_hints must be a list of instrument names")
        if not row.get("genius_url") and not row.get("genius_url_romanized"):
            # Instrumental songs legitimately have no lyric source. The runner
            # records lyrics/join as not_applicable and still builds rhythm data.
            row["lyrics_not_applicable"] = True
    return rows


def note_dicts(notes):
    return [asdict(n) for n in notes]
