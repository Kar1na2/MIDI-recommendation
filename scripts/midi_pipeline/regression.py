"""Small reproducible suite; no library enumeration, labels never enter inference."""
import argparse
import json
import os
from pathlib import Path

from .backend import YourMT3Backend
from .evaluation import motif_score, score
from .fixtures import generate
from .run import execute
from .store import digest, load_run, save

TRACK_ID = "6rQCPgrLJMQavladOGpwZt"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("synthetic", "meaningful"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    backend = YourMT3Backend(device=args.device)
    results = []
    if args.suite == "synthetic":
        cases = generate(args.output / "fixtures")
        for case in cases:
            run_path = execute(backend, case["audio"], case["case"], "synthetic-mix", "none-synthetic",
                               output_root=args.output / "runs")
            _, notes = load_run(run_path)
            reference = json.loads(Path(case["reference"]).read_text())
            analysis = json.loads((run_path / "analysis.json").read_text())
            metrics = score(reference, notes)
            metrics["rhythm_pattern"] = motif_score(reference, analysis, "rhythm")
            metrics["motif_recurrence"] = motif_score(reference, analysis, "notes")
            save(run_path / "evaluation.json", metrics)
            results.append({"case": case["case"], "run": str(run_path), "metrics": metrics})
            save(args.output / "results.json", results)
    else:
        original = Path("liked-audio") / f"Silo - meaningful love - instrumental [{TRACK_ID}].mp3"
        stem_root = Path("instrumentals/stems") / TRACK_ID
        # Compare every stem; do not infer that Other owns every pitched note.
        cases = [(original, "original-mix", "none", 0., 24.)]
        cases += [(stem_root / f"{stem}.wav", f"dedicated-{stem}",
                   "existing-demucs-htdemucs_ft-unverified-version", 6., 12.)
                  for stem in ("bass", "drums")]
        cases += [(stem_root / "other.wav", "dedicated-other-context",
                   "existing-demucs-htdemucs_ft-unverified-version", 0., 24.)]
        old_root = Path("stems") / original.stem
        cases += [(old_root / f"{stem}.wav", f"library-{stem}",
                   "existing-demucs-htdemucs_ft-unverified-version", 6., 12.)
                  for stem in ("other", "bass", "drums", "vocals")]
        for path, stem, separation, start, duration in cases:
            run_path = execute(backend, path, TRACK_ID, stem, separation, start, duration,
                               output_root=args.output / "runs")
            run, notes = load_run(run_path)
            analysis = json.loads((run_path / "analysis.json").read_text())
            results.append({"stem": stem, "run": str(run_path), "source_sha256": run["identity"]["source_sha256"],
                            "note_count": len(notes["notes"]), "decoding_errors": run["backend_result"]["decoding_errors"],
                            "comparison": analysis.get("six_second_comparison")})
            save(args.output / "results.json", results)


if __name__ == "__main__":
    main()
