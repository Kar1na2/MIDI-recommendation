import unittest
from pathlib import Path
from unittest.mock import Mock

from pipeline.batch import SongTimeout, timeout_diagnostic_budget
from pipeline.beats import build_grid
from pipeline.join import run as join_run
from pipeline.library import (_powercfg_timeout_seconds, pace_warning, scope_songs,
                              text_language)

ROOT = Path(__file__).resolve().parents[1]


class LibraryScopeTests(unittest.TestCase):
    def test_powercfg_reads_sleep_and_hibernate_from_their_own_blocks(self):
        output = '''\
Power Setting GUID: x (Sleep after)
Current AC Power Setting Index: 0x00000000
Power Setting GUID: y (Hibernate after)
Current AC Power Setting Index: 0x00000384
'''
        self.assertEqual(_powercfg_timeout_seconds(output, 'Sleep after'), 0)
        self.assertEqual(_powercfg_timeout_seconds(output, 'Hibernate after'), 900)
        self.assertIsNone(_powercfg_timeout_seconds(output, 'Missing setting'))

    def test_library_scope_excludes_both_frozen_cohorts(self):
        songs = [{'id': str(i)} for i in range(8)]
        self.assertEqual([s['id'] for s in scope_songs(songs, {'1', '4'}, {'2', '7'})],
                         ['0', '3', '5', '6'])

    def test_rolling_transcription_warning_uses_twenty_distinct_songs(self):
        songs = [{'id': str(i), 'duration_s': 10.0} for i in range(20)]
        events = [{'song_id': str(i), 'stage': 'transcription',
                   'status': 'complete', 'seconds': 26.0} for i in range(20)]
        warning = pace_warning(events, songs)
        self.assertAlmostEqual(warning['seconds_per_audio_second'], 2.6)
        self.assertEqual(warning['last_song_id'], '19')
        self.assertIsNone(pace_warning([{**event, 'seconds': 25.0} for event in events], songs))

    def test_diagnostic_retry_only_extends_a_library_transcription_timeout(self):
        timeout = SongTimeout('song deadline')
        library = ROOT / 'library'
        self.assertEqual(timeout_diagnostic_budget(timeout, 'transcription', library), 2700)
        self.assertEqual(timeout_diagnostic_budget(timeout, 'beats', library), 0)
        self.assertEqual(timeout_diagnostic_budget(timeout, 'transcription', ROOT / 'batch'), 0)
        self.assertEqual(timeout_diagnostic_budget(timeout, 'transcription', library, 1), 0)

    def test_zero_shuffles_reports_no_library_hypothesis_test(self):
        grid = build_grid(list(range(9)), [0, 4, 8], 8.1)
        lyrics = {'sections': [{'label': 'Verse', 'raw_start_s': 1.0, 'raw_end_s': 7.0}]}
        patterns = {'patterns': [], 'rhythmic_sections': [], 'runs': [], 'bars': []}
        result = join_run(lyrics, patterns, grid, [], baseline_shuffles=0)
        baseline = result['boundary_agreement_baseline']
        self.assertEqual(baseline['shuffles'], 0)
        self.assertEqual(baseline['shuffle_distribution'], [])
        self.assertIsNone(baseline['one_sided_empirical_p'])

    def test_library_language_id_keeps_task_a_script_override(self):
        identifier = Mock()
        identifier.classify.return_value = ('en', .98)
        identifier.rank.return_value = [('en', .98), ('ko', .02)]
        language, confidence, evidence = text_language('안녕하세요 한국어', identifier)
        self.assertEqual(language, 'ko')
        self.assertEqual(confidence, .02)
        self.assertEqual(evidence['confidence_definition_version'], 2)


if __name__ == '__main__':
    unittest.main()
