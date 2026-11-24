"""Isolated per-model replication workers. Reuse registered numerical kernels."""
import argparse
from collections import Counter
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from .audit import ROOT, R2, DEST, read, save, sha, now
from .controls import pre_scoring

os.environ.update(PYTHONDONTWRITEBYTECODE='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
    HF_HUB_DISABLE_TELEMETRY='1',TOKENIZERS_PARALLELISM='false',
    XDG_CACHE_HOME=str(DEST/'scratch/xdg'),NUMBA_CACHE_DIR=str(DEST/'scratch/numba'),
    HF_MODULES_CACHE=str(DEST/'scratch/hf_modules'))
sys.path[:0]=[str(R2),str(ROOT/'emotion'),str(ROOT/'emotion/vendor')]
sys.path.append(str(R2/'vendor'))
from registration import verify_registration,verify_weights

def selected():
    return [s for s in read(DEST/'prepared_songs.json')['songs'] if s['backend_ok']]

def inputs(tid):
    p=DEST/'sections_input'/f'{tid}.json';q=DEST/'private'/p.name
    public=read(p);private=read(q)
    texts={s['section_id']:s.get('text') or '' for s in private['sections']}
    for sec in public['sections']:
        text=texts.get(sec['section_id'],'')
        if text and hashlib.sha256(text.encode()).hexdigest()!=sec['text_sha256']:raise RuntimeError('Private text hash mismatch')
    return public,texts,p.read_bytes(),q.read_bytes()

def cached(target,fingerprint):
    if not target.exists():return False
    if read(target).get('input_sha256')!=fingerprint:raise RuntimeError('Checkpoint input mismatch: '+str(target))
    return True

def model_path(reg,role):
    item=next(m for m in reg['manifest']['models'] if m['role']==role)
    path=ROOT/'emotion/model_cache'/item['id'].replace('/','--') if role in ('T1','V1') else R2/'model_cache'/role
    verify_weights(reg['manifest'],role,path)
    return item,path

def verified_stem(tid,name):
    if name=='instrumental':
        path=DEST/'stems'/tid/'instrumental.flac';meta=path.parent/'separation.json'
        key='instrumental_sha256'
    else:
        path=ROOT/'outputs'/tid/'stems/vocals.wav';meta=ROOT/'outputs'/tid/'separation.json';key=None
    if not path.is_file() or not meta.is_file():return None
    record=read(meta)
    if record.get('status') not in ('complete','ok','ok_with_warning'):return None
    expected=record.get(key) if key else (record.get('data') or {}).get('stem_sha256',{}).get('vocals')
    if expected!=sha(path):raise RuntimeError('Unverified '+name+' stem: '+tid)
    return path

def emit(role,tid,**kwargs):
    print(json.dumps({'utc':now(),'role':role,'track_id':tid,**kwargs}),flush=True)

def text_scores(role):
    from score_text_model import strict_reply
    reg=verify_registration();item,path=model_path(reg,role)
    import torch
    from transformers import AutoTokenizer,AutoModelForSequenceClassification,AutoModelForCausalLM
    if not torch.cuda.is_available():raise RuntimeError('CUDA unavailable')
    torch.manual_seed(20261115)
    tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True)
    model=(AutoModelForCausalLM.from_pretrained(path,local_files_only=True,torch_dtype=torch.float16,device_map='cuda') if role=='L1' else AutoModelForSequenceClassification.from_pretrained(path,local_files_only=True).to('cuda')).eval()
    labels={v.lower():k for k,v in model.config.id2label.items()} if role!='L1' else {}
    prompt=(R2/'l1_prompt.txt').read_text(encoding='utf8');prompt_hash=hashlib.sha256(prompt.encode()).hexdigest()
    if prompt_hash!='46f33c7d62256022323d6de55f3f821a8a955af72ea94ef0c6b711bd7fff4571':raise RuntimeError('Registered prompt mismatch')
    for song in selected():
        tid=song['track_id'];public,texts,pb,qb=inputs(tid);start=time.monotonic()
        fingerprint=hashlib.sha256(pb+qb+reg['amendment_commit'].encode()+role.encode()+prompt_hash.encode()).hexdigest()
        target=DEST/'scores'/role/f'{tid}.json'
        if cached(target,fingerprint):continue
        results=[]
        for row in public['sections']:
            content=texts.get(row['section_id'],'')
            rec={k:row.get(k) for k in ('section_id','set','language','text_sha256')};rec['status']='missing_text'
            if not content:results.append(rec);continue
            if role=='T1' and row.get('language') not in item['languages']:
                rec.update(status='unsupported_or_uncertain_language',valence=None);results.append(rec);continue
            with torch.inference_mode():
                if role=='L1':
                    ids=tokenizer.apply_chat_template([{'role':'user','content':prompt+'\n'+content}],tokenize=True,add_generation_prompt=True,return_tensors='pt').to('cuda')
                    generated=model.generate(ids,attention_mask=torch.ones_like(ids),max_new_tokens=64,do_sample=False,pad_token_id=tokenizer.eos_token_id)
                    parsed=strict_reply(tokenizer.decode(generated[0,ids.shape[1]:],skip_special_tokens=True))
                    rec.update(parsed or {},status='ok' if parsed else 'unparseable_response')
                else:
                    encoded=tokenizer(content,truncation=True,max_length=512,return_tensors='pt').to('cuda')
                    probabilities=model(**encoded).logits.softmax(dim=-1)[0]
                    rec.update(valence=float(probabilities[labels['positive']]-probabilities[labels['negative']]),status='ok',truncated=len(tokenizer.encode(content,add_special_tokens=True,truncation=False))>512)
            results.append(rec)
        save(target,{'track_id':tid,'cohort':public['cohort'],'role':role,'amendment_commit':reg['amendment_commit'],
                     'manifest_sha256':reg['amended_manifest_sha256'],'prompt_sha256':prompt_hash if role=='L1' else None,
                     'input_sha256':fingerprint,'seconds':time.monotonic()-start,'sections':results})
        emit(role,tid,sections=len(results),seconds=time.monotonic()-start)

def separate():
    from guards import verify
    verify(['source_separation'])
    import soundfile as sf
    for song in selected():
        tid=song['track_id'];audio=Path(song['audio_path']);target=DEST/'stems'/tid/'separation.json'
        if not audio.resolve().is_relative_to((ROOT/'liked-audio').resolve()):raise RuntimeError('Audio outside library root')
        fingerprint=sha(audio);dest=target.parent;out=dest/'instrumental.flac'
        if target.exists():
            previous=read(target)
            if previous.get('audio_sha256')!=fingerprint:raise RuntimeError('Separation audio changed')
            if previous['status']=='complete' and previous['instrumental_sha256']!=sha(out):raise RuntimeError('Separation stem changed')
            continue
        dest.mkdir(parents=True,exist_ok=True);work=DEST/'scratch/separation'/tid;work.mkdir(parents=True,exist_ok=True)
        start=time.monotonic();result={'track_id':tid,'cohort':song['cohort'],'audio_sha256':fingerprint,'status':'failed',
                                     'model':'htdemucs','shifts':0,'overlap':.25}
        try:
            env={**os.environ,'TORCH_HOME':str(ROOT/'emotion/model_cache/torch'),
                 'PYTHONPATH':str(ROOT/'emotion/vendor')+os.pathsep+str(ROOT)}
            with (work/'demucs.log').open('w') as log:
                subprocess.run([sys.executable,str(ROOT/'emotion/pinned_demucs.py'),'--two-stems','vocals','-n','htdemucs','-d','cuda','--shifts','0','--overlap','0.25','-o',str(work),str(audio)],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=3600,check=True)
            candidates=list(work.rglob('no_vocals.wav'))
            if len(candidates)!=1:raise RuntimeError('Expected one no_vocals.wav')
            temp=dest/'instrumental.tmp.flac'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(candidates[0]),'-c:a','flac','-y',str(temp)],check=True,capture_output=True,timeout=600)
            if sf.info(temp).frames<=0:raise RuntimeError('Empty instrumental')
            temp.replace(out);result.update(status='complete',instrumental_sha256=sha(out),audio_duration_s=sf.info(out).duration,size_bytes=out.stat().st_size)
        except Exception as error:result.update(error=type(error).__name__+': '+str(error),failure_category='emotion_separation')
        result['wall_seconds']=time.monotonic()-start;save(target,result)
        if result['status']=='complete':
            import shutil
            # Work is an explicitly checked replication-only target.
            if not work.resolve().is_relative_to((DEST/'scratch/separation').resolve()):raise RuntimeError('Unsafe scratch cleanup')
            shutil.rmtree(work)
        emit('emotion_separation',tid,status=result['status'],seconds=result['wall_seconds'])

def gates():
    from audio_frames import read_mono,windows
    from voicing import SAMPLE_RATE,section_probabilities,gate_from_probabilities
    for song in selected():
        tid=song['track_id'];public,_,pb,_=inputs(tid);target=DEST/'scores/voice_gate'/f'{tid}.json'
        trans=ROOT/'outputs'/tid/'transcription.json'
        stem=verified_stem(tid,'vocals');stem_hash=sha(stem) if stem else None
        fingerprint=hashlib.sha256(pb+(stem_hash or '').encode()+sha(trans).encode()).hexdigest()
        if cached(target,fingerprint):continue
        result={'track_id':tid,'cohort':song['cohort'],'input_sha256':fingerprint,'stem_sha256':stem_hash,
                'sections_sha256':hashlib.sha256(pb).hexdigest(),'transcription_sha256':sha(trans),
                'method':'pyin probability >=0.5, >=20% of 4 s frames; padded frames unvoiced','sections':[]}
        if song['ring1_source']=='instrumental':result['status']='not_applicable_instrumental'
        elif stem is None:result.update(status='vocals_stem_unavailable',failure_category='missing_vocal_stem')
        else:
            audio=read_mono(stem,SAMPLE_RATE);notes=[n for n in (read(trans).get('data') or {}).get('notes',[]) if n.get('instrument')=='voice' and isinstance(n.get('onset_s'),(int,float)) and isinstance(n.get('offset_s'),(int,float)) and n['offset_s']>n['onset_s']]
            result.update(status='complete',voice_notes=len(notes))
            for sec in public['sections']:
                a,b=sec.get('start_s'),sec.get('end_s');out={'section_id':sec['section_id'],'set':sec.get('set'),'frames':[]}
                if not isinstance(a,(int,float)) or not isinstance(b,(int,float)) or b<=a:out['status']='missing_timing'
                else:
                    try:
                        probabilities=section_probabilities(audio,a,b);out['status']='ok'
                        for frame in windows(a,b,audio,SAMPLE_RATE):
                            gate=gate_from_probabilities(probabilities,frame['start_s']-a,frame['actual_seconds'])
                            note=any(n['onset_s']<frame['end_s'] and n['offset_s']>frame['start_s'] for n in notes)
                            out['frames'].append({'start_s':frame['start_s'],'end_s':frame['end_s'],'coverage':frame['coverage'],**gate,'muscriptor_voice_note_active':note})
                    except (ValueError,RuntimeError) as e:out.update(status='error',error=type(e).__name__+': '+str(e),frames=[])
                result['sections'].append(out)
        save(target,result);emit('voice_gate',tid,status=result['status'],sections=len(result['sections']))

def audio_scores(role):
    from audio_frames import read_mono
    from audio_scores_r2 import score_m3,score_v1,score_v3
    reg=verify_registration();_,path=model_path(reg,role)
    if role=='M3':
        import onnxruntime as ort
        session=ort.InferenceSession(str(path/'mert_emotion_int8.onnx'),providers=['CPUExecutionProvider'])
        torch=model=processor=None
    else:
        import torch
        if not torch.cuda.is_available():raise RuntimeError('CUDA unavailable')
        if role=='V3':
            from transformers import AutoModelForAudioClassification,AutoProcessor
            processor=AutoProcessor.from_pretrained(path,local_files_only=True)
            model=AutoModelForAudioClassification.from_pretrained(path,trust_remote_code=True,local_files_only=True).to('cuda').eval()
        else:
            from transformers import Wav2Vec2FeatureExtractor
            from voice_model import EmotionModel
            processor=Wav2Vec2FeatureExtractor.from_pretrained(path,local_files_only=True)
            model,info=EmotionModel.from_pretrained(path,local_files_only=True,output_loading_info=True)
            if info['missing_keys'] or info['unexpected_keys'] or info['mismatched_keys']:raise RuntimeError('Incomplete V1 load')
            model=model.to('cuda').eval()
    for song in selected():
        tid=song['track_id'];public,_,pb,_=inputs(tid);target=DEST/'scores'/role/f'{tid}.json';start=time.monotonic()
        stem=verified_stem(tid,'instrumental' if role=='M3' else 'vocals');stem_hash=sha(stem) if stem else None
        gp=DEST/'scores/voice_gate'/f'{tid}.json';gate_data=read(gp) if role!='M3' and gp.exists() else None
        fingerprint=hashlib.sha256(pb+(stem_hash or '').encode()+reg['amendment_commit'].encode()+(gp.read_bytes() if gate_data else b'')).hexdigest()
        if cached(target,fingerprint):continue
        result={'track_id':tid,'cohort':public['cohort'],'role':role,'input_sha256':fingerprint,'amendment_commit':reg['amendment_commit'],
                'manifest_sha256':reg['amended_manifest_sha256'],'stem_sha256':stem_hash,'sections':[]}
        if role!='M3' and song['ring1_source']=='instrumental':result['status']='not_applicable_instrumental'
        elif stem is None:result['status']='instrumental_stem_unavailable' if role=='M3' else 'vocals_stem_unavailable'
        elif role!='M3' and (gate_data is None or gate_data.get('status')!='complete'):result['status']='voice_gate_unavailable'
        else:
            if gate_data and (gate_data['stem_sha256']!=stem_hash or gate_data['sections_sha256']!=hashlib.sha256(pb).hexdigest()):raise RuntimeError('Gate source hash mismatch')
            signal=read_mono(stem,24000 if role=='M3' else 16000)
            by_gate={s['section_id']:{round(f['start_s'],6):f for f in s['frames']} for s in (gate_data or {}).get('sections',[])}
            for sec in public['sections']:
                a,b=sec.get('start_s'),sec.get('end_s');rec={'section_id':sec['section_id'],'set':sec.get('set')}
                if not isinstance(a,(int,float)) or not isinstance(b,(int,float)) or b<=a:rec['status']='missing_timing'
                else:
                    try:
                        if role=='M3':
                            scored=score_m3(session,signal,sec);rec['status']='ok' if scored else 'no_frames'
                            if scored:rec.update(scored)
                        else:rec.update((score_v3 if role=='V3' else score_v1)(model,processor,torch,signal,sec,by_gate.get(sec['section_id'],{})))
                    except (ValueError,RuntimeError) as e:rec.update(status='error',error=type(e).__name__+': '+str(e))
                result['sections'].append(rec)
            result['status']='complete'
        result['seconds']=time.monotonic()-start;save(target,result);emit(role,tid,status=result['status'],sections=len(result['sections']),seconds=result['seconds'])

def main():
    p=argparse.ArgumentParser();p.add_argument('role',choices=['separate','gate','T1','T3','L1','M1','M2','M3','V1','V3']);a=p.parse_args();pre_scoring()
    if a.role=='separate':separate()
    elif a.role=='gate':gates()
    elif a.role in ('T1','T3','L1'):text_scores(a.role)
    elif a.role in ('M1','M2'):
        from .background import score
        score(a.role)
    else:audio_scores(a.role)

if __name__=='__main__':main()
