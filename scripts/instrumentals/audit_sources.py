"""Flag substituted covers/arrangements before they can enter the pattern page."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from scripts.instrumentals.ingest import UNWANTED, contains_phrase

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with sqlite3.connect(ROOT / "instrumentals/catalog.sqlite", timeout=30) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute("SELECT * FROM tracks WHERE source_title IS NOT NULL AND source_kind!='user_provided_instrumental'")]
        flagged = []
        for row in rows:
            bad = [marker for marker in UNWANTED if contains_phrase(row["source_title"], marker) and not contains_phrase(row["title"], marker)]
            if not bad:
                continue
            flagged.append(row)
            print(f"SOURCE REVIEW: {row['artist']} — {row['title']} => {row['source_title']} ({', '.join(bad)})", flush=True)
            if args.apply:
                reason = "The selected source title identifies a substituted cover or arrangement (" + ", ".join(bad) + "). Find a source that preserves the original song's instrumental arrangement."
                db.execute("UPDATE tracks SET status='source_review_required',reason=? WHERE track_id=?", (reason, row["track_id"]))
                db.execute("DELETE FROM analysis WHERE track_id=?", (row["track_id"],))
                output = ROOT / "docs/data/instrumentals/songs" / f"{row['track_id']}.json"
                output.unlink(missing_ok=True)
        print(f"{len(flagged)} source(s) flagged; {'applied' if args.apply else 'read-only review'}", flush=True)


if __name__ == "__main__":
    main()
