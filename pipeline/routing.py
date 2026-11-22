"""Merge explicitly configured instrument-specific transcription routes."""
from collections import Counter
from dataclasses import asdict


def apply_piano_route(backend, full_mix_notes, no_drums_path, hints,
                      target_instrument="acoustic_piano", tuning_corrected=False,
                      full_mix_path=None, bass_hint=None):
    """Replace one instrument track from no_drums / piano-only MuScriptor output."""
    routed = backend.transcribe(no_drums_path, hints)
    selected = [note for note in routed if note.instrument == target_instrument]
    def value(note, key):
        return note[key] if isinstance(note, dict) else getattr(note, key)

    merged = [dict(note) if isinstance(note, dict) else asdict(note)
              for note in full_mix_notes if value(note, "instrument") != target_instrument]
    applied = bool(selected)
    route_summary = {"source": "no_drums", "source_audio_path": str(no_drums_path),
        "instrument": target_instrument, "hints": list(hints),
        "all_routed_note_count": len(routed),
        "routed_instrument_labels": dict(Counter(n.instrument for n in routed)),
        "selected_note_count": len(selected), "replaced_full_mix_note_count": sum(
            value(note, "instrument") == target_instrument for note in full_mix_notes),
        "route_applied": applied,
        "decision": "replace only the configured instrument label; retain other full-mix notes"
                   if applied else "route emitted no target-instrument notes; retained the full-mix track"}
    bass_comparison = {"status": "not_required_tuning_within_25_cents"}
    if tuning_corrected:
        bass_comparison = compare_bass_window(backend, full_mix_path, no_drums_path,
                                              bass_hint or ["electric_bass"])
    if applied:
        merged.extend(asdict(note) for note in selected)
    merged.sort(key=lambda note: (note["onset_s"], note["instrument"], note["pitch"]))
    return {"merged_notes": merged,
            "route_notes": [asdict(note) for note in selected],
            "route_summary": route_summary,
            "bass_sequence_comparison": bass_comparison}


def compare_bass_window(backend, full_mix_path, no_drums_path, hints,
                        start_s=6., end_s=19.):
    """Compare the same bass-only inference on corrected full and drumless mixes."""
    full_notes = backend.transcribe(full_mix_path, hints)
    drumless_notes = backend.transcribe(no_drums_path, hints)

    def sequence(notes):
        selected = [note for note in notes if start_s <= note.onset_s < end_s
                    and "bass" in note.instrument.casefold()]
        return [{"pitch": note.pitch, "onset_s": note.onset_s,
                 "offset_s": note.offset_s, "instrument": note.instrument}
                for note in sorted(selected, key=lambda note: (note.onset_s, note.pitch))]

    full_sequence = sequence(full_notes)
    drumless_sequence = sequence(drumless_notes)
    return {"status": "complete", "window": {"start_s": start_s, "end_s": end_s},
            "hints": list(hints), "full_mix_notes": full_sequence,
            "no_drums_notes": drumless_sequence,
            "full_mix_pitch_sequence": [note["pitch"] for note in full_sequence],
            "no_drums_pitch_sequence": [note["pitch"] for note in drumless_sequence],
            "pitch_sequence_identical": [note["pitch"] for note in full_sequence] ==
                                         [note["pitch"] for note in drumless_sequence],
            "event_count_identical": len(full_sequence) == len(drumless_sequence),
            "comparison_definition": "Ordered pitch sequences, using same model and bass-only hints; source onset/offset times are reported but do not enter pitch-sequence equality."}
