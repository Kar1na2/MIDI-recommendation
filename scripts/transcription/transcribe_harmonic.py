#!/usr/bin/env python3
"""
Transcription stage, Part A: Basic Pitch over the "other" and "bass" Demucs
stems, writing midi/<song_name>/{other,bass}.mid.

Reads from stems/<song_name>/{other.wav,bass.wav} (output of separate_stems.py).
The "vocals" stem is intentionally not transcribed here - Basic Pitch's pitch
model is tuned for melodic/harmonic instruments, not sung pitch/lyrics, so it's
left as unused output for now (see README note).

Resumable: a (song, stem) pair is skipped if midi/<song_name>/<stem>.mid already
exists, so a re-run after a crash only redoes what's missing.

Failures are logged to midi/_failures_harmonic.log (one line per failure)
instead of aborting the batch.

Runs on CPU: Basic Pitch's TensorFlow backend can't see this machine's GPU
(TF can't locate the CUDA runtime the way torch's private nvidia-* wheels
provide it - see README note on the transcription stage). Not a practical
problem: the model is small and CPU inference is fast per file.

Usage (from the repo root; ARGS is forwarded as CLI flags):
    make transcribe-harmonic                              # process everything
    make transcribe-harmonic ARGS="--limit 3"             # first 3 unfinished jobs (smoke test)
    make transcribe-harmonic ARGS="--dry-run"             # show what would run, do nothing
    make transcribe-harmonic ARGS="--retry-failed"        # clear the failure log and retry those too
"""
import argparse
import sys
import time
import traceback
from pathlib import Path

STEMS_TO_TRANSCRIBE = ("other", "bass")
STEMS_ROOT = Path("stems")
OUTPUT_ROOT = Path("midi")
FAILURE_LOG = OUTPUT_ROOT / "_failures_harmonic.log"

# Mirrors basic_pitch.inference.DEFAULT_ONSET_THRESHOLD / DEFAULT_FRAME_THRESHOLD
# (the library's own predict() defaults - not currently exported as named
# constants in basic-pitch 0.4.0, so pinned here to the values in its
# predict() signature instead of imported).
DEFAULT_ONSET_THRESHOLD = 0.5
DEFAULT_FRAME_THRESHOLD = 0.3

# Per-stem Basic Pitch thresholds. Both stems use the library defaults -
# no custom override for either.
#
# "other" onset threshold was previously lowered 0.5 -> 0.45, diagnosed on
# stems/tmp/other_basic_pitch.npz around the 10.79s mark of
# "1nonly - Meaningless Love", where a G#5 note had frame activation peak
# 0.307 (clears the 0.3 frame threshold) and onset activation peak 0.474
# (real signal, but just under the 0.5 default onset threshold) - the note
# was detected but silently dropped for missing the onset cutoff alone.
# That fix (and a follow-on minimum_note_length investigation) was reverted:
# see README, "'other' stem transcription: known limitation on short/soft
# high notes", for why this is now accepted as a known limitation instead.
STEM_THRESHOLDS = {
    "other": {"onset_threshold": DEFAULT_ONSET_THRESHOLD, "frame_threshold": DEFAULT_FRAME_THRESHOLD},
    "bass": {"onset_threshold": DEFAULT_ONSET_THRESHOLD, "frame_threshold": DEFAULT_FRAME_THRESHOLD},
}


def collect_jobs():
    """(song_name, stem_name, input_wav_path) for every stems/<song>/{other,bass}.wav that exists."""
    jobs = []
    if not STEMS_ROOT.is_dir():
        return jobs
    for song_dir in sorted(p for p in STEMS_ROOT.iterdir() if p.is_dir()):
        for stem in STEMS_TO_TRANSCRIBE:
            wav = song_dir / f"{stem}.wav"
            if wav.exists():
                jobs.append((song_dir.name, stem, wav))
    return jobs


def is_done(song_name: str, stem: str) -> bool:
    return (OUTPUT_ROOT / song_name / f"{stem}.mid").exists()


def log_failure(song_name: str, stem: str, path: Path, error: str):
    FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with FAILURE_LOG.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{song_name}\t{stem}\t{path}\t{error}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None,
                         help="Only process the first N not-yet-done (song, stem) jobs (for smoke testing)")
    parser.add_argument("--dry-run", action="store_true",
                         help="List what would be processed, without running Basic Pitch")
    parser.add_argument("--retry-failed", action="store_true",
                         help="Clear the failure log first so previously-failed jobs are attempted again")
    args = parser.parse_args()

    if args.retry_failed and FAILURE_LOG.exists():
        FAILURE_LOG.unlink()

    jobs = collect_jobs()
    todo = [j for j in jobs if not is_done(j[0], j[1])]
    print(f"{len(jobs)} (song, stem) pairs found under {STEMS_ROOT}/, "
          f"{len(jobs) - len(todo)} already done, {len(todo)} to process.")

    if args.limit:
        todo = todo[:args.limit]

    if args.dry_run:
        for name, stem, wav in todo:
            print(f"WOULD TRANSCRIBE: {name}/{stem}  <-  {wav}")
        return

    if not todo:
        print("Nothing to do.")
        return

    # Import + build the model once. TF warm-up is the slow part (tens of
    # seconds), so we pay it once per batch run, not once per file.
    print("Loading Basic Pitch model (TensorFlow warm-up)...", file=sys.stderr)
    from basic_pitch.inference import predict, ICASSP_2022_MODEL_PATH

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    ok, failed = 0, 0
    for i, (song_name, stem, wav) in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {song_name}/{stem} ...", flush=True)
        out_dir = OUTPUT_ROOT / song_name
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{stem}.mid"
        try:
            thresholds = STEM_THRESHOLDS[stem]
            _, midi_data, _ = predict(
                str(wav), ICASSP_2022_MODEL_PATH,
                onset_threshold=thresholds["onset_threshold"],
                frame_threshold=thresholds["frame_threshold"],
            )
            tmp_path = out_path.with_suffix(".mid.tmp")
            midi_data.write(str(tmp_path))
            tmp_path.rename(out_path)  # atomic-ish: a half-written file never looks "done"
            ok += 1
        except Exception as e:
            log_failure(song_name, stem, wav, f"{type(e).__name__}: {e}")
            print(f"  FAILED (see {FAILURE_LOG})")
            traceback.print_exc(file=sys.stderr)
            failed += 1

    print(f"Done. {ok} succeeded, {failed} failed.")
    if failed:
        print(f"See {FAILURE_LOG} for details. Re-run this script to retry (or pass --retry-failed).")


if __name__ == "__main__":
    main()
