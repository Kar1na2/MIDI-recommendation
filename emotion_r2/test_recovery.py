import unittest
from recover_lyrics import match_plain_to_synced

class MatchTest(unittest.TestCase):
    def test_repeated_lines_are_paired_in_order(self):
        rows=[{'text':'one'},{'text':'two'},{'text':'one'}]
        result=match_plain_to_synced('one\ntwo\none',rows)
        self.assertEqual([(v['source_index'],k) for k,v in result.items()],[(0,0),(1,1),(2,2)])

    def test_unmatched_lines_remain_missing(self):
        result=match_plain_to_synced('alpha', [{'text':'completely different'}])
        self.assertEqual(result,{})

if __name__=='__main__': unittest.main()
