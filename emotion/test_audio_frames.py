import unittest

import numpy as np
from audio_frames import vocal_active, windows


class TestAudioFrames(unittest.TestCase):
    def test_tail_is_padded_without_crossing_section(self):
        signal = np.ones(100, dtype=np.float32)
        rows = list(windows(1., 3.5, signal, 10))
        self.assertEqual([r['start_s'] for r in rows], [1., 2., 3.])
        self.assertEqual([r['actual_seconds'] for r in rows], [2.5, 1.5, 0.5])
        self.assertTrue(np.all(rows[0]['samples'][:25] == 1))
        self.assertTrue(np.all(rows[0]['samples'][25:] == 0))

    def test_invalid_span_has_no_frames(self):
        self.assertEqual(list(windows(2., 2., np.ones(100), 10)), [])

    def test_activity_threshold_and_silence(self):
        silent = np.zeros(400, dtype=np.float32)
        self.assertFalse(vocal_active(silent, 100))
        silent[:80] = 0.02
        self.assertTrue(vocal_active(silent, 100))


if __name__ == '__main__':
    unittest.main()
