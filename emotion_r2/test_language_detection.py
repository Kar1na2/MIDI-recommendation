import unittest
from language_detection import choose

class LanguageChoiceTest(unittest.TestCase):
    def test_disagreement_is_uncertain(self):
        self.assertEqual(choose('ja',.99,'ko',.45,None)[0],'uncertain')
    def test_known_pilot_kept(self):
        self.assertEqual(choose('en',.99,'ja',.9,'ko')[0],'ko')
    def test_instrumental_not_applicable(self):
        self.assertEqual(choose(None,None,None,None,None,True)[0],'not_applicable')

if __name__=='__main__': unittest.main()
