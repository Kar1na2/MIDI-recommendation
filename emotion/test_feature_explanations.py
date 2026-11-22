import json
import tempfile
import unittest
from pathlib import Path

from feature_explanations import key_features, read_stage


class TestFeatureSources(unittest.TestCase):
    def test_missing_or_failed_stage_is_unavailable(self):
        scratch = Path(__file__).resolve().parent/'scratch'
        with tempfile.TemporaryDirectory(dir=scratch) as temp:
            path = Path(temp)/'stage.json'
            self.assertIsNone(read_stage(path))
            path.write_text(json.dumps({'status': 'failed', 'data': {'notes': []}}))
            self.assertIsNone(read_stage(path))
            path.write_text(json.dumps({'status': 'complete', 'data': {'notes': []}}))
            self.assertEqual(read_stage(path), {'notes': []})

    def test_key_estimate_needs_pitch_information(self):
        self.assertEqual(key_features([]), (None, None))
        strength, major = key_features([{'pitch': p} for p in (60, 64, 67, 72, 76, 79)])
        self.assertTrue(-1 <= strength <= 1)
        self.assertIn(major, (0., 1.))


if __name__ == '__main__':
    unittest.main()
