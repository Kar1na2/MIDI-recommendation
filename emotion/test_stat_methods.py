"""Checks for the preregistered emotion statistics on synthetic data."""
import math
import unittest

from stat_methods import correlation, profile, relation, repeat_test, shuffle_control, standardization, z


def test_relation_and_duration_weighted_profile():
    assert relation(0.5, 0.8) == 'supports'
    assert relation(-0.5, 0.8) == 'contradicts'
    assert relation(0.49, -1) == 'neutral/ambiguous'
    assert relation(None, 1) is None
    assert profile([('supports', 6), ('contradicts', 2), ('neutral/ambiguous', 2)])['profile'] == 'congruent'
    assert profile([('supports', 1), ('contradicts', 1)])['profile'] == 'flat'
    assert profile([])['profile'] == 'unavailable'


def test_normalization_and_missing_pairs():
    params = standardization([1, 2, 3, None, float('nan')])
    assert params['n'] == 3
    assert z(2, params) == 0
    assert standardization([1, 1]) is None
    assert z(None, params) is None
    assert correlation([(1, 3), (2, 2), (3, 1), (None, 9)])['rho'] == -1


def test_repeat_control_detects_consistent_repeated_labels():
    rows = []
    for i in range(12):
        for label, score in [('verse', 0), ('verse', 0.01), ('chorus', 2), ('chorus', 2.01)]:
            rows.append({'track_id': str(i), 'label': label, 'score': score})
    result = repeat_test(rows, iterations=999, seed=42)
    assert result['songs'] == 12
    assert result['difference'] > 1
    assert result['p'] < .01
    assert repeat_test([{'track_id': 'x', 'label': 'verse', 'score': 1}])['p'] is None


def test_shuffle_control_requires_a_different_song_in_same_language():
    assert shuffle_control([{'track_id': 'a', 'language': 'en', 'words': 1, 'background': 1}], iterations=10)['n'] == 0
    rows = [{'track_id': 'a', 'language': 'en', 'words': 1, 'background': 1},
            {'track_id': 'b', 'language': 'en', 'words': -1, 'background': -1},
            {'track_id': 'c', 'language': 'ja', 'words': 1, 'background': 1}]
    result = shuffle_control(rows, iterations=100, seed=7)
    assert result['n'] == 2
    assert result['excluded_no_partner'] == 1
    assert result['observed'] == 1
    assert result['null_mean'] == -1
    assert math.isfinite(result['p'])


class TestStatistics(unittest.TestCase):
    def test_relation_and_profile(self):
        test_relation_and_duration_weighted_profile()

    def test_normalization(self):
        test_normalization_and_missing_pairs()

    def test_repeated_sections(self):
        test_repeat_control_detects_consistent_repeated_labels()

    def test_shuffle_control(self):
        test_shuffle_control_requires_a_different_song_in_same_language()
