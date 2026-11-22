import json
import tempfile
import unittest
from pathlib import Path

from prepare_inputs import prepare, timed_text


class TestPreparation(unittest.TestCase):
    def test_onsets_use_half_open_interval(self):
        rows = [{'start_s': 1., 'text': 'first'}, {'start_s': 2., 'text': 'next'}]
        self.assertEqual(timed_text(rows, 1., 2.), ['first'])

    def test_text_remains_private_and_missing_times_are_retained(self):
        scratch = Path(__file__).resolve().parent / 'scratch'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temp:
            folder = Path(temp)
            (folder/'lyrics.json').write_text(json.dumps({'data': {'sections': [
                {'label': 'Verse', 'raw_start_s': 1, 'raw_end_s': 2, 'validated': True, 'timing_source': 'lrclib'},
                {'label': 'Chorus', 'raw_start_s': None, 'raw_end_s': None, 'validated': False, 'timing_source': 'unknown'}]}}))
            (folder/'lrclib.json').write_text(json.dumps({'syncedLyrics': '[00:01.00]synthetic private text\n[00:02.00]other'}))
            private, public = prepare({'track_id': 'unit', 'title': 'fake', 'cohort': 'random'}, 'en', folder)
            self.assertEqual(private[0]['text'], 'synthetic private text')
            self.assertNotIn('synthetic private text', json.dumps(public))
            self.assertEqual(public[0]['line_count'], 1)
            self.assertEqual(public[0]['set'], 'primary')
            self.assertEqual(public[1]['set'], 'secondary')
            self.assertIsNone(public[1]['start_s'])
            self.assertIsNone(public[1]['text_sha256'])

    def test_unrelated_cached_source_is_not_used(self):
        scratch = Path(__file__).resolve().parent / 'scratch'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temp:
            folder = Path(temp)
            (folder/'lyrics.json').write_text(json.dumps({'data': {'source_url': 'correct', 'sections': [
                {'raw_start_s': 0, 'raw_end_s': 1, 'validated': True, 'timing_source': 'manual', 'genius_section_index': 0}]}}))
            (folder/'genius.json').write_text(json.dumps({'source_url': 'wrong', 'sections': [{'lines': ['not the source']}]}))
            private, public = prepare({'track_id': 'unit', 'title': 'fake', 'cohort': 'pilot'}, '', folder)
            self.assertEqual(private[0]['lines'], [])
            self.assertEqual(public[0]['text_source'], 'text_provenance_unavailable')


if __name__ == '__main__':
    unittest.main()
