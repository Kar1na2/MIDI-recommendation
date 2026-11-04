"""Check every exported audio object with HEAD requests; no audio is downloaded."""
from __future__ import annotations

import argparse
import csv
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://kar1na2.github.io"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--attempts", type=int, default=1,
                        help="attempt each HEAD request this many times")
    parser.add_argument("--retry-failures", action="store_true",
                        help="retry only failed object keys from verification.json")
    args = parser.parse_args()
    config = json.loads((ROOT / "docs/data/instrumentals/media-config.json").read_text(encoding="utf-8"))
    base = (config.get("audio_base_url") or "").rstrip("/")
    if not base.startswith("https://"):
        raise ValueError("Configure an HTTPS audio_base_url before checking the host.")
    with (ROOT / "instrumental-audio-host/upload_manifest.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    all_rows = rows
    verification_path = ROOT / "instrumental-audio-host/verification.json"
    previous_failures = []
    if args.retry_failures:
        previous = json.loads(verification_path.read_text(encoding="utf-8"))
        if previous.get("audio_base_url") != base or previous.get("total_objects") != len(rows):
            raise ValueError("Existing verification does not match the configured host and upload inventory.")
        previous_failures = previous.get("failures", [])
        failed_keys = {failure["object_key"] for failure in previous_failures}
        rows = [row for row in rows if row["object_key"] in failed_keys]
        if not rows:
            print("There are no failed objects to retry.", flush=True)
            return

    def check(row):
        url = f"{base}/v1/{row['track_id']}/{row['object_key'].rsplit('/', 1)[1]}"
        request = Request(url, method="HEAD", headers={
            "Origin": ORIGIN,
            "User-Agent": "bepop-audio-host-check/1.0",
        })
        last_error = None
        for _ in range(max(1, args.attempts)):
            try:
                with urlopen(request, timeout=45) as response:
                    problems = []
                    if response.status != 200:
                        problems.append(f"HTTP {response.status}")
                    if response.headers.get("Content-Length") != row["bytes"]:
                        problems.append("File size differs from the upload manifest")
                    if response.headers.get_content_type() != row["content_type"]:
                        problems.append("Unexpected audio content type")
                    if response.headers.get("Accept-Ranges") != "bytes":
                        problems.append("Byte-range seeking is not advertised")
                    if response.headers.get("Access-Control-Allow-Origin") not in ("*", ORIGIN):
                        problems.append("GitHub Pages browser access is not allowed")
                    if not problems:
                        return None
                    last_error = problems
            except HTTPError as error:
                last_error = [f"HTTP {error.code}"]
            except Exception as error:
                last_error = [str(error)]
        return {"object_key": row["object_key"], "errors": last_error}

    failures = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 16))) as pool:
        futures = [pool.submit(check, row) for row in rows]
        for count, future in enumerate(as_completed(futures), 1):
            result = future.result()
            if result:
                failures.append(result)
            if count % 500 == 0:
                print(f"Checked {count:,}/{len(rows):,} audio objects; {len(failures)} issues", flush=True)

    if args.retry_failures:
        retried_keys = {row["object_key"] for row in rows}
        failures.extend(failure for failure in previous_failures
                        if failure["object_key"] not in retried_keys)
        failures.sort(key=lambda item: item["object_key"])

    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "audio_base_url": base,
        "method": "HEAD: size, content type, byte-range support, and CORS",
        "total_objects": len(all_rows),
        "total_bytes": sum(int(row["bytes"]) for row in all_rows),
        "verified_objects": len(all_rows) - len(failures),
        "failures": sorted(failures, key=lambda item: item["object_key"]),
    }
    verification_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {result['verified_objects']:,}/{result['total_objects']:,} hosted objects; {len(failures)} issues", flush=True)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
