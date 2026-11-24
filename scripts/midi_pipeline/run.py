"""One WAV/recording -> native MIDI -> canonical notes -> independent analysis."""
import argparse
import importlib.metadata
import json
import math
import os
from pathlib import Path
import time
import traceback

from .backend import MODEL_ROOT, model_arguments, select
from .schema import from_midi
from .store import (ANALYSIS_VERSION, BACKEND, CHECKPOINT_SHA, DEFAULT_OUTPUT, REVISION,
                    SCHEMA, digest, fingerprint, implementation_digest, save)


def execute(backend, audio_path, source_id, stem, separation_id, start=0., duration=None,
            output_root=DEFAULT_OUTPUT, bpm=None, beat_origin=0., source_origin=0.):
    import numpy as np
    import soundfile as sf
    if not math.isfinite(start) or start < 0 or (duration is not None and
            (not math.isfinite(duration) or duration <= 0)):
        raise ValueError("Finite nonnegative start and positive duration required")
    if bpm is not None and (not math.isfinite(bpm) or bpm <= 0):
        raise ValueError("Positive reviewed tempo required")
    if not math.isfinite(source_origin) or source_origin < 0:
        raise ValueError("Finite nonnegative source origin required")
    if not math.isfinite(beat_origin):
        raise ValueError("Finite beat origin required")
    audio_path = Path(audio_path).resolve()
    info = sf.info(audio_path)
    first = int(start * info.samplerate)
    stop = None if duration is None else first + int(duration * info.samplerate)
    audio, rate = sf.read(audio_path, start=first, stop=stop, always_2d=True, dtype="float32")
    if not len(audio) or not np.isfinite(audio).all():
        raise ValueError("Empty/nonfinite source audio")
    source_start, source_end = source_origin + first / rate, source_origin + (first + len(audio)) / rate
    identity = {"source_sha256": digest(audio_path), "source_audio_id": source_id, "stem": stem,
                "separation_model_version": separation_id, "backend": BACKEND,
                "checkpoint_sha256": CHECKPOINT_SHA, "revision": REVISION,
                "analysis_version": ANALYSIS_VERSION, "source_lock": backend.source_lock,
                "code_sha256": implementation_digest(),
                "processing": {"source_start": source_start, "source_end": source_end,
                               "device": backend.device, "batch_size": backend.batch_size,
                               "seed": backend.seed, "precision": "float32", "resampling": "scipy.resample_poly",
                               "mono": "channel_mean", "model_arguments": model_arguments(),
                               "bpm": bpm, "beat_origin": beat_origin, "source_origin": source_origin},
                "environment": {p: importlib.metadata.version(p) for p in
                                ("torch", "torchaudio", "transformers", "numpy", "scipy", "pretty-midi")}}
    run_id = fingerprint(identity)
    output = Path(output_root).resolve() / run_id
    output.mkdir(parents=True, exist_ok=False)
    run = {"schema": SCHEMA, "identity": identity, "run_id": run_id, "status": "running",
           "source_path": str(audio_path), "source_info": {"sample_rate": rate, "channels": info.channels,
           "recording_duration": info.duration}, "artifacts": {}}
    save(output / "run.json", run)
    elapsed = time.monotonic()
    try:
        # Save the exact decoded input for aligned review, independent of source format.
        sf.write(output / "input.wav", audio, rate, subtype="FLOAT")
        run["backend_result"] = backend.transcribe(audio, rate, output)
        notes = from_midi(output / "raw.mid", identity, run_id, source_start, source_end)
        save(output / "notes.json", notes)
        from .analysis import analyze, compare_windows
        result = analyze(notes, bpm=bpm, beat_origin=beat_origin)
        if source_start <= 6 and source_end >= 18:
            result["six_second_comparison"] = compare_windows(notes, (6, 12), (12, 18))
        save(output / "analysis.json", result)
        from .diagnostics import create
        create(output, notes, result)
        run["status"] = "prediction_available_unvalidated"
        run["note_count"] = len(notes["notes"])
        run["artifacts"] = {p.name: digest(p) for p in output.iterdir() if p.is_file() and p.name != "run.json"}
    except Exception as error:
        run["status"] = "transcription_unavailable"
        run["error"] = {"type": type(error).__name__, "message": str(error)}
        (output / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise
    finally:
        run["runtime_seconds"] = time.monotonic() - elapsed
        save(output / "run.json", run)
        print(json.dumps({"run": str(output), "status": run["status"], "notes": run.get("note_count"),
                          "seconds": run["runtime_seconds"]}), flush=True)
    return output
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--stem", required=True)
    parser.add_argument("--separation-id", required=True, help="Model/version or explicit unknown-existing-stem")
    parser.add_argument("--start", type=float, default=0.)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--model-root", type=Path, default=MODEL_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--backend", default=BACKEND)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--bpm", type=float, help="Explicit reviewed beat grid; native MIDI tempo is ignored")
    parser.add_argument("--beat-origin", type=float, default=0.)
    parser.add_argument("--source-origin", type=float, default=0., help="Original recording offset of a previously cropped source")
    args = parser.parse_args()
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    try:
        backend = select(args.backend)(args.model_root, args.device, args.batch_size, args.seed)
        execute(backend, args.audio, args.source_id, args.stem, args.separation_id, args.start,
                args.duration, args.output_root, args.bpm, args.beat_origin, args.source_origin)
    except Exception as error:
        # This includes initialization failures, before a source run can be created.
        failure = {"status": "transcription_unavailable", "backend": args.backend,
                   "source": str(args.audio), "error": f"{type(error).__name__}: {error}"}
        args.output_root.mkdir(parents=True, exist_ok=True)
        save(args.output_root / f"failure-{time.time_ns()}.json", failure)
        print(json.dumps(failure), flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
