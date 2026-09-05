#!/usr/bin/env python3
"""
Transcription stage, Part B: ADTLib over the "drums" Demucs stem, writing
midi/<song_name>/drums.mid.

*** Must be run with the isolated ADTLib environment's interpreter: ***

    tools/adtlib-env/bin/python transcribe_drums.py [options]

Why a separate env: ADTLib's drum model depends on TensorFlow 1.15 (it uses
the graph-mode `tensorflow.contrib` API, deleted in TF 2.0) and on an
unmaintained `madmom` (last PyPI release Nov 2018 - needs numpy<1.19, and
breaks on Python >=3.10 where `collections.MutableSequence` etc. were
removed). Both are incompatible with the main project's Python 3.11 /
torch / demucs / basic-pitch stack. tools/adtlib-env/ is a from-source
Python 3.7.17 build + venv (numpy 1.18.5, tensorflow==1.15.5, protobuf
pinned to 3.19.6, madmom, ADTLib) that hosts this stack in isolation.
See README for how it was built and why (Omnizart was evaluated as an
alternative - it hits the same broken madmom dependency and adds a much
heavier footprint, e.g. portaudio/fluidsynth/vamp system libs, for no gain).

Reads from stems/<song_name>/drums.wav (output of separate_stems.py).
ADTLib only detects three drum classes - kick, snare, closed hi-hat (no
toms/cymbals/open hi-hat) - as onset times, which we map to General MIDI
drum note numbers (36/38/42) on channel 10 with a fixed 50ms note length.
That's a real ceiling on transcription detail; see README for the trade-off
against Omnizart's heavier CNN-based drum model.

Resumable: a song is skipped if midi/<song_name>/drums.mid already exists.
Failures are logged to midi/_failures_drums.log instead of aborting the batch.

Usage:
    tools/adtlib-env/bin/python transcribe_drums.py                # everything
    tools/adtlib-env/bin/python transcribe_drums.py --limit 3       # smoke test
    tools/adtlib-env/bin/python transcribe_drums.py --dry-run
    tools/adtlib-env/bin/python transcribe_drums.py --retry-failed
"""
import argparse
import sys
import time
import traceback
from pathlib import Path

import mido

STEMS_ROOT = Path("stems")
OUTPUT_ROOT = Path("midi")
FAILURE_LOG = OUTPUT_ROOT / "_failures_drums.log"

# ADTLib's three onset classes -> General MIDI drum note numbers (channel 10)
GM_DRUM_NOTE = {"Kick": 36, "Snare": 38, "Hihat": 42}  # Acoustic Bass Drum / Acoustic Snare / Closed Hi-Hat
TICKS_PER_BEAT = 480
TEMPO_US_PER_BEAT = 500000  # 120 BPM (no tempo detection - onset times are absolute seconds either way)
NOTE_DURATION_SEC = 0.05


def collect_jobs():
    jobs = []
    if not STEMS_ROOT.is_dir():
        return jobs
    for song_dir in sorted(p for p in STEMS_ROOT.iterdir() if p.is_dir()):
        wav = song_dir / "drums.wav"
        if wav.exists():
            jobs.append((song_dir.name, wav))
    return jobs


def is_done(song_name: str) -> bool:
    return (OUTPUT_ROOT / song_name / "drums.mid").exists()


def log_failure(song_name: str, path: Path, error: str):
    FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with FAILURE_LOG.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{song_name}\t{path}\t{error}\n")


def onsets_to_midi(onsets: dict, out_path: Path):
    """onsets: {'Kick': [t0, t1, ...], 'Snare': [...], 'Hihat': [...]} (seconds) -> a .mid file."""
    events = []  # (time_sec, is_note_on, pitch, velocity)
    for name, times in onsets.items():
        pitch = GM_DRUM_NOTE[name]
        for t in times:
            t = float(t)
            events.append((t, True, pitch, 100))
            events.append((t + NOTE_DURATION_SEC, False, pitch, 0))
    # note_off sorts before note_on at the same timestamp, so a dense retrigger
    # of the same drum doesn't collapse into a single held note
    events.sort(key=lambda e: (e[0], e[1]))

    mid = mido.MidiFile(ticks_per_beat=TICKS_PER_BEAT)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=TEMPO_US_PER_BEAT, time=0))
    track.append(mido.Message("program_change", program=0, channel=9, time=0))

    ticks_per_sec = TICKS_PER_BEAT * 1_000_000 / TEMPO_US_PER_BEAT
    last_tick = 0
    for t, is_on, pitch, velocity in events:
        tick = round(t * ticks_per_sec)
        delta = max(0, tick - last_tick)
        last_tick = max(last_tick, tick)
        track.append(mido.Message("note_on" if is_on else "note_off",
                                   note=pitch, velocity=velocity, channel=9, time=delta))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".mid.tmp")
    mid.save(str(tmp_path))
    tmp_path.rename(out_path)  # atomic-ish: a half-written file never looks "done"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None,
                         help="Only process the first N unfinished songs (for smoke testing)")
    parser.add_argument("--dry-run", action="store_true",
                         help="List what would be processed, without running ADTLib")
    parser.add_argument("--retry-failed", action="store_true",
                         help="Clear the failure log first so previously-failed songs are attempted again")
    args = parser.parse_args()

    if sys.version_info[:2] != (3, 7):
        print(f"WARNING: this script expects the tools/adtlib-env Python 3.7 interpreter, "
              f"but is running under Python {sys.version.split()[0]}. "
              f"ADTLib/madmom/TensorFlow 1.15 will likely fail to import.", file=sys.stderr)

    if args.retry_failed and FAILURE_LOG.exists():
        FAILURE_LOG.unlink()

    jobs = collect_jobs()
    todo = [j for j in jobs if not is_done(j[0])]
    print(f"{len(jobs)} songs with a drums.wav found under {STEMS_ROOT}/, "
          f"{len(jobs) - len(todo)} already done, {len(todo)} to process.")

    if args.limit:
        todo = todo[:args.limit]

    if args.dry_run:
        for name, wav in todo:
            print(f"WOULD TRANSCRIBE: {name}/drums  <-  {wav}")
        return

    if not todo:
        print("Nothing to do.")
        return

    from ADTLib import ADT  # deferred: slow TF1.15 import

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    ok, failed = 0, 0
    for i, (song_name, wav) in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {song_name}/drums ...", flush=True)
        out_path = OUTPUT_ROOT / song_name / "drums.mid"
        try:
            result = ADT([str(wav)], text="no", tab="no", output_act="no")
            onsets = result[0]
            onsets_to_midi(onsets, out_path)
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
