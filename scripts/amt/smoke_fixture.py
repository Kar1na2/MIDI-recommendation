"""Generate tiny known-label audio/MIDI pairs for engineering checks only.

These deliberately simple synthetic tones cannot validate real-music accuracy.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pretty_midi
import soundfile as sf


def create_fixture(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for split, pitches in [("train", [60, 64, 67, 72]), ("validation", [62, 65, 69, 74])]:
        audio = np.zeros(40000, dtype=np.float32)
        midi = pretty_midi.PrettyMIDI(initial_tempo=120)
        instrument = pretty_midi.Instrument(program=0, name="synthetic_piano_label")
        for index, pitch in enumerate(pitches):
            onset, duration = .1 + index * .5, .35
            instrument.notes.append(pretty_midi.Note(80, pitch, onset, onset + duration))
            time = np.arange(int(duration * 16000)) / 16000
            frequency = pretty_midi.note_number_to_hz(pitch)
            envelope = np.minimum(time / .01, 1) * np.minimum((duration - time) / .03, 1)
            wave = .15 * envelope * (np.sin(2 * np.pi * frequency * time) + .25 * np.sin(4 * np.pi * frequency * time))
            first = int(onset * 16000)
            audio[first:first + len(wave)] += wave
        midi.instruments.append(instrument)
        sf.write(output / f"{split}.wav", audio, 16000)
        midi.write(str(output / f"{split}.mid"))
        rows.append({"audio": f"{split}.wav", "midi": f"{split}.mid", "group_id": f"engineering-{split}",
                     "split": split, "label_source": "synthetic", "license": "project-generated test tones",
                     "complete_annotation": True})
    (output / "pairs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return output / "pairs.jsonl"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(create_fixture(args.output_dir))


if __name__ == "__main__":
    main()
