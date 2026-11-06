"""Experimental supervised output-head tuning of the public YourMT3+ model.

Requires complete, aligned MIDI supervision; validates on separate compositions.
This is a small local adaptation, not reproduction of Songscription training.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import random
import sys

import numpy as np
import pretty_midi
import soundfile as sf
from scipy.signal import resample_poly

from .bootstrap_yourmt3 import CHECKPOINT, DEFAULT_ROOT, sha256
from .dataset import load_manifest
from .transcribe import load_verified_model


def prepare_window(row, start, model):
    from utils.note_event_dataclasses import Note
    from utils.note2event import note2note_event, slice_multiple_note_events_and_ties_to_bundle
    info = sf.info(row["audio"])
    sr, size = model.audio_cfg["sample_rate"], model.audio_cfg["input_frames"]
    duration = size / sr
    first = int(start * info.samplerate)
    start = first / info.samplerate
    audio, original_sr = sf.read(row["audio"], start=first,
        frames=int(math.ceil(duration * info.samplerate)), dtype="float32", always_2d=True)
    divisor = math.gcd(original_sr, sr)
    audio = resample_poly(audio.mean(axis=1), sr // divisor, original_sr // divisor)
    audio = np.pad(audio[:size], (0, max(0, size - len(audio))))
    if not np.isfinite(audio).all():
        raise ValueError("Nonfinite audio samples")
    midi = pretty_midi.PrettyMIDI(row["midi"])
    notes = [Note(inst.is_drum, inst.program, n.start, n.end, n.pitch, 1)
             for inst in midi.instruments for n in inst.notes]
    unknown = {n.program for n in notes if not n.is_drum} - set(model.task_manager.program2channel_vocab)
    if unknown:
        raise ValueError(f"Reference contains unsupported programs: {sorted(unknown)}")
    events = note2note_event(notes)
    bundle = slice_multiple_note_events_and_ties_to_bundle(events, [start], duration)
    tokens = model.task_manager.tokenize_note_events_batch(bundle)
    # The upstream tokenizer may truncate dense labels. Never silently train
    # an incomplete target as if it represented the complete audio.
    if not np.all(np.any(tokens == model.task_manager.tokenizer.eos_id, axis=-1)):
        raise ValueError("Target exceeds token budget; choose a less dense excerpt")
    return audio.astype(np.float32)[None, None, :], tokens.astype(np.int64)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cuda")
    args = parser.parse_args()
    if args.steps < 1 or not math.isfinite(args.learning_rate) or args.learning_rate <= 0:
        parser.error("Steps and finite learning rate must be positive")
    if args.output_dir.exists():
        parser.error("Output directory already exists")
    rows = load_manifest(args.manifest)
    training = [r for r in rows if r["split"] == "train"]
    validation = [r for r in rows if r["split"] == "validation"]
    if not training or not validation:
        parser.error("Require separate train and validation compositions")
    root = args.model_root.resolve()
    sys.path[:0] = [str(root), str(root / "amt/src")]
    import torch
    torch.manual_seed(args.seed)
    torch.set_num_threads(4)
    rng = random.Random(args.seed)
    model = load_verified_model(root, args.device)
    # Freeze the acoustic model and decoder; adapt the vocabulary projection.
    # This is deliberately small enough to verify on the local GPU.
    for param in model.parameters():
        param.requires_grad_(False)
    for param in model.lm_head.parameters():
        param.requires_grad_(True)
    trainable = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(trainable, lr=args.learning_rate)
    size = model.audio_cfg["input_frames"]
    sr = model.audio_cfg["sample_rate"]

    def tensors(row, start):
        audio, tokens = prepare_window(row, start, model)
        return torch.from_numpy(audio).to(args.device), torch.from_numpy(tokens).to(args.device)

    # Fixed validation starts, never sampled for optimizer updates.
    validation_windows = [tensors(row, max(0., (row["duration"] - size / sr) / 2)) for row in validation[:8]]

    def validate():
        with torch.inference_mode():
            return float(np.mean([model(x, y)["loss"].item() for x, y in validation_windows]))

    initial_validation = validate()
    losses = []
    for step in range(args.steps):
        row = rng.choice(training)
        start = rng.uniform(0., max(0., row["duration"] - size / sr))
        x, y = tensors(row, start)
        optimizer.zero_grad(set_to_none=True)
        loss = model(x, y)["loss"]
        if not torch.isfinite(loss):
            raise ValueError("Nonfinite training loss")
        loss.backward()
        torch.nn.utils.clip_grad_norm_(trainable, 1.)
        optimizer.step()
        losses.append(float(loss.item()))
        print(f"step {step + 1}/{args.steps}: loss={losses[-1]:.5f}", flush=True)
    final_validation = validate()
    if not np.isfinite([initial_validation, final_validation]).all():
        raise ValueError("Nonfinite validation loss")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    base_hash = sha256(root / CHECKPOINT)
    state = {key: tensor.detach().cpu() for key, tensor in model.state_dict().items() if key.startswith("lm_head.")}
    torch.save({"schema_version": 1, "base_checkpoint_sha256": base_hash, "state_dict": state},
               args.output_dir / "adapter.pt")
    report = {"method": "supervised_output_head_only", "base_checkpoint_sha256": base_hash,
              "steps": args.steps, "seed": args.seed, "learning_rate": args.learning_rate,
              "trainable_parameters": sum(p.numel() for p in trainable), "train_loss": losses,
              "initial_validation_token_loss": initial_validation, "final_validation_token_loss": final_validation,
              "status": "experimental_requires_note_level_evaluation",
              "manifest_sha256": sha256(args.manifest), "examples": rows,
              "validation_window_count": len(validation_windows), "test_examples_used": 0}
    (args.output_dir / "training.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"examples", "train_loss"}}, indent=2))


if __name__ == "__main__":
    main()
