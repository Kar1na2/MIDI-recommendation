import unittest
from score_text_model import strict_reply

class LocalLLMReplyTests(unittest.TestCase):
    def test_valid_response(self):
        self.assertEqual(strict_reply('{"valence":0.25,"arousal":-0.3,"uncertain":false}'),{'valence':.25,'arousal':-.3,'uncertain':False})
    def test_reject_missing_flag_and_invalid_values(self):
        self.assertIsNone(strict_reply('{"valence":0,"arousal":0}'))
        self.assertIsNone(strict_reply('{"valence":2,"arousal":0,"uncertain":false}'))
        self.assertIsNone(strict_reply('{"valence":0,"arousal":0,"uncertain":"false"}'))
        self.assertIsNone(strict_reply('prefix {"valence":0,"arousal":0,"uncertain":false}'))
if __name__=='__main__': unittest.main()
