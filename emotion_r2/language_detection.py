"""Whole-song local text language ID plus 30-second vocal Whisper ID."""
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

if sys.executable != '/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
sys.path.append('/home/usr/venvs/whisperx/lib/python3.11/site-packages')
import langid
import numpy as np
from faster_whisper import WhisperModel

MODEL_ROOT=Path('/home/usr/.cache/huggingface/hub/models--Systran--faster-whisper-small/snapshots')
TEXT_CONFIDENCE=.8
AUDIO_CONFIDENCE=.6

def read(path): return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}

def choose(text_lang,text_conf,audio_lang,audio_conf,pilot_lang,instrumental=False):
    if instrumental: return 'not_applicable','pilot_instrumental',None
    if pilot_lang: return pilot_lang,'pilot_selection_manifest',None
    if text_lang and audio_lang:
        if text_lang != audio_lang: return 'uncertain','text_audio_disagreement',None
        if text_conf >= TEXT_CONFIDENCE and audio_conf >= AUDIO_CONFIDENCE:
            return text_lang,'whole_text_langid_and_whisper_agree',min(text_conf,audio_conf)
        return 'uncertain','matching_but_low_confidence',None
    if text_lang and text_conf >= TEXT_CONFIDENCE: return text_lang,'whole_text_langid_audio_unavailable',text_conf
    if audio_lang and audio_conf >= AUDIO_CONFIDENCE: return audio_lang,'whisper_audio_text_unavailable',audio_conf
    return 'uncertain','insufficient_confident_evidence',None

def text_language(text,classifier):
    if not text.strip(): return None,None,'no_text'
    chars=[c for c in text if c.isalpha()]
    if not chars: return None,None,'no_letters'
    hangul=sum('\uac00'<=c<='\ud7af' for c in chars)/len(chars)
    kana=sum(('\u3040'<=c<='\u30ff') for c in chars)/len(chars)
    language, confidence=classifier.classify(text)
    confidence=float(confidence)
    if hangul>=.20 and language!='ko': return 'ko',confidence,'hangul_script_disagrees_with_model'
    if kana>=.10 and language!='ja': return 'ja',confidence,'kana_script_disagrees_with_model'
    return language,confidence,'whole_song_langid_with_script_check'

def sample_audio(path,start):
    run=subprocess.run(['ffmpeg','-v','error','-ss',str(max(0,start)),'-t','30','-i',str(path),
                        '-ac','1','-ar','16000','-f','f32le','-'],capture_output=True,check=True)
    data=np.frombuffer(run.stdout,dtype='<f4')
    if len(data)<16000: raise ValueError('Vocal stem contains less than one second of audio')
    return data

def main():
    if not read(HERE/'integrity_start.json').get('passed'): raise RuntimeError('Initial integrity check failed')
    if not (HERE/'recovery_results.json').exists(): raise RuntimeError('Run lyric recovery first')
    snapshots=sorted(x for x in MODEL_ROOT.glob('*') if (x/'model.bin').exists())
    if len(snapshots)!=1: raise RuntimeError('Expected one cached faster-whisper-small revision')
    model=WhisperModel(str(snapshots[0]),device='cpu',compute_type='int8',cpu_threads=2,local_files_only=True)
    classifier=langid.langid.LanguageIdentifier.from_modelstring(langid.langid.model,norm_probs=True)
    pilot={x['track_id']:x for x in read(ROOT/'pilot/selection.json')['songs']}
    with (ROOT/'mvp_325/summary.csv').open(encoding='utf-8-sig',newline='') as f: songs=list(csv.DictReader(f))
    with (ROOT/'analysis_v2/language.csv').open(encoding='utf-8-sig',newline='') as f: previous={x['track_id']:x.get('language','') for x in csv.DictReader(f)}
    cache=HERE/'language_progress.jsonl'; done={}
    if cache.exists():
        for line in cache.read_text(encoding='utf-8').splitlines():
            record=json.loads(line); done[record['track_id']]=record
    with cache.open('a',encoding='utf-8') as log:
        for i,song in enumerate(songs,1):
            tid=song['track_id']
            if tid in done: continue
            p=pilot.get(tid) or {}; instr=bool(p.get('instrumental')) or song.get('ring1_source')=='instrumental'
            text='\n'.join(x.get('text','') for x in read(HERE/'private'/f'{tid}.json').get('sections',[]))
            text_lang,text_conf,text_method=text_language(text,classifier)
            audio_lang=audio_conf=None; audio_error=None
            folder=ROOT/('outputs' if song['cohort']=='random' else 'mvp_325/songs')/tid
            vocal=folder/'stems/vocals.wav'
            sections=read(ROOT/'emotion/validation/input_sections'/f'{tid}.json').get('sections') or []
            start=next((s['start_s'] for s in sections if s.get('set')=='primary' and isinstance(s.get('start_s'),(int,float))),0)
            if not instr:
                try:
                    if not vocal.is_file(): raise FileNotFoundError('vocal stem missing')
                    audio_lang,audio_conf,_=model.detect_language(audio=sample_audio(vocal,start))
                    audio_conf=float(audio_conf)
                except Exception as error: audio_error=type(error).__name__+': '+str(error)
            language,method,confidence=choose(text_lang,text_conf,audio_lang,audio_conf,p.get('language'),instr)
            if text_method.endswith('disagrees_with_model') and not p.get('language'):
                language,method,confidence='uncertain',text_method,None
            record={'track_id':tid,'language':language,'method':method,'confidence':confidence,
                    'round1_language':previous.get(tid) or None,'text_language':text_lang,
                    'text_confidence':text_conf,'text_method':text_method,'audio_language':audio_lang,
                    'audio_confidence':audio_conf,'audio_start_s':start if not instr else None,
                    'audio_error':audio_error,'audio_seconds_requested':30 if not instr else None}
            done[tid]=record; log.write(json.dumps(record,ensure_ascii=False)+'\n'); log.flush()
            if i%20==0: print(f'language IDs {i}/{len(songs)}',flush=True)
    fields=['track_id','language','method','confidence','round1_language','text_language','text_confidence',
            'text_method','audio_language','audio_confidence','audio_start_s','audio_error','audio_seconds_requested']
    with (HERE/'language.csv').open('w',encoding='utf-8',newline='') as out:
        writer=csv.DictWriter(out,fieldnames=fields); writer.writeheader(); writer.writerows(done[s['track_id']] for s in songs)
    print(json.dumps({'songs':len(done),'uncertain':sum(x['language']=='uncertain' for x in done.values()),
                      'audio_failures':sum(bool(x['audio_error']) for x in done.values()),
                      'audio_model_revision':snapshots[0].name}),flush=True)

if __name__=='__main__': main()
