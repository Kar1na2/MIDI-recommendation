import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import audio_scores_r2


class StemVerificationTest(unittest.TestCase):
    def test_vocal_stem_uses_cohort_folder_and_checks_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for cohort, prefix in [('random', 'outputs'), ('pilot', 'mvp_325/songs')]:
                folder = root / prefix / 'track'
                (folder / 'stems').mkdir(parents=True)
                path = folder / 'stems' / 'vocals.wav'
                path.write_bytes(b'vocal-test-data')
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                (folder / 'separation.json').write_text(json.dumps({
                    'status': 'ok', 'data': {'stem_sha256': {'vocals': digest}}
                }))
                with patch.object(audio_scores_r2, 'ROOT', root):
                    self.assertEqual(audio_scores_r2.verified_stem('track', 'vocals', cohort), path)
                    path.write_bytes(b'changed')
                    with self.assertRaisesRegex(RuntimeError, 'Unverified'):
                        audio_scores_r2.verified_stem('track', 'vocals', cohort)

    def test_vocal_hash_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'outputs' / 'track'
            (folder / 'stems').mkdir(parents=True)
            (folder / 'stems' / 'vocals.wav').write_bytes(b'vocal-test-data')
            (folder / 'separation.json').write_text(json.dumps({'status': 'ok', 'data': {}}))
            with patch.object(audio_scores_r2, 'ROOT', root):
                with self.assertRaisesRegex(RuntimeError, 'Unverified'):
                    audio_scores_r2.verified_stem('track', 'vocals', 'random')


if __name__ == '__main__':
    unittest.main()
