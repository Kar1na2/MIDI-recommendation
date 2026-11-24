import unittest
from label_cached import assign_genius

class LabelAlignmentTest(unittest.TestCase):
    def test_matching_lines_give_genius_labels(self):
        source=[{'label':'Verse','display_label':'Verse 1','number':1,'performers':[],
                 'genius_section_index':0,'lines':['first line','second line']},
                {'label':'Chorus','display_label':'Chorus 1','number':1,'performers':[],
                 'genius_section_index':1,'lines':['third line','fourth line']}]
        timed=[{'text':t,'start_s':i*2} for i,t in enumerate(['first line','second line','third line','fourth line'])]
        sections=[{'section_id':'a','start_s':0,'end_s':4},{'section_id':'b','start_s':4,'end_s':8}]
        result=assign_genius(sections,timed,source,8)
        self.assertEqual(result['a']['label'],'Verse'); self.assertEqual(result['b']['label'],'Chorus')

    def test_unmatched_sections_are_not_labeled(self):
        source=[{'label':'Verse','display_label':'Verse 1','number':1,'performers':[],
                 'genius_section_index':0,'lines':['unrelated words']}]
        timed=[{'text':'completely different','start_s':1}]
        self.assertEqual(assign_genius([{'section_id':'a','start_s':0,'end_s':4}],timed,source,4),{})

if __name__=='__main__': unittest.main()
