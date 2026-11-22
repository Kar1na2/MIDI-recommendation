"""Fixed event-distance and bass-routing regression contracts."""
import unittest

from pipeline.patterns import encode_bar, event_similarity, sequence_clusters, run
from pipeline.layers import split_notes
from pipeline.quantize import quantize
from tests.test_pipeline import grid, note


class SequenceMatcherTests(unittest.TestCase):
    def encoding(self, pitches, steps=None):
        steps = steps or list(range(len(pitches)))
        return encode_bar([{**note(step * .1, pitch), "step": step}
                           for step, pitch in zip(steps, pitches)])

    def test_one_dropped_event_of_eight_scores_seven_eighths(self):
        first = self.encoding([60, 62, 64, 65, 67, 69, 71, 72])
        second = self.encoding([60, 62, 65, 67, 69, 71, 72], [0, 1, 3, 4, 5, 6, 7])
        self.assertEqual(event_similarity(first["events"], second["events"]), .875)
        ids = sequence_clusters({0: first, 1: second}, "m")
        self.assertEqual(ids[0][0], ids[1][0])

    def test_exact_and_shape_distinguish_transposition(self):
        enc = {0: self.encoding([60, 64, 67, 72]), 1: self.encoding([62, 66, 69, 74])}
        exact = sequence_clusters(enc, "m")
        shape = sequence_clusters(enc, "s", shape=True)
        self.assertNotEqual(exact[0][0], exact[1][0])
        self.assertEqual(shape[0][0], shape[1][0])

    def test_first_representative_is_fixed_no_transitive_chain(self):
        enc = {0: self.encoding([60] * 8), 1: self.encoding([61] + [60] * 7),
               2: self.encoding([61, 62] + [60] * 6)}
        ids = sequence_clusters(enc, "m")
        self.assertEqual(ids[0][0], ids[1][0])
        self.assertNotEqual(ids[0][0], ids[2][0])

    def test_first_eligible_cluster_wins_even_if_later_is_closer(self):
        enc = {0: self.encoding([60] * 8), 1: self.encoding([61, 62] + [60] * 6),
               2: self.encoding([61] + [60] * 7)}
        ids = sequence_clusters(enc, "m")
        self.assertEqual(ids[2][0], ids[0][0])

    def test_rhythm_and_absolute_pitch_both_contribute(self):
        first = self.encoding([60, 64, 67, 72])
        changed = self.encoding([60, 63, 67, 72])
        shifted = self.encoding([60, 64, 67, 72], [2, 3, 4, 5])
        self.assertEqual(event_similarity(first["events"], changed["events"]), .75)
        self.assertLess(event_similarity(first["events"], shifted["events"]), .85)

    def test_pipeline_acceptance_id_is_absolute_pitch(self):
        notes = [note(bar * 2 + step * .25, p)
                 for bar, pitches in enumerate(((60, 64, 67, 72), (62, 66, 69, 74)))
                 for step, p in enumerate(pitches)]
        result = run(quantize(notes, grid(4)), grid(4))
        rows = result["bars"]
        self.assertNotEqual(rows[0]["exact_id"], rows[1]["exact_id"])
        self.assertEqual(rows[0]["shape_id"], rows[1]["shape_id"])

    def test_layer_names_inherit_instrument_grid_and_keep_late_onsets(self):
        notes = [{**note(.8, 60, "piano__line"), "bar": 0, "step": 20},
                 {**note(1.6, 64, "piano__line"), "bar": 0, "step": 40}]
        result = run({"notes": notes, "steps_per_bar": 16,
                      "instrument_steps_per_bar": {"piano": 48}}, grid(4))
        row = result["bars"][0]
        self.assertEqual(row["steps_per_bar"], 48)
        self.assertEqual(row["pattern_type"], "regular_bar")
        self.assertEqual(row["onset_count"], 2)
        self.assertEqual(len(result["patterns"][0]["onset_pattern"]), 48)


class BassRoutingTests(unittest.TestCase):
    def test_silo_window_registers_including_short_arpeggio_attacks(self):
        # Window 1 E2 and window 2 D2/C2/B1, including b23b's D2→D3.
        for low, upper, duration in ((40, [55, 59, 62, 66], .7),
                                     (38, [50, 60, 64, 66], .1),
                                     (36, [48, 60, 64, 66], .1),
                                     (35, [47, 60, 63, 67], .1)):
            with self.subTest(low=low):
                notes = [{**note(0, low, "acoustic_piano", duration), "bar": 0, "step": 0}]
                notes.extend({**note(.1 + i * .2, pitch, "acoustic_piano"),
                              "bar": 0, "step": 1 + i * 2} for i, pitch in enumerate(upper))
                result = split_notes(notes, grid(4), {"acoustic_piano": 16})
                self.assertEqual(next(n for n in result["notes"] if n["pitch"] == low)["layer"], "bass")
                self.assertTrue(all(n["pitch"] != low for n in result["notes"] if n["layer"] == "line"))

    def test_short_note_without_octave_separation_stays_in_line(self):
        notes = [{**note(i * .2, p), "bar": 0, "step": i * 2}
                 for i, p in enumerate((60, 62, 64, 65))]
        result = split_notes(notes, grid(4), {"piano": 16})
        self.assertFalse(any(n["layer"] == "bass" for n in result["notes"]))


class PeriodRevisionTests(unittest.TestCase):
    def test_one_step_tolerance_requires_same_pitch_and_one_to_one(self):
        self.assertEqual(event_similarity([(0, 60), (4, 64)], [(1, 60), (3, 64)]), 1.)
        self.assertEqual(event_similarity([(0, 60), (1, 60)], [(1, 60)]), .5)
        self.assertEqual(event_similarity([(0, 60)], [(1, 61)]), 0.)
        self.assertEqual(event_similarity([(0, 60)], [(2, 60)]), 0.)

    def test_tolerance_preserves_time_order_no_crossing(self):
        self.assertLess(event_similarity([(0, 60), (1, 64)], [(0, 64), (1, 60)]), 1.)

    def test_four_chord_period_passes_despite_low_dominant_share(self):
        from pipeline.period import period_match
        cycle = [[(0, root), (2, root+4), (4, root+7)] for root in (60, 62, 65, 67)]
        result = period_match(cycle * 2)
        self.assertEqual(result["period"], 4)
        self.assertEqual(result["match_rate"], 1.)
        self.assertTrue(result["pass"])

    def test_exactly_three_of_four_comparisons_pass_and_variation_is_listed(self):
        from pipeline.period import period_match
        cycle = [[(0, root), (2, root+4), (4, root+7)] for root in (60, 62, 65, 67)]
        variation = [*cycle[:-1], [(0, 67), (2, 71), (4, 75)]]
        result = period_match(cycle + variation)
        self.assertEqual(result["period"], 4)
        self.assertEqual(result["match_rate"], .75)
        self.assertTrue(result["pass"])
        self.assertTrue(result["pairs"][-1]["differences"])

    def test_empty_sustains_and_different_progressions_fail(self):
        from pipeline.period import period_match
        self.assertFalse(period_match([[]] * 8)["pass"])
        one_note = period_match([[(0, 60)]] * 8)
        self.assertTrue(one_note["pass"])
        self.assertEqual(one_note["period"], 1)
        a = [[(0, root), (2, root+4)] for root in (60, 62, 65, 67)] * 2
        b = [[(0, root), (2, root+4)] for root in (61, 63, 66, 68)] * 2
        self.assertFalse(period_match(a + b)["pass"])


if __name__ == "__main__":
    unittest.main()
