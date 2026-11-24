import unittest
from unittest.mock import patch

import numpy as np

from pipeline import onion


class OnionTests(unittest.TestCase):
    def test_major_sections_detect_texture_change_and_respect_minimum_length(self):
        vectors = np.array([[0.0, 0.0]] * 8 + [[1.0, 1.0]] * 8)
        ranges, _ = onion.major_sections(vectors)
        self.assertEqual(ranges, [(0, 8), (8, 16)])
        self.assertTrue(all(end - start >= 4 for start, end in ranges))

    def test_instrument_entries_nest_inside_major_sections_without_pattern_ids(self):
        bars = [
            {"bar": i + 1, "start_s": float(i), "end_s": float(i + 1), "beats_per_bar": 4}
            for i in range(16)
        ]
        grid = {"bars": bars, "duration_s": 16.0, "downbeat_times": [float(i) for i in range(16)]}
        records = [
            {"bar": i + 1, "start_s": float(i), "end_s": float(i + 1),
             "layers": {}, "active_instruments": ["piano"] if i < 2 else ["piano", "drums"],
             "median_pitch": 60.0, "rms": 0.1, "rms_db": -20.0,
             "line_note_density": 0., "mean_note_duration_s": 0., "chord_coverage": 0.,
             "chord_onset_rate": 0., "line_instrument_counts": {}}
            for i in range(16)
        ]
        vectors = np.r_[np.zeros((8, 6)), np.ones((8, 6))]
        with patch("pipeline.texture.features", return_value=(vectors, records)), \
             patch("pipeline.texture.novelty_sections", return_value=([(0, 8), (8, 16)], {}, {})):
            data = onion.build("test", "unused.wav", grid, [], instrumental=True)

        outer = data["layers"][1]["segments"]
        inner = data["layers"][2]["segments"]
        self.assertEqual(len(outer), 2)
        self.assertTrue(data["meta"]["pattern_clusters_used_by_rings_2_3"] is False)
        for segment in inner:
            parent = outer[segment["parent_index"]]
            self.assertGreaterEqual(segment["start"], parent["start"])
            self.assertLessEqual(segment["end"], parent["end"])

    def test_unverified_vocal_ring_is_not_called_instrumental(self):
        segments, source, reason = onion.lyric_ring(10, {"downbeat_times": []}, None, False,
                                                      "Genius page unavailable")
        self.assertEqual(source, "unavailable")
        self.assertEqual(segments[0]["label"], "Lyric sections unavailable")
        self.assertEqual(reason, "Genius page unavailable")


if __name__ == "__main__":
    unittest.main()
