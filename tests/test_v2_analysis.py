import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from pipeline import core, profiles, v2_stats
from pipeline.common import read, save

ROOT = Path(__file__).resolve().parents[1]


class StatisticalTests(unittest.TestCase):
    def test_circular_shift_keeps_count_and_cyclic_spacing(self):
        own=[1,4,8]
        draws,reason=v2_stats.draw_boundaries(3,12,'circular',1,shuffles=100,own=own)
        self.assertIsNone(reason)
        expected=sorted(np.diff(sorted(own)+[min(own)+12]))
        for row in draws:
            ordered=sorted(row)
            self.assertEqual(sorted(np.diff(ordered+[ordered[0]+12])),expected)

    def test_phrase_null_shortage_is_explicit(self):
        draws,reason=v2_stats.draw_boundaries(3,12,'phrase',1,phrase=[3,7],shuffles=10)
        self.assertIsNone(draws)
        self.assertIn('exceed',reason)

    def test_uniform_boundaries_are_unique_and_seeded(self):
        a,_=v2_stats.draw_boundaries(4,12,'uniform',12,shuffles=20)
        b,_=v2_stats.draw_boundaries(4,12,'uniform',12,shuffles=20)
        np.testing.assert_array_equal(a,b)
        self.assertTrue(all(len(set(r))==4 for r in a))

    def test_no_rhythm_evidence_scores_zero(self):
        self.assertEqual(v2_stats.agreement([2,5],[],1),0.)
        rates=v2_stats.null_rates([2,5],np.empty((10,0),dtype=int),1)
        np.testing.assert_array_equal(rates,np.zeros(10))

    def test_tolerance_and_exact_are_distinct(self):
        self.assertEqual(v2_stats.agreement([2,5],[3,5],1),1.)
        self.assertEqual(v2_stats.agreement([2,5],[3,5],0),.5)

    def test_pooled_null_preserves_ties(self):
        record={'observed':.7,'lift':0.,'p':1.}
        result=v2_stats.pooled([record,record],[np.full(10000,.7),np.full(10000,.7)])
        self.assertEqual(result['pooled_p'],1.)
        self.assertEqual(result['median_lift_ci95'],[0.,0.])

    def test_song_seeds_are_distinct_reproducible(self):
        self.assertEqual(v2_stats.seed_for('a'),v2_stats.seed_for('a'))
        self.assertNotEqual(v2_stats.seed_for('a'),v2_stats.seed_for('b'))

    def test_removing_voice_recomputes_novelty_instead_of_reusing_boundaries(self):
        from pipeline.beats import build_grid
        grid=build_grid(list(range(33)),list(range(0,33,4)),32.1)
        rows=[]
        for bar in range(len(grid['bars'])):
            for inst in ('voice__line','acoustic_guitar__line'):
                label=('a' if bar<4 else 'b') if inst.startswith('voice') else 'constant'
                rows.append({'instrument':inst,'bar':bar,'pattern_type':'regular_bar','active':True,
                    'onset_pattern_id':inst+label,'pitch_pattern_id':inst+label,'pattern_id':inst+label})
        pat={'bars':rows,'irregular_bars':[],'rhythmic_sections':[{'cached':'only'}]}
        self.assertEqual(len(v2_stats.voice_sections(pat,grid,'excluded')),1)
        self.assertEqual(len(v2_stats.voice_sections(pat,grid,'only')),2)
        self.assertEqual(v2_stats.voice_sections(pat,grid,'included'),[{'cached':'only'}])

    def test_absent_voice_does_not_create_voice_boundaries_from_meter(self):
        from pipeline.beats import build_grid
        grid=build_grid(list(range(17)),[0,4,8,12,16],16.1)
        pat={'bars':[],'irregular_bars':[],'rhythmic_sections':[]}
        self.assertEqual(v2_stats.voice_sections(pat,grid,'only'),[])

    def test_only_internal_section_starts_count_as_lyric_boundaries(self):
        from pipeline.beats import build_grid
        grid=build_grid(list(range(17)),[0,4,8,12,16],16.1)
        lyric={'sections':[
            {'label':'Verse','raw_start_s':1.,'raw_end_s':7.,'validated':True},
            {'label':'Chorus','raw_start_s':8.,'raw_end_s':15.,'validated':True},
            {'label':'Instrumental','raw_start_s':12.,'raw_end_s':15.,'validated':True}]}
        _,lyrical,rhythmic=v2_stats.boundary_indices(grid,lyric,[{'start_s':0.},{'start_s':8.}])
        self.assertEqual(lyrical,[1]);self.assertEqual(rhythmic,[1])


class ProfileTests(unittest.TestCase):
    def test_script_override_reports_probability_of_selected_language(self):
        from analysis_v2.language import text_language
        from unittest.mock import Mock
        identifier=Mock()
        identifier.classify.return_value=('en',.98)
        identifier.rank.return_value=[('en',.98),('ko',.02)]
        language,confidence,evidence=text_language('안녕하세요 한국어',identifier)
        self.assertEqual(language,'ko');self.assertEqual(confidence,.02)
        self.assertEqual(evidence['langid_winning_probability'],.98)

    def test_default_v1_calls_original_without_changed_arguments(self):
        with patch.object(core,'recompute',return_value={'status':'ok'}) as run:
            self.assertEqual(profiles.recompute({'id':'x'}),{'status':'ok'})
            run.assert_called_once_with({'id':'x'})

    def test_explicit_source_root_is_used_for_isolated_v1(self):
        with tempfile.TemporaryDirectory(prefix='profile_test_') as tmp:
            root=Path(tmp); source=root/'source'/'x'; source.mkdir(parents=True)
            save(source/'transcription.json',{'data':{'notes':['isolated']}})
            with patch.object(core,'recompute',return_value={'status':'ok'}) as run:
                profiles.recompute({'id':'x'},'v1',root/'destination',source_root=root/'source')
            self.assertEqual(read(root/'destination'/'x'/'transcription.json')['data']['notes'],['isolated'])
            run.assert_called_once_with({'id':'x'},root/'destination',refresh_beats=False)

    def test_output_guard_rejects_protected_roots(self):
        for root in ('outputs','mvp','pilot','docs','outputs/x'):
            with self.assertRaises(Exception):
                profiles.output_folder(ROOT/root,'song')

    def test_resnap_preserves_raw_times_and_validation(self):
        from pipeline.beats import build_grid
        grid=build_grid(list(range(13)),[0,4,8,12],12.1)
        lyric={'sections':[{'raw_start_s':3.2,'raw_end_s':7.3,'validated':False,'flags':['rejected']}]}
        before=copy.deepcopy(lyric)
        result=profiles.resnap(lyric,grid)
        self.assertEqual(lyric,before)
        self.assertFalse(result['sections'][0]['validated'])
        self.assertEqual(result['sections'][0]['raw_start_s'],3.2)
        self.assertEqual(result['sections'][0]['snapped_start_s'],4.)

    def test_v1_recompute_keeps_three_cached_mvp_sources_unchanged(self):
        sample=read(ROOT/'mvp/sample.json')['songs']
        stages=('beats','quantize','layers','patterns','rhythm','lyrics','join')
        selected=sorted((s for s in sample if all((ROOT/'outputs'/s['id']/f'{st}.json').exists()
            for st in stages) and read(ROOT/'outputs'/s['id']/'transcription.json').get('status')
            in ('complete','ok_with_warning')),key=lambda s:s['id'])[:3]
        self.assertEqual(len(selected),3)
        originals={s['id']:{stage:(ROOT/'outputs'/s['id']/f'{stage}.json').read_bytes()
                            for stage in stages} for s in selected}
        with tempfile.TemporaryDirectory(prefix='v1_recompute_') as tmp:
            for song in selected:
                with patch('pipeline.beats.run',side_effect=AssertionError('Inference forbidden')):
                    profiles.recompute(song,'v1',tmp)
                for stage in stages:
                    self.assertTrue((Path(tmp)/song['id']/f'{stage}.json').is_file())
        for song in selected:
            for stage in stages:
                self.assertEqual(originals[song['id']][stage],
                    (ROOT/'outputs'/song['id']/f'{stage}.json').read_bytes(),
                    f"v1 recompute modified protected {stage} cache for {song['id']}")

if __name__=='__main__':
    unittest.main()
