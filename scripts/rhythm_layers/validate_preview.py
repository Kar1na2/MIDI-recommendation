"""Validate the public sample schema and optionally its localhost audio routes."""
import argparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .common import PREVIEW, OUT, ROOT, read_json


def private_fields(value):
    if isinstance(value, dict):
        assert not {"lyrics", "lines", "timed_words", "alignment_text", "raw_alignment", "asr_transcript", "text"}.intersection(value), "Private transcript field in page export"
        for child in value.values(): private_fields(child)
    elif isinstance(value, list):
        for child in value: private_fields(child)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url")
    args = parser.parse_args()
    data, media = read_json(PREVIEW / "data.json"), read_json(OUT / "media-index.json", {})
    private_fields(data)
    assert data["tracks"] and not data.get("errors"), "Sample catalog is empty or contains build errors"
    base = args.url.rstrip("/") if args.url else None
    if base:
        with urlopen(Request(base + "/", method="HEAD"), timeout=10) as response:
            assert response.status == 200
    routes = 0
    for track in data["tracks"]:
        assert len(track["views"]["main"]["layers"]) == 3
        assert all(len(track["views"][part]["layers"]) == 2 for part in ("bass", "drums", "other"))
        for view in track["views"].values():
            for layer in view["layers"]:
                windows = layer["segments"]
                assert [s["index"] for s in windows] == list(range(len(windows)))
                assert all(0 <= s["start"] <= s["end"] <= layer["duration"] + .1 for s in windows)
                assert all(abs(a["end"] - b["start"]) <= .1 for a, b in zip(windows, windows[1:]))
        for view, available in track["audio"].items():
            if not available: continue
            path = (ROOT / media[track["track_id"]][view]).resolve()
            assert path.is_relative_to(ROOT.resolve()) and path.is_file()
            if base:
                url = f"{base}/audio/{track['track_id']}/{view}"
                with urlopen(Request(url, headers={"Range": "bytes=0-31"}), timeout=10) as response:
                    assert response.status == 206 and len(response.read()) == 32
                    assert response.headers["Accept-Ranges"] == "bytes"
                routes += 1
        print(f"{track['artist']} — {track['title']}: layer, time-window, source-file and privacy checks passed")
    if base:
        try:
            urlopen(base + "/private/lyrics/", timeout=10)
        except HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("Private lyric path should not be served")
        print(f"HTTP page check and {routes} MP3/WAV byte-range routes passed")


if __name__ == "__main__":
    main()
