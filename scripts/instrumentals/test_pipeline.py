"""Guardrail checks for source identity and unforced hierarchical patterns."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from scripts.instrumentals.analyze import detect_cuts, make_view, window_similarity
from scripts.instrumentals.export_site import index_original_audio
from scripts.instrumentals.ingest import evaluate


class SourceMatchingTests(unittest.TestCase):
    song = {"title": "Night Train", "artist": "Milena", "duration_ms": 196000}

    def entry(self, title="Milena - Night Train (Instrumental)", duration=196):
        return {"title": title, "uploader": "Milena", "duration": duration}

    def test_accepts_identity_matched_instrumental(self):
        self.assertIsNotNone(evaluate(self.song, self.entry()))

    def test_rejects_vocal_mix_wrong_title_and_cover(self):
        for title in ["Milena - Night Train Official Audio", "Milena - Day Train Instrumental",
                      "Milena - Night Train Piano Instrumental Cover"]:
            with self.subTest(title=title):
                self.assertIsNone(evaluate(self.song, self.entry(title)))

    def test_rejects_wrong_duration(self):
        self.assertIsNone(evaluate(self.song, self.entry(duration=600)))

    def test_cover_marker_uses_word_boundaries(self):
        song = {"title": "Discovery", "artist": "Milena", "duration_ms": 196000}
        self.assertIsNotNone(evaluate(song, self.entry("Milena - Discovery Instrumental")))

    def test_original_mp3_lookup_uses_spotify_id_in_filename(self):
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            expected = folder / "Milena - Night Train [7FT7a5DV7Mk6Mc9ZRJnX70].mp3"
            expected.touch()
            (folder / "unmatched-name.mp3").touch()
            indexed = index_original_audio(folder)
            self.assertEqual(indexed["7FT7a5DV7Mk6Mc9ZRJnX70"], expected)
            self.assertEqual(len(indexed), 1)


class PatternGuardrailTests(unittest.TestCase):
    def test_constant_audio_features_do_not_create_boundaries(self):
        features = np.zeros((120, 6), dtype=np.float32)
        energy = {stem: np.ones(120) for stem in ("bass", "drums", "other")}
        self.assertEqual(detect_cuts(features, 0, 120, 9, 16), [])
        view = make_view("instrumental", 120, features, energy, 3)
        self.assertEqual([layer["new_boundaries"] for layer in view["layers"]], [0, 0, 0])
        self.assertTrue(all(len(layer["segments"]) == 1 for layer in view["layers"]))

    def test_one_real_transition_does_not_force_more_inner_splits(self):
        features = np.zeros((120, 6), dtype=np.float32)
        features[60:] = 3
        energy = {stem: np.ones(120) for stem in ("bass", "drums", "other")}
        view = make_view("instrumental", 120, features, energy, 3)
        counts = [layer["new_boundaries"] for layer in view["layers"]]
        self.assertEqual(counts[0], 1)
        self.assertEqual(counts[1:], [0, 0])
        for layer in view["layers"]:
            segments = layer["segments"]
            self.assertEqual(segments[0]["start"], 0)
            self.assertEqual(segments[-1]["end"], 120)
            self.assertTrue(all(a["end"] == b["start"] for a, b in zip(segments, segments[1:])))
            self.assertTrue(all(segment["end"] > segment["start"] for segment in segments))

    def test_short_intro_change_can_be_detected(self):
        features = np.zeros((120, 6), dtype=np.float32)
        features[5:] = 3
        cuts = detect_cuts(features, 0, 120, context=9, minimum_gap=12, sensitivity=3.4)
        self.assertTrue(cuts)
        self.assertLessEqual(abs(cuts[0][0] - 5), 2)

    def test_short_intro_and_following_arrangement_change_can_both_survive(self):
        features = np.zeros((120, 6), dtype=np.float32)
        features[5:18] = 3
        features[18:] = -3
        cuts = detect_cuts(features, 0, 120, context=9, minimum_gap=12, sensitivity=3.4)
        positions = [position for position, _ in cuts]
        self.assertTrue(any(abs(position - 5) <= 2 for position in positions))
        self.assertTrue(any(abs(position - 18) <= 2 for position in positions))

    def test_busy_audio_cannot_create_unbounded_rings(self):
        features = np.zeros((180, 6), dtype=np.float32)
        for boundary in range(10, 180, 10):
            features[boundary:] += 1
        energy = {stem: np.ones(180) for stem in ("bass", "drums", "other")}
        view = make_view("instrumental", 180, features, energy, 3)
        counts = [layer["new_boundaries"] for layer in view["layers"]]
        self.assertTrue(all(actual <= limit for actual, limit in zip(counts, [3, 3, 4])))
        windows = [len(layer["segments"]) for layer in view["layers"]]
        self.assertTrue(all(actual <= limit for actual, limit in zip(windows, [4, 7, 11])))

    def test_flat_windows_are_not_called_repeated_patterns(self):
        self.assertEqual(window_similarity(np.ones((64, 6)), 64), [])

    def test_identical_moving_sequences_can_recur_without_overlap(self):
        shape = np.sin(np.arange(8)[:, None] + np.arange(6)[None, :])
        features = np.tile(shape, (6, 1))
        matches = window_similarity(features, len(features))
        self.assertTrue(matches)
        self.assertTrue(all(item["repeat_start"] >= item["end"] for item in matches))

    def test_six_second_phrase_recurrence_is_found(self):
        phrase = np.sin(np.arange(6)[:, None] + np.arange(5)[None, :])
        features = np.random.default_rng(4).normal(size=(40, 5))
        features[2:8] = phrase
        features[19:25] = phrase
        matches = window_similarity(features, len(features))
        self.assertTrue(any(item["start"] == 2 and item["repeat_start"] == 19 for item in matches))


if __name__ == "__main__":
    unittest.main()
