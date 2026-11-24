import unittest

from aggregate_scores import aggregate_section, index_sections, summarize_frames


class TestAggregation(unittest.TestCase):
    def test_missing_frames_do_not_become_zero(self):
        values = summarize_frames([{'x': 1}, {'x': None}, {'x': 3}], 'x')
        self.assertEqual(values['mean'], 2)
        self.assertEqual(values['median'], 2)
        self.assertEqual(values['std'], 1)
        self.assertEqual(values['frame_share'], 2/3)
        self.assertIsNone(summarize_frames([], 'x')['frame_share'])

    def test_identity_and_duplicate_sections_are_checked(self):
        payload = {'track_id': 'a', 'cohort': 'random', 'plan_commit': 'abc',
                   'sections': [{'section_id': 's'}, {'section_id': 's'}]}
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            index_sections(payload, 'a', 'random', 'abc')
        with self.assertRaisesRegex(ValueError, 'identity'):
            index_sections(payload, 'b', 'random', 'abc')

    def test_join_uses_frame_starts_and_carries_cohort(self):
        public = {'track_id': 'a', 'cohort': 'pilot', 'language': 'ko',
                  'section_id': 's', 'text_sha256': 'hash'}
        text = {'text_sha256': 'hash', 'T1': 0.2, 'lines': [{'line_no': 1, 'text_sha256': 'h', 'text': 'must not leak'}]}
        audio = {'m1': {'status': 'ok', 'frames': [{'start_s': 0, 'end_s': 4, 'M1_valence': 1}]},
                 'm2': {'status': 'ok', 'frames': [{'start_s': 0, 'end_s': 4.00001, 'M2_valence': 2}]},
                 'voice': None}
        section, curves = aggregate_section(public, text, audio)
        self.assertEqual(len(curves), 1)
        self.assertEqual(curves[0]['cohort'], 'pilot')
        self.assertEqual(curves[0]['M1_valence'], 1)
        self.assertEqual(curves[0]['M2_valence'], 2)
        self.assertEqual(section['audio_models']['voice']['status'], 'cache_unavailable')
        self.assertNotIn('text', section['line_scores'][0])

    def test_rejects_text_hash_mismatch_and_duplicate_frames(self):
        public = {'track_id': 'a', 'cohort': 'random', 'section_id': 's', 'text_sha256': 'right'}
        with self.assertRaisesRegex(ValueError, 'hash'):
            aggregate_section(public, {'text_sha256': 'wrong'}, {})
        repeated = {'m1': {'frames': [{'start_s': 0, 'end_s': 1}, {'start_s': 0, 'end_s': 1}]}}
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            aggregate_section(public, None, repeated)

    def test_empty_zero_length_rounding_artifact_is_recorded_and_excluded(self):
        public = {'track_id': 'a', 'cohort': 'random', 'section_id': 's', 'text_sha256': None}
        audio = {'m1': {'status': 'ok', 'frames': [
            {'start_s': 1, 'end_s': 2, 'instrumental_coverage': 1, 'M1_valence': 2},
            {'start_s': 2, 'end_s': 2, 'instrumental_coverage': 0, 'M1_valence': None}]}}
        section, frames = aggregate_section(public, None, audio)
        self.assertEqual(len(frames), 1)
        self.assertEqual(section['audio_models']['m1']['excluded_zero_length_frames'], 1)
        self.assertEqual(section['audio_models']['m1']['scores']['M1_valence']['frame_share'], 1)
        audio['m1']['frames'][1]['M1_valence'] = 3
        with self.assertRaisesRegex(ValueError, 'Nonempty frame'):
            aggregate_section(public, None, audio)


if __name__ == '__main__':
    unittest.main()
