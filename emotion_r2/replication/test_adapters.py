"""Synthetic checks only: never inspect effects on un-frozen library songs."""
import unittest
from .features import scoped

class AdapterTests(unittest.TestCase):
    def test_unknown_source_replacement_aborts(self):
        from explanations_r2 import fit
        with self.assertRaisesRegex(RuntimeError,'source no longer matches'):
            scoped(fit,[('a nonexistent source fragment','changed')])

    def test_explanation_scope_adapter_preserves_calculation(self):
        from explanations_r2 import fit
        adapted=scoped(fit,[("r['cohort']=='random' and ","")])
        synthetic=[]
        for i in range(60):
            synthetic.append({'track_id':str(i//6),'cohort':'random','set':'primary',
                'consensus':{'background_arousal':i*.03+(i%7)*.02},
                'features':{'instrumental_rms':i*.01,'tempo_bpm':100+i%5,
                            'onset_density_total':1+i%4,'instrumental_brightness_hz':200+i*3}})
        original=fit(synthetic,'background_arousal')
        result=adapted([{**r,'cohort':'replication_library'} for r in synthetic],'background_arousal')
        self.assertEqual(original,result)

    def test_genius_provenance_required(self):
        from prepare_statistics_r2 import source_label
        sec={'section_id':'lyric-0000','label':'Chorus'}
        self.assertEqual(source_label(sec,[{}]),(None,None))
        self.assertEqual(source_label(sec,[{'genius_section_index':0}]),('Chorus','chorus'))

if __name__=='__main__':unittest.main()
