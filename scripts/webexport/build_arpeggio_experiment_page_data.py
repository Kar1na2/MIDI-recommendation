"""Build the static MIDI note data used by the Meaningless Love experiment page."""
from __future__ import annotations

import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/data/experiment/arpeggio_clips.json"

# Passage times are scan findings; excerpts include a three-second lead-in/out.
CLIPS = [
    {
        "id": "meaningless-love", "title": "1nonly — Meaningless Love", "kind": "target",
        "label": "Original · other-stem MIDI", "source": "Basic Pitch transcription of the separated other stem",
        "midi_path": ROOT / "midi/1nonly - Meaningless Love (Official Music Video)/other.mid",
        "passage_start": 7.55, "passage_end": 8.91, "clip_start": 4.55, "clip_end": 11.91,
        "audio": {"type": "local", "src": "audio/1nonly-meaningless-love-official-music-video.mp3"},
    },
    {
        "id": "chopin-etude-op10-no1", "title": "Chopin — Étude Op. 10 No. 1", "kind": "reference",
        "label": "Classical reference · MIDI", "source": "Outside reference-score MIDI · Mutopia edition",
        "midi_path": ROOT / "output/analysis/arpeggio_experiment/references/Chopin_Etude_Op10_No1.mid",
        "passage_start": 21.7329, "passage_end": 22.7557, "clip_start": 18.7329, "clip_end": 25.7557,
        "audio": {"type": "youtube", "video_id": "9E82wwNc7r8"},
    },
    {
        "id": "moonlight-sonata", "title": "Beethoven — Moonlight Sonata, Movement I", "kind": "reference",
        "label": "Classical reference · MIDI", "source": "Outside reference-score MIDI · public-domain score transcription",
        "midi_path": ROOT / "output/analysis/arpeggio_experiment/references/Moonlight/moonlight1.mid",
        "passage_start": 16.0, "passage_end": 19.0, "clip_start": 13.0, "clip_end": 22.0,
        "audio": {"type": "youtube", "video_id": "JnBxRCQbcxs"},
    },
    {
        "id": "debussy-arabesque", "title": "Debussy — Arabesque No. 1", "kind": "reference",
        "label": "Classical reference · MIDI", "source": "Outside reference-score MIDI · Mutopia edition",
        "midi_path": ROOT / "output/analysis/arpeggio_experiment/references/Debussy_Arabesque_1.mid",
        "passage_start": 40.1388, "passage_end": 42.3610, "clip_start": 37.1388, "clip_end": 45.3610,
        "audio": {"type": "youtube", "video_id": "NyYm0fmizlE"},
    },
    {
        "id": "taeyang-eyes-nose-lips", "title": "TAEYANG — Eyes, Nose, Lips", "kind": "mention",
        "label": "Honorable mention · local MIDI candidate", "source": "Project-catalogue Basic Pitch MIDI",
        "midi_path": ROOT / "midi/TAEYANG - 눈,코,입 (Eyes, Nose, Lips) [0lYtIvI7bO51PZSeK22Mbz]/other.mid",
        "passage_start": 34.4795, "passage_end": 36.7568, "clip_start": 31.4795, "clip_end": 39.7568,
        "audio": {"type": "youtube", "video_id": "UwuAPyOImoI"},
    },
    {
        "id": "kiiikii-delulu", "title": "KiiiKiii — Delulu", "kind": "mention",
        "label": "Honorable mention · local MIDI candidate", "source": "Project-catalogue Basic Pitch MIDI",
        "midi_path": ROOT / "midi/KiiiKiii - Delulu [3BWXNAPGGWhCJwsOFolnBB]/other.mid",
        "passage_start": 6.6091, "passage_end": 8.6545, "clip_start": 3.6091, "clip_end": 11.6545,
        "audio": {"type": "youtube", "video_id": "PBKbf9wlntg"},
    },
]


def variable_length(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    while True:
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 0x7F)
        if byte < 0x80:
            return value, offset


def read_midi(path: Path) -> list[tuple[float, float, int, int]]:
    """Read note events from a standard PPQN MIDI file without third-party packages."""
    data = path.read_bytes()
    if data[:4] != b"MThd":
        raise ValueError(f"Not a standard MIDI file: {path}")
    header_length = struct.unpack_from(">I", data, 4)[0]
    _, track_count, division = struct.unpack_from(">HHH", data, 8)
    if division & 0x8000:
        raise ValueError(f"SMPTE time division is not supported: {path}")
    offset = 8 + header_length
    tracks: list[list[tuple[int, int, int, int]]] = []
    tempos: list[tuple[int, int]] = [(0, 500_000)]
    for _ in range(track_count):
        if data[offset:offset + 4] != b"MTrk":
            raise ValueError(f"Malformed track chunk: {path}")
        size = struct.unpack_from(">I", data, offset + 4)[0]
        track = data[offset + 8:offset + 8 + size]
        offset += 8 + size
        cursor = tick = running = 0
        events = []
        while cursor < len(track):
            delta, cursor = variable_length(track, cursor)
            tick += delta
            status = track[cursor]
            if status < 0x80:
                status = running
            else:
                cursor += 1
                if status < 0xF0:
                    running = status
            if status == 0xFF:
                meta = track[cursor]
                length, cursor = variable_length(track, cursor + 1)
                payload = track[cursor:cursor + length]
                cursor += length
                if meta == 0x51 and length == 3:
                    tempos.append((tick, int.from_bytes(payload, "big")))
                if meta == 0x2F:
                    break
            elif status in (0xF0, 0xF7):
                length, cursor = variable_length(track, cursor)
                cursor += length
                running = 0
            else:
                kind = status & 0xF0
                channel = status & 0x0F
                first = track[cursor]
                cursor += 1
                second = 0
                if kind not in (0xC0, 0xD0):
                    second = track[cursor]
                    cursor += 1
                if kind in (0x80, 0x90):
                    events.append((tick, kind, first, second, channel))
        tracks.append(events)

    tempo_points = sorted(set(tempos))
    def seconds_at(target_tick: int) -> float:
        seconds = 0.0
        previous_tick = 0
        tempo = 500_000
        for tick, next_tempo in tempo_points:
            if tick > target_tick:
                break
            seconds += (tick - previous_tick) * tempo / (division * 1_000_000)
            previous_tick, tempo = tick, next_tempo
        return seconds + (target_tick - previous_tick) * tempo / (division * 1_000_000)

    result = []
    for events in tracks:
        active: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for tick, kind, pitch, velocity, channel in events:
            key = (channel, pitch)
            if kind == 0x90 and velocity > 0:
                active.setdefault(key, []).append((tick, velocity))
            elif key in active:
                start_tick, start_velocity = active[key].pop(0)
                result.append((seconds_at(start_tick), seconds_at(tick), pitch, start_velocity))
    return result


def main() -> None:
    output = []
    for clip in CLIPS:
        notes = []
        for note_start, note_end, pitch, velocity in read_midi(clip["midi_path"]):
            if note_end <= clip["clip_start"] or note_start >= clip["clip_end"]:
                continue
            notes.append({
                "start": round(max(note_start, clip["clip_start"]) - clip["clip_start"], 4),
                "end": round(min(note_end, clip["clip_end"]) - clip["clip_start"], 4),
                "pitch": pitch, "velocity": velocity,
            })
        notes.sort(key=lambda item: (item["start"], item["pitch"]))
        output.append({
            **{key: value for key, value in clip.items() if key != "midi_path"},
            "duration": round(clip["clip_end"] - clip["clip_start"], 4),
            "passage_start_relative": round(clip["passage_start"] - clip["clip_start"], 4),
            "passage_end_relative": round(clip["passage_end"] - clip["clip_start"], 4),
            "note_count": len(notes), "notes": notes,
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(output)} MIDI excerpts to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
