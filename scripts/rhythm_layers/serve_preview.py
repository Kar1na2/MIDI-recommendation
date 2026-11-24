"""Read-only localhost preview with byte-range MP3/WAV playback."""
from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
from pathlib import Path
import re
from urllib.parse import urlsplit

from .common import ROOT, OUT, PREVIEW, read_json


class PreviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PREVIEW), **kwargs)

    def list_directory(self, path):
        self.send_error(404)
        return None

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def do_HEAD(self):
        if urlsplit(self.path).path.startswith("/audio/"):
            self.serve_audio(head=True)
        else:
            super().do_HEAD()

    def do_GET(self):
        if urlsplit(self.path).path.startswith("/audio/"):
            self.serve_audio()
        else:
            super().do_GET()

    def serve_audio(self, head=False):
        components = urlsplit(self.path).path.strip("/").split("/")
        if len(components) != 3 or not re.fullmatch(r"[A-Za-z0-9]{22}", components[1]):
            self.send_error(404)
            return
        tid, view = components[1:]
        media = read_json(OUT / "media-index.json", {})
        reference = media.get(tid, {}).get(view)
        if not reference:
            self.send_error(404)
            return
        path = (ROOT / reference).resolve()
        if not path.is_relative_to(ROOT.resolve()) or not path.is_file() or path.suffix.lower() not in (".mp3", ".wav"):
            self.send_error(404)
            return
        size = path.stat().st_size
        start, end, partial = 0, size - 1, False
        if value := self.headers.get("Range"):
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
            if not match or not any(match.groups()):
                self.send_error(416)
                return
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(size - 1, int(last)) if last else size - 1
            else:
                start = max(0, size - int(last))
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            partial = True
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "private, max-age=3600")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head:
            return
        try:
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = stream.read(min(128 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    if not (PREVIEW / "data.json").is_file():
        parser.error("Build the sample catalog first: python -m scripts.rhythm_layers.build_preview --samples")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), PreviewHandler)
    print(f"Local review: http://127.0.0.1:{args.port}/", flush=True)
    print("Read-only local files; private lyrics are outside the served directory. Ctrl+C stops the preview.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
