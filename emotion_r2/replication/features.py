"""Read-only scoped adapter for the unchanged development feature calculations."""
import inspect
import statistics
from .audit import ROOT,DEST,read,save
from .worker import verified_stem

def scoped(function, replacements, **globals_override):
    source=inspect.getsource(function)
    for old,new in replacements:
        if source.count(old)!=1:raise RuntimeError('Scoped adapter source no longer matches: '+old)
        source=source.replace(old,new)
    namespace={**function.__globals__,**globals_override}
    exec(compile(source,'<Task K path/scope adapter>','exec'),namespace)
    return namespace[function.__name__]

def extract(songs,rows):
    import numpy as np
    import librosa
    from scipy import signal as scipy_signal
    from audio_frames import read_mono,windows,vocal_active
    from emotion import feature_explanations as origin
    # Only storage roots differ. Numerical definitions remain original source.
    calculate=scoped(origin.build_features,
        [("ROOT/('outputs' if song['cohort']=='random' else 'mvp_325/songs')/track","ROOT/'outputs'/track")],HERE=DEST)
    by={s['track_id']:{'song':s,'sections':[]} for s in songs}
    for row in rows:by[row['track_id']]['sections'].append(row)
    # Feature prosody uses the original diagnostic frame selection, not a new
    # model gate. V1/V3 have already been scored with the frozen shared pYIN mask.
    import ast
    source=(ROOT/'emotion/voice_scores.py').read_text(encoding='utf8')
    definition=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='prosody')
    namespace={'np':np,'scipy_signal':scipy_signal,'SR':16000,'HOP':320}
    exec(compile(ast.Module(body=[definition],type_ignores=[]),'<registered prosody>','exec'),namespace)
    prosody=namespace['prosody']
    for song in songs:
        tid=song['track_id'];stem=verified_stem(tid,'vocals')
        if stem is None or song['ring1_source']=='instrumental':continue
        audio=read_mono(stem,16000)
        notes=(read(ROOT/'outputs'/tid/'transcription.json').get('data') or {}).get('notes',[])
        voice=[n for n in notes if n.get('instrument')=='voice'];onsets=[n['onset_s'] for n in voice if isinstance(n.get('onset_s'),(int,float))]
        for sec in by[tid]['sections']:
            a,b=sec.get('start_s'),sec.get('end_s')
            if a is None or b is None or b<=a:continue
            crop=audio[round(a*16000):min(round(b*16000),len(audio))]
            if not len(crop):continue
            f0,_,_=librosa.pyin(crop,fmin=librosa.note_to_hz('C2'),fmax=librosa.note_to_hz('C6'),sr=16000,frame_length=2048,hop_length=320,center=True)
            times=np.arange(len(f0))*320/16000
            values=[]
            for frame in windows(a,b,audio,16000):
                if vocal_active(frame['samples'],16000) and frame['actual_seconds']:
                    selected=f0[(times>=frame['start_s']-a)&(times<frame['end_s']-a)]
                    count=sum(frame['start_s']<=onset<frame['end_s'] for onset in onsets) if voice else None
                    values.append(prosody(frame,selected,count))
            keys=sorted(set().union(*(v.keys() for v in values))) if values else []
            summaries={}
            for key in keys:
                available=[v[key] for v in values if isinstance(v.get(key),(int,float)) and np.isfinite(v[key])]
                summaries[key]={'mean':statistics.fmean(available) if available else None,'available_frames':len(available)}
            sec['audio_models']={'voice':{'prosody':summaries}}
    missing=calculate(by)
    for row in rows:row.pop('audio_models',None)
    save(DEST/'validation/feature_missingness.json',missing)
    return rows
