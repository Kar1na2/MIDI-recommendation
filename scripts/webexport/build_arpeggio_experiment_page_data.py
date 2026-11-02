"""Build MIDI excerpt data for the three-rising-arpeggios experiment page."""
from __future__ import annotations

import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/data/experiment/arpeggio_clips.json"

# MIDI views include three seconds before and after each reported passage.
# Local audio starts at the passage and continues through the three-second tail.
# YouTube audio is constrained to the chosen performance excerpt only.
CLIPS = [
    {
        "id": "meaningless-love", "title": "1nonly — Meaningless Love", "kind": "target",
        "label": "Original · other-stem MIDI", "source": "Basic Pitch transcription; timing remains a working hypothesis",
        "midi_path": ROOT / "midi/1nonly - Meaningless Love (Official Music Video)/other.mid",
        "passage_start": 7.55, "passage_end": 8.91,
        "audio": {"type": "local", "src": "audio/1nonly-meaningless-love-official-music-video.mp3"},
    },
    {
        "id": "bach-prelude-c", "title": "Bach — Prelude in C major, BWV 846", "kind": "reference",
        "label": "Outside-catalogue score lead · 3–5–3 rises",
        "source": "Score-side contour lead; scanned MIDI is a two-guitar arrangement, not an audio-verified piano transcription",
        "midi_path": ROOT / "output/analysis/arpeggio_experiment/references/Bach_WTK_BWV846.mid",
        "passage_start": 1.25, "passage_end": 3.75,
        "audio": {"type": "youtube", "video_id": "tCarlLNwekA"},
    },
    {
        "id": "moonlight-sonata-mvt3", "title": "Beethoven — Moonlight Sonata, Movement III", "kind": "reference",
        "label": "Score-side reference · 4–4–4 rises",
        "source": "Rising-inversion figures in the piano score; this movement is already represented in the local catalogue",
        "midi_path": ROOT / "output/analysis/arpeggio_experiment/references/Moonlight/moonlight3.mid",
        "passage_start": 4.25, "passage_end": 7.0,
        "audio": {"type": "youtube", "video_id": "qvgORWX_B0s"},
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
        clip_start = max(0.0, clip["passage_start"] - 3.0)
        clip_end = clip["passage_end"] + 3.0
        leading_pad = max(0.0, 3.0 - (clip["passage_start"] - clip_start))
        notes = []
        for note_start, note_end, pitch, velocity in read_midi(clip["midi_path"]):
            if note_end <= clip_start or note_start >= clip_end:
                continue
            notes.append({
                "start": round(max(note_start, clip_start) - clip_start + leading_pad, 4),
                "end": round(min(note_end, clip_end) - clip_start + leading_pad, 4),
                "pitch": pitch, "velocity": velocity,
            })
        notes.sort(key=lambda item: (item["start"], item["pitch"]))
        output.append({
            **{key: value for key, value in clip.items() if key != "midi_path"},
            "clip_start": round(clip_start, 4), "clip_end": round(clip_end, 4),
            "duration": round(clip_end - clip_start + leading_pad, 4),
            "leading_pad": round(leading_pad, 4),
            "passage_start_relative": 3.0,
            "passage_end_relative": round(clip["passage_end"] - clip["passage_start"] + 3.0, 4),
            "audio": {
                **clip["audio"],
                "play_start": clip["audio"].get("start", clip["passage_start"]),
                "play_end": clip["audio"].get("end", clip["passage_end"] if clip["audio"]["type"] == "youtube" else clip_end),
            },
            "note_count": len(notes), "notes": notes,
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(output)} MIDI excerpts to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
