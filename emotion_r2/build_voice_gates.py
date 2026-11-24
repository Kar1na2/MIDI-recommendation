"""Compute shared pYIN gates and a seeded MuScriptor note-activity check."""
import argparse, csv, hashlib, json, random, sys, time
from pathlib import Path
if sys.executable != '/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
HERE=Path(__file__).resolve().parent; ROOT=HERE.parent
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'emotion'))
from audio_frames import read_mono, windows
from audio_scores_r2 import verified_stem
from voicing import SAMPLE_RATE, section_probabilities, gate_from_probabilities

SEED=20261117
def read(path): return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
def main():
    p=argparse.ArgumentParser(); p.add_argument('--validate-ten',action='store_true'); p.add_argument('--song',action='append'); args=p.parse_args()
    with (ROOT/'mvp_325/summary.csv').open(encoding='utf-8-sig',newline='') as f: songs=list(csv.DictReader(f))
    if len(songs)!=325 or len({s['track_id'] for s in songs})!=325: raise RuntimeError('Unexpected sample')
    eligible=[]; validation_eligible=[]
    for song in songs:
        tid=song['track_id']; folder=ROOT/('outputs' if song['cohort']=='random' else 'mvp_325/songs')/tid
        secpath=HERE/'sections_input'/f'{tid}.json'
        if song.get('ring1_source')!='instrumental' and (folder/'stems/vocals.wav').is_file() and (folder/'transcription.json').is_file() and secpath.is_file():
            sections=read(secpath).get('sections') or []
            eligible.append(song)
            if any(s.get('set')=='primary' and isinstance(s.get('start_s'),(int,float)) and isinstance(s.get('end_s'),(int,float)) and s['end_s']>s['start_s'] for s in sections):
                validation_eligible.append(song)
    eligible.sort(key=lambda s:s['track_id'])
    validation_eligible.sort(key=lambda s:s['track_id'])
    if args.validate_ten:
        if len(validation_eligible)<10: raise RuntimeError('Fewer than 10 eligible vocal songs')
        chosen=random.Random(SEED).sample(validation_eligible,10)
    elif args.song:
        chosen=[s for s in eligible if s['track_id'] in set(args.song)]
        if len(chosen)!=len(set(args.song)): raise RuntimeError('Requested song not eligible in sample')
    else: chosen=eligible
    dest=HERE/'scores/voice_gate'; dest.mkdir(parents=True,exist_ok=True)
    summary=[]; confusion={'both_active':0,'gate_only':0,'note_only':0,'neither':0}; started=time.monotonic()
    for song in chosen:
        tid=song['track_id']; folder=ROOT/('outputs' if song['cohort']=='random' else 'mvp_325/songs')/tid
        stem=verified_stem(tid,'vocals',song['cohort'])
        if stem is None: raise RuntimeError('Unverified or unavailable vocal stem: '+tid)
        path=HERE/'sections_input'/f'{tid}.json'; public=read(path)
        with stem.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
        section_hash=hashlib.sha256(path.read_bytes()).hexdigest()
        trans_hash=hashlib.sha256((folder/'transcription.json').read_bytes()).hexdigest()
        cached=read(dest/(tid+'.json'))
        if cached.get('stem_sha256')==digest and cached.get('sections_sha256')==section_hash and cached.get('transcription_sha256')==trans_hash and cached.get('method')=='pyin probability >=0.5, >=20% of 4 s frames; padded frames unvoiced':
            sample_counts={'both_active':0,'gate_only':0,'note_only':0,'neither':0}
            for sec in cached.get('sections',[]):
                if sec.get('set')!='primary': continue
                for frame in sec['frames']:
                    key=('both_active' if frame['muscriptor_voice_note_active'] else 'gate_only') if frame['active'] else ('note_only' if frame['muscriptor_voice_note_active'] else 'neither')
                    sample_counts[key]+=1;confusion[key]+=1
            summary.append({'track_id':tid,'cohort':song['cohort'],'voice_notes':cached.get('voice_notes'),'confusion':sample_counts})
            print(json.dumps({'track_id':tid,'cohort':song['cohort'],'status':'cached'}),flush=True);continue
        audio=read_mono(stem,SAMPLE_RATE); trans=read(folder/'transcription.json').get('data') or {}
        notes=[n for n in trans.get('notes') or [] if n.get('instrument')=='voice' and isinstance(n.get('onset_s'),(int,float)) and isinstance(n.get('offset_s'),(int,float)) and n['offset_s']>n['onset_s']]
        gate_sections=[]; sample_counts={'both_active':0,'gate_only':0,'note_only':0,'neither':0}
        for sec in public.get('sections',[]):
            a,b=sec.get('start_s'),sec.get('end_s'); output={'section_id':sec['section_id'],'set':sec.get('set'),'frames':[]}
            if not isinstance(a,(int,float)) or not isinstance(b,(int,float)) or b<=a:
                output['status']='missing_timing';gate_sections.append(output);continue
            probs=section_probabilities(audio,a,b);output['status']='ok'
            for frame in windows(a,b,audio,SAMPLE_RATE):
                gate=gate_from_probabilities(probs,frame['start_s']-a,frame['actual_seconds'])
                note_active=any(n['onset_s']<frame['end_s'] and n['offset_s']>frame['start_s'] for n in notes)
                key=('both_active' if note_active else 'gate_only') if gate['active'] else ('note_only' if note_active else 'neither')
                if sec.get('set')=='primary': sample_counts[key]+=1;confusion[key]+=1
                output['frames'].append({'start_s':frame['start_s'],'end_s':frame['end_s'],'coverage':frame['coverage'],**gate,'muscriptor_voice_note_active':note_active})
            gate_sections.append(output)
        payload={'track_id':tid,'cohort':song['cohort'],'stem_sha256':digest,'sections_sha256':section_hash,'voice_notes':len(notes),
                 'transcription_sha256':trans_hash,
                 'method':'pyin probability >=0.5, >=20% of 4 s frames; padded frames unvoiced','sections':gate_sections}
        temp=dest/(tid+'.tmp');temp.write_text(json.dumps(payload,allow_nan=False)+'\n',encoding='utf8');temp.replace(dest/(tid+'.json'))
        summary.append({'track_id':tid,'cohort':song['cohort'],'voice_notes':len(notes),'confusion':sample_counts})
        print(json.dumps({'track_id':tid,'cohort':song['cohort'],'voice_notes':len(notes),'windows':sum(sample_counts.values())}),flush=True)
    if args.validate_ten:
        total=sum(confusion.values()); agreement=(confusion['both_active']+confusion['neither'])/total if total else None
        result={'seed':SEED,'eligible_songs':len(validation_eligible),'selection':'uniform sample of vocal songs with stem, transcription and timed primary sections','songs':summary,'confusion':confusion,'window_agreement':agreement,'windows':total,'seconds':time.monotonic()-started,'caveat':'MuScriptor activity is an imperfect comparison, not a ground-truth voicing annotation.'}
        (HERE/'voice_gate_validation.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf8')
        print(json.dumps({k:v for k,v in result.items() if k!='songs'},indent=2),flush=True)
if __name__=='__main__': main()
