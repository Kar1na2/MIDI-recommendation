"""Cached, interpretable section features and grouped-by-song rating models."""
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal as scipy_signal
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import r2_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from aggregate_scores import is_number
from stems import library_active

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
MAJOR=np.array([6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88])
MINOR=np.array([6.33,2.68,3.52,5.38,2.60,3.53,2.54,4.75,3.98,2.69,3.34,3.17])
LABELS={'tempo_bpm':'tempo','onset_density_total':'note onset density','off_strong_beat_share':'off-strong-beat share',
 'median_register_midi':'median note register','key_strength':'pitch-class key strength','major_mode_score':'estimated major mode',
 'chord_layer_onset_rate':'chord-layer onset rate','instrumental_rms':'instrumental loudness',
 'instrumental_brightness_hz':'instrumental spectral brightness'}

def read(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def read_stage(path):
 path=Path(path)
 if not path.is_file(): return None
 payload=read(path)
 if payload.get('status') not in ('ok','ok_with_warning','complete'):
  return None
 return payload.get('data') if isinstance(payload.get('data'),dict) else None
def numeric(x): return is_number(x)

def key_features(notes):
 hist=np.zeros(12); pitches=[]
 for n in notes:
  p=n.get('pitch')
  if numeric(p): hist[int(p)%12]+=1; pitches.append(int(p))
 if not pitches or hist.std()==0: return None,None
 candidates=[]
 for mode,profile in [('major',MAJOR),('minor',MINOR)]:
  for tonic in range(12):
   score=float(np.corrcoef(hist,np.roll(profile,tonic))[0,1])
   candidates.append((score,mode,tonic))
 score,mode,_=max(candidates)
 return score,1.0 if mode=='major' else 0.0

def build_features(sections):
 missing=defaultdict(list)
 for item in sections.values():
  if library_active(): raise RuntimeError('Library processing detected; emotion feature work paused')
  song=item['song']; track=song['track_id']
  folder=ROOT/('outputs' if song['cohort']=='random' else 'mvp_325/songs')/track
  stages={name:read_stage(folder/f'{name}.json') for name in ('beats','quantize','patterns','layers','transcription')}
  required={'quantize':'notes','patterns':'rhythmic_sections','layers':'notes','transcription':'notes'}
  for name,field in required.items():
   if stages[name] is not None and not isinstance(stages[name].get(field),list): stages[name]=None
  for name,value in stages.items():
   if value is None: missing[name].append(track)
  beats=stages['beats'] or {}; quant=stages['quantize'] or {}; pattern=stages['patterns'] or {}; layers=stages['layers'] or {}
  transcription=(stages['transcription'] or {}).get('notes') or []
  qnotes=quant.get('notes') or []; rhythm=pattern.get('rhythmic_sections') or []
  layer_notes=layers.get('notes') or []
  audio_path=HERE/'stems'/track/'instrumental.flac'
  signal=rate=None
  if audio_path.is_file(): signal,rate=sf.read(audio_path,dtype='float32',always_2d=True)
  for sec in item['sections']:
   start,end=sec.get('start_s'),sec.get('end_s')
   duration=end-start if numeric(start) and numeric(end) and end>start else None
   q=[n for n in qnotes if duration and numeric(n.get('onset_s')) and start<=n['onset_s']<end]
   raw=[n for n in transcription if duration and numeric(n.get('onset_s')) and start<=n['onset_s']<end]
   feat={'tempo_bpm':beats.get('estimated_bpm'),'onset_density_total':len(q)/duration if duration and stages['quantize'] is not None else None}
   counts=Counter(n.get('instrument') if isinstance(n.get('instrument'),str) else 'unknown' for n in q)
   for instr,count in counts.items():
    key=''.join(c if c.isalnum() else '_' for c in instr)
    feat[f'onset_density__{key}']=count/duration if duration else None
   off=[]
   for n in q:
    spb,step=n.get('steps_per_bar'),n.get('step')
    if numeric(spb) and numeric(step) and spb>0: off.append(int(int(step)%max(1,round(spb/4))!=0))
   feat['off_strong_beat_share']=statistics.fmean(off) if off else None
   pitches=[n['pitch'] for n in raw if numeric(n.get('pitch'))]
   feat['median_register_midi']=statistics.median(pitches) if pitches else None
   feat['key_strength'],feat['major_mode_score']=key_features(raw)
   chord_onsets={(n.get('instrument'),n.get('bar'),n.get('step')) for n in layer_notes
                 if n.get('layer')=='chord' and numeric(n.get('onset_s')) and duration
                 and start<=n['onset_s']<end and n.get('bar') is not None and n.get('step') is not None}
   feat['chord_layer_onset_rate']=len(chord_onsets)/duration if duration and stages['layers'] is not None else None
   active=[rs for rs in rhythm if duration and numeric(rs.get('start_s')) and numeric(rs.get('end_s'))
           and rs['start_s']<end and rs['end_s']>start]
   comp=[c.get('compression_ratio') for rs in active for c in rs.get('instrument_compression',[])
         if numeric(c.get('compression_ratio')) and c.get('regular_bars',0)>0]
   feat['pattern_compression_ratio']=statistics.fmean(comp) if comp else None
   bars=sum(c.get('regular_bars',0) for rs in active for c in rs.get('instrument_compression',[]))
   runs=sum(c.get('pattern_runs',0) for rs in active for c in rs.get('instrument_compression',[]))
   feat['pattern_repetition_rate']=max(0,bars-runs)/bars if bars else None
   if signal is not None and duration:
    clip=signal[max(0,round(start*rate)):min(len(signal),round(end*rate))]
    if len(clip):
     mono=clip.mean(axis=1).astype(np.float64)
     feat['instrumental_rms']=float(np.sqrt(np.mean(mono**2)))
     if len(mono)>=2:
      nperseg=min(1024,len(mono)); freq,_,spec=scipy_signal.stft(mono,fs=rate,nperseg=nperseg,
        noverlap=nperseg//2,boundary=None,padded=False)
      magnitude=np.abs(spec); totals=magnitude.sum(axis=0)
      centroids=np.divide((magnitude*freq[:,None]).sum(axis=0),totals,
                          out=np.full_like(totals,np.nan),where=totals>0)
      centroids=centroids[np.isfinite(centroids)]
      feat['instrumental_brightness_hz']=float(np.mean(centroids)) if centroids.size else None
     else: feat['instrumental_brightness_hz']=None
    else: feat.update(instrumental_rms=None,instrumental_brightness_hz=None)
   else: feat.update(instrumental_rms=None,instrumental_brightness_hz=None)
   prosody=sec.get('audio_models',{}).get('voice',{}).get('prosody',{})
   for name,summary in prosody.items():
    feat['voice_'+name]=summary.get('mean') if summary.get('available_frames') else None
   sec['features']=feat
   sec['feature_source_status']={name:'available' if value is not None else 'unavailable' for name,value in stages.items()}
 return dict(missing)

def fit(sections,trust):
 rows=[(item,sec) for item in sections.values() if item['song']['cohort']=='random'
       for sec in item['sections'] if sec.get('set')=='primary']
 columns=sorted({key for _,sec in rows for key,value in sec.get('features',{}).items() if numeric(value)})
 results={'note':'Explains pretrained model ratings, not ground truth or causation.',
          'targets':{},'section_reasons':{}}
 for target,trust_key in [('background_valence','background_valence'),('background_arousal','background_arousal'),
                          ('voice_valence','voice_valence'),('voice_arousal','voice_arousal')]:
  if not trust[trust_key]['trusted']:
   results['targets'][target]={'status':'not_fit_channel_untrusted'}; continue
  selected=[(it,s) for it,s in rows if numeric(s.get('model_z',{}).get(target+'_consensus'))]
  groups=np.array([it['song']['track_id'] for it,_ in selected]); y=np.array([s['model_z'][target+'_consensus'] for _,s in selected])
  songs=set(groups)
  if len(songs)<5 or not columns:
   results['targets'][target]={'status':'insufficient grouped observations','n_sections':len(selected)}; continue
  X=np.array([[s.get('features',{}).get(c,np.nan) for c in columns] for _,s in selected],dtype=float)
  preds=np.full(len(y),np.nan); perm=defaultdict(list)
  cv=GroupKFold(n_splits=5)
  for fold,(train,test) in enumerate(cv.split(X,y,groups),1):
   model=make_pipeline(SimpleImputer(strategy='median',keep_empty_features=True),StandardScaler(),Ridge(alpha=1.0))
   model.fit(X[train],y[train]); preds[test]=model.predict(X[test])
   baseline=r2_score(y[test],preds[test]) if len(test)>1 else 0.
   rng=np.random.default_rng(20261115+fold)
   for j,name in enumerate(columns):
    drops=[]
    for _ in range(10):
     shuffled=X[test].copy(); shuffled[:,j]=rng.permutation(shuffled[:,j])
     drops.append(baseline-r2_score(y[test],model.predict(shuffled)))
    perm[name].append(float(np.mean(drops)))
  final=make_pipeline(SimpleImputer(strategy='median',keep_empty_features=True),StandardScaler(),Ridge(alpha=1.0))
  final.fit(X,y); imputer,scaler,reg=(final.named_steps['simpleimputer'],final.named_steps['standardscaler'],final.named_steps['ridge'])
  all_scored=[(item,sec) for item in sections.values() for sec in item['sections']
              if numeric(sec.get('model_z',{}).get(target+'_consensus'))]
  for item,sec in all_scored:
   raw=np.array([sec.get('features',{}).get(c,np.nan) for c in columns],dtype=float)
   vals=np.where(np.isfinite(raw),raw,imputer.statistics_); contrib=((vals-scaler.mean_)/scaler.scale_)*reg.coef_
   order=np.argsort(np.abs(contrib))[::-1][:3]; reason=[]
   for j in order:
    name=columns[j]; label=LABELS.get(name,name.replace('__',' ').replace('_',' ')); score=float(contrib[j])
    reason.append({'feature':name,'label':label,'sign':'higher' if score>=0 else 'lower','contribution':score,
                   'reason':f'{label} contributes {"upward" if score>=0 else "downward"} to the model rating'})
   results['section_reasons'][f"{item['song']['track_id']}:{sec['section_id']}:{target}"]=reason
  imp=sorted([{'feature':k,'mean_r2_drop':float(np.mean(v))} for k,v in perm.items()],key=lambda x:x['mean_r2_drop'],reverse=True)
  results['targets'][target]={'status':'fit','n_sections':len(selected),'n_songs':len(songs),'grouped_cv_folds':5,
    'ridge_alpha':1.0,'grouped_cv_r2':float(r2_score(y,preds)),'permutation_importance':imp[:20]}
 return results
