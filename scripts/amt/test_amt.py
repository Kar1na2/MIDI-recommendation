"""Behavioral checks for note timing, polyphony, evaluation, and split safety."""
import json
from pathlib import Path
import tempfile
import unittest

import pretty_midi

from .dataset import load_manifest
from .evaluate import frame_score, score
from .midi_patterns import analyze, attack_groups
from .smoke_fixture import create_fixture


def note(pitch, start, duration=.2):
    return pretty_midi.Note(80, pitch, start, start + duration)


class SymbolicAnalysisTests(unittest.TestCase):
    def test_sustained_repeated_pitch_keeps_reattacks(self):
        groups = attack_groups([note(60, 0, 2), note(60, .5, 2), note(64, .01, 1)])
        self.assertEqual([len(g) for g in groups], [2, 1])

    def test_nearby_attacks_cannot_chain_into_one_chord(self):
        self.assertEqual(len(attack_groups([note(60, 0), note(64, .02), note(67, .04)])), 2)

    def test_register_changes_are_not_equal_note_patterns(self):
        midi = pretty_midi.PrettyMIDI()
        inst = pretty_midi.Instrument(0)
        inst.notes = [note(p, i * .25) for i, p in enumerate([60, 64, 67, 72, 72, 76, 79, 84])]
        midi.instruments.append(inst)
        result = analyze(midi, end=2.)["tracks"][0]
        self.assertTrue(result["rhythm_repeats"])
        self.assertFalse(result["note_repeats"])
        self.assertTrue(analyze(midi, end=2., transpose=True)["tracks"][0]["note_repeats"])

    def test_drums_have_no_melodic_contour(self):
        midi = pretty_midi.PrettyMIDI()
        inst = pretty_midi.Instrument(0, is_drum=True)
        inst.notes = [note(p, i * .25) for i, p in enumerate([36, 38, 42, 38] * 2)]
        midi.instruments.append(inst)
        result = analyze(midi, end=2.)
        self.assertIsNone(result["bpm"])
        self.assertIsNone(result["tracks"][0]["monophonic_contour"])
        self.assertTrue(result["tracks"][0]["drum_class_repeats"])

    def test_cropped_analysis_does_not_invent_attack_for_carry_in(self):
        midi = pretty_midi.PrettyMIDI()
        inst = pretty_midi.Instrument(0)
        inst.notes = [note(60, 0, 4), note(64, 2, 1)]
        midi.instruments.append(inst)
        events = analyze(midi, start=1, end=3, bpm=120)["tracks"][0]["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["onset"], 2)
        self.assertEqual(events[0]["beat"], 4)

    def test_serialization_after_real_midi_roundtrip(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "notes.mid"
            midi = pretty_midi.PrettyMIDI()
            inst = pretty_midi.Instrument(24)
            inst.notes = [note(60, 6), note(64, 6.5)]
            midi.instruments.append(inst)
            midi.write(str(path))
            result = analyze(pretty_midi.PrettyMIDI(str(path)), start=6, end=7)
            self.assertEqual(json.loads(json.dumps(result))["tracks"][0]["program"], 24)


class EvaluationTests(unittest.TestCase):
    def test_wrong_pitch_can_pass_attack_test_and_fail_note_test(self):
        reference = [{"onset": 6., "offset": 6.4, "pitch": 60}]
        result = score(reference, [note(72, 6., .4)], 6, 7)
        self.assertEqual(result["attack_onset"]["f1"], 1)
        self.assertEqual(result["note_onset"]["f1"], 0)

    def test_partial_labels_never_claim_pitch_accuracy(self):
        result = score([{"onset": 6., "offset": None, "pitch": None}], [note(60, 6)], 6, 7)
        self.assertNotIn("note_onset", result)
        self.assertNotEqual(result.get("status"), "no_reviewed_reference")

    def test_empty_reference_is_pending_not_a_zero_score(self):
        self.assertEqual(score([], [note(60, 6)], 6, 7)["status"], "no_reviewed_reference")

    def test_frame_metric_retains_carry_in_and_penalizes_wrong_duration(self):
        reference = [{"onset": 0., "offset": 2., "pitch": 60}]
        self.assertEqual(frame_score(reference, [note(60, 0, 2)], 1, 2)["f1"], 1)
        self.assertLess(frame_score(reference, [note(60, 1, .1)], 1, 2)["f1"], .2)

    def test_nan_reference_does_not_silently_disappear(self):
        with self.assertRaisesRegex(ValueError, "nonfinite"):
            score([{"onset": float("nan"), "offset": None, "pitch": None}], [], 0, 1)


class DatasetTests(unittest.TestCase):
    def test_reject_composition_leakage_and_prediction_labels(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = create_fixture(Path(temporary) / "fixture")
            self.assertEqual(len(load_manifest(manifest)), 2)
            rows = [json.loads(s) for s in manifest.read_text().splitlines()]
            rows[1]["group_id"] = rows[0]["group_id"]
            manifest.write_text("\n".join(json.dumps(r) for r in rows))
            with self.assertRaisesRegex(ValueError, "multiple splits"):
                load_manifest(manifest)
            rows[1]["group_id"] = "validation"
            rows[0]["label_source"] = "basic_pitch"
            manifest.write_text("\n".join(json.dumps(r) for r in rows))
            with self.assertRaisesRegex(ValueError, "not automatic predictions"):
                load_manifest(manifest)

    def test_reject_identical_audio_across_splits_despite_different_group_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = create_fixture(Path(temporary) / "fixture")
            rows = [json.loads(s) for s in manifest.read_text().splitlines()]
            rows[1]["audio"] = rows[0]["audio"]
            manifest.write_text("\n".join(json.dumps(r) for r in rows))
            with self.assertRaisesRegex(ValueError, "identical audio"):
                load_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
