"""Fresh, non-destructive Demucs separation with source clocks and weight hashes."""
import argparse
import importlib.metadata
from pathlib import Path

from .store import digest, fingerprint, save


def separate(audio_path, output_root, start=0., duration=None, model_name="htdemucs_ft", device="cuda"):
    import numpy as np
    import soundfile as sf
    import torch
    import torchaudio.functional as functional
    from demucs.api import Separator
    if start < 0 or not np.isfinite(start) or (duration is not None and (not np.isfinite(duration) or duration <= 0)):
        raise ValueError("Invalid source interval")
    signatures = {"htdemucs_ft": ("f7e0c4bc", "d12395a8", "92cfc3b6", "04573f0d"),
                  "htdemucs": ("955717e8",)}
    if model_name not in signatures:
        raise ValueError("Unreviewed separation backend")
    checkpoints = Path(torch.hub.get_dir()) / "checkpoints"
    weights = {}
    for signature in signatures[model_name]:
        found = list(checkpoints.glob(signature + "-*.th"))
        if len(found) != 1:
            raise FileNotFoundError(f"Install/review Demucs weight {signature} explicitly; no automatic model download")
        weights[found[0].name] = digest(found[0])
    info = sf.info(audio_path)
    first = int(start * info.samplerate)
    stop = None if duration is None else first + int(duration * info.samplerate)
    audio, rate = sf.read(audio_path, start=first, stop=stop, always_2d=True, dtype="float32")
    if not len(audio) or not np.isfinite(audio).all():
        raise ValueError("Invalid input audio")
    identity = {"source_sha256": digest(audio_path), "model": model_name, "weights": weights,
                "demucs_version": importlib.metadata.version("demucs"), "start": first / rate,
                "duration": len(audio) / rate, "shifts": 0, "overlap": .25, "device": device,
                "code_sha256": digest(__file__)}
    output = Path(output_root) / fingerprint(identity)
    output.mkdir(parents=True, exist_ok=False)
    save(output / "separation.json", {"status": "running", "identity": identity})
    try:
        separator = Separator(model=model_name, device=device, shifts=0, overlap=.25, progress=True)
        tensor = torch.from_numpy(audio.T.copy())
        _, stems = separator.separate_tensor(tensor, sr=rate)
        for name, stem in stems.items():
            sf.write(output / f"{name}.wav", stem.cpu().numpy().T, separator.samplerate, subtype="FLOAT")
        save(output / "separation.json", {"status": "complete", "identity": identity,
             "source_path": str(Path(audio_path).resolve()), "source_origin_seconds": first / rate,
             "stems": {name: digest(output / f"{name}.wav") for name in stems}})
    except Exception as error:
        save(output / "separation.json", {"status": "separation_unavailable", "identity": identity, "error": str(error)})
        raise
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=Path("output/midi_pipeline/separations"))
    parser.add_argument("--start", type=float, default=0.)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--model", choices=("htdemucs_ft", "htdemucs"), default="htdemucs_ft")
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    args = parser.parse_args()
    print(separate(args.audio, args.output_root, args.start, args.duration, args.model, args.device))


if __name__ == "__main__":
    main()
