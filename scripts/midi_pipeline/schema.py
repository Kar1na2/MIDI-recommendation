"""Canonical events preserve the native MIDI clock plus an explicit source offset."""
import math

from .store import SCHEMA, BACKEND, CHECKPOINT_SHA


def from_midi(path, identity, run_id, source_start, source_end):
    import pretty_midi
    midi = pretty_midi.PrettyMIDI(str(path))
    notes = []
    tracks = []
    for index, instrument in enumerate(midi.instruments):
        tracks.append({"track": index, "name": instrument.name, "program": int(instrument.program),
                       "is_drum": bool(instrument.is_drum)})
        for n in instrument.notes:
            onset, offset = n.start + source_start, n.end + source_start
            notes.append({"id": len(notes), "source_audio_id": identity["source_audio_id"],
                          "source_sha256": identity["source_sha256"], "source_stem": identity["stem"],
                          "backend": BACKEND, "checkpoint_sha256": CHECKPOINT_SHA,
                          "run_id": run_id, "separation_identifier": identity["separation_model_version"],
                          "track": index, "instrument": instrument.name, "program": int(instrument.program),
                          "is_drum": bool(instrument.is_drum), "pitch": int(n.pitch), "onset": float(onset),
                          "offset": float(offset), "duration": float(offset - onset), "velocity": int(n.velocity),
                          "native_onset": float(n.start), "native_offset": float(n.end),
                          "in_source_window": bool(source_start <= onset < source_end)})
    notes.sort(key=lambda n: (n["onset"], n["track"], n["pitch"]))
    document = {"schema": SCHEMA, "run_id": run_id, "clock": "source_recording_seconds",
                "source_window": [source_start, source_end], "native_to_source_offset": source_start,
                "timing": "native MIDI times, unquantized, unclipped; padding flagged",
                "confidence": "not provided by backend", "velocity_provenance": "native writer constant 100",
                "drum_duration_provenance": "native writer synthetic 0.01 seconds",
                "tracks": tracks, "notes": notes}
    validate(document, identity)
    return document


def validate(document, identity):
    start, end = document["source_window"]
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
        raise ValueError("Invalid source window")
    identifiers = set()
    tracks = {t["track"]: t for t in document["tracks"]}
    for n in document["notes"]:
        if n["id"] in identifiers:
            raise ValueError("Duplicate note ID")
        identifiers.add(n["id"])
        if any(not math.isfinite(n[k]) for k in ("onset", "offset", "duration", "native_onset", "native_offset")):
            raise ValueError("Nonfinite timing")
        if (n["offset"] <= n["onset"] or abs(n["duration"] - (n["offset"] - n["onset"])) > 1e-8
                or not 0 <= n["pitch"] <= 127 or not 0 <= n["velocity"] <= 127
                or not 0 <= n["program"] <= 127):
            raise ValueError("Invalid note")
        for key, expected in (("backend", BACKEND), ("checkpoint_sha256", CHECKPOINT_SHA),
                              ("source_sha256", identity["source_sha256"]),
                              ("source_audio_id", identity["source_audio_id"]), ("source_stem", identity["stem"]),
                              ("run_id", document["run_id"]),
                              ("separation_identifier", identity["separation_model_version"])):
            if n[key] != expected:
                raise ValueError(f"Note provenance mismatch: {key}")
        track = tracks[n["track"]]
        if n["program"] != track["program"] or n["is_drum"] != track["is_drum"]:
            raise ValueError("Track identity mismatch")
        if "confidence" in n:
            raise ValueError("This backend does not provide calibrated confidence")
        if (abs(n["onset"] - n["native_onset"] - start) > 1e-8
                or abs(n["offset"] - n["native_offset"] - start) > 1e-8):
            raise ValueError("Source clock mismatch")
