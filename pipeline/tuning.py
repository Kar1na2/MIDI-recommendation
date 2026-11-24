"""Measure and, when necessary, correct global tuning before transcription."""
from pathlib import Path

from .common import atomic_write_audio, digest, StageError


TARGET_TUNING_CENTS = 25.0
ANALYSIS_SAMPLE_RATE = 22050


def compare_note_pitches(before, after, onset_tolerance_s=.05):
    """Greedily compare same-instrument note events by nearest onset."""
    from collections import defaultdict

    old_by_instrument, new_by_instrument = defaultdict(list), defaultdict(list)
    for row in before:
        old_by_instrument[row["instrument"]].append(row)
    for row in after:
        new_by_instrument[row["instrument"]].append(row)
    changed, matched, missing_old, missing_new = [], 0, 0, 0
    by_instrument = []
    for instrument in sorted(old_by_instrument.keys() | new_by_instrument.keys()):
        old = sorted(old_by_instrument[instrument], key=lambda row: (row["onset_s"], row["pitch"]))
        new = sorted(new_by_instrument[instrument], key=lambda row: (row["onset_s"], row["pitch"]))
        used = set()
        instrument_changed = instrument_matched = 0
        for old_row in old:
            choices = [(abs(old_row["onset_s"] - row["onset_s"]), index)
                       for index, row in enumerate(new) if index not in used
                       and abs(old_row["onset_s"] - row["onset_s"]) <= onset_tolerance_s]
            if not choices:
                missing_old += 1
                continue
            _, index = min(choices)
            used.add(index)
            instrument_matched += 1
            matched += 1
            new_row = new[index]
            if old_row["pitch"] != new_row["pitch"]:
                instrument_changed += 1
                changed.append({"instrument": instrument, "onset_before_s": old_row["onset_s"],
                                "onset_after_s": new_row["onset_s"],
                                "pitch_before": old_row["pitch"], "pitch_after": new_row["pitch"]})
        missing_new += len(new) - len(used)
        by_instrument.append({"instrument": instrument, "matched_notes": instrument_matched,
                              "changed_pitch_notes": instrument_changed})
    return {"matching_rule": f"one-to-one same-instrument nearest onset within {onset_tolerance_s * 1000:.0f} ms",
            "before_note_count": len(before), "after_note_count": len(after),
            "matched_note_count": matched, "pitch_changed_note_count": len(changed),
            "unmatched_before_count": missing_old, "unmatched_after_count": missing_new,
            "changed_notes": changed, "by_instrument": by_instrument}


def run(audio_path, corrected_audio_path):
    """Estimate a mix-wide tuning offset and cache an A440-shifted copy if needed."""
    import librosa
    import numpy as np
    import soundfile as sf

    source = Path(audio_path)
    corrected = Path(corrected_audio_path)
    audio, sample_rate = sf.read(source, dtype="float32", always_2d=True)
    if audio.size == 0:
        raise StageError("Cannot estimate tuning from an empty audio file")
    mono = np.mean(audio, axis=1)
    analysis = librosa.resample(mono, orig_sr=sample_rate, target_sr=ANALYSIS_SAMPLE_RATE)
    offset = float(librosa.estimate_tuning(y=analysis, sr=ANALYSIS_SAMPLE_RATE,
                                          resolution=.01)) * 100.0
    if not np.isfinite(offset):
        raise StageError("Tuning estimator returned a non-finite offset")
    should_correct = abs(offset) > TARGET_TUNING_CENTS
    corrected_digest = None
    if should_correct:
        shifted = librosa.effects.pitch_shift(audio.T, sr=sample_rate,
                                             n_steps=-offset / 100.0)
        corrected.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_audio(corrected, shifted.T, sample_rate, subtype="PCM_24")
        corrected_digest = digest(corrected)
        input_path = str(corrected.resolve())
    else:
        input_path = str(source.resolve())
    return {
        "source_audio_path": str(source.resolve()),
        "source_sha256": digest(source),
        "source_sample_rate": sample_rate,
        "analysis_sample_rate": ANALYSIS_SAMPLE_RATE,
        "method": "librosa.estimate_tuning on the mono full mix",
        "offset_cents": offset,
        "target_tuning_cents": TARGET_TUNING_CENTS,
        "corrected": should_correct,
        "correction_method": "librosa.effects.pitch_shift" if should_correct else None,
        "correction_shift_cents": -offset if should_correct else 0.0,
        "corrected_audio_path": str(corrected.resolve()) if should_correct else None,
        "corrected_audio_sha256": corrected_digest,
        "transcription_audio_path": input_path,
        "original_audio_preserved": True,
    }
