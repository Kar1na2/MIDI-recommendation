#!/usr/bin/env python3
"""
Chord extraction stage: runs the Chordino Vamp plugin (NNLS-Chroma) over the
"other" Demucs stem of every song, writing chords/<song_name>/other_chordnotes.csv.

This is a diagnostic/corroborating signal, independent of the ML transcription
pipeline (Basic Pitch/ADTLib) - chord detection never reads or writes anything
under midi/ or merged/, and nothing here modifies an existing MIDI file.

Reads from stems/<song_name>/other.wav (output of separate_stems.py). Only the
"other" stem is chorded - it's the harmonic/melodic bucket that actually
carries chord identity; "bass" is usually monophonic and "drums" has none.

We use Chordino's "chordnotes" output rather than its "simplechord" output:
chordnotes gives the actual MIDI pitch content Chordino judged present in each
chord segment (one row per note, several rows sharing the same onset/offset),
not a chord-name label - that's the raw pitch-class membership data the
downstream chord-tone tagging pass (tag_chord_tones.py) needs. See
"Chordino install" in the README for how the plugin itself is built (no
sonic-annotator or system package involved - see tools/vamp-build/README.md).

Resumable: a song is skipped if chords/<song_name>/other_chordnotes.csv
already exists and is newer than stems/<song_name>/other.wav.

Failures are logged to chords/_failures.log instead of aborting the batch.

Usage:
    uv run extract_chords.py                # process everything
    uv run extract_chords.py --limit 3       # first 3 unfinished songs (smoke test)
    uv run extract_chords.py --dry-run       # show what would run, do nothing
    uv run extract_chords.py --retry-failed  # clear the failure log and retry those too
"""
import argparse
import csv
import os
import sys
import time
import traceback
from pathlib import Path

STEMS_ROOT = Path("stems")
OUTPUT_ROOT = Path("chords")
FAILURE_LOG = OUTPUT_ROOT / "_failures.log"

PLUGIN_KEY = "nnls-chroma:chordino"
PLUGIN_OUTPUT = "chordnotes"
CSV_HEADER = ["segment", "onset", "offset", "pitch", "pitch_class"]

# Built by tools/vamp-build/ (see its README) into a plain directory of
# plugin binaries - no system-wide Vamp plugin install involved.
VAMP_PLUGIN_DIR = Path(__file__).resolve().parent / "tools" / "vamp-plugins"


def collect_jobs():
    """(song_name, input_wav_path) for every stems/<song>/other.wav that exists."""
    jobs = []
    if not STEMS_ROOT.is_dir():
        return jobs
    for song_dir in sorted(p for p in STEMS_ROOT.iterdir() if p.is_dir()):
        wav = song_dir / "other.wav"
        if wav.exists():
            jobs.append((song_dir.name, wav))
    return jobs


def is_done(song_name: str, wav: Path) -> bool:
    out_path = OUTPUT_ROOT / song_name / "other_chordnotes.csv"
    return out_path.exists() and out_path.stat().st_mtime >= wav.stat().st_mtime


def log_failure(song_name: str, path: Path, error: str):
    FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with FAILURE_LOG.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{song_name}\t{path}\t{error}\n")


def extract_one(wav: Path):
    """Run Chordino over wav, return a list of CSV_HEADER-shaped rows."""
    import soundfile as sf
    import vamp

    audio, sr = sf.read(str(wav))
    if audio.ndim > 1:
        audio = audio.mean(axis=1)  # Chordino wants mono

    result = vamp.collect(audio, sr, PLUGIN_KEY, output=PLUGIN_OUTPUT)
    features = result["list"]

    rows = []
    segment = -1
    prev_key = None
    for feat in features:
        onset = float(feat["timestamp"])
        offset = onset + float(feat["duration"])
        key = (onset, offset)
        if key != prev_key:
            segment += 1
            prev_key = key
        pitch = float(feat["values"][0])
        rows.append({
            "segment": segment,
            "onset": onset,
            "offset": offset,
            "pitch": pitch,
            "pitch_class": int(round(pitch)) % 12,
        })
    return rows


def write_csv(rows, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".csv.tmp")
    with tmp_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        writer.writeheader()
        writer.writerows(rows)
    tmp_path.rename(out_path)  # atomic-ish: a half-written file never looks "done"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None,
                         help="Only process the first N unfinished songs (for smoke testing)")
    parser.add_argument("--dry-run", action="store_true",
                         help="List what would be processed, without running Chordino")
    parser.add_argument("--retry-failed", action="store_true",
                         help="Clear the failure log first so previously-failed songs are attempted again")
    args = parser.parse_args()

    if not VAMP_PLUGIN_DIR.is_dir():
        print(f"ERROR: {VAMP_PLUGIN_DIR} not found - build the Chordino plugin first "
              f"(see tools/vamp-build/README.md).", file=sys.stderr)
        sys.exit(1)
    os.environ["VAMP_PATH"] = str(VAMP_PLUGIN_DIR)

    if args.retry_failed and FAILURE_LOG.exists():
        FAILURE_LOG.unlink()

    jobs = collect_jobs()
    todo = [j for j in jobs if not is_done(*j)]
    print(f"{len(jobs)} songs with an 'other' stem found under {STEMS_ROOT}/, "
          f"{len(jobs) - len(todo)} already chorded, {len(todo)} to process.")

    if args.limit:
        todo = todo[:args.limit]

    if args.dry_run:
        for name, wav in todo:
            print(f"WOULD EXTRACT CHORDS: {name}  <-  {wav}")
        return

    if not todo:
        print("Nothing to do.")
        return

    import vamp
    if PLUGIN_KEY not in vamp.list_plugins():
        print(f"ERROR: {PLUGIN_KEY} not found by the Vamp host (VAMP_PATH={VAMP_PLUGIN_DIR}). "
              f"Is the plugin built? See tools/vamp-build/README.md.", file=sys.stderr)
        sys.exit(1)

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    ok, failed = 0, 0
    for i, (song_name, wav) in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {song_name} ...", flush=True)
        try:
            rows = extract_one(wav)
            out_path = OUTPUT_ROOT / song_name / "other_chordnotes.csv"
            write_csv(rows, out_path)
            n_segments = (rows[-1]["segment"] + 1) if rows else 0
            print(f"    {n_segments} chord segments, {len(rows)} note rows -> {out_path}")
            ok += 1
        except Exception as e:
            log_failure(song_name, wav, f"{type(e).__name__}: {e}")
            print(f"  FAILED (see {FAILURE_LOG})")
            traceback.print_exc(file=sys.stderr)
            failed += 1

    print(f"Done. {ok} succeeded, {failed} failed.")
    if failed:
        print(f"See {FAILURE_LOG} for details. Re-run this script to retry (or pass --retry-failed).")


if __name__ == "__main__":
    main()
