"""Regression checks against the unchanged 253-pattern ATTITUDE reference."""
import json
import unittest
from pathlib import Path

from pipeline.patterns import encode_bar, expand_onset_notation, format_pattern_tokens


class PatternNotationTests(unittest.TestCase):
    def test_phase_and_full_length_are_preserved(self):
        for pattern in (".x..............", "x...x...x...x...", ".x...x...x...x..", "." * 16):
            notation = format_pattern_tokens(["x" if step == "x" else "~" for step in pattern])
            self.assertEqual(expand_onset_notation(notation, len(pattern)), pattern)
        with self.assertRaises(ValueError):
            expand_onset_notation("x", 16)

    def test_all_253_reference_patterns_round_trip(self):
        root = Path(__file__).resolve().parents[1]
        path = root / "outputs" / "aespa-attitude" / "patterns.json"
        patterns = json.loads(path.read_text(encoding="utf-8"))["data"]["patterns"]
        self.assertEqual(len(patterns), 253)
        old_passes = 0
        for row in patterns:
            try:
                old_passes += expand_onset_notation(row["onset_mini_notation"], len(row["onset_pattern"])) == row["onset_pattern"]
            except ValueError:
                pass
        self.assertEqual(old_passes, 40)
        for row in patterns:
            with self.subTest(pattern=row["pattern_id"]):
                steps = len(row["onset_pattern"])
                notes = [{"step": step, "pitch": pitch, "onset_s": float(step)}
                         for step, pitch in row["events"]]
                result = encode_bar(notes, steps, row["instrument"].split("__")[0] == "drums")
                self.assertEqual(result["onset_pattern"], row["onset_pattern"])
                self.assertEqual(expand_onset_notation(result["onset_mini_notation"], steps), row["onset_pattern"])


if __name__ == "__main__":
    unittest.main()
