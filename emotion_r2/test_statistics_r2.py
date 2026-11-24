import unittest
from statistics_r2 import chorus_lift, trust


class StatisticsTests(unittest.TestCase):
    def test_chorus_lift_requires_both_categories_and_keeps_zero(self):
        rows = [dict(track_id='a', genius_category=label, consensus={'bg': score})
                for label, score in [('verse', 1), ('chorus', 2), ('pre-chorus', 99)]]
        rows += [dict(track_id='b', genius_category='verse', consensus={'bg': 8})]
        result = chorus_lift(rows, 'bg', iterations=100)
        self.assertEqual(result['n'], 1)
        self.assertEqual(result['median'], 1)

    def test_missing_pair_cannot_make_words_trusted(self):
        rows = [dict(track_id=str(i), model_z={'T1': None, 'T3': i, 'L1': i},
                     consensus={'words': i}, genius_label='verse') for i in range(5)]
        result = trust(rows, ['T1', 'T3', 'L1'], 'words', iterations=10, words=True)
        self.assertIsNone(result['median_rho'])
        self.assertFalse(result['trusted'])


if __name__ == '__main__':
    unittest.main()
