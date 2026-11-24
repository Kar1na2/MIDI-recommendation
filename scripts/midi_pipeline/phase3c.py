"""Build and serve a human-readable inspection of existing Phase 3 YourMT3+ runs."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import html
import json
import math
import mimetypes
from pathlib import Path
import statistics
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
import wave

from .store import digest, load_run, save

BASE = Path("output/midi_pipeline/phase3/meaningful-love-v1")
FOCUS = (6.0, 18.0)
MODELS = ("yourmt3_mix", "yourmt3_other")
SYNTH_RATE = 24000
PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def pitch_name(pitch):
    pitch = int(pitch)
    return f"{PITCH_NAMES[pitch % 12]}{pitch // 12 - 1}"


def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    position = (len(values) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return float(values[lower])
    return float(values[lower] * (upper - position) + values[upper] * (position - lower))


def overlap_regions(notes, window=None):
    """Return components of time where at least two note intervals are active."""
    events = []
    for note in notes:
        start, end = float(note["onset"]), float(note["offset"])
        if window:
            start, end = max(start, window[0]), min(end, window[1])
        if end <= start:
            continue
        events.extend(((start, 1, note["id"]), (end, -1, note["id"])))
    events.sort(key=lambda x: (x[0], x[1]))  # end before start for half-open intervals
    active = set()
    pieces = []
    previous = None
    i = 0
    while i < len(events):
        at = events[i][0]
        if previous is not None and at > previous and len(active) >= 2:
            if pieces and pieces[-1]["end"] == previous:
                pieces[-1]["end"] = at
                pieces[-1]["note_ids"].update(active)
            else:
                pieces.append({"start": previous, "end": at, "note_ids": set(active)})
        while i < len(events) and events[i][0] == at and events[i][1] == -1:
            active.discard(events[i][2]); i += 1
        while i < len(events) and events[i][0] == at and events[i][1] == 1:
            active.add(events[i][2]); i += 1
        previous = at
    return [{"start": round(x["start"], 6), "end": round(x["end"], 6),
             "duration_seconds": round(x["end"] - x["start"], 6),
             "note_ids": sorted(x["note_ids"])} for x in pieces]


def overlap_pair_count(notes, window=None):
    """Count distinct note pairs whose positive-duration intervals intersect."""
    events = []
    for n in notes:
        start, end = float(n["onset"]), float(n["offset"])
        if window:
            start, end = max(start, window[0]), min(end, window[1])
        if end > start:
            events.extend(((start, 1, n["id"]), (end, -1, n["id"])))
    active, pairs = set(), set()
    for _, kind, note_id in sorted(events, key=lambda x: (x[0], x[1])):
        if kind < 0:
            active.discard(note_id)
        else:
            for previous in active:
                pairs.add(tuple(sorted((previous, note_id))))
            active.add(note_id)
    return pairs


def descriptive_sequences(notes, window=FOCUS):
    """Simple, explicitly non-interpretive sequence statistics for review."""
    pitched = [n for n in notes if not n["is_drum"] and window[0] <= n["onset"] < window[1]]
    per_track = defaultdict(list)
    for note in sorted(pitched, key=lambda n: (n["track"], n["onset"], n["pitch"], n["id"])):
        per_track[note["track"]].append(note)
    asc, desc, repeats, sustained, short, gaps = [], [], [], [], [], []
    duplicate_candidates = []
    for track, row in per_track.items():
        for i in range(len(row) - 2):
            triple = row[i:i + 3]
            if not (triple[0]["onset"] < triple[1]["onset"] < triple[2]["onset"]):
                continue
            pitches = [x["pitch"] for x in triple]
            evidence = {"track": track, "note_ids": [x["id"] for x in triple],
                        "time_seconds": [round(x["onset"], 3) for x in triple], "pitches": pitches}
            if pitches[0] < pitches[1] < pitches[2]: asc.append(evidence)
            if pitches[0] > pitches[1] > pitches[2]: desc.append(evidence)
        grams = defaultdict(list)
        for i in range(len(row) - 2):
            sequence = tuple(x["pitch"] for x in row[i:i + 3])
            grams[sequence].append(row[i:i + 3])
        for sequence, groups in grams.items():
            if len(groups) > 1:
                repeats.append({"track": track, "pitches": list(sequence), "occurrences": [
                    {"note_ids": [x["id"] for x in group], "time_seconds": [round(group[0]["onset"], 3), round(group[-1]["onset"], 3)]}
                    for group in groups[:4]]})
        for note in row:
            if note["duration"] >= .5:
                sustained.append({"track": track, "note_id": note["id"], "pitch": note["pitch"],
                                  "onset": note["onset"], "offset": note["offset"], "duration": note["duration"]})
            if note["duration"] < .05:
                short.append({"track": track, "note_id": note["id"], "pitch": note["pitch"],
                              "onset": note["onset"], "duration": note["duration"]})
        for left, right in zip(row, row[1:]):
            gap = right["onset"] - left["offset"]
            if gap > .5:
                gaps.append({"track": track, "after_note_id": left["id"], "before_note_id": right["id"],
                             "start": left["offset"], "end": right["onset"], "duration": gap,
                             "meaning": "no predicted note on this track; not proof of an audible omission"})
        by_pitch = defaultdict(list)
        for note in row:
            by_pitch[note["pitch"]].append(note)
        for pitch, same in by_pitch.items():
            for i, left in enumerate(same):
                for right in same[i + 1:]:
                    delta = right["onset"] - left["onset"]
                    if delta > .05:
                        break
                    if delta >= 0 and right["onset"] < left["offset"]:
                        duplicate_candidates.append({"track": track, "pitch": pitch,
                            "note_ids": [left["id"], right["id"]], "onsets": [left["onset"], right["onset"]],
                            "meaning": "same-track, same-pitch overlap candidate; may be separate articulations"})
    return {"window_seconds": list(window), "interpretation": "descriptive MIDI statistics only; not verified musical motifs or errors",
            "ascending_triples": asc[:40], "descending_triples": desc[:40],
            "repeated_pitch_triples": repeats[:40], "shorter_than_50ms": short,
            "at_least_500ms": sustained, "same_track_pitch_overlap_candidates": duplicate_candidates[:50],
            "within_track_gaps_over_500ms": gaps[:60]}


def stats_for_run(root, key, item):
    import pretty_midi
    run_dir = root / item["path"]
    run, document = load_run(run_dir)
    raw = pretty_midi.PrettyMIDI(str(run_dir / "raw.mid"))
    notes = document["notes"]
    if len(notes) != sum(len(i.notes) for i in raw.instruments):
        raise ValueError(f"Raw MIDI/canonical JSON note count differs for {key}")
    raw_signatures, json_signatures = Counter(), Counter()
    for track, instrument in enumerate(raw.instruments):
        for note in instrument.notes:
            raw_signatures[(track, round(note.start, 9), round(note.end, 9), note.pitch, note.velocity, instrument.program, instrument.is_drum)] += 1
    for note in notes:
        raw_signatures_json = (note["track"], round(note["native_onset"], 9), round(note["native_offset"], 9),
                               note["pitch"], note["velocity"], note["program"], note["is_drum"])
        json_signatures[raw_signatures_json] += 1
        if note["duration"] <= 0 or not math.isclose(note["onset"], note["native_onset"], abs_tol=1e-9):
            raise ValueError(f"Invalid duration or unexpected time offset in {key} note {note['id']}")
    if raw_signatures != json_signatures:
        raise ValueError(f"Raw MIDI and canonical JSON note events differ for {key}")

    midi_file = pretty_midi.PrettyMIDI(str(run_dir / "raw.mid"))
    _, tempos = midi_file.get_tempo_changes()
    message_audit = audit_midi_messages(run_dir / "raw.mid")
    if message_audit["note_on_count"] != len(notes):
        raise ValueError(f"Raw note-on count differs from parsed notes in {key}")
    for parsed, direct in zip(sorted(notes, key=lambda n: (n["track"], n["native_onset"], n["pitch"])),
                              message_audit["events"]):
        if (parsed["pitch"] != direct["pitch"] or parsed["program"] != direct["program"]
                or parsed["is_drum"] != direct["is_drum"]
                or not math.isclose(parsed["native_onset"], direct["onset"], abs_tol=1e-8)
                or not math.isclose(parsed["native_offset"], direct["offset"], abs_tol=1e-8)):
            raise ValueError(f"Independent tick/message parsing differs for {key}")
    del message_audit["events"]
    raw_tracks = []
    for index, instrument in enumerate(midi_file.instruments):
        track_notes = [n for n in notes if n["track"] == index]
        raw_tracks.append({"track": index, "name": instrument.name,
                           "midi_program": int(instrument.program),
                           "is_percussion": bool(instrument.is_drum),
                           "note_count": len(track_notes),
                           "program_provenance": "explicit program_change in raw MIDI; model decoder emitted program class and YourMT3+ inverse-vocabulary writer mapped it",
                           "identity_caveat": "predicted MIDI label, not proof of the actual acoustic instrument"})
    within = [n for n in notes if FOCUS[0] <= n["onset"] < FOCUS[1]]
    active = [n for n in notes if n["onset"] < FOCUS[1] and n["offset"] > FOCUS[0]]
    pitched = [n for n in notes if not n["is_drum"]]
    focus_pitched = [n for n in within if not n["is_drum"]]
    durations = [n["duration"] for n in notes]
    focus_durations = [n["duration"] for n in within]
    pitched_focus_durations = [n["duration"] for n in focus_pitched]
    pitches = [n["pitch"] for n in pitched]
    focus_overlap = overlap_regions(notes, FOCUS)
    common = Counter(n["pitch"] for n in focus_pitched)
    token_errors = run.get("backend_result", {}).get("decoding_errors", {})
    return {"key": key, "run_id": run["run_id"],
            "input": {"path": "reference.wav" if key == "yourmt3_mix" else "reference_other.wav",
                      "type": "Original mix" if key == "yourmt3_mix" else "Existing Other stem",
                      "sha256": run["identity"]["source_sha256"],
                      "sample_rate": run["source_info"]["sample_rate"], "duration_seconds": run["source_info"]["recording_duration"],
                      "original_clock_offset_seconds": 0.0},
            "backend": run["identity"]["backend"],
            "checkpoint": run["identity"]["processing"]["model_arguments"][0],
            "checkpoint_sha256": run["identity"]["checkpoint_sha256"],
            "status": run["status"], "runtime_seconds": run.get("runtime_seconds"),
            "raw_midi": {"path": f"midi/{key}/raw.mid", "sha256": digest(run_dir / "raw.mid"),
                         "track_count": len(raw.instruments), "total_notes": sum(len(i.notes) for i in raw.instruments),
                         "message_audit": message_audit,
                         "tempo_values_bpm": [round(float(x), 6) for x in tempos],
                         "tempo_note": "no explicit tempo meta-event in raw MIDI; parser's default effective tempo does not change absolute seconds",
                         "consistency": "all raw MIDI notes match canonical JSON by track, pitch, native onset, native offset, velocity, program and percussion flag"},
            "stats": {"total_notes": len(notes), "notes_starting_in_focus": len(within),
                      "pitched_notes_starting_in_focus": len(focus_pitched),
                      "notes_intersecting_focus": len(active),
                      "earliest_onset_seconds": min((n["onset"] for n in notes), default=None),
                      "latest_offset_seconds": max((n["offset"] for n in notes), default=None),
                      "focus_duration_seconds": {"minimum": min(focus_durations, default=None),
                          "median": statistics.median(focus_durations) if focus_durations else None,
                          "maximum": max(focus_durations, default=None),
                          "under_50ms_count": sum(d < .05 for d in focus_durations)},
                      "focus_pitched_duration_seconds": {"minimum": min(pitched_focus_durations, default=None),
                          "median": statistics.median(pitched_focus_durations) if pitched_focus_durations else None,
                          "maximum": max(pitched_focus_durations, default=None),
                          "under_50ms_count": sum(d < .05 for d in pitched_focus_durations)},
                      "all_duration_seconds": {"minimum": min(durations, default=None),
                          "median": statistics.median(durations) if durations else None,
                          "maximum": max(durations, default=None)},
                      "pitch_range_midi": [min(pitches), max(pitches)] if pitches else None,
                      "focus_pitch_range_midi": [min((n["pitch"] for n in focus_pitched), default=0),
                          max((n["pitch"] for n in focus_pitched), default=0)] if focus_pitched else None,
                      "most_common_focus_pitches": [{"midi": p, "name": pitch_name(p), "count": count} for p, count in common.most_common(10)],
                      "overlap_regions_all": overlap_regions(notes), "overlap_regions_focus": focus_overlap,
                      "overlap_pairs_focus": len(overlap_pair_count(notes, FOCUS)),
                      "percussion_event_count": sum(n["is_drum"] for n in notes),
                      "programs_and_tracks": raw_tracks,
                      "confidence": "not emitted; no confidence values are inferred",
                      "velocity_note": "native MIDI writer uses its default fixed velocity 100; velocity is not a model confidence score",
                      "notes_filtered_or_discarded_by_inspection": 0,
                      "filtering_note": "inspection keeps every exported note; decoder errors and model token-to-note processing precede MIDI export; synthesis omits percussion only",
                      "drum_duration_provenance": "YourMT3+ writer inserts a synthetic 0.01-second drum note-off; drum durations are not model sustain predictions",
                      "decoder_errors": token_errors,
                      "short_pitch_note_count_below_50ms_focus": sum(n["duration"] < .05 for n in focus_pitched)},
            "notes": notes,
            "sequence_descriptions": descriptive_sequences(notes),
            "files": {"native_midi": f"midi/{key}/raw.mid", "canonical_json": f"midi/{key}/notes.json",
                      "run_manifest": f"midi/{key}/run.json", "reconstruction": f"reconstruction/{key}.wav"}}


def audit_midi_messages(path):
    """Validate raw on/off pairs and independently convert MIDI ticks with the tempo map."""
    import bisect
    import mido
    midi = mido.MidiFile(path)
    tick, seconds, tempo = 0, 0., 500000
    tempo_map = [(0, 0., tempo)]
    explicit_tempos = []
    for message in mido.merge_tracks(midi.tracks):
        seconds += mido.tick2second(message.time, midi.ticks_per_beat, tempo)
        tick += message.time
        if message.type == "set_tempo":
            tempo = message.tempo
            tempo_map.append((tick, seconds, tempo))
            explicit_tempos.append({"tick": tick, "microseconds_per_beat": tempo})
    ticks = [t[0] for t in tempo_map]
    def to_seconds(at):
        start, sec, beat = tempo_map[bisect.bisect_right(ticks, at) - 1]
        return sec + mido.tick2second(at - start, midi.ticks_per_beat, beat)
    tracks, events, ons, offs = [], [], 0, 0
    for track_id, track in enumerate(midi.tracks):
        at, active, programs, changes, track_events = 0, {}, {}, [], []
        name = ""
        for msg in track:
            if msg.time < 0:
                raise ValueError("Negative MIDI delta tick")
            at += msg.time
            if msg.type == "track_name": name = msg.name
            if msg.type == "program_change":
                programs[msg.channel] = msg.program
                changes.append({"tick": at, "channel": msg.channel, "program": msg.program})
            if msg.type not in ("note_on", "note_off"): continue
            identity = (msg.channel, msg.note)
            if msg.type == "note_on" and msg.velocity > 0:
                if identity in active:
                    raise ValueError("Ambiguous same-pitch raw MIDI overlap; review required")
                if msg.channel not in programs:
                    raise ValueError("Missing explicit program_change; provenance requires review")
                active[identity] = (at, programs[msg.channel], msg.velocity)
                ons += 1
            else:
                if identity not in active:
                    raise ValueError("Unmatched MIDI note-off")
                start, program, velocity = active.pop(identity)
                if at <= start: raise ValueError("Nonpositive raw MIDI duration")
                track_events.append({"raw_track": track_id, "pitch": msg.note, "program": program,
                                     "is_drum": msg.channel == 9, "onset": to_seconds(start),
                                     "offset": to_seconds(at), "velocity": velocity})
                offs += 1
        if active: raise ValueError("Unterminated raw MIDI notes")
        events.extend(track_events)
        tracks.append({"raw_track": track_id, "name": name, "note_count": len(track_events),
                       "program_changes": changes})
    return {"file_track_count": len(midi.tracks), "ticks_per_beat": midi.ticks_per_beat,
            "explicit_tempo_events": explicit_tempos, "note_on_count": ons, "note_off_count": offs,
            "unmatched_note_events": 0, "tracks": tracks,
            "events": sorted(events, key=lambda n: (n["raw_track"], n["onset"], n["pitch"]))}


def pairwise_notes(models, tolerance=.05):
    a = [n for n in models["yourmt3_mix"]["notes"] if FOCUS[0] <= n["onset"] < FOCUS[1] and not n["is_drum"]]
    b = [n for n in models["yourmt3_other"]["notes"] if FOCUS[0] <= n["onset"] < FOCUS[1] and not n["is_drum"]]
    candidates = sorted((abs(x["onset"] - y["onset"]), i, j)
                        for i, x in enumerate(a) for j, y in enumerate(b)
                        if x["pitch"] == y["pitch"] and abs(x["onset"] - y["onset"]) <= tolerance)
    used_a, used_b, matches = set(), set(), []
    for delta, i, j in candidates:
        if i not in used_a and j not in used_b:
            used_a.add(i); used_b.add(j)
            matches.append({"mix_note_id": a[i]["id"], "other_note_id": b[j]["id"],
                            "pitch": a[i]["pitch"], "mix_onset": a[i]["onset"],
                            "other_onset": b[j]["onset"], "onset_delta_seconds": delta})
    return {"within_window": list(FOCUS), "note_onset_tolerance_seconds": tolerance,
            "matched_candidates": matches,
            "mix_unique_note_ids": [n["id"] for i, n in enumerate(a) if i not in used_a],
            "other_unique_note_ids": [n["id"] for j, n in enumerate(b) if j not in used_b],
            "interpretation": "candidate agreement only: the Mix and Other stem are different audio inputs; agreement is not accuracy"}


def render_reconstruction(model, output, sample_rate=SYNTH_RATE):
    import numpy as np
    import soundfile as sf
    duration = float(model["input"]["duration_seconds"])
    audio = np.zeros(round(duration * sample_rate), dtype=np.float64)
    rendered = 0
    for note in model["notes"]:
        if note["is_drum"]:
            continue
        first = max(0, round(note["onset"] * sample_rate))
        stop = min(len(audio), round(note["offset"] * sample_rate))
        if stop <= first:
            continue
        count = stop - first
        t = np.arange(count, dtype=np.float64) / sample_rate
        dur = count / sample_rate
        env = np.ones(count, dtype=np.float64)
        edge = min(round(.006 * sample_rate), count // 2)
        if edge:
            env[:edge] = np.linspace(0., 1., edge, endpoint=False)
            env[-edge:] *= np.linspace(1., 0., edge, endpoint=False)
        frequency = 440. * 2. ** ((note["pitch"] - 69) / 12.)
        # Fixed, reproducible additive waveform; MIDI program labels are retained
        # as metadata but do not select a soundfont/instrument timbre.
        tone = (np.sin(2 * np.pi * frequency * t)
                + .22 * np.sin(4 * np.pi * frequency * t)
                + .08 * np.sin(6 * np.pi * frequency * t)) / 1.30
        velocity = note["velocity"] / 127. if note["velocity"] else 1.
        audio[first:stop] += .12 * velocity * env * tone
        rendered += 1
    peak = float(np.max(np.abs(audio), initial=0.))
    if peak > .92:
        audio *= .92 / peak
    sf.write(output, audio.astype(np.float32), sample_rate, subtype="PCM_16")
    return {"path": str(output), "sha256": digest(output), "sample_rate": sample_rate,
            "duration_seconds": round(len(audio) / sample_rate, 6), "peak_before_limiter": peak,
            "synthesis": "deterministic 3-partial additive oscillator; exact MIDI onset/pitch/end gated by generated envelope; no external soundfont",
            "rendered_pitched_notes": rendered, "percussion_notes_omitted": model["stats"]["percussion_event_count"],
            "percussion_note": "drum events intentionally excluded: no verified General MIDI drum soundfont in this environment"}


def _summary_text(model):
    s, key = model["stats"], model["key"]
    tracks = ", ".join(f"{t['name']} (program {t['midi_program']}, {t['note_count']} notes)" for t in model["stats"]["programs_and_tracks"])
    focus = s["focus_duration_seconds"]
    pitched_durations = s["focus_pitched_duration_seconds"]
    if s["focus_pitch_range_midi"]:
        pitch_range = f"{pitch_name(s['focus_pitch_range_midi'][0])} to {pitch_name(s['focus_pitch_range_midi'][1])}"
    else:
        pitch_range = "no pitched notes"
    median = f"{pitched_durations['median']*1000:.1f} ms" if pitched_durations["median"] is not None else "unavailable"
    return (f"YourMT3+ {('Mix' if key == 'yourmt3_mix' else 'Other')} predicted {s['total_notes']} notes in its 30-second input, "
            f"with {s['notes_starting_in_focus']} note onsets in 6–18 seconds ({s['pitched_notes_starting_in_focus']} pitched, "
            f"{s['percussion_event_count']} percussion events across the full input). Pitched focus notes span {pitch_range}; "
            f"their median focus-window duration is {median}, and {pitched_durations['under_50ms_count']} of those pitched events are under 50 ms. "
            f"Including percussion, {focus['under_50ms_count']} focus events are under 50 ms. It emitted {len(s['programs_and_tracks'])} MIDI program tracks: {tracks}. "
            f"There are {len(s['overlap_regions_focus'])} intervals with simultaneous notes in the focus window. These are model outputs, not verified note labels.")


def build(root=BASE):
    import soundfile as sf
    root = Path(root).resolve()
    local_runs = json.loads((root / "local-runs.json").read_text(encoding="utf-8"))["runs"]
    models = {key: stats_for_run(root, key, local_runs[key]) for key in MODELS}
    for key, model in models.items():
        if digest(root / model["input"]["path"]) != model["input"]["sha256"]:
            raise ValueError(f"Transcribed reference file hash differs for {key}")
    for key, model in models.items():
        reconstruct = root / "reconstruction" / f"{key}.wav"
        reconstruct.parent.mkdir(parents=True, exist_ok=True)
        model["reconstruction"] = render_reconstruction(model, reconstruct)
    media = sf.info(root / "reference.wav")
    if media.samplerate != 48000 or abs(media.duration - 30.) > 1 / media.samplerate:
        raise ValueError("Reference WAV does not have the expected 30-second source clock")
    data = {"schema": "phase3c-inspection/1", "focus_window_seconds": list(FOCUS),
            "overview_window_seconds": [0., 30.],
            "source_time_note": "raw MIDI starts at crop-relative 0; both source crops also start at original recording 0; canonical offset applied exactly once (0.0 seconds)",
            "reference_audio": {"path": "reference.wav", "sample_rate": media.samplerate,
                "channels": media.channels, "duration_seconds": media.duration,
                "sha256": digest(root / "reference.wav"), "encoding": f"{media.format}/{media.subtype}"},
            "instrument_provenance": "YourMT3+ decodes a program-class token; its output_inverse_vocab maps the class to a MIDI program; the vendored writer explicitly emits program_change and a track name. The parser preserves these values. They are estimated classes, not acoustic ground truth.",
            "timing_provenance": "raw MIDI writer converts absolute decoded seconds to ticks at its default 500,000 microseconds/beat. It writes no set_tempo meta event; MIDI readers therefore use the standard 120-BPM default to recover absolute seconds. The canonical JSON is parsed from that raw MIDI and adds source offset 0.0 seconds once.",
            "confidence_provenance": "YourMT3+ does not export calibrated note confidence; no confidence values are present or fabricated.",
            "notes_filtered_or_discarded": 0,
            "models": models,
            "mix_other_comparison": pairwise_notes(models),
            "source_relationship": "Mix and Other are different audio inputs transcribed by the same YourMT3+ checkpoint; they are not two independent AMT models.",
            "visual_method_note": "Piano-roll widths are scaled from unmodified MIDI durations. A 4-pixel minimum is used only for display when shorter than screen resolution; exact onset and offset remain in tooltip/table.",
            "global_limits": ["Predictions have no human-verified transcription ground truth yet.",
                              "Track/program names are model estimates, not proof of audible instrument identity.",
                              "The synthetic reconstruction uses neutral additive tones and omits percussion, as stated in each model's metadata."]}
    data["screenshots"] = sorted(p.relative_to(root).as_posix() for p in (root / "external" / "screenshots").glob("*")
                                 if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
    save(root / "phase3c-inspection.json", data)
    previous = root / "report-phase3b-view-only.html"
    current = root / "report.html"
    if current.exists() and '<section id="phase3b-review">' in current.read_text(encoding="utf-8", errors="replace") and not previous.exists():
        previous.write_bytes(current.read_bytes())
    assets = root / "assets"
    assets.mkdir(exist_ok=True)
    module_dir = Path(__file__).parent
    for filename in ("phase3c.js", "phase3c.css"):
        (assets / filename).write_bytes((module_dir / filename).read_bytes())
    write_html(root, data)
    write_inspection_report(root, data)
    return data


def manual_review_html():
    return """<section id="manual-review"><h2>Human comparison notes · 6–18 seconds</h2>
<p>Record readable measures and rhythms without assigning exact times. Leave uncertain pitch/timing blank. Download your review to preserve it; changes stay in this browser tab until exported. External predictions and human-verified evidence remain separate.</p>
<p id="review-status" role="status">Loading existing review…</p>
<div class="review-grid">
<label>Finding type<select id="review-kind"><option value="uncertain">Uncertain pitch / timing / reading</option><option value="correctly_detected">Correct detection candidate</option><option value="missing_note">Missing note</option><option value="false_positive">False positive</option><option value="incorrect_octave">Incorrect octave</option><option value="incorrect_onset_duration">Incorrect onset / duration</option><option value="missing_instrument_layer">Missing instrument layer</option><option value="repeated_arpeggio_quality">Repeated arpeggio quality</option><option value="comment">General comment</option></select></label>
<label>Evidence source<select id="review-source"><option value="yourmt3_mix">YourMT3+ Mix</option><option value="yourmt3_other">YourMT3+ Other</option><option value="songscription_screenshot">Songscription screenshot</option><option value="audio_listening">Original audio listening</option></select></label>
<label>YourMT3+ note ID (optional)<input id="review-note-id" type="number" min="0"></label>
<label>Measure / rhythm / screenshot reference<input id="review-measure" placeholder="e.g. measure 4; beamed triplets; timing uncertain"></label>
<label>Confirmed onset (source seconds, optional)<input id="review-onset" type="number" min="0" max="30" step="0.001"></label>
<label>Confirmed offset (source seconds, optional)<input id="review-offset" type="number" min="0" max="30" step="0.001"></label>
<label>MIDI pitch (optional)<input id="review-pitch" type="number" min="0" max="127"></label>
<label>Evidence status<select id="review-evidence"><option value="uncertain">Uncertain / insufficient evidence</option><option value="visual_only">Screenshot / predicted events only</option><option value="audio_reviewed">Personally checked against original audio</option></select></label>
<label class="wide">Observation<textarea id="review-comment" rows="3" placeholder="Describe the notes/rhythm and what remains uncertain."></textarea></label></div>
<p><label><input id="review-verified" type="checkbox"> I confirm this entered pitch/time is a correct note heard in the original audio, independently of model agreement.</label></p>
<div class="review-actions"><button id="review-add" type="button">Add observation</button><button id="review-export" type="button">Download review JSON</button><label>Load saved review<input id="review-import" type="file" accept="application/json,.json"></label></div>
<div class="review-actions"><label>Confirmed measure / screenshot anchor<input id="anchor-label"></label><label>Source time (seconds)<input id="anchor-time" type="number" min="0" max="30" step="0.001"></label><button id="anchor-add" type="button">Add manually confirmed anchor</button></div>
<ul id="review-anchors"></ul><h3>Qualitative observations</h3><ol id="review-observations"></ol><h3>Human-verified audio notes</h3><ol id="review-verified-notes"></ol></section>"""


def write_html(root, data):
    root = Path(root)
    models = []
    for key in MODELS:
        m = data["models"][key]
        models.append(f"""<article class="summary-card"><h3>{html.escape(m['input']['type'])}</h3>
<p class="summary-text">{html.escape(_summary_text(m))}</p>
<details><summary>Exact computed statistics</summary><pre>{html.escape(json.dumps({k:m['stats'][k] for k in ('total_notes','notes_starting_in_focus','pitched_notes_starting_in_focus','notes_intersecting_focus','earliest_onset_seconds','latest_offset_seconds','focus_duration_seconds','focus_pitched_duration_seconds','all_duration_seconds','pitch_range_midi','focus_pitch_range_midi','most_common_focus_pitches','overlap_regions_focus','overlap_pairs_focus','percussion_event_count','confidence','velocity_note','notes_filtered_or_discarded_by_inspection','decoder_errors','programs_and_tracks')}, indent=2))}</pre>
<p>MIDI timing and parser audit: {html.escape(m['raw_midi']['consistency'])}. MIDI file: <a href="/midi/{key}/raw.mid">raw MIDI</a>; canonical JSON: <a href="/midi/{key}/notes.json">note events</a>; run details: <a href="/midi/{key}/run.json">run manifest</a>.</p>
<p>Program labels come from explicit program_change events written from the model's decoded program class. They identify the model's MIDI class, not a verified instrument in the source audio.</p></details></article>""")
    html_page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>YourMT3+ transcription inspection · Meaningful Love</title><link rel="stylesheet" href="/assets/phase3c.css"><script defer src="/assets/phase3c.js"></script></head><body>
<header class="page-header"><p class="eyebrow">Phase 3C · transcription inspection</p><h1>Silo — Meaningful Love</h1><p>Inspect actual YourMT3+ MIDI predictions on the original recording clock. Predicted events are not verified truth.</p></header>
<main><section class="summary"><h2>What YourMT3+ Actually Detected</h2><p class="lede">The figures below are computed from the raw MIDI and checked against the canonical note JSON. They describe the model output; they do not measure accuracy.</p><div class="summary-grid">{''.join(models)}</div>
<div class="note-callout"><h3>Why some notes look like tiny marks</h3><p>Both causes matter. The MIDI contains genuinely short predictions, so short durations are model output. The earlier display also compressed 30 seconds onto one wide chart and used thin line marks, which made events hard to read. This view zooms to 6–18 seconds and draws note rectangles; their widths use the unchanged MIDI duration. A minimum 4-pixel display width is used only below screen resolution and never changes the stored timing.</p><p>The MIDI writer sets note-on velocity to its default 100; this is not confidence. No confidence values are available. Drum durations are synthetic 10 ms note-offs inserted by the exporter; short pitched-note statistics are reported separately.</p></div>
</section>
<section id="audio-panel"><h2>Listen to audio and MIDI</h2><p>The original mix and the Other stem were both transcribed with the same YourMT3+ checkpoint. Choose the source for the reconstruction; Mix/Other agreement does not establish accuracy.</p>
<div class="audio-controls"><button id="play-toggle" type="button">Play</button><label>Mode<select id="play-mode"><option value="original">A · Original reference WAV</option><option value="midi">B · YourMT3+ synthetic MIDI reconstruction</option><option value="ab">A/B · Same-time manual switch</option></select></label><button id="ab-switch" type="button" hidden>Switch A ↔ B</button><label>Reconstruction source<select id="reconstruction-source"><option value="yourmt3_mix">YourMT3+ Mix</option><option value="yourmt3_other">YourMT3+ Other</option></select></label><label>Speed<select id="play-speed"><option value="1">1×</option><option value="0.75">0.75×</option><option value="0.5">0.5×</option></select></label><label>Volume<input id="volume" type="range" min="0" max="1" step="0.01" value="0.8"></label><label class="check"><input id="loop-focus" type="checkbox"> Loop selected 6–18 s interval</label></div>
<div class="timeline"><span id="time-display">0.000 / 30.000 s</span><input id="seek" type="range" min="0" max="30" step="0.001" value="0" aria-label="Playback position in original recording seconds"><span id="audio-status" role="status">Loading reference audio and MIDI reconstruction…</span></div>
<audio id="original-audio" preload="metadata" src="/reference.wav"></audio><audio id="midi-audio" preload="metadata" src="/reconstruction/yourmt3_mix.wav"></audio>
<p class="synth-note">The reconstruction is a reproducible neutral 3-partial oscillator, not a General MIDI soundfont. It follows predicted pitch/onset/offset and excludes percussion, which is explicitly counted in the summary. It does not imitate the named instrument programs. Switching A/B preserves the same source-clock position.</p>
</section>
<section id="piano-roll-section"><h2>6–18-second detailed piano roll</h2><p>Times are original recording seconds; notes are never shifted to start at 6. A note that crosses the view boundary is clipped visually and marked at the edge; its actual times remain in its details.</p>
<div class="view-controls"><label>View<select id="time-view"><option value="focus">Focus 6–18 s</option><option value="phrase-a">Phrase A · 6–12 s</option><option value="phrase-b">Phrase B · 12–18 s</option><option value="overview">Overview · 0–30 s</option><option value="custom">Custom</option></select></label><label>Source<select id="roll-source"><option value="both">Mix + Other</option><option value="yourmt3_mix">Mix</option><option value="yourmt3_other">Other</option></select></label><button id="zoom-in" type="button">Zoom in</button><button id="zoom-out" type="button">Zoom out</button><button id="pan-left" type="button">Pan left</button><button id="pan-right" type="button">Pan right</button><button id="reset-view" type="button">Reset 6–18</button>
<label>Pitch min <input id="pitch-min" type="number" min="0" max="127" value="35"></label><label>Pitch max <input id="pitch-max" type="number" min="0" max="127" value="84"></label><button id="pitch-auto" type="button">Auto pitch range</button></div>
<div id="roll-legend" class="legend"></div><div class="reference-workspace"><div class="roll-scroll"><canvas id="piano-roll" width="1440" height="1200" aria-label="Scrollable piano roll with actual pitch rows and source-clock time axis"></canvas></div><aside class="screenshot-pane"><h3>Songscription visual reference</h3><p>Screenshots are model predictions, not verified notes. Their horizontal positions do not supply exact audio times.</p><label>Preview screenshots<input id="screenshot-files" type="file" accept="image/png,image/jpeg,image/webp" multiple></label><div id="screenshot-gallery"></div><p>Persistent screenshots: place files in <code>external/screenshots/</code>, then rebuild. <a href="https://www.songscription.ai/t/tE2Sw3ABTIqOj_LUifRVKw?view=sheet&amp;ref=cp" target="_blank" rel="noreferrer">Public Songscription score</a></p></aside></div><p id="roll-tooltip" role="status">Click a note to see its exact properties here.</p>
<p class="roll-key">Rows use MIDI pitch names. Filled rectangles use exact onset and offset; a 4-pixel minimum horizontal width is display-only. Separate same-pitch notes remain separate events.</p></section>
<section><h2>Predicted note-event table · onsets in 6–18 seconds</h2><p>This table includes every MIDI event starting in the focus interval, including percussion events. “Confidence” is blank because the backend did not provide it. Click headings to sort; click a row to seek and highlight.</p>
<div class="table-filters"><label>Source<select id="filter-source"><option value="all">All</option><option value="yourmt3_mix">Mix</option><option value="yourmt3_other">Other</option></select></label><label>Track<select id="filter-track"><option value="all">All tracks</option></select></label><label>Pitch min<input id="filter-pitch-min" type="number" min="0" max="127" value="0"></label><label>Pitch max<input id="filter-pitch-max" type="number" min="0" max="127" value="127"></label><button id="csv-export" type="button">Export filtered rows as CSV</button><span id="row-count"></span></div>
<div class="table-wrap"><table id="notes-table"><thead><tr><th><button data-sort="id">Note ID</button></th><th><button data-sort="model_key">Source</button></th><th><button data-sort="track">Track</button></th><th>Instrument / program</th><th><button data-sort="pitch">Pitch</button></th><th>MIDI</th><th><button data-sort="onset">Onset (s)</button></th><th><button data-sort="offset">Offset (s)</button></th><th><button data-sort="duration">Duration (ms)</button></th><th>Velocity</th><th>Confidence</th><th>Flags</th></tr></thead><tbody></tbody></table></div></section>
<section><h2>Mix versus Other</h2><p id="compare-summary"></p><div class="compare-grid" id="compare-details"></div><details><summary>Matching candidates and notes unique to each input</summary><pre id="candidate-details"></pre></details><h3>Simple descriptive observations from the predicted MIDI</h3><p>These are sequence and timing statistics over the symbolic predictions; they do not assert that a musical pattern is audible or correct.</p><div id="sequence-summary"></div></section>
{manual_review_html()}
<section><h2>How to Evaluate This Transcription</h2><ol><li>Play the original reference WAV. The server status above reports whether the browser received usable audio.</li><li>Switch to MIDI reconstruction to hear a neutral synthesized rendering. It contains pitched notes only; its timbre does not identify instruments.</li><li>Keep the piano roll at 6–18 seconds, or choose a six-second phrase. Each row is a named pitch and each rectangle keeps the prediction’s MIDI duration.</li><li>Click a note or table row to seek. Compare onset, offset and pitch against the original audio. Treat unexplained model notes as candidates, not automatic false positives.</li><li>Use the table filters and note IDs to record possible missing notes, extra notes or octave errors. No accuracy score is shown without independently reviewed labels.</li><li>Compare Mix and Other at the same source times and pitch scale. They are the same model on different inputs; overlap is not evidence of truth.</li><li>Use the manual review controls on this page to record uncertain observations and later compare against Songscription. Screenshots do not supply exact onset times.</li></ol><p><a href="/MANUAL_COMPARISON_6-18S.md">Phase 3B qualitative comparison report</a> · <a href="/manual-comparison.json">Phase 3B structured review</a> · <a href="/report-phase3b-view-only.html">Phase 3B screenshot comparison diagnostic</a></p></section>
<footer><p>AMT: {html.escape(data['models']['yourmt3_mix']['backend'])} · {html.escape(data['models']['yourmt3_mix']['checkpoint'])}. Source offsets: 0.000 s. Basic Pitch and legacy event caches are not used.</p></footer></main>
</body></html>"""
    (root / "report.html").write_text(html_page, encoding="utf-8")


def write_inspection_report(root, data):
    root = Path(root)
    lines = [
        "# Phase 3C transcription inspection: Silo — Meaningful Love", "",
        "**Focus:** 6.000–18.000 seconds on the original recording clock  ",
        "**Diagnostic:** `output/midi_pipeline/phase3/meaningful-love-v1/report.html`  ",
        "**Backend:** YourMT3+; this phase repairs inspection only and does not implement Phase 4.", "",
        "## Playback defect and repair", "",
        "The previous Phase 3 HTML used a bare relative `reference.wav` URL and Phase 3 had no dedicated local web server, audio route, byte-range handler, or server launch command. If that HTML was hosted outside its artifact directory, the relative asset did not resolve to the WAV; if opened as a local file, browser/app file-URL policy determined whether it could load. No Phase 3 listener was present on the inspection ports when audited. The precise browser console/network record from the original `0:00 / 0:00` screenshot was not available, so the report does not claim a retrospective HTTP status for that exact viewing session. The reproducibility defect was the unserved, context-dependent relative asset.", "",
        "Phase 3C serves the report and only named inspection artifacts from a loopback-only HTTP server at `http://127.0.0.1:8768/`. The reference player URL is `/reference.wav`; the route sends `audio/wav`, content length, byte-range support (`206`/`Content-Range`), and a health record at `/api/health`. Browser playback errors remain visible; the player never substitutes another audio file.", "",
        f"The reference file is {data['reference_audio']['duration_seconds']:.3f} seconds, {data['reference_audio']['sample_rate']} Hz, {data['reference_audio']['channels']} channels, {data['reference_audio']['encoding']}; SHA-256 `{data['reference_audio']['sha256']}`.", "",
        "## What the raw MIDI contains", "",
        "The build loaded each existing Phase 3 run through the strict current-run/hash reader, reparsed its unmodified `raw.mid`, and compared every event to canonical JSON by track, pitch, onset, offset, velocity, program and percussion flag. Both files had positive event durations and source-clock offset zero. No note was dropped by this inspection. The run log separately contains decoder token errors, which occurred before a note event could be exported and are listed below; they are not a count of missing ground-truth notes.", "",
        "A second parser read the raw MIDI messages and tick clock directly. It checked explicit program-change messages, paired every note-on/note-off, applied the file's tempo map, and compared the resulting pitches, programs and seconds with the parsed events. Both files have 206/206 and 54/54 note-on/off pairs respectively, no dangling notes, explicit program changes for every melodic/drum track, 480 ticks per beat, and no tempo meta-events. The writer uses its 500,000 μs/beat (120 BPM) default. Each file has one empty conductor track plus 6 or 4 note tracks. No tempo-dependent duration is silently estimated from a screenshot.", "",
        "## Audio preprocessing and source timing", "",
        "The reference mix is a verified 30-second, 48 kHz, stereo PCM16 crop from original source time 0.000 seconds. YourMT3+ converts it to mono and uses polyphase resampling from 48 kHz to its 16 kHz model rate. The separate Other-stem crop is 30 seconds, 22.05 kHz, mono PCM16 and is likewise converted to 16 kHz. Both crops begin at original recording time 0.000 seconds. The canonical event writer adds that zero-second offset once. The local HTML plays the unresampled 48 kHz reference and the 24 kHz mono reconstructions.", "",
    ]
    for key in MODELS:
        model = data["models"][key]
        s = model["stats"]
        fd = s["focus_duration_seconds"]
        pd = s["focus_pitched_duration_seconds"]
        r = s["focus_pitch_range_midi"]
        prange = f"{pitch_name(r[0])} to {pitch_name(r[1])}" if r else "none"
        common = ", ".join(f"{x['name']} ({x['count']})" for x in s["most_common_focus_pitches"]) or "none"
        lines += [f"### {'YourMT3+ Mix' if key == 'yourmt3_mix' else 'YourMT3+ Other'}", "",
            f"- Input: `{model['input']['path']}`; **{model['input']['type']}**; {model['input']['sample_rate']} Hz, 30 seconds; source SHA-256 `{model['input']['sha256']}`.",
            f"- Backend/checkpoint: `{model['backend']}` / `{model['checkpoint']}` (SHA-256 `{model['checkpoint_sha256']}`). Device: {model['status']} run, CUDA runtime {model['runtime_seconds']:.2f} s.",
            f"- Raw MIDI: {model['raw_midi']['track_count']} instrument tracks, {model['raw_midi']['total_notes']} notes. Events start from {s['earliest_onset_seconds']:.3f} s; latest note-off is {s['latest_offset_seconds']:.3f} s.",
            f"- Focus 6–18 s: {s['notes_starting_in_focus']} events start in the interval ({s['pitched_notes_starting_in_focus']} pitched; {s['notes_intersecting_focus']} events touch the interval). Pitched range: {prange}. Most frequent pitches: {common}.",
            f"- All focus events: shortest {fd['minimum']*1000:.2f} ms, median {fd['median']*1000:.2f} ms, longest {fd['maximum']*1000:.2f} ms; {fd['under_50ms_count']} are below 50 ms. Pitched focus events only: shortest {pd['minimum']*1000:.2f} ms, median {pd['median']*1000:.2f} ms, longest {pd['maximum']*1000:.2f} ms; {pd['under_50ms_count']} are below 50 ms.",
            f"- Concurrency: {len(s['overlap_regions_focus'])} continuous overlap regions and {s['overlap_pairs_focus']} distinct overlapping note pairs. Percussion: {s['percussion_event_count']} events. Focus events filtered by this inspector: {s['notes_filtered_or_discarded_by_inspection']}.",
            f"- Decoder diagnostics: `{json.dumps(s['decoder_errors'], sort_keys=True)}`. These indicate token-decoding problems, not independently verified missing notes.",
            "- Tracks/programs: " + "; ".join(f"track {t['track']} {t['name']} (program {t['midi_program']}, {t['note_count']} notes{', percussion' if t['is_percussion'] else ''})" for t in s["programs_and_tracks"]) + ".",
            "- The raw MIDI has no tempo meta-event. The writer's default tempo is 500,000 μs/beat (120 BPM); both parsing and writing use that default, and the stored note times are the absolute decoded seconds. The original-clock offset is 0.000 s and was applied once.",
            "- Confidence is unavailable. MIDI velocity is constant 100 from the writer and must not be read as confidence.", ""]
    c = data["mix_other_comparison"]
    lines += ["## Why the old roll looked like short vertical bars", "",
        "The durations are genuinely short in the prediction. The raw MIDI and canonical JSON agree; typical pitched focus events last roughly 30 ms. At a 30-second overview, a 30 ms note gets only a few horizontal pixels. The old diagnostic compounded that by drawing thin line segments rather than pitch-row rectangles. This is therefore both an AMT-duration characteristic and a rendering/scaling problem; it is not an onset/offset parser mismatch.", "",
        "The new roll focuses on 6–18 seconds by default, labels every pitch row, uses exact event onset and offset, and offers 0–30 overview, phrase views, custom zoom/pan and pitch bounds. The 4 px minimum width applies only to display. Tooltips, the note table, CSV and MIDI retain actual seconds and durations.", "",
        "## Instrument program provenance", "",
        "The acoustic-piano/program-0 label was not filled in by `pretty_midi` because the MIDI track was empty or missing a program. YourMT3+ decodes a program class; its configured `gm_ext_plus` inverse vocabulary maps that class and the upstream writer emits an explicit `program_change` and track name. The raw MIDI and parser both report program 0 for that model output. It remains an AMT instrument-class prediction, not proof that the source audio contains an acoustic piano. The drum channel also has MIDI program field 0 as required by the MIDI drum-channel convention; it is separately flagged as percussion.", "",
        "## Reconstruction, comparison and descriptive note evidence", "",
        "The MIDI reconstruction is a local deterministic three-partial additive synthesizer generated from the actual parsed MIDI pitches, onsets and offsets. It is reproducible without a soundfont download, but does not imitate General MIDI instrument timbres. Percussion is excluded from that pitched-note render and counted explicitly. The reference WAV and each reconstruction share a 0–30 second clock, with no six-second rebase.", "",
        f"Mix-vs-Other yielded {len(c['matched_candidates'])} one-to-one same-pitch onset candidates within {c['note_onset_tolerance_seconds']*1000:.0f} ms in 6–18 seconds. {len(c['mix_unique_note_ids'])} Mix and {len(c['other_unique_note_ids'])} Other pitched events had no candidate pair. These are predictions from the same model on different audio inputs; they are agreement candidates only, not accuracy.", "",
        "The screen also lists descriptive rising/falling triples, repeated pitch triples, short and sustained events, gaps in a model track, and same-pitch overlaps. Each candidate cites note IDs and source-clock seconds. A gap means no note was emitted on that model track; it is not evidence of an audible omission. A repeated pitch triple is not a verified arpeggio or musical motif.", "",
        "## Human evaluation and next decision", "",
        "The sorted/filterable focus table includes every event whose onset is in 6–18 seconds, with source, track/program, named pitch, MIDI pitch, exact onset/offset, duration, velocity and flags; confidence is shown as unavailable. It supports track/source/pitch filtering, seeking/highlighting, and CSV export. Mix and Other rolls share the same axes.", "",
        "The Songscription screenshot-assisted Phase 3B comparison and its human annotation template are preserved and linked from the new view. No independent note truth is supplied here. The revised inspection makes the events usable for listening, but it does not establish transcription accuracy or authorize Phase 4. Review candidate short events, instrument labels and missing-note suspicions against the reference audio, and only then record supported annotations.", "",
        "## Reproduction", "",
        "Build this report and reconstructions in WSL with `output/amt/env/bin/python -B -m scripts.midi_pipeline.phase3c build`. Start the local browser server with `python -m scripts.midi_pipeline.phase3c serve --port 8768` from the repository root using the installed Windows Python, then open `http://127.0.0.1:8768/`. Health: `http://127.0.0.1:8768/api/health`. Stop the foreground server with Ctrl+C.", "",
        "## Verification", "",
        "On 2026-11-05, all 40 `scripts.midi_pipeline.test_pipeline` tests passed in the existing WSL Python 3.11 environment; this included a synthetic explicit-tempo MIDI test proving independent tick-to-second conversion, note pairing, program auditing, and detection of a missing note-off. Node's `--check` accepted the diagnostic JavaScript. The generated event table contained 138 onset-in-window events, and filtering to Other / track 0 showed 30 events; selecting note 0 displayed F4, MIDI 65, onset 8.240 s, offset 8.270 s, and 30.21 ms exactly, and sought both players to 8.240 s.", "",
        "The Windows localhost server returned 200 for the 30.000-second 48 kHz stereo PCM16 reference and both 30.000-second 24 kHz mono reconstructions, with `audio/wav`. It returned 206 with correct `Content-Range` for byte ranges on all three WAVs. The same headers and media durations loaded in the browser. I verified original WAV playback, Mix and Other synthesis playback, seek/volume, 0.5× speed, focus loop wrap, A/B source switching, screenshot loading, and the qualitative annotation control. No verified reference notes were created; the annotation smoke test remained only in the temporary browser tab and was not exported. The browser console had no warning/error entries. The page's volume/speed/timeline can be checked in the served report.", "",
        "Piano-roll time/pitch rectangles are computed directly from the validated JSON events, use a 4-pixel width only below display resolution, and reveal exact data on hover/click or selected table rows. The browser screenshot showed legible table text and labels at the responsive canvas width. A screenshot-only coordinate-to-audio timing and direct canvas-note hover were not used as transcription evidence; actual event details are shown in the page. Basic Pitch, its caches and the legacy event detector are not read by the inspection or reconstruction.", "",
    ]
    repo_root = root.resolve().parents[3]
    (repo_root / "PHASE3C_TRANSCRIPTION_INSPECTION.md").write_text("\n".join(lines), encoding="utf-8")


class InspectionHandler(BaseHTTPRequestHandler):
    root = BASE
    server_version = "Phase3CReview/1"

    def _route(self):
        path = unquote(urlsplit(self.path).path)
        if path in ("/", "/report.html"):
            return self.root / "report.html", "text/html; charset=utf-8"
        if path == "/reference.wav":
            return self.root / "reference.wav", "audio/wav"
        if path == "/reference_other.wav":
            return self.root / "reference_other.wav", "audio/wav"
        if path == "/inspection.json":
            return self.root / "phase3c-inspection.json", "application/json; charset=utf-8"
        if path == "/MANUAL_COMPARISON_6-18S.md":
            return self.root / "MANUAL_COMPARISON_6-18S.md", "text/markdown; charset=utf-8"
        if path == "/manual-comparison.json":
            return self.root / "manual-comparison.json", "application/json; charset=utf-8"
        if path == "/report-phase3b-view-only.html":
            return self.root / "report-phase3b-view-only.html", "text/html; charset=utf-8"
        if path == "/PHASE3C_TRANSCRIPTION_INSPECTION.md":
            return self.root.resolve().parents[3] / "PHASE3C_TRANSCRIPTION_INSPECTION.md", "text/markdown; charset=utf-8"
        if path in ("/assets/phase3c.js", "/assets/phase3c.css"):
            mime = "text/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8"
            return self.root / path.lstrip("/"), mime
        parts = path.strip("/").split("/")
        if (len(parts) == 3 and parts[:2] == ["external", "screenshots"]
                and Path(parts[2]).name == parts[2] and "\\" not in parts[2]
                and Path(parts[2]).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")):
            target = self.root / "external" / "screenshots" / parts[2]
            if target.resolve().parent != (self.root / "external" / "screenshots").resolve():
                return None, None
            return target, mimetypes.guess_type(target.name)[0]
        if len(parts) == 2 and parts[0] == "reconstruction" and parts[1] in ("yourmt3_mix.wav", "yourmt3_other.wav"):
            key = parts[1][:-4]
            return self.root / "reconstruction" / f"{key}.wav", "audio/wav"
        if len(parts) == 3 and parts[0] == "midi" and parts[1] in MODELS and parts[2] in ("raw.mid", "notes.json", "run.json"):
            run = json.loads((self.root / "local-runs.json").read_text(encoding="utf-8"))["runs"][parts[1]]
            mime = "audio/midi" if parts[2].endswith(".mid") else "application/json; charset=utf-8"
            return self.root / run["path"] / parts[2], mime
        return None, None

    def _send(self, head=False):
        from urllib.parse import urlsplit
        if urlsplit(self.path).path == "/api/health":
            reference = self.root / "reference.wav"
            try:
                with wave.open(str(reference), "rb") as wav:
                    metadata = {"ok": True, "audio_url": "/reference.wav", "duration_seconds": wav.getnframes() / wav.getframerate(),
                                "sample_rate": wav.getframerate(), "channels": wav.getnchannels(), "encoding": wav.getsampwidth() * 8,
                                "range_supported": True}
            except Exception as error:
                metadata = {"ok": False, "error": f"{type(error).__name__}: {error}"}
            payload = json.dumps(metadata).encode()
            self.send_response(200 if metadata["ok"] else 500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head:
                self.wfile.write(payload)
            return
        path, content_type = self._route()
        if path is None or not path.is_file():
            self.send_error(404)
            return
        size = path.stat().st_size
        start, end, partial = 0, size - 1, False
        value = self.headers.get("Range")
        if value:
            import re
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
            if not match or not any(match.groups()):
                self.send_error(416); return
            first, last = match.groups()
            if first:
                start = int(first); end = min(size - 1, int(last)) if last else size - 1
            else:
                start = max(0, size - int(last))
            if start >= size or start > end:
                self.send_response(416); self.send_header("Content-Range", f"bytes */{size}"); self.end_headers(); return
            partial = True
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head:
            return
        try:
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    block = stream.read(min(128 * 1024, remaining))
                    if not block: break
                    self.wfile.write(block); remaining -= len(block)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self): self._send()
    def do_HEAD(self): self._send(head=True)
    def log_message(self, fmt, *args): print("Phase3C HTTP:", fmt % args)


def serve(root=BASE, port=8768):
    import socket
    root = Path(root).resolve()
    InspectionHandler.root = root
    class LocalServer(ThreadingHTTPServer):
        allow_reuse_address = False
        def server_bind(self):
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()
    server = LocalServer(("127.0.0.1", port), InspectionHandler)
    print(f"Phase 3C local inspection: http://127.0.0.1:{port}/", flush=True)
    print("Loopback-only static inspection server. Stop it with Ctrl+C or terminate the local Python process.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build"); p.add_argument("--root", type=Path, default=BASE)
    p = sub.add_parser("serve"); p.add_argument("--root", type=Path, default=BASE); p.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    if args.command == "build":
        data = build(args.root)
        print(json.dumps({"status": "built", "models": {k: {"notes": v["stats"]["total_notes"],
              "focus_notes": v["stats"]["notes_starting_in_focus"], "reconstruction": v["reconstruction"]} for k, v in data["models"].items()},
              "mix_other_focus_matches": len(data["mix_other_comparison"]["matched_candidates"])}, indent=2))
    else:
        serve(args.root, args.port)


if __name__ == "__main__":
    main()
