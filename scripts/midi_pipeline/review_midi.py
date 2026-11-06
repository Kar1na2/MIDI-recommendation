"""Exact source-clock MIDI inspection and conservative, one-to-one comparison.

Note offsets are physical key releases. Pedal release is separate evidence.
Repeated same-key note-ons are paired FIFO: MIDI does not identify voices.
"""
from bisect import bisect_right
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from fractions import Fraction
import math
from pathlib import Path

from .store import digest


def number(value, label="time"):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite numeric cell")
    return float(value)


def valid_note(n):
    start, end = number(n["onset"]), number(n["offset"])
    if start < 0 or end <= start:
        raise ValueError("Notes need nonnegative start and strictly positive duration")
    for field, maximum in (("pitch", 127), ("program", 127), ("velocity", 127), ("channel", 15)):
        value = n.get(field, 0)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
            raise ValueError(f"Invalid {field}: {value}")


def note_name(pitch):
    return ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")[pitch % 12] + str(pitch // 12 - 1)


def parse_pitch(value):
    import re
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value) or value != int(value) or not 0 <= value <= 127:
            raise ValueError("Pitch must be an integer MIDI number 0–127 or note name")
        return int(value)
    match = re.fullmatch(r"([A-Ga-g])([#b]?)(-?\d+)", str(value).strip())
    if not match:
        raise ValueError(f"Invalid pitch: {value}")
    pitch = (int(match[3]) + 1) * 12 + dict(C=0, D=2, E=4, F=5, G=7, A=9, B=11)[match[1].upper()] + {"": 0, "#": 1, "b": -1}[match[2]]
    return parse_pitch(pitch)


def parse_midi(path, source_key, offset=0.):
    import mido
    offset = number(offset, "global offset")
    midi = mido.MidiFile(str(path), clip=False)
    if midi.type == 2 or midi.ticks_per_beat <= 0:
        raise ValueError("Independent type-2 sequences and SMPTE divisions need an explicit separate clock")
    events, names = [], {}
    for ti, track in enumerate(midi.tracks):
        tick = 0
        for mi, msg in enumerate(track):
            if msg.time < 0:
                raise ValueError("Negative delta tick")
            tick += msg.time
            events.append((tick, ti, mi, msg))
            if msg.type == "track_name":
                names[ti] = msg.name
    events.sort(key=lambda e: e[:3])
    # All tracks share the tempo map. Fraction integration avoids cumulative drift.
    ticks, seconds, tempos = [0], [Fraction(0)], [500000]
    warnings = []
    for tick, ti, mi, msg in events:
        if msg.type == "set_tempo":
            if msg.tempo <= 0:
                raise ValueError("MIDI tempo must be positive")
            sec = seconds[-1] + Fraction((tick - ticks[-1]) * tempos[-1], midi.ticks_per_beat * 1000000)
            if tick == ticks[-1]:
                if tick and tempos[-1] != msg.tempo:
                    warnings.append({"issue": "conflicting_same_tick_tempo", "tick": tick})
                tempos[-1] = msg.tempo
            else:
                ticks.append(tick); seconds.append(sec); tempos.append(msg.tempo)
    def clock(tick):
        i = bisect_right(ticks, tick) - 1
        return float(seconds[i] + Fraction((tick - ticks[i]) * tempos[i], midi.ticks_per_beat * 1000000)) + offset
    active, held = defaultdict(deque), defaultdict(list)
    program, pedal = defaultdict(int), defaultdict(bool)
    notes, controls = [], []
    sha = digest(path)
    def release(key, tick):
        if not active[key]:
            warnings.append({"issue": "orphan_note_off", "track_channel_pitch": key, "tick": tick})
            return
        n = active[key].popleft()
        n.update(offset=clock(tick), end_tick=tick)
        n["duration"] = n["offset"] - n["onset"]
        if n["duration"] <= 0:
            warnings.append({"issue": "nonpositive_note_dropped", "event_id": n["event_id"]})
            return
        n["sustain_end"] = None if pedal[key[1]] and not n["is_drum"] else n["offset"]
        if n["sustain_end"] is None:
            held[key[1]].append(n)
        notes.append(n)
    for tick, ti, mi, msg in events:
        if msg.type == "program_change":
            program[msg.channel] = msg.program
        elif msg.type == "control_change":
            controls.append({"track": ti, "channel": msg.channel, "control": msg.control,
                             "value": msg.value, "time": clock(tick)})
            if msg.control == 64:
                pedal[msg.channel] = msg.value >= 64
                if not pedal[msg.channel]:
                    for n in held.pop(msg.channel, []):
                        n["sustain_end"] = clock(tick)
            elif msg.control in (120, 123):
                for key in list(active):
                    if key[1] == msg.channel:
                        while active[key]:
                            release(key, tick)
                if msg.control == 120:
                    for n in held.pop(msg.channel, []):
                        n["sustain_end"] = clock(tick)
        elif msg.type == "note_on" and msg.velocity:
            key = (ti, msg.channel, msg.note)
            active[key].append({"event_id": f"{source_key}:{sha[:16]}:t{ti}:m{mi}",
                "id": f"t{ti}:m{mi}", "source_key": source_key, "track": ti,
                "instrument": names.get(ti, f"Track {ti}"), "channel": msg.channel,
                "program": program[msg.channel], "is_drum": msg.channel == 9,
                "pitch": msg.note, "velocity": msg.velocity, "onset": clock(tick), "start_tick": tick})
        elif msg.type == "note_off" or (msg.type == "note_on" and not msg.velocity):
            release((ti, msg.channel, msg.note), tick)
    for queue in active.values():
        for n in queue:
            warnings.append({"issue": "missing_note_off_excluded", "event_id": n["event_id"]})
    for group in held.values():
        for n in group:
            warnings.append({"issue": "sustain_release_missing", "event_id": n["event_id"]})
    notes.sort(key=lambda n: (n["onset"], n["track"], n["pitch"], n["event_id"]))
    return {"notes": notes, "controls": controls, "warnings": warnings, "sha256": sha,
            "ticks_per_beat": midi.ticks_per_beat, "midi_type": midi.type,
            "tempo_map": [{"tick": t, "seconds": float(s) + offset, "microseconds_per_beat": p}
                          for t, s, p in zip(ticks, seconds, tempos)],
            "timing_policy": "integrated tempo map; FIFO same-key overlaps; physical key-release offsets; sustain_end separate; missing offs excluded"}


@dataclass
class MatchConfig:
    onset: float = .05
    offset: float = .1
    duration: float = .1
    candidate_onset: float = 1.
    candidate_duration: float = 2.
    instrument: str = "advisory"

    def validate(self):
        for key, value in asdict(self).items():
            if key != "instrument" and number(value, key) < 0:
                raise ValueError("Matching tolerances must be nonnegative")
        if self.instrument not in ("advisory", "program", "family"):
            raise ValueError("Instrument mode must be advisory, program or family")


def compare_notes(a, b, config=None):
    """Resolve only unique reciprocal candidates; retain ambiguity per event."""
    c = config or MatchConfig(); c.validate()
    left, right = set(range(len(a))), set(range(len(b)))
    rows = []
    def context(x, y):
        if x.get("is_drum", False) != y.get("is_drum", False):
            return False
        if c.instrument == "program":
            return x["program"] == y["program"]
        if c.instrument == "family":
            return x["program"] // 8 == y["program"] // 8
        return True
    def agrees(x, y):
        return (x["pitch"] == y["pitch"] and abs(x["onset"] - y["onset"]) <= c.onset + 1e-12
                and abs(x["offset"] - y["offset"]) <= c.offset + 1e-12
                and abs(x["duration"] - y["duration"]) <= c.duration + 1e-12)
    def row(i, j, status, candidates=None):
        x, y = a[i] if i is not None else None, b[j] if j is not None else None
        return {"a": x, "b": y, "status": status,
                "reference_start": min(n["onset"] for n in (x, y) if n is not None),
                "candidate_event_ids": candidates or []}
    def candidate(x, y):
        same_pitch = (x["pitch"] == y["pitch"] and abs(x["onset"]-y["onset"]) <= c.candidate_onset
                      and abs(x["duration"]-y["duration"]) <= c.candidate_duration)
        pitch_disagreement = (abs(x["onset"]-y["onset"]) <= c.onset
                              and abs(x["offset"]-y["offset"]) <= c.offset
                              and abs(x["pitch"]-y["pitch"]) <= 12)
        return same_pitch or pitch_disagreement
    for strict in (True, False):
        edges = {i: [j for j in sorted(right) if context(a[i], b[j]) and
                     (agrees(a[i], b[j]) if strict else candidate(a[i], b[j]))]
                 for i in sorted(left)}
        reverse = {j: [i for i in edges if j in edges[i]] for j in sorted(right)}
        paired_a, paired_b = set(), set()
        for i, js in edges.items():
            if len(js) == 1 and len(reverse[js[0]]) == 1:
                j = js[0]
                status = "Agreement" if agrees(a[i], b[j]) else ("Pitch disagreement" if a[i]["pitch"] != b[j]["pitch"] else "Timing disagreement")
                rows.append(row(i, j, status)); paired_a.add(i); paired_b.add(j)
        left -= paired_a; right -= paired_b
        # Any non-unique eligible edge is unresolved, even if one greedy choice
        # could produce a plausible-looking maximum matching.
        ambiguous_a = {i for i in left if edges[i]}
        ambiguous_b = {j for j in right if reverse[j]}
        for i in sorted(ambiguous_a):
            rows.append(row(i, None, "Ambiguous YourMT3+", [b[j]["event_id"] for j in edges[i]]))
        for j in sorted(ambiguous_b):
            rows.append(row(None, j, "Ambiguous Songscription", [a[i]["event_id"] for i in reverse[j]]))
        left -= ambiguous_a; right -= ambiguous_b
    rows += [row(i, None, "YourMT3+ only") for i in sorted(left)]
    rows += [row(None, j, "Songscription only") for j in sorted(right)]
    return sorted(rows, key=lambda r: (r["reference_start"], r["status"]))


def write_midi(document, path):
    """Encode absolute seconds at 60k ticks/s; retain CC events, no beat snapping."""
    import mido
    groups = defaultdict(list)
    for n in document["notes"]:
        valid_note(n)
        groups[(n["track"], n["program"], n["is_drum"])].append(n)
    midi = mido.MidiFile(type=1, ticks_per_beat=30000)
    conductor = mido.MidiTrack(); conductor.append(mido.MetaMessage("set_tempo", tempo=500000)); midi.tracks.append(conductor)
    channels = iter(i for i in range(16) if i != 9)
    for (track, program, drum), ns in sorted(groups.items()):
        # Split same-pitch overlap into lanes, so note-off identity survives playback.
        lanes = []
        for n in sorted(ns, key=lambda n: (n["onset"], n["pitch"])):
            lane = next((l for l in lanes if all(k["pitch"] != n["pitch"] or k["offset"] <= n["onset"] for k in l)), None)
            if lane is None:
                lane = []; lanes.append(lane)
            lane.append(n)
        for lane in lanes:
            channel = 9 if drum else next(channels, None)
            if channel is None:
                raise ValueError("Corrected MIDI exceeds 15 melodic channel lanes")
            out = mido.MidiTrack(); midi.tracks.append(out)
            out.append(mido.MetaMessage("track_name", name=lane[0]["instrument"]))
            out.append(mido.Message("program_change", channel=channel, program=program))
            events = []
            for n in lane:
                start_tick,end_tick=round(n["onset"]*60000),round(n["offset"]*60000)
                if end_tick<=start_tick:
                    raise ValueError("Note duration is below the corrected MIDI tick resolution")
                events.extend([(start_tick, 2, mido.Message("note_on", channel=channel, note=n["pitch"], velocity=n["velocity"])),
                               (end_tick, 1, mido.Message("note_off", channel=channel, note=n["pitch"], velocity=0))])
            for ctrl in document.get("controls", []):
                if any(ctrl["track"] == n.get("midi_track") and ctrl["channel"] == n.get("channel") for n in lane):
                    events.append((round(ctrl["time"]*60000), 0, mido.Message("control_change", channel=channel, control=ctrl["control"], value=ctrl["value"])))
            previous = 0
            for tick, _, msg in sorted(events, key=lambda e: (e[0], e[1])):
                if tick < 0:
                    raise ValueError("Negative MIDI tick")
                out.append(msg.copy(time=tick-previous)); previous = tick
    midi.save(str(path))
