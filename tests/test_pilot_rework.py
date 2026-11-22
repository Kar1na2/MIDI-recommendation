import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from pipeline import genius_matching, texture, onion
from pipeline.lyric_validation import diagnose_sections, per_section_ring, vocal_activity


class GeniusMatchingTests(unittest.TestCase):
    def test_parenthetical_english_title_is_an_exact_variant(self):
        song = {"artist": "JANNABI", "title": "for lovers who hesitate"}
        row = genius_matching.score_candidate(song, {"id": 1,
            "title": "주저하는 연인들을 위해 (for lovers who hesitate)",
            "primary_artist": {"name": "JANNABI"}, "url": "https://genius.com/test"})
        self.assertTrue(row["eligible"])
        self.assertEqual(row["title_similarity"], 1.0)

    def test_title_match_never_overrides_wrong_artist(self):
        row = genius_matching.score_candidate({"artist": "Heize", "title": "Jenga"},
            {"title": "Jenga", "primary_artist": {"name": "Unrelated"}})
        self.assertFalse(row["eligible"])
        self.assertEqual(row["reason"], "artist_mismatch")


class SectionValidationTests(unittest.TestCase):
    def test_missing_next_section_preserves_measured_end_and_other_spans_unaligned(self):
        source = [{"label": "Verse", "lines": ["one"]}, {"label": "Chorus", "lines": ["two"]}]
        lines = [{"text": "one", "start_s": 1., "end_s": 2., "confidence": .9},
                 {"text": "two", "start_s": None, "end_s": None, "confidence": 0.}]
        sections = diagnose_sections(source, lines, 5.)
        self.assertTrue(sections[0]["validated"])
        self.assertEqual(sections[0]["raw_end_s"], 2.)
        self.assertFalse(sections[1]["validated"])
        ring = per_section_ring(sections, 5., "unused")
        self.assertEqual([row["label"] for row in ring["ring1_segments"]], ["unaligned", "Verse 1", "unaligned"])

    def test_prepared_ring_keeps_raw_times_instead_of_collapsing_on_grid(self):
        segments = [{"start": .1, "end": .2, "label": "Chorus 1"}]
        ring, source, _ = onion.lyric_ring(2., {"downbeat_times": [0., 2.]},
            {"ring1_segments": segments, "source": "genius_per_section"}, False)
        self.assertEqual(ring, segments)
        self.assertEqual(source, "genius_per_section")

    def test_vocal_activity_has_two_second_minimum(self):
        samples = np.r_[np.zeros(200), np.ones(300), np.zeros(100), np.ones(400)]
        with patch("soundfile.read", return_value=(samples[:, None], 100)):
            result = vocal_activity("unused", 10.)
        self.assertTrue(all(row["end"]-row["start"] >= 2 for row in result["segments"]))
        self.assertEqual(result["segments"][0]["start"], 0.)
        self.assertEqual(result["segments"][-1]["end"], 10.)


class TextureReworkTests(unittest.TestCase):
    def test_instrument_identity_does_not_enter_ring2_vector(self):
        units = [{"unit": 0, "start_s": 0., "end_s": 2.}]
        note = {"onset_s": .2, "offset_s": 1., "pitch": 60, "layer": "line", "instrument": "acoustic_piano"}
        with patch("soundfile.read", return_value=(np.zeros((200, 1)), 100)):
            left, _ = texture.features([note], units, "unused")
            right, _ = texture.features([{**note, "instrument": "flute"}], units, "unused")
        np.testing.assert_equal(left, right)
        self.assertEqual(left.shape[1], 6)

    def test_minimum_eight_units_and_time_fallback(self):
        vectors = np.r_[np.zeros((8, 6)), np.ones((8, 6))]
        sections, _, _ = texture.novelty_sections(vectors, 8)
        self.assertEqual(sections, [(0, 8), (8, 16)])
        units, basis, minimum = texture.analysis_units({"duration_s": 40., "bars": []}, ["possible_half_tempo"])
        self.assertEqual((basis, minimum, len(units)), ("time", 8, 20))


if __name__ == "__main__":
    unittest.main()
