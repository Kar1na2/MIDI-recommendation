"""Known MIDI -> deterministic additive audio; labels never come from predictions."""
import argparse
from pathlib import Path

import numpy as np
import pretty_midi
import soundfile as sf

from .store import digest, save


def generate(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    # program, pitch, onset, offset; silence is intentionally empty.
    cases = {
        "silence": [],
        "sustained": [(0, 60, .5, 4.), (0, 67, 4.5, 6.)],
        "overlap": [(0, p, .5, 3.) for p in (60, 64, 67)] + [(32, 48, 1.5, 5.)],
        "arpeggios": [(0, p, .5 + repeat * 2.8 + i * .4, .8 + repeat * 2.8 + i * .4)
                      for repeat in range(2) for i, p in enumerate((60, 64, 67, 72, 67, 64))],
        "instrumentation": [(program, p, .5 + repeat * 2.8 + i * .4, .8 + repeat * 2.8 + i * .4)
                            for repeat, program in enumerate((0, 24)) for i, p in enumerate((60, 64, 67, 72, 67, 64))],
        "tempo_variation": [(0, p + shift, base + i * gap, base + i * gap + gap * .75)
                            for base, gap, shift in ((.5, .32, 0), (3., .46, 2))
                            for i, p in enumerate((60, 64, 67, 72, 67, 64))],
    }
    rows = []
    rate = 16000
    for name, events in cases.items():
        folder = root / name
        folder.mkdir()
        midi = pretty_midi.PrettyMIDI(initial_tempo=120)
        instruments = {}
        for program, pitch, onset, offset in events:
            inst = instruments.setdefault(program, pretty_midi.Instrument(program, name=f"synthetic-program-{program}"))
            inst.notes.append(pretty_midi.Note(80, pitch, onset, offset))
        midi.instruments.extend(instruments.values())
        midi.write(str(folder / "reference.mid"))
        # Render the saved MIDI, not an independent list, so rounding matches labels.
        midi = pretty_midi.PrettyMIDI(str(folder / "reference.mid"))
        wave = np.zeros(7 * rate, dtype=np.float32)
        labels = []
        for track, inst in enumerate(midi.instruments):
            for n in inst.notes:
                first, stop = round(n.start * rate), round(n.end * rate)
                t = np.arange(stop - first) / rate
                attack = .008 if inst.program == 0 else .025
                envelope = np.minimum(t / attack, 1) * np.minimum((n.end - n.start - t) / .03, 1)
                frequency = pretty_midi.note_number_to_hz(n.pitch)
                harmonics = (1., .3, .12) if inst.program == 0 else (1., .6, .3)
                tone = sum(weight * np.sin(2 * np.pi * frequency * (i + 1) * t)
                           for i, weight in enumerate(harmonics))
                wave[first:stop] += .1 * envelope * tone
                labels.append({"pitch": int(n.pitch), "onset": float(n.start), "offset": float(n.end),
                               "program": int(inst.program), "is_drum": False, "track": track})
        sf.write(folder / "audio.wav", wave, rate, subtype="FLOAT")
        pairs = ([[.5, 3.3]] if name in ("arpeggios", "instrumentation") else
                 [[.5, 3.]] if name == "tempo_variation" else [])
        save(folder / "reference.json", {"label_source": "synthetic_midi_render", "complete_notes": True,
             "notes": labels, "target_motif_occurrence_start_pairs": pairs,
             "motif_scope": "the intentionally repeated six-note figure; target-specific evaluation"})
        rows.append({"case": name, "audio": str(folder / "audio.wav"), "midi": str(folder / "reference.mid"),
                     "reference": str(folder / "reference.json"), "audio_sha256": digest(folder / "audio.wav")})
    save(root / "manifest.json", {"renderer": "deterministic additive harmonics, no reverb or pedal; engineering fixtures, not realistic instrument benchmarks",
                                  "sample_rate": rate, "cases": rows})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(generate(parser.parse_args().output))


if __name__ == "__main__":
    main()
