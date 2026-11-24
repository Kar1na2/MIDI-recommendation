"""Run the pinned YourMT3+ model on a recording or stem; preserve its clock.

Decoding follows the public mimbres/YourMT3 model_helper.py interface.
Experimental output is kept separate from midi/, merged/, and the player.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager, nullcontext
import csv
import importlib.metadata
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

import numpy as np
import pretty_midi
import soundfile as sf
from scipy.signal import resample_poly

from .bootstrap_yourmt3 import DEFAULT_ROOT, EXPERIMENT, CHECKPOINT, sha256


@contextmanager
def working_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def model_arguments():
    return [EXPERIMENT + "@last.ckpt", "-p", "2024", "-tk", "mc13_full_plus_256",
            "-dec", "multi-t5", "-nl", "26", "-enc", "perceiver-tf", "-sqr", "1",
            "-ff", "moe", "-wf", "4", "-nmoe", "8", "-kmoe", "2", "-act", "silu",
            "-epe", "rope", "-rp", "1", "-ac", "spec", "-hop", "300", "-atc", "1", "-pr", "32"]


def load_verified_model(root, device):
    """Require every inference weight; upstream loads with strict=False."""
    import torch
    from model_helper import load_model_checkpoint
    with working_directory(root):
        model = load_model_checkpoint(args=model_arguments(), device="cpu")
    checkpoint = torch.load(root / CHECKPOINT, map_location="cpu", weights_only=False)
    state = {k: v for k, v in checkpoint["state_dict"].items() if "pitchshift" not in k}
    model.load_state_dict(state, strict=True)
    del checkpoint, state
    return model.to(device).eval()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory for this run")
    parser.add_argument("--model-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--start", type=float, default=0.)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--precision", choices=["float32", "float16"], default="float32")
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--adapter", type=Path, help="Output-head adapter from scripts.amt.finetune")
    args = parser.parse_args()
    if not math.isfinite(args.start) or args.start < 0 or (args.duration is not None and
            (not math.isfinite(args.duration) or args.duration <= 0)) or args.batch_size < 1 or args.threads < 1:
        parser.error("Use a finite nonnegative start, positive duration, batch size, and thread count")
    audio_path, root, out = args.audio.resolve(), args.model_root.resolve(), args.output_dir.resolve()
    if out.exists():
        parser.error("Output directory already exists; choose a new run name")
    if not (root / "provenance.json").is_file() or not (root / CHECKPOINT).is_file():
        parser.error("Run python -m scripts.amt.bootstrap_yourmt3 first")
    info = sf.info(audio_path)
    first = int(args.start * info.samplerate)
    stop = None if args.duration is None else first + int(args.duration * info.samplerate)
    audio, sr = sf.read(audio_path, start=first, stop=stop, dtype="float32", always_2d=True)
    source_start = first / sr
    if not len(audio) or not np.isfinite(audio).all():
        parser.error("Requested audio is empty or contains nonfinite samples")
    source_duration = len(audio) / sr
    audio = audio.mean(axis=1)
    import torch
    torch.set_num_threads(args.threads)
    device = "cuda" if args.device == "auto" and torch.cuda.is_available() else args.device
    if device == "auto":
        device = "cpu"
    if device == "cuda" and not torch.cuda.is_available():
        parser.error("CUDA requested but unavailable")
    if device != "cuda" and args.precision == "float16":
        parser.error("Float16 inference requires CUDA")
    sys.path[:0] = [str(root), str(root / "amt/src")]
    from utils.audio import slice_padded_array
    from utils.note2event import mix_notes
    from utils.event2note import merge_zipped_note_events_and_ties_to_notes
    from utils.utils import write_model_output_as_midi

    elapsed = time.monotonic()
    print(f"YourMT3+: {audio_path.name}, {source_start:.3f}–{source_start + source_duration:.3f}s ({device})", flush=True)
    model = load_verified_model(root, device)
    if args.adapter:
        adapter = torch.load(args.adapter, map_location="cpu", weights_only=True)
        if adapter.get("schema_version") != 1 or adapter.get("base_checkpoint_sha256") != sha256(root / CHECKPOINT):
            raise ValueError("Adapter does not match this base checkpoint")
        expected = {k for k in model.state_dict() if k.startswith("lm_head.")}
        if set(adapter["state_dict"]) != expected:
            raise ValueError("Invalid output-head adapter keys")
        model.load_state_dict(adapter["state_dict"], strict=False)
    target_sr = model.audio_cfg["sample_rate"]
    divisor = math.gcd(sr, target_sr)
    audio = resample_poly(audio, target_sr // divisor, sr // divisor).astype(np.float32)
    size = model.audio_cfg["input_frames"]
    segments = slice_padded_array(audio.reshape(1, -1), size, size)
    segments = torch.from_numpy(segments.astype(np.float32)).unsqueeze(1).to(device)
    precision = torch.autocast("cuda", dtype=torch.float16) if args.precision == "float16" else nullcontext()
    with torch.inference_mode(), precision:
        predictions, _ = model.inference_file(bsz=args.batch_size, audio_segments=segments)
    start_times = [size * i / target_sr for i in range(len(segments))]
    notes_by_channel, errors = [], Counter()
    for channel in range(model.task_manager.num_decoding_channels):
        channel_tokens = [batch[:, channel, :] for batch in predictions]
        zipped, _, token_errors = model.task_manager.detokenize_list_batches(
            channel_tokens, start_times, return_events=True)
        notes, note_errors = merge_zipped_note_events_and_ties_to_notes(zipped)
        notes_by_channel.append(notes)
        errors.update(token_errors)
        errors.update(note_errors)
    with tempfile.TemporaryDirectory() as temporary:
        write_model_output_as_midi(mix_notes(notes_by_channel), temporary, "transcription", model.midi_output_inverse_vocab)
        midi = pretty_midi.PrettyMIDI(str(Path(temporary) / "model_output/transcription.mid"))
    removed_padding = 0
    for instrument in midi.instruments:
        retained = []
        for note in instrument.notes:
            if note.start >= source_duration or note.end <= 0:
                removed_padding += 1
                continue
            note.start = max(0., note.start) + source_start
            note.end = min(source_duration, note.end) + source_start
            if note.end > note.start:
                retained.append(note)
        instrument.notes = retained
        for event in instrument.control_changes + instrument.pitch_bends:
            event.time += source_start
    rows = [{"track": index, "instrument": inst.name, "program": int(inst.program),
             "is_drum": bool(inst.is_drum), "onset": float(n.start), "offset": float(n.end), "pitch": int(n.pitch),
             "velocity": int(n.velocity)} for index, inst in enumerate(midi.instruments) for n in inst.notes]
    rows.sort(key=lambda n: (n["onset"], n["track"], n["pitch"]))
    meta = {"engine": "YourMT3+ YPTF.MoE+Multi noPS", "status": "experimental_unvalidated",
            "source_audio": str(audio_path), "source_sha256": sha256(audio_path),
            "source_start": source_start, "source_end": source_start + source_duration,
            "absolute_timestamps": True, "clock": "input_recording_seconds",
            "tempo_status": "placeholder_120_bpm_not_estimated", "quantized": False,
            "model": json.loads((root / "provenance.json").read_text(encoding="utf-8")),
            "checkpoint_sha256": sha256(root / CHECKPOINT), "model_arguments": model_arguments(),
            "device": device, "precision": args.precision, "batch_size": args.batch_size, "note_count": len(rows),
            "adapter_sha256": sha256(args.adapter) if args.adapter else None,
            "tracks": [{"track": i, "name": inst.name, "program": int(inst.program),
                        "is_drum": bool(inst.is_drum), "notes": len(inst.notes)} for i, inst in enumerate(midi.instruments)],
            "decoding_errors": {k: int(v) for k, v in errors.items()}, "padding_notes_removed": removed_padding,
            "runtime_seconds": time.monotonic() - elapsed,
            "versions": {name: importlib.metadata.version(name) for name in
                         ["torch", "transformers", "numpy", "pretty-midi", "lightning"]}}
    serialized = json.dumps(meta, indent=2)
    out.mkdir(parents=True, exist_ok=False)
    midi.write(str(out / "transcription.mid"))
    with (out / "notes.csv").open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=["track", "instrument", "program", "is_drum", "onset", "offset", "pitch", "velocity"])
        writer.writeheader()
        writer.writerows(rows)
    (out / "run.json").write_text(serialized, encoding="utf-8")
    print(json.dumps({k: meta[k] for k in ["note_count", "tracks", "runtime_seconds", "decoding_errors"]}, indent=2))


if __name__ == "__main__":
    main()
