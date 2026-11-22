import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from . import controls
from .audit import normalize, title_core, verify, save

class ControlsTests(unittest.TestCase):
    def test_allowlist_rejects_protected_and_pages(self):
        for name in ('docs/index.html','frontend/pages-release/index.html','emotion/REPORT.md',
                     'emotion_r2/REPORT.md','emotion_r2/validation/normalization.json','mvp_325/summary.csv'):
            self.assertFalse(controls.allowed(name))
        self.assertTrue(controls.allowed('emotion_r2/replication/frozen_manifest.json'))

    def test_no_analysis_before_freeze(self):
        with tempfile.TemporaryDirectory() as d, patch.object(controls,'DEST',Path(d)):
            with self.assertRaisesRegex(RuntimeError,'No peeking'):controls.frozen()

    def test_uncommitted_freeze_rejected(self):
        with tempfile.TemporaryDirectory() as d, patch.object(controls,'DEST',Path(d)):
            save(Path(d)/'frozen_manifest.json',{'files':{}})
            with patch.object(controls,'git',return_value=b'different'):
                with self.assertRaisesRegex(RuntimeError,'committed'):controls.frozen()

    def test_snapshot_mismatch_is_not_rebased(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'source').write_text('actual')
            save(p/'snapshot.json',{'files':{'source':{'size':6,'sha256':'wrong'}}})
            self.assertFalse(verify(p/'snapshot.json',p)['passed'])
            self.assertEqual((p/'source').read_text(),'actual')

    def test_duplicate_metadata_normalization(self):
        self.assertEqual(title_core('MONTAGEM YUESHI - Slowed'),title_core('MONTAGEM YUESHI'))
        self.assertEqual(normalize('Ethan Bortnick'),normalize('ETHAN BORTNICK'))

    def test_commit_rejects_preexisting_foreign_staging(self):
        with patch.object(controls,'git',return_value=b'docs/index.html\0') as call:
            with self.assertRaisesRegex(RuntimeError,'Foreign'):controls.commit(['emotion_r2/REPLICATION_NOTES.md'],'test')
            self.assertEqual(call.call_count,1)

if __name__=='__main__':unittest.main()
