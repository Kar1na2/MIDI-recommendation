import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
import numpy as np
import soundfile as sf
from pipeline import beats,quantize,retention,genius_matching,genius_queue,lyric_backend,handcheck,mvp
from pipeline.backends.muscriptor import MuScriptorBackend,logged_generation
from pipeline.common import save,read
from tests.test_pipeline import grid,note
from pipeline import core,whisperx_runner
import sys


class EosRetryTests(unittest.TestCase):
    def test_timeout_generation_logging_preserves_token_stream(self):
        class Decoder:
            def generate(self, value=None):
                self.seen=value
                yield from range(205)
        decoder=Decoder(); events=[]
        with logged_generation(decoder,events.append):
            tokens=list(decoder.generate(value='same'))
        self.assertEqual(tokens,list(range(205)))
        self.assertEqual(decoder.seen,'same')
        self.assertEqual([e['event'] for e in events],
                         ['chunk_start','chunk_heartbeat','chunk_heartbeat','chunk_end'])
        self.assertEqual(events[1]['tokens_emitted'],100)
        self.assertEqual(events[2]['tokens_emitted'],200)
        self.assertEqual(events[-1]['outcome'],'decoder_returned')

    def test_eos_retries_once_and_records_chunk(self):
        backend=MuScriptorBackend(); backend.event_diagnostics={}
        error=RuntimeError('chunk 9 (seek=45.0s) did not emit EOS within 2000 tokens')
        with patch.object(backend,'_transcribe',side_effect=[error,['notes']]) as call:
            self.assertEqual(backend.transcribe('audio'),['notes'])
        self.assertEqual([c.kwargs['strict_eos'] for c in call.call_args_list],[True,False])
        self.assertTrue(backend.event_diagnostics['eos_warning'])
        self.assertEqual(backend.event_diagnostics['eos_affected_chunks'],[{'chunk':9,'start_s':45.}])

    def test_failed_retry_remains_failed(self):
        backend=MuScriptorBackend()
        with patch.object(backend,'_transcribe',side_effect=[RuntimeError('did not emit EOS'),RuntimeError('again')]) as call:
            with self.assertRaisesRegex(RuntimeError,'again'): backend.transcribe('audio')
        self.assertEqual(call.call_count,2)


class MeterTests(unittest.TestCase):
    def test_auto_requires_both_conditions(self):
        raw=grid(); dbn=grid()
        raw['bars'][0]['beats_per_bar']=2
        self.assertEqual(beats.auto_choice(raw,dbn)['chosen_grid'],'dbn')
        shifted={**dbn,'downbeat_times':[t+.1 for t in dbn['downbeat_times']]}
        self.assertEqual(beats.auto_choice(raw,shifted)['chosen_grid'],'raw')
        self.assertEqual(beats.auto_choice(dbn,dbn)['chosen_grid'],'raw')

    def test_selected_record_keeps_both_and_no_clicks(self):
        raw=grid(); dbn=grid()
        result=beats.select_grid({'raw_grid':raw,'dbn_grid':dbn,'raw':{}},'auto')
        self.assertIs(result['raw_grid'],raw); self.assertIs(result['dbn_grid'],dbn)
        self.assertNotIn('click',result)


class GridRuleTests(unittest.TestCase):
    def test_coarsest_grid_that_meets_both_requirements(self):
        result=quantize.quantize([note(0),note(.5),note(1),note(1.5)],grid())
        self.assertEqual(result['instrument_steps_per_bar']['piano'],16)

    def test_no_qualifying_grid_is_explicitly_flagged(self):
        # All raw attacks are too far from the only supplied grid.
        result=quantize.quantize([note(.061),note(.19)],grid(),step_candidates=[16])
        self.assertIn('no_grid_meets_90pct_within30ms_and_10pct_collision',result['flags'])


class RetentionTests(unittest.TestCase):
    def test_only_requested_stems_remain_and_savings_are_measured(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp); folder=out/'stems'; folder.mkdir()
            for name in ('bass','other','drums','vocals'):
                sf.write(folder/f'{name}.wav',np.ones((400,2),dtype='float32')*.01,100,subtype='FLOAT')
            result=retention.apply(out)
            self.assertEqual({p.stem for p in folder.glob('*.wav')},{'vocals','no_drums'})
            data,_=sf.read(folder/'no_drums.wav'); self.assertAlmostEqual(data.mean(),.03,places=5)
            self.assertGreater(result['net_bytes_saved'],0)


class LyricOrderTests(unittest.TestCase):
    def test_lrclib_wins_over_cached_whisperx(self):
        source={'sections':[{'label':'Verse','number':1,'performers':[],'genius_section_index':0,'lines':['hello world']}]}
        lrc={'lines':[{'text':'hello world','start_s':1.}], 'lrclib_paragraphs':[]}
        with tempfile.TemporaryDirectory() as tmp, patch.object(lyric_backend.lrclib,'fetch',return_value=lrc), \
             patch.object(lyric_backend,'cached_source',return_value=source), \
             patch.object(lyric_backend,'cpu_alignment') as wx:
            result=lyric_backend.run({'id':'s','artist':'a','title':'t','language':'en'},tmp,grid(),{'vocals':'unused'})
        self.assertEqual(result['source'],'lrclib'); self.assertEqual(result['validated_section_count'],1)
        wx.assert_not_called()

    def test_invalid_manual_input_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(lyric_backend.lrclib,'fetch') as lrc:
            path=Path(tmp)/'manual.csv'; path.write_text('label,start_s,end_s\nVerse,3,1\n')
            with self.assertRaises(Exception):
                lyric_backend.run({'manual_sections_csv':str(path)},tmp,grid(),{'vocals':'unused'})
            lrc.assert_not_called()


class QueueTests(unittest.TestCase):
    def test_retry_after_seconds_and_http_date(self):
        self.assertEqual(genius_queue.retry_delay('123',0),123)
        self.assertEqual(genius_queue.retry_delay('Thu, 01 Jan 1970 00:02:00 GMT',0),120)

    def test_catalog_is_capped_at_five_and_title_search_is_first(self):
        calls=[]
        candidate={'id':1,'title':'Unrelated','url':'https://genius.com/a','primary_artist':{'id':9,'name':'artist'}}
        def fake(endpoint,params,*args):
            calls.append((endpoint,params))
            return {'hits':[{'result':candidate}]} if endpoint=='/search' else {'songs':[candidate],'next_page':params['page']+1}
        with patch.object(genius_matching,'_api_get',side_effect=fake):
            result=genius_matching.lookup({'artist':'artist','title':'zzzz'},'token')
        self.assertEqual(calls[0][0],'/search'); self.assertIn('zzzz',calls[0][1]['q'])
        self.assertEqual(max(p['page'] for e,p in calls if '/artists/' in e),5)
        self.assertIsNone(result['selected'])

    def test_rate_limited_song_is_pending(self):
        with patch.object(genius_matching,'_api_get',side_effect=genius_queue.GeniusPending('key',123,'queued')):
            result=genius_matching.lookup({'artist':'a','title':'b'},'token')
        self.assertEqual(result['status'],'pending_genius')

    def test_every_response_is_cached_and_429_is_queued(self):
        response=Mock(status_code=429,headers={'Retry-After':'120'}); response.json.return_value={'meta':{'status':429}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(genius_queue,'ROOT',Path(tmp)), \
             patch('requests.get',return_value=response):
            with self.assertRaises(genius_queue.GeniusPending): genius_queue.request('/search',{'q':'test'},'secret')
            self.assertEqual(len(list((Path(tmp)/'data/genius_api/responses').rglob('*.json'))),1)
            queue=read(Path(tmp)/'data/genius_queue.json')
            self.assertEqual(next(iter(queue['requests'].values()))['status'],'waiting')


class SampleAndScoreTests(unittest.TestCase):
    def test_cpu_systematic_failures_stop_but_pending_api_does_not(self):
        states={str(i):{'status':'failed','failure_category':'lyrics_cpu','updated_at':i} for i in range(5)}
        self.assertIn('five_consecutive',mvp.cpu_failure_stop_reason(states))
        states['5']={'status':'pending_genius','updated_at':5}
        states['6']={'status':'complete','updated_at':6}
        self.assertIsNone(mvp.cpu_failure_stop_reason(states))

    def test_spot_check_uses_the_midi_that_patterns_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp); midi=out/'transcription_routed.mid'; midi.write_bytes(b'midi')
            save(out/'routing.json',{'status':'complete','data':{'routed_midi_path':str(midi)}})
            self.assertEqual(mvp.review_midi(out),str(midi))

    def test_stop_requests_finish_the_active_stage_without_a_signal(self):
        from pipeline.batch import stop_batch
        with tempfile.TemporaryDirectory() as tmp, patch('os.kill') as kill:
            out=Path(tmp); (out/'batch.pid').write_text('123')
            self.assertEqual(stop_batch(out),0)
            kill.assert_called_once_with(123,0)
            self.assertEqual((out/'stop.requested').read_text(),'user_requested')

    def test_blind_template_archives_old_boundaries_and_contains_none(self):
        titles=['meaningful love','ATTITUDE','505','HUMBLE.','you should see me in a crown',
                'Chopin Nocturne Op 9 No 1','Bling-Bang-Bang-Born','Love Virus']
        with tempfile.TemporaryDirectory() as tmp, patch.object(handcheck,'ROOT',Path(tmp)):
            root=Path(tmp); (root/'pilot').mkdir()
            save(root/'pilot/selection.json',{'songs':[{'title':s,'track_id':str(i)} for i,s in enumerate(titles)]})
            (root/'pilot/HAND_CHECK.csv').write_text('old anchored boundaries')
            handcheck.prepare()
            import csv
            with (root/'pilot/HAND_CHECK_SECTIONS.csv').open() as stream: rows=list(csv.DictReader(stream))
            self.assertEqual(len(rows),16)
            self.assertTrue(all(not row[k] for row in rows for k in handcheck.FIELDS[3:]))
            self.assertFalse((root/'pilot/HAND_CHECK.csv').exists())
            self.assertTrue((root/'pilot/archive/HAND_CHECK_before_backend_mvp.csv').exists())

    def test_random_sample_is_reproducible_and_has_no_duplicates(self):
        songs=[{'id':str(i)} for i in range(1028)]
        a=mvp.sample_rows(songs); b=mvp.sample_rows(list(reversed(songs)))
        self.assertEqual(a,b); self.assertEqual(len({s['id'] for s in a}),300)

    def test_one_boundary_cannot_get_multiple_credits(self):
        result=handcheck.compare([1.,1.1],[1.],.2)
        self.assertEqual(result['matched'],1); self.assertEqual(result['recall'],.5)

    def test_empty_handcheck_rows_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(handcheck,'ROOT',Path(tmp)):
            root=Path(tmp); (root/'pilot').mkdir()
            save(root/'pilot/selection.json',{'songs':[]})
            path=root/'pilot/HAND_CHECK_SECTIONS.csv'
            path.write_text(','.join(handcheck.FIELDS)+'\nabc,title,1,,,,\n')
            result=handcheck.score()
            self.assertEqual(result['songs_scored'],0); self.assertEqual(result['empty_rows_skipped'],1)

    def test_pooled_permutation_statistic(self):
        values={'observed_share_within_one_bar':.8,'observed_minus_baseline':.6,
                'one_sided_empirical_p':.01,'shuffle_distribution':[.1]*1000}
        reports=[{'boundary_evaluations':{'raw':values}} for _ in range(3)]
        result=mvp.hypothesis(reports,'raw')
        self.assertEqual(result['songs'],3); self.assertLess(result['pooled_permutation_p'],.05)
        self.assertEqual(result['share_p_below_005'],1.)


class CoreRecomputeTests(unittest.TestCase):
    def test_rejected_lyric_sections_remain_visible_in_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); audio=root/'source.wav'; audio.write_bytes(b'source')
            song={'id':'test','title':'test','artist':'test','audio_path':str(audio)}
            out=root/'test'; out.mkdir(); g=grid()
            save(out/'transcription.json',{'status':'complete','data':{'notes':[note(i*.5) for i in range(10)]}})
            save(out/'beats.json',{'status':'complete','data':{'raw_grid':g,'dbn_grid':g,'raw':{}}})
            save(out/'lyrics.json',{'data':{'source':'lrclib','validated_section_count':1,'sections':[
                {'label':'Verse','raw_start_s':1.,'raw_end_s':2.,'validated':True},
                {'label':'Chorus','raw_start_s':None,'raw_end_s':None,'validated':False,'flags':['unaligned']}]}})
            result=core.recompute(song,root,refresh_beats=False,evaluate_grids=False)
            self.assertEqual(len(result['lyric_sections']),2)
            self.assertEqual(len(result['joined_lyric_sections']),1)
            self.assertEqual(result['lyric_sections'][1]['flags'],['unaligned'])

    def test_cached_notes_and_both_grids_never_invoke_a_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); audio=root/'source.wav'; audio.write_bytes(b'source')
            song={'id':'test','title':'test','artist':'test','audio_path':str(audio)}
            out=root/'test'; out.mkdir()
            g=grid()
            save(out/'transcription.json',{'status':'complete','data':{'notes':[note(i*.5) for i in range(10)]}})
            save(out/'beats.json',{'status':'complete','data':{'raw_grid':g,'dbn_grid':g,'raw':{}}})
            with patch.object(beats,'run',side_effect=AssertionError('network/model forbidden')):
                result=core.recompute(song,root,refresh_beats=False)
            self.assertEqual(result['status'],'ok')
            self.assertTrue((out/'rhythm.json').exists()); self.assertFalse((out/'click.wav').exists())
            self.assertEqual(read(out/'beats.json')['computation_device'],'cpu')

    def test_clicks_are_generated_only_on_explicit_request(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(core,'ROOT',Path(tmp)), patch.object(beats,'write_click') as write:
            out=Path(tmp)/'outputs/test'; out.mkdir(parents=True)
            save(out/'beats.json',{'data':{'raw_grid':grid(),'dbn_grid':grid()}})
            core.click({'id':'test','audio_path':'unused'},'both')
            self.assertEqual(write.call_count,2)
            self.assertEqual([c.args[2].name for c in write.call_args_list],['click.wav','click_dbn.wav'])

    def test_cpu_asr_numpy_scores_publish_real_json(self):
        fake=Mock()
        fake.load_model.return_value.transcribe.return_value={'language':'en','segments':[{'text':'hello','start':0,'end':2}]}
        fake.load_align_model.return_value=(Mock(),{})
        fake.align.return_value={'segments':[{'start':np.float32(0),'end':np.float32(2),
            'words':[{'score':np.float32(.9)}]}]}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules,{'whisperx':fake}):
            request=Path(tmp)/'request.json'; output=Path(tmp)/'output.json'
            save(request,{'vocals':'unused','lines':[],'language':None,'device':'cpu'})
            with patch.object(sys,'argv',['runner','--request',str(request),'--output',str(output)]):
                whisperx_runner.main()
            self.assertTrue(read(output)['sections'][0]['validated'])
