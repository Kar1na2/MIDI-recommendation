"""Exercise the freeze contract using temporary synthetic checkpoints only."""
import hashlib
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from . import finish,worker
from .audit import save,sha,read

class FreezeTests(unittest.TestCase):
    def fixture(self,root,bad_mask=False):
        r2=root/'emotion_r2';dest=r2/'replication';dest.mkdir(parents=True)
        tid='synthetic';cohort='replication_library';audio=root/'liked-audio/sample.wav';audio.parent.mkdir();audio.write_bytes(b'synthetic fixture, not music')
        r2.joinpath('l1_prompt.txt').write_bytes(b'unit-test prompt\r\n')
        r2.joinpath('REPLICATION_NOTES.md').write_text('unit-test fixture')
        save(r2/'validation/normalization.json',{'fixed_synthetic_parameter':True})
        save(r2/'validation/trust.json',{'fixed_synthetic_verdict':True})
        for name in ('inventory.json','replication_sample.json','prepared_songs.json','registered_source_hashes.json'):
            save(dest/name,{'songs':[{'track_id':tid}]})
        (dest/'near_duplicates.csv').write_text('track_id\n')
        summary=root/'mvp_325/summary.csv';summary.parent.mkdir();summary.write_text('track_id\nprotected\n')
        public={'track_id':tid,'cohort':cohort,'source_hashes':{},'sections':[{'section_id':'lyric-0000','set':'primary'}]}
        save(dest/'sections_input'/f'{tid}.json',public);save(dest/'private'/f'{tid}.json',{'sections':[]})
        pb=(dest/'sections_input'/f'{tid}.json').read_bytes();qb=(dest/'private'/f'{tid}.json').read_bytes()
        trans=root/'outputs'/tid/'transcription.json';save(trans,{'data':{'notes':[]}})
        stems={}
        for name in ('vocals','instrumental'):
            p=dest/'stems'/tid/(name+'.flac');p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(name.encode());stems[name]=p
        ph=hashlib.sha256(r2.joinpath('l1_prompt.txt').read_text().encode()).hexdigest().encode();amend=b'fixed-amendment'
        gate={'track_id':tid,'cohort':cohort,'status':'complete','sections':[{'section_id':'lyric-0000','status':'ok','frames':[{'start_s':0.,'active':True}]}]}
        gate['input_sha256']=hashlib.sha256(pb+sha(stems['vocals']).encode()+sha(trans).encode()).hexdigest()
        gp=dest/'scores/voice_gate'/f'{tid}.json';save(gp,gate)
        for role in finish.ROLES:
            if role=='voice_gate':continue
            data={'track_id':tid,'cohort':cohort,'status':'complete','amendment_commit':amend.decode(),'manifest_sha256':'manifest',
                  'sections':[{'section_id':'lyric-0000','status':'ok','frames':[]}]}
            if role in ('T1','T3','L1'):fp=hashlib.sha256(pb+qb+amend+role.encode()+ph).hexdigest()
            elif role in ('M1','M2'):fp=hashlib.sha256(pb+sha(stems['instrumental']).encode()+(sha(audio).encode() if role=='M1' else b'')).hexdigest()
            else:
                fp=hashlib.sha256(pb+sha(stems['instrumental' if role=='M3' else 'vocals']).encode()+amend+(gp.read_bytes() if role!='M3' else b'')).hexdigest()
            data['input_sha256']=fp
            if role in ('V1','V3'):data['sections'][0]['frames']=[{'start_s':0.,'active':not bad_mask if role=='V3' else True}]
            save(dest/'scores'/role/f'{tid}.json',data)
        for name in ('text_model_probs','l1_readings'):save(r2/'display'/name/f'{tid}.json',{'synthetic':True})
        song={'track_id':tid,'cohort':cohort,'audio_path':str(audio),'ring1_source':'fixture'}
        return r2,dest,public,pb,qb,stems,song

    def execute(self,root,bad_mask=False):
        r2,dest,public,pb,qb,stems,song=self.fixture(root,bad_mask)
        import registration,guards
        with ExitStack() as stack:
            for name,value in (('ROOT',root),('R2',r2),('DEST',dest)):
                stack.enter_context(patch.object(finish,name,value))
            stack.enter_context(patch.object(finish,'pre_scoring'))
            stack.enter_context(patch.object(finish,'selected',return_value=[song]))
            stack.enter_context(patch.object(finish,'inputs',return_value=(public,{},pb,qb)))
            stack.enter_context(patch.object(finish,'verified_stem',side_effect=lambda tid,name:stems[name]))
            stack.enter_context(patch.object(worker,'model_path'))
            stack.enter_context(patch.object(registration,'verify_registration',return_value={'amendment_commit':'fixed-amendment','amended_manifest_sha256':'manifest'}))
            stack.enter_context(patch.object(guards,'verify'))
            finish.freeze()
        return dest

    def test_all_checkpoint_formats_freeze_without_statistics(self):
        with tempfile.TemporaryDirectory() as temp:
            dest=self.execute(Path(temp));manifest=read(dest/'frozen_manifest.json')
            self.assertEqual(manifest['scored_ids'],['synthetic'])
            self.assertTrue(manifest['no_interim_h1_computed'])
            self.assertFalse((dest/'validation/h1.json').exists())

    def test_shared_gate_mask_mismatch_stops_freeze(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(RuntimeError,'shared gate mask mismatch'):
                self.execute(Path(temp),bad_mask=True)
            self.assertFalse((Path(temp)/'emotion_r2/replication/frozen_manifest.json').exists())

if __name__=='__main__':unittest.main()
