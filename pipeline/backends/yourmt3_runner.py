"""Native comparison process, isolated from the primary NumPy 2 environment."""
import argparse
from pathlib import Path

from ..common import Note, note_dicts, save


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import soundfile as sf
    import pretty_midi
    from .yourmt3_native import YourMT3Backend
    audio, rate = sf.read(args.audio, always_2d=True, dtype="float32")
    backend = YourMT3Backend(root=args.root, device=args.device)
    provenance = backend.transcribe(audio, rate, args.output)
    midi = pretty_midi.PrettyMIDI(str(args.output / "raw.mid"))
    notes = [Note(n.start, n.end, n.pitch, None,
                  "drums" if inst.is_drum else f"gm_{inst.program:03d}")
             for inst in midi.instruments for n in inst.notes]
    save(args.output / "notes.json", {"notes": note_dicts(notes), "provenance": provenance,
         "warnings": ["YourMT3+ drum offsets are writer conventions; velocities are uncalibrated."]})


if __name__ == "__main__":
    main()
