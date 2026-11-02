"""Build the static MIDI note data used by the Meaningless Love experiment page."""
from __future__ import annotations

import json
from pathlib import Path

import pretty_midi

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/data/experiment/arpeggio_clips.json"

CLIPS = [
    {
        "id": "meaningless-love",
        "title": "1nonly — Meaningless Love",
        "kind": "target",
        "label": "Original · other-stem MIDI",
        "source": "Basic Pitch transcription of the separated other stem",
        "midi_path": ROOT / "midi/1nonly - Meaningless Love (Official Music Video)/other.mid",
        "clip_start": 3.0,
        "clip_end": 15.0,
        "focus_start": 7.55,
        "focus_end": 8.91,
        "audio": {"type": "local", "src": "audio/1nonly-meaningless-love-official-music-video.mp3"},
    },
    {
        "id": "debussy-arabesque",
        "title": "Debussy — Arabesque No. 1",
        "kind": "reference",
        "label": "Reference MIDI · equal-count triple",
        "source": "Outside reference-score MIDI · Mutopia edition",
        "midi_path": ROOT / "output/analysis/arpeggio_experiment/references/Debussy_Arabesque_1.mid",
        "clip_start": 52.9721326667,
        "clip_end": 60.083242,
        "focus_start": 55.9721326667,
        "focus_end": 57.083242,
        "audio": {"type": "midi", "src": None},
    },
    {
        "id": "taeyang-eyes-nose-lips",
        "title": "TAEYANG — Eyes, Nose, Lips",
        "kind": "mention",
        "label": "Honorable mention · local MIDI candidate",
        "source": "Project-catalogue Basic Pitch MIDI",
        "midi_path": ROOT / "midi/TAEYANG - 눈,코,입 (Eyes, Nose, Lips) [0lYtIvI7bO51PZSeK22Mbz]/other.mid",
        "clip_start": 31.4795454545,
        "clip_end": 39.7568181818,
        "focus_start": 34.4795454545,
        "focus_end": 36.7568181818,
        "audio": {
            "type": "youtube",
            "video_id": "UwuAPyOImoI",
            "clip_start": 31.4795454545,
            "clip_end": 39.7568181818,
        },
    },
    {
        "id": "kiiikii-delulu",
        "title": "KiiiKiii — Delulu",
        "kind": "mention",
        "label": "Honorable mention · local MIDI candidate",
        "source": "Project-catalogue Basic Pitch MIDI",
        "midi_path": ROOT / "midi/KiiiKiii - Delulu [3BWXNAPGGWhCJwsOFolnBB]/other.mid",
        "clip_start": 3.6090909091,
        "clip_end": 11.6545454545,
        "focus_start": 6.6090909091,
        "focus_end": 8.6545454545,
        "audio": {
            "type": "youtube",
            "video_id": "PBKbf9wlntg",
            "clip_start": 3.6090909091,
            "clip_end": 11.6545454545,
        },
    },
]


def main() -> None:
    output = []
    for clip in CLIPS:
        midi = pretty_midi.PrettyMIDI(str(clip["midi_path"]))
        notes = []
        for instrument in midi.instruments:
            for note in instrument.notes:
                if note.end <= clip["clip_start"] or note.start >= clip["clip_end"]:
                    continue
                notes.append({
                    "start": round(max(note.start, clip["clip_start"]) - clip["clip_start"], 4),
                    "end": round(min(note.end, clip["clip_end"]) - clip["clip_start"], 4),
                    "pitch": int(note.pitch),
                    "velocity": int(note.velocity),
                })
        notes.sort(key=lambda item: (item["start"], item["pitch"]))
        output.append({
            **{key: value for key, value in clip.items() if key != "midi_path"},
            "duration": round(clip["clip_end"] - clip["clip_start"], 4),
            "focus_start_relative": round(clip["focus_start"] - clip["clip_start"], 4),
            "focus_end_relative": round(clip["focus_end"] - clip["clip_start"], 4),
            "note_count": len(notes),
            "notes": notes,
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(output)} MIDI excerpts to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
