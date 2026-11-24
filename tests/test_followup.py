"""Regression tests for reference inclusion, raw texture and contour/resolution."""
import unittest

from pipeline.note_metrics import onset_texture
from pipeline.patterns import arpeggio_acceptance, contour_clusters, encode_bar, run
from pipeline.quantize import quantize
from pipeline.reference import instrument_mapping
from pipeline.layers import split_notes
from pipeline.runner import Runner
from pipeline.routing import apply_piano_route
from pipeline.tuning import compare_note_pitches
from pipeline.common import Note
from tests.test_pipeline import grid, note


class ResolutionTests(unittest.TestCase):
    def test_distinct_raw_attacks_choose_coarsest_qualifying_grid(self):
        result = quantize([note(.01), note(.06, 64)], grid())
        stats = result["instrument_stats"][0]
        self.assertEqual(stats["collision_rate_16"], 1.)
        self.assertEqual(stats["collision_rate_32"], 0.)
        self.assertFalse(stats["requantized_to_32"])
        self.assertEqual(result["instrument_steps_per_bar"]["piano"], 24)
        self.assertEqual([n["step"] for n in result["notes"]], [0, 1])

    def test_true_chord_does_not_count_as_quantization_collision(self):
        result = quantize([note(0, 60), note(.02, 64), note(.03, 67)], grid())
        self.assertEqual(result["instrument_stats"][0]["collision_rate_16"], 0)
        self.assertEqual(result["instrument_steps_per_bar"]["piano"], 16)

    def test_exactly_ten_percent_does_not_upgrade_and_other_instruments_stay_16(self):
        notes = [note(.01), note(.06, 64)]
        notes.extend(note(i * .125) for i in range(1, 10))
        notes.extend([note(.01, 48, "bass"), note(.06, 50, "bass")])
        result = quantize(notes, grid())
        by_instrument = {r["instrument"]: r for r in result["instrument_stats"]}
        self.assertEqual(by_instrument["piano"]["collision_rate_16"], .1)
        self.assertFalse(by_instrument["piano"]["requantized_to_32"])
        self.assertEqual(by_instrument["bass"]["steps_per_bar"], 24)

    def test_explicit_32_and_manifest_configuration(self):
        result = quantize([note(.06)], grid(), 32)
        self.assertEqual(result["notes"][0]["steps_per_bar"], 32)
        self.assertFalse(result["resolution_adjustments"])
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            runner = Runner({"id": "x", "audio_path": "missing",
                             "rhythm": {"steps_per_bar": 32}}, folder)
            self.assertEqual(runner.steps, 32)

    def test_grid_family_prefers_coarser_candidate_within_five_ms(self):
        result = quantize([note(0), note(.5), note(1.)], grid(), step_candidates=[16, 24, 32, 48])
        stats = result["instrument_stats"][0]
        self.assertEqual(stats["steps_per_bar"], 16)
        self.assertEqual(len(result["collision_policy"]["grid_family_debug"][0]["candidates"]), 4)


class ContourTests(unittest.TestCase):
    def test_major_and_minor_ascending_arpeggios_share_cluster(self):
        encodings = {}
        for name, pitches in (("major", (60, 64, 67, 72)), ("minor", (60, 63, 67, 72))):
            notes = [{**note(step * .125, pitches[step % 4]), "step": step}
                     for step in range(16)]
            encodings[name] = encode_bar(notes)
        self.assertEqual(encodings["major"]["contour"], encodings["minor"]["contour"])
        assignments = contour_clusters(encodings, .25, "p")
        self.assertEqual(assignments["major"][0], assignments["minor"][0])
        self.assertNotEqual(encodings["major"]["mini_notation"], encodings["minor"]["mini_notation"])
        # Exercise the main pipeline path as well as its cluster helper.
        notes = [note(bar * 2 + step * .125,
                      (60, 64 if bar == 0 else 63, 67, 72)[step % 4])
                 for bar in range(2) for step in range(16)]
        patterns = run(quantize(notes, grid(4)), grid(4))
        # The newer absolute-pitch event matcher deliberately separates major
        # and minor figures (four pitch substitutions out of sixteen = 25%).
        self.assertEqual(len({r["exact_id"] for r in patterns["bars"] if r.get("exact_id")}), 2)

    def test_up_and_down_contours_do_not_merge(self):
        encodings = {name: encode_bar([{**note(i * .25, pitch), "step": i * 2}
                                      for i, pitch in enumerate(pitches)])
                     for name, pitches in (("up", (60, 62, 64, 66)),
                                           ("down", (66, 64, 62, 60)))}
        assignments = contour_clusters(encodings, .25, "p")
        self.assertNotEqual(assignments["up"][0], assignments["down"][0])

    def test_contours_one_edit_apart_share_cluster(self):
        encodings = {
            "a": {"onset_pattern": "x...x...x...x...", "contour": "UUU", "pitch_intervals": [4, 3, 5]},
            "b": {"onset_pattern": "x...x...x...x...", "contour": "UUD", "pitch_intervals": [4, 3, -5]},
        }
        assignments = contour_clusters(encodings, .25, "p", contour_threshold=1)
        self.assertEqual(assignments["a"][0], assignments["b"][0])


class LayerTests(unittest.TestCase):
    def test_halfbar_routes_sustained_bass_chords_and_skyline_line(self):
        g = grid(2)
        qnotes = [
            {**note(0.0, 40, "piano", 1.1), "bar": 0, "step": 0},
            {**note(.2, 64), "bar": 0, "step": 2},
            {**note(.205, 67), "bar": 0, "step": 2},
            {**note(.21, 71), "bar": 0, "step": 2},
            {**note(.6, 60), "bar": 0, "step": 6},
            {**note(.605, 72), "bar": 0, "step": 6},
            {**note(.9, 76), "bar": 0, "step": 9},
        ]
        result = split_notes(qnotes, g, {"piano": 16})
        first = {(row["pitch"], row["layer"]) for row in result["notes"]
                 if row["bar"] == 0 and row["layer"] != "irregular"}
        self.assertIn((40, "bass"), first)
        self.assertEqual(sum(layer == "chord" for _, layer in first), 3)
        self.assertIn((72, "line"), first)
        self.assertNotIn((60, "line"), first)
        line_unit = next(row for row in result["half_bar_layers"]
                         if row["instrument"] == "piano" and row["layer"] == "line")
        self.assertIn("c5", line_unit["mini_notation"])


class TuningAndRoutingTests(unittest.TestCase):
    class Backend:
        def transcribe(self, audio, hints):
            return [Note(1., 1.2, 72, None, "acoustic_piano"),
                    Note(2., 2.2, 60, None, "electric_piano")]

    def test_pitch_comparison_matches_onset_before_counting_changed_notes(self):
        before = [note(1., 60), note(2., 64)]
        after = [note(1.01, 61), note(2.02, 64), note(3., 67)]
        result = compare_note_pitches(before, after)
        self.assertEqual(result["pitch_changed_note_count"], 1)
        self.assertEqual(result["unmatched_after_count"], 1)

    def test_no_drums_piano_route_replaces_only_acoustic_piano(self):
        full = [note(.5, 60, "acoustic_piano"), note(1., 36, "electric_bass")]
        result = apply_piano_route(self.Backend(), full, "no_drums.wav", ["acoustic_piano"])
        self.assertTrue(result["route_summary"]["route_applied"])
        self.assertEqual([row["pitch"] for row in result["merged_notes"]
                          if row["instrument"] == "acoustic_piano"], [72])
        self.assertEqual(len([row for row in result["merged_notes"]
                              if row["instrument"] == "electric_bass"]), 1)

    def test_acoustic_piano_is_an_unambiguous_muscriptor_hint(self):
        from muscriptor.tokenizer.mt3 import resolve_instrument_names
        self.assertEqual(resolve_instrument_names(["acoustic_piano"]), ["acoustic_piano"])

    def test_chord_internal_pitch_order_is_not_a_fake_ascending_contour(self):
        encoded = encode_bar([{**note(0, pitch), "step": 0} for pitch in (60, 64, 67)]
                             + [{**note(.5, pitch), "step": 4} for pitch in (59, 63, 66)])
        self.assertEqual(encoded["contour"], "D")


class ReferenceTests(unittest.TestCase):
    def data(self):
        return {"bars": [{"instrument": "acoustic_piano", "bar": i,
                          "pitch_pattern_id": f"m{i}", "pattern_type": "regular_bar"}
                         for i in range(4)],
                "pitch_pattern_definitions": [{"pitch_pattern_id": f"m{i}", "mini_notation": "0 4 7 12"}
                                               for i in range(4)]}

    def test_bar_requires_at_least_half_its_duration_in_reference(self):
        result = arpeggio_acceptance({"start_s": 1.1, "end_s": 6.9, "instrument": "piano"},
                                    grid(), self.data())
        self.assertEqual([b["bar"] for b in result["cluster_ids_per_bar"]], [2, 3])
        exactly_half = arpeggio_acceptance({"start_s": 1, "end_s": 7, "instrument": "piano"},
                                          grid(), self.data())
        self.assertEqual(exactly_half["regular_section_bar_count"], 4)

    def test_half_bar_acceptance_can_pass_when_one_bar_does_not(self):
        notes = [{**note(bar * 2 + step * .125, (60, 64, 67, 72)[(step % 8) // 2],
                         "acoustic_piano"), "bar": bar, "step": step}
                 for bar in range(4) for step in (0, 2, 4, 6, 8, 10, 12, 14)]
        quantized = {"notes": notes, "steps_per_bar": 16}
        result = arpeggio_acceptance({"start_s": 0, "end_s": 8, "instrument": "piano"},
                                    grid(), self.data(), quantized=quantized)
        self.assertFalse(result["levels"]["one_bar"]["pass"])
        self.assertTrue(result["levels"]["half_bar"]["pass"])
        self.assertEqual(result["levels"]["half_bar"]["unit_count"], 8)
        self.assertEqual(result["levels"]["two_bar_phrase"]["unit_count"], 2)
        self.assertTrue(result["pass"])

    def test_strings_maps_to_emitted_synth_pad_by_window_evidence(self):
        notes = [note(67, 60, "synth_pad", 80), note(68, 75, "synth_lead", 68.1),
                 note(90, 65, "synth_strings", 95)]
        result = instrument_mapping(["strings"], ["synth_pad", "synth_lead", "synth_strings"],
                                    notes, 67, 80)
        self.assertEqual(result[0]["matched_instruments"], ["synth_pad"])

    def test_simultaneous_event_share_and_ascending_runs_use_raw_onsets(self):
        chord = [note(0, p) for p in (60, 64, 67)]
        run = [note(1 + i * .1, 60 + i) for i in range(4)]
        result = onset_texture(chord + run)
        self.assertAlmostEqual(result["simultaneous_onset_share"], 3 / 7)
        self.assertEqual(result["failure_classification"], "matching")
        self.assertEqual(result["ascending_single_note_run_count"], 1)
        self.assertEqual(onset_texture(chord)["failure_classification"], "transcription")
        different = onset_texture([note(0, 60, "piano"), note(0, 60, "synth_pad")])
        self.assertEqual(different["simultaneous_onset_share"], 0)


if __name__ == "__main__":
    unittest.main()
