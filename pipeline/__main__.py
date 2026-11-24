"""python -m pipeline run --song <id> | --all; diagnose --song ..."""
import argparse
import importlib
import json
import subprocess
import sys

from .common import StageError, load_songs
from .runner import Runner, STAGES


def preflight(command="run", whisperx_python=None):
    """Fail before creating outputs or running any stage if core dependencies are absent."""
    print(f"Python executable: {sys.executable}", flush=True)
    failures = []
    if sys.version_info < (3, 11):
        failures.append(f"Python {sys.version_info.major}.{sys.version_info.minor}: project requires Python >=3.11")
        print(f"Preflight Python version: FAILED ({sys.version_info.major}.{sys.version_info.minor}; requires >=3.11)", flush=True)
    else:
        print(f"Preflight Python version: ok ({sys.version_info.major}.{sys.version_info.minor})", flush=True)
    modules = ["muscriptor", "beat_this", "madmom", "huggingface_hub", "pretty_midi",
               "numpy", "soundfile", "yaml", "bs4", "rapidfuzz", "pykakasi",
               "matplotlib", "librosa"]
    if command in ("run", "diagnose"):
        modules.append("demucs")
    for module in modules:
        try:
            importlib.import_module(module)
            print(f"Preflight import {module}: ok", flush=True)
        except Exception as error:
            failures.append(f"{module}: {type(error).__name__}: {error}")
            print(f"Preflight import {module}: FAILED ({type(error).__name__})", flush=True)
    if failures:
        raise StageError("Pipeline preflight failed; no stages started. " + "; ".join(failures))
    if whisperx_python:
        probe = subprocess.run([whisperx_python, "-c",
                                "import sys, whisperx; print(sys.executable)"],
                               capture_output=True, text=True)
        if probe.returncode:
            raise StageError("WhisperX preflight failed; no stages started. " +
                             probe.stderr[-600:])
        print(f"WhisperX executable: {probe.stdout.strip()}", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Full-mix beat-grid rhythm and timestamped lyric sections")
    sub = parser.add_subparsers(dest="command", required=True)
    recompute = sub.add_parser('recompute', help='CPU-only recomputation from cached transcriptions')
    recompute.add_argument('--from', dest='from_stage', choices=('beats',), required=True)
    recompute.add_argument('--songs', nargs='+', required=True)
    recompute.add_argument('--profile', choices=('v1','v2'), default='v1')
    recompute.add_argument('--output-root', default=None)
    click = sub.add_parser('click')
    click.add_argument('--song', required=True)
    click.add_argument('--grid', choices=('raw','dbn','both'), default='both')
    sub.add_parser('score-handcheck')
    for command in ("run", "diagnose"):
        p = sub.add_parser(command)
        selection = p.add_mutually_exclusive_group(required=True)
        selection.add_argument("--song")
        if command == "run":
            selection.add_argument("--all", action="store_true")
        p.add_argument("--manifest", default="songs.yaml")
        p.add_argument("--output-root", default="outputs")
        p.add_argument("--force", action="append", choices=STAGES + ("diagnose",), default=[])
        p.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
        p.add_argument("--backend", choices=("muscriptor", "yourmt3"), default="muscriptor")
        p.add_argument("--model", choices=("small", "medium", "large"), default=None)
        p.add_argument("--meter-policy", choices=("strict", "as_detected", "dbn", "auto"), default=None)
        p.add_argument("--steps-per-bar", type=int, choices=(16, 24, 32, 48), default=None,
                       help="Base grid resolution; per-instrument grid candidates may be selected from the manifest")
        p.add_argument("--novelty-threshold", type=float, default=.5,
                       help="Minimum local novelty for a rhythm-section boundary")
        p.add_argument("--onset-distance", type=float, default=.25,
                       help="Maximum normalized onset symmetric-difference distance (0–1)")
        p.add_argument("--pitch-distance", type=int, default=2,
                       help="Legacy compatibility option; event clustering uses global config similarity 0.85")
        p.add_argument("--contour-distance", type=int, default=1,
                       help="Legacy compatibility option; contours are diagnostic only")
        p.add_argument("--section-window", type=int, default=2,
                       help="Bars on each side of a candidate novelty boundary")
        p.add_argument("--min-section-bars", type=int, default=4,
                       help="Minimum rhythmic section length in bars")
        p.add_argument("--boundary-shuffles", type=int, default=1000,
                       help="Random-downbeat baseline shuffles")
        p.add_argument("--match-confidence", type=float, default=.65)
        p.add_argument("--yourmt3-python", help="Interpreter with the pinned YourMT3+ stack")
        p.add_argument("--yourmt3-root", default="output/amt/yourmt3")
        p.add_argument("--whisperx-python", help="Interpreter with requirements-whisperx.txt")
        if command == "diagnose":
            p.add_argument("--start", required=True, type=float)
            p.add_argument("--end", required=True, type=float)
    args = parser.parse_args(argv)
    if args.command in ('recompute','click','score-handcheck'):
        from . import core, handcheck
        if args.command=='score-handcheck':
            print(json.dumps(handcheck.score(),indent=2)); return 0
        if args.command=='click':
            core.click(core.resolve_songs(args.song)[0],args.grid); return 0
        for song in core.resolve_songs(','.join(args.songs)):
            from .profiles import recompute as profile_recompute
            result=profile_recompute(song, profile=args.profile, output_root=args.output_root)
            print(json.dumps({'song':song['id'],'status':result['status'],
                              'auto_would_choose':result['auto_choice']}))
        return 0
    if (not 0 <= args.novelty_threshold <= 1 or not 0 <= args.match_confidence <= 1 or
            not 0 <= args.onset_distance <= 1 or args.pitch_distance < 0 or args.contour_distance < 0 or
            min(args.section_window, args.min_section_bars) < 1 or args.boundary_shuffles < 1):
        parser.error("Thresholds, distances, windows, and shuffle count are out of range")
    try:
        preflight(args.command, args.whisperx_python)
        songs = load_songs(args.manifest)
        if args.song:
            songs = [s for s in songs if s["id"] == args.song]
            if not songs:
                parser.error(f"No song id {args.song!r} in {args.manifest}")
        failures = 0
        for song in songs:
            runner = Runner(song, output_root=args.output_root, force=args.force,
                            device=args.device, backend=args.backend, model=args.model,
                            meter_policy=args.meter_policy,
                            steps=args.steps_per_bar, threshold=args.novelty_threshold,
                            onset_distance=args.onset_distance, pitch_distance=args.pitch_distance,
                            contour_distance=args.contour_distance,
                            section_window=args.section_window, min_section_bars=args.min_section_bars,
                            boundary_shuffles=args.boundary_shuffles,
                            confidence=args.match_confidence, yourmt3_python=args.yourmt3_python,
                            yourmt3_root=args.yourmt3_root, whisperx_python=args.whisperx_python)
            if args.command == "run":
                result = runner.execute()
                print(json.dumps({"song": song["id"], "status": result["status"],
                                  "acceptance": result["acceptance"],
                                  "report": str(runner.output / "report.json")}, indent=2))
            else:
                from .diagnose import run
                result = run(runner, args.start, args.end)
                print(json.dumps({k: result[k] for k in ("status", "table", "reason") if k in result}, indent=2))
            failures += result["status"] == "failed"
            if any(r.get("model_unavailable") for r in runner.stage_records.values()):
                print("Stopping batch: MuScriptor/model access unavailable; no alternative backend selected.", flush=True)
                break
        return 1 if failures else 0
    except (StageError, OSError, ValueError) as error:
        print(f"Pipeline failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
