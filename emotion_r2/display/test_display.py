"""Display-export checks only; registered statistical tests are untouched."""
import unittest
from export_readings import overlapping

class OverlapScanTests(unittest.TestCase):
    def test_four_words_are_blocked_case_and_punctuation_insensitive(self):
        self.assertTrue(overlapping('A description: ONE, two three FOUR.', 'one two three four five')['four_word_overlap'])

    def test_three_words_do_not_reach_the_four_word_rule(self):
        self.assertFalse(any(overlapping('one two three', 'one two three four').values()))

    def test_unrelated_description(self):
        self.assertFalse(any(overlapping('The tone sounds defiant and playful.', 'red green orange blue').values()))

    def test_japanese_chinese_and_korean_character_runs(self):
        for source,reply in [('あいうえお','あい、うえ'),('天地玄黄宇宙','天地玄黄'),('가나다라마','가 나 다 라')]:
            self.assertTrue(overlapping(reply,source)['four_cjk_character_overlap'])

    def test_less_than_four_cjk_characters(self):
        self.assertFalse(any(overlapping('天地玄','天地玄黄').values()))

    def test_nfkc_equivalence(self):
        self.assertTrue(overlapping('Ａ Ｂ Ｃ Ｄ', 'a b c d')['four_word_overlap'])

if __name__=='__main__':unittest.main()
