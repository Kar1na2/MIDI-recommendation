import unittest
from language_weighted import choose_vote

class WeightedLanguageTests(unittest.TestCase):
    def test_confidence_and_allowed_codes(self):
        self.assertEqual(choose_vote({'en':.95},1,0)[0],'en')
        self.assertEqual(choose_vote({'en':.4},1,0)[0],'uncertain')
        self.assertEqual(choose_vote({'cy':.99},1,0)[0],'uncertain')
        self.assertEqual(choose_vote({'en':.9},1,1)[0],'uncertain')
    def test_vote_share_and_instrumental(self):
        self.assertEqual(choose_vote({'en':.95,'ko':.9},2,0)[0],'uncertain')
        self.assertEqual(choose_vote({},0,0,True)[0],'not_applicable')
if __name__=='__main__': unittest.main()
