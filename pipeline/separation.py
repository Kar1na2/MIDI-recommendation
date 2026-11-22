"""Stage 2: fresh htdemucs separation retaining all four stems."""
from pathlib import Path

from .common import StageError, atomic_path, digest


def run(audio_path, output, device="cpu"):
    from demucs.api import Separator
    import soundfile as sf
    separator = Separator(model="htdemucs", device=device, shifts=0, overlap=.25)
    _, stems = separator.separate_audio_file(str(audio_path))
    required = {"bass", "drums", "other", "vocals"}
    if set(stems) != required:
        raise StageError(f"Expected four htdemucs stems, got {sorted(stems)}")
    folder = Path(output) / "stems"
    folder.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, tensor in stems.items():
        target = folder / f"{name}.wav"
        temporary = atomic_path(target)
        try:
            sf.write(temporary, tensor.cpu().numpy().T, separator.samplerate,
                     subtype="FLOAT", format="WAV")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        paths[name] = str(target.resolve())
    return {"model": "htdemucs", "shifts": 0, "overlap": .25,
            "sample_rate": separator.samplerate, "source_start_s": 0,
            "stems": paths, "stem_sha256": {k: digest(p) for k, p in paths.items()}}


def run_no_drums(audio_path, target, device="cpu"):
    """Create the requested two-stem drumless mix for a piano-only route."""
    import shutil
    import subprocess
    import sys
    import soundfile as sf

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    work = target.parent / "demucs_two_stems"
    command = [sys.executable, "-m", "demucs", "--two-stems", "drums", "--name", "htdemucs",
               "--other-method", "add", "--device", device, "--shifts", "0", "--overlap", ".25", "-o", str(work),
               str(audio_path)]
    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode:
        raise StageError("Demucs --two-stems drums failed: " + process.stderr[-1200:])
    outputs = list(work.rglob("no_drums.wav"))
    if len(outputs) != 1:
        raise StageError(f"Expected one Demucs no_drums.wav output, found {len(outputs)}")
    if outputs[0].resolve() != target.resolve():
        temporary = atomic_path(target)
        try:
            shutil.copy2(outputs[0], temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return {"model": "htdemucs", "two_stems": "drums", "selected_stem": "no_drums",
            "sample_rate": sf.info(target).samplerate, "source_sha256": digest(audio_path),
            "no_drums_path": str(target.resolve()), "no_drums_sha256": digest(target)}
