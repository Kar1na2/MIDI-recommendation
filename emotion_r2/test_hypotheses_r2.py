import unittest
from hypotheses_r2 import h3


class HypothesesTests(unittest.TestCase):
    def test_h3_counts_only_strong_audio_agreement(self):
        def row(voice, background, words):
            return {'consensus': {'voice_valence': voice, 'background_valence': background, 'words_valence': words}}
        result = h3([row(.7,.8,-.9), row(-.6,-.8,-.9), row(.8,-.8,-.9), row(None,.8,-.9)])
        self.assertEqual(result['eligible_sections'], 2)
        self.assertEqual(result['opposite_words'], 1)
        self.assertEqual(result['share'], .5)


if __name__ == '__main__':
    unittest.main()
