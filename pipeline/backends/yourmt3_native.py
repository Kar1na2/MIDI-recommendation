"""Modular local backend using pinned upstream inference, decoding and MIDI writing."""
from collections import Counter
from contextlib import contextmanager
import importlib.metadata
import json
import math
import os
from pathlib import Path
import random
import shutil
import sys
from typing import Protocol

from .yourmt3_support import CHECKPOINT, CHECKPOINT_SHA, EXPERIMENT, REVISION, digest, fingerprint

MODEL_ROOT = Path("output/amt/yourmt3")
# Independently reviewed digest of yourmt3-source-lock.json; protects against a
# mutable model manifest silently blessing changed implementation/checkpoint bytes.
SOURCE_LOCK_SHA256 = "69fb4379ce44d6a0a4a58dc6958d0566b56d9373e0b894f9de29310a23392294"


class TranscriptionBackend(Protocol):
    def transcribe(self, audio, sample_rate: int, output: Path) -> dict: ...


def model_arguments():
    return [EXPERIMENT + "@last.ckpt", "-p", "2024", "-tk", "mc13_full_plus_256",
            "-dec", "multi-t5", "-nl", "26", "-enc", "perceiver-tf", "-sqr", "1",
            "-ff", "moe", "-wf", "4", "-nmoe", "8", "-kmoe", "2", "-act", "silu",
            "-epe", "rope", "-rp", "1", "-ac", "spec", "-hop", "300", "-atc", "1", "-pr", "32"]


def verify(root):
    """Compare bytes to a reviewed, repository-owned manifest, not a mutable download marker."""
    trusted = json.loads(Path(__file__).with_name("yourmt3-source-lock.json").read_text())
    lock_path = Path(__file__).with_name("yourmt3-source-lock.json")
    if digest(lock_path) != SOURCE_LOCK_SHA256 or trusted["revision"] != REVISION:
        raise ValueError("Unexpected source revision")
    allowed = set()
    for entry in trusted["files"]:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid locked path")
        path = root / relative
        if not path.resolve().is_relative_to(root.resolve()) or digest(path) != entry["sha256"]:
            raise ValueError(f"YourMT3 source/checkpoint hash mismatch: {relative}")
        allowed.add(relative.as_posix())
    actual = {p.relative_to(root).as_posix() for p in (root / "amt/src").rglob("*.py")}
    if not actual.issubset(allowed) or digest(root / CHECKPOINT) != CHECKPOINT_SHA:
        raise ValueError("Unreviewed source or checkpoint")
    return digest(lock_path)


@contextmanager
def at_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class YourMT3Backend:
    def __init__(self, root=MODEL_ROOT, device="cuda", batch_size=1, seed=0):
        import numpy as np
        import torch
        self.root = Path(root).resolve()
        self.source_lock = verify(self.root)
        if device not in ("cuda", "cpu") or batch_size < 1:
            raise ValueError("Explicit cpu/cuda device and positive batch size required")
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable; transcription unavailable")
        self.device, self.batch_size, self.seed = device, batch_size, seed
        torch.set_num_threads(4)
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        torch.use_deterministic_algorithms(True)
        torch.backends.cudnn.benchmark = False
        # These upstream generic module names must resolve to the verified source.
        for name in ("model_helper", "model", "config", "utils"):
            if name in sys.modules:
                raise RuntimeError(f"Use a clean process: upstream module collision {name}")
        sys.path[:0] = [str(self.root), str(self.root / "amt/src")]
        from model_helper import load_model_checkpoint
        with at_directory(self.root):
            model = load_model_checkpoint(args=model_arguments(), device="cpu")
        checkpoint = torch.load(self.root / CHECKPOINT, map_location="cpu", weights_only=False)
        state = {k: v for k, v in checkpoint["state_dict"].items() if "pitchshift" not in k}
        model.load_state_dict(state, strict=True)
        del checkpoint, state
        self.model = model.to(device).eval()

    def transcribe(self, audio, sample_rate, output):
        import numpy as np
        from scipy.signal import resample_poly
        import torch
        from utils.audio import slice_padded_array
        from utils.note2event import mix_notes
        from utils.event2note import merge_zipped_note_events_and_ties_to_notes
        from utils.utils import write_model_output_as_midi
        target_rate = self.model.audio_cfg["sample_rate"]
        divisor = math.gcd(sample_rate, target_rate)
        waveform = resample_poly(audio.mean(axis=1), target_rate // divisor,
                                 sample_rate // divisor).astype(np.float32)
        size = self.model.audio_cfg["input_frames"]
        segments = slice_padded_array(waveform.reshape(1, -1), size, size)
        # Keep full-file segments on CPU. Native inference transfers only each batch.
        segments = torch.from_numpy(segments.astype(np.float32)).unsqueeze(1)
        with torch.inference_mode():
            predictions, _ = self.model.inference_file(bsz=self.batch_size, audio_segments=segments)
        np.savez_compressed(output / "native-tokens.npz", **{f"batch_{i}": p for i, p in enumerate(predictions)})
        starts = [size * i / target_rate for i in range(len(segments))]
        channels, errors = [], Counter()
        for channel in range(self.model.task_manager.num_decoding_channels):
            tokens = [batch[:, channel, :] for batch in predictions]
            zipped, _, token_errors = self.model.task_manager.detokenize_list_batches(tokens, starts, return_events=True)
            notes, note_errors = merge_zipped_note_events_and_ties_to_notes(zipped)
            channels.append(notes)
            errors.update(token_errors)
            errors.update(note_errors)
        write_model_output_as_midi(mix_notes(channels), str(output), "native", self.model.midi_output_inverse_vocab)
        shutil.copyfile(output / "model_output/native.mid", output / "raw.mid")
        shutil.rmtree(output / "model_output")
        return {"decoding_errors": {str(k): int(v) for k, v in errors.items()}, "sample_rate": target_rate, "segment_frames": size,
                "segments": len(segments), "source_lock": self.source_lock,
                "hardware": torch.cuda.get_device_name() if self.device == "cuda" else "cpu",
                "peak_cuda_bytes": torch.cuda.max_memory_allocated() if self.device == "cuda" else 0,
                "versions": {p: importlib.metadata.version(p) for p in
                             ("torch", "torchaudio", "transformers", "lightning", "numpy", "pretty-midi")}}


def select(name):
    if name != "yourmt3-plus":
        raise ValueError(f"Unsupported independent backend: {name}; no fallback")
    return YourMT3Backend
