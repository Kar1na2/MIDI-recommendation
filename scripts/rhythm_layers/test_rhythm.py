"""Focused checks for timing contracts and loudness-independent pattern cuts."""
import tempfile
import json
from pathlib import Path
import unittest

import numpy as np
import soundfile as sf

from .align_vocals import split_timed_sections
from .build_preview import assert_no_lyrics
from .patterns import SR, extract, grid, make_layers, cycle_summary, map_time


class RhythmChecks(unittest.TestCase):
    def test_original_heading_splits_win_over_proportional_grouping(self):
        sources = [{"name": "Verse", "text": "la la"}, {"name": "Chorus", "text": "do"}]
        raw = [{"text": "la la", "words": [{"word": "la", "start": 2., "end": 3.}, {"word": "la", "start": 4., "end": 5.}]},
               {"text": "do", "words": [{"word": "do", "start": 10., "end": 11.}]}]
        words = [{"text": w["word"], "start": w["start"], "end": w["end"], "confidence": None} for s in raw for w in s["words"]]
        result = split_timed_sections(sources, words, raw)
        self.assertEqual([(s["label"], s["start"], s["end"]) for s in result], [("Verse", 2., 5.), ("Chorus", 10., 11.)])
        self.assertTrue(all(s["section_grouping"] == "preserved Genius heading split" for s in result))

    def test_drum_recurrence_does_not_depend_on_pitch_classes(self):
        times = np.arange(256) / 8.
        rhythm = np.tile([1., 0., 0., 0., 1., 0., 1., 0.], 32)
        first = {"rhythm": rhythm, "harmonic": np.tile(np.eye(12)[0], (256, 1)), "shape": np.ones((256, 24)) / np.sqrt(24)}
        second = {**first, "harmonic": np.eye(12)[np.arange(256) % 12]}
        a, b = [cycle_summary(times, {"drums": x}, 0, times[-1]) for x in (first, second)]
        self.assertEqual(a["beats"], b["beats"])
        self.assertEqual(a["rhythm_similarity"], b["rhythm_similarity"])
        self.assertIsNone(a["harmony_similarity"])
        json.dumps(a)

    def test_constant_gain_and_slow_crescendo_do_not_add_broad_sections(self):
        seconds = 24.
        y = np.zeros(int(seconds * SR), dtype="float32")
        for note, at in enumerate(np.arange(.2, seconds - .2, .25)):
            t = np.arange(int(.12 * SR)) / SR
            tone = np.sin(2 * np.pi * [220., 277.18, 329.63, 440.][note % 4] * t) * np.exp(-t * 28)
            start = int(at * SR); y[start:start + len(tone)] += tone
        variants = [y, y * .17, y * np.linspace(.1, 1., len(y))]
        counts, onsets = [], []
        with tempfile.TemporaryDirectory() as folder:
            for index, variant in enumerate(variants):
                path = Path(folder) / f"gain-{index}.wav"; sf.write(path, variant, SR, subtype="FLOAT")
                features = extract(path, cache=False)
                times, data = grid({"other": features}, np.arange(0, seconds + .5, .5), seconds)
                counts.append(len(make_layers(times, data, seconds)[0]["segments"]))
                onsets.append(features["onsets"])
        self.assertEqual(counts[0], counts[1])
        self.assertLessEqual(counts[2], counts[0] + 1)
        self.assertLess(abs(len(onsets[2]) - len(onsets[0])), max(3, int(.1 * len(onsets[0]))))

    def test_unmatched_clocks_cannot_silently_map_audio(self):
        self.assertIsNone(map_time({"status": "low_confidence", "confidence": .4}, 5.))
        self.assertEqual(map_time({"status": "matched", "confidence": .9, "instrumental_time": [0., 5., 10.], "original_time": [0., 6., 12.]}, 5.), 6.)

    def test_private_transcripts_cannot_be_exported(self):
        with self.assertRaises(ValueError):
            assert_no_lyrics({"track": {"timed_words": []}})
        with self.assertRaises(ValueError):
            assert_no_lyrics({"raw_alignment": {"text": "synthetic test"}})


if __name__ == "__main__":
    unittest.main()
