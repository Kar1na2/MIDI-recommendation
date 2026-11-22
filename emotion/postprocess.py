"""Finalize Task D from complete score caches; never reads private lyric text."""
import csv
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from aggregate_scores import aggregate_section, index_sections, is_number
from guards import verify
from feature_explanations import build_features, fit as fit_explanations
from stat_methods import correlation, profile, repeat_test, shuffle_control, standardization, z
from stems import songs as registered_songs

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
COMMIT=(HERE/'validation'/'plan_commit.txt').read_text(encoding='utf-8').strip()
FIELDS={
 'T1':('text_scores','T1'),'T2_valence':('text_scores','T2_valence'),'T2_arousal':('text_scores','T2_arousal'),
 'M1_valence':('audio_models','m1','scores','M1_valence','mean'),
 'M1_arousal':('audio_models','m1','scores','M1_arousal','mean'),
 'M1_full_mix_valence':('audio_models','m1','scores','M1_full_mix_valence','mean'),
 'M1_full_mix_arousal':('audio_models','m1','scores','M1_full_mix_arousal','mean'),
 'M2_valence':('audio_models','m2','scores','M2_valence','mean'),
 'M2_arousal':('audio_models','m2','scores','M2_arousal','mean'),
 'V1_valence':('audio_models','voice','scores','V1_valence','mean'),
 'V1_arousal':('audio_models','voice','scores','V1_arousal','mean')}
PAIRS={'text_valence':('T1','T2_valence'),'background_valence':('M1_valence','M2_valence'),
 'background_arousal':('M1_arousal','M2_arousal'),'voice_valence':('V1_valence','V2_valence'),
 'voice_arousal':('V1_arousal','V2_arousal')}

def load(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def save(path,value):
 p=Path(path).resolve()
 if not p.is_relative_to(HERE.resolve()): raise ValueError('Output escaped emotion/')
 p.parent.mkdir(parents=True,exist_ok=True); tmp=p.with_suffix(p.suffix+'.tmp')
 tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8'); tmp.replace(p)
def get(v,path):
 for k in path:
  if not isinstance(v,dict): return None
  v=v.get(k)
 return v
def cache(track,cohort,stage,instrumental=False):
 p=HERE/'scratch'/stage/f'{track}.json'
 if not p.is_file():
  if stage=='voice' and instrumental: return None
  raise FileNotFoundError(f'Missing {stage} cache: {track}')
 d=load(p)
 if d.get('status') not in (None,'complete') or d.get('track_id')!=track or d.get('plan_commit')!=COMMIT:
  raise ValueError(f'Unverified {stage} cache for {track}')
 return d
def feature_values(section): return {k:get(section,p) for k,p in FIELDS.items()}

def load_all(summary):
 audio={s['track_id']:s for s in registered_songs()}
 if set(audio)!={s['track_id'] for s in summary}: raise ValueError('Cohort/audio manifest mismatch')
 output={}
 for song in summary:
  track,cohort=song['track_id'],song['cohort']; folder=ROOT/('outputs' if cohort=='random' else 'mvp_325/songs')/track
  prepared=load(HERE/'validation'/'input_sections'/f'{track}.json')
  if prepared.get('track_id')!=track or prepared.get('cohort')!=cohort: raise ValueError('Input identity mismatch')
  instrumental=song.get('ring1_source')=='instrumental'
  caches={stage:cache(track,cohort,stage,instrumental) for stage in ('text','m1','m2','voice')}
  indexed={stage:index_sections(payload,track,cohort,COMMIT) for stage,payload in caches.items() if payload}
  public_sections=prepared.get('sections') or []
  if song.get('ring1_source')=='instrumental' and not public_sections:
   sep=load(HERE/'stems'/track/'separation.json'); duration=sep.get('audio_duration_s')
   if not is_number(duration): raise ValueError(f'Missing instrumental duration: {track}')
   public_sections=[{'track_id':track,'title':song['title'],'cohort':cohort,'language':song.get('language'),
     'section_id':'instrumental-whole','label':'Instrumental','display_label':'Instrumental','start_s':0.,
     'end_s':duration,'set':'instrumental','validated':False,'line_count':0,'text_sha256':None}]
  sections=[]; curves={}
  for pub in public_sections:
   sid=pub['section_id']; inputs={stage:indexed.get(stage,{}).get(sid) for stage in ('text','m1','m2','voice')}
   row,curve=aggregate_section(pub,inputs['text'],{'m1':inputs['m1'],'m2':inputs['m2'],'voice':inputs['voice']})
   row['scores_raw']=feature_values(row); row['model_z']={}; sections.append(row); curves[sid]=curve
  output[track]={'song':{**song,'audio_path':audio[track]['audio_path']},'sections':sections,'curves':curves,
                 'voice_track_present':(caches.get('voice') or {}).get('voice_track_present',False)}
 return output

def add_v2(sections):
 names=('f0_median_hz','f0_range_semitones','rms_mean','rms_variance','brightness_hz','voice_note_onset_rate_hz')
 raw=defaultdict(list)
 for item in sections.values():
  if item['song']['cohort']!='random': continue
  for s in item['sections']:
   if s.get('set')!='primary': continue
   for name in names:
    rec=s['audio_models']['voice'].get('prosody',{}).get(name,{})
    raw[name].append(rec.get('mean') if rec.get('available_frames') else None)
 params={name:standardization(vals) for name,vals in raw.items()}
 for item in sections.values():
  for s in item['sections']:
   p=s['audio_models']['voice'].get('prosody',{}); zv={}
   for name in names:
    rec=p.get(name,{})
    zv[name]=z(rec.get('mean') if rec.get('available_frames') else None,params[name])
   vp=[zv.get('brightness_hz'),zv.get('f0_median_hz'),-zv['rms_variance'] if is_number(zv.get('rms_variance')) else None]
   ap=[zv.get(x) for x in ('rms_mean','rms_variance','f0_range_semitones','brightness_hz','voice_note_onset_rate_hz')]
   s['scores_raw']['V2_valence']=statistics.fmean(vp) if all(is_number(x) for x in vp) else None
   s['scores_raw']['V2_arousal']=statistics.fmean(ap) if all(is_number(x) for x in ap) else None
   s['model_z'].update({'V2_'+k:v for k,v in zv.items()})
 return params

def normalize(sections):
 params={}
 for name in FIELDS:
  vals=[s['scores_raw'].get(name) for item in sections.values() if item['song']['cohort']=='random'
        for s in item['sections'] if s.get('set')=='primary']
  params[name]=standardization(vals)
  for item in sections.values():
   for s in item['sections']: s['model_z'][name]=z(s['scores_raw'].get(name),params[name])
 for name in ('V2_valence','V2_arousal'):
  vals=[s['scores_raw'].get(name) for item in sections.values() if item['song']['cohort']=='random'
        for s in item['sections'] if s.get('set')=='primary']
  params[name]=standardization(vals)
  for item in sections.values():
   for s in item['sections']: s['model_z'][name]=z(s['scores_raw'].get(name),params[name])
 for dimension,pair in PAIRS.items():
  for item in sections.values():
   for s in item['sections']:
    vals=[s['model_z'].get(x) for x in pair]
    s['model_z'][dimension+'_consensus']=statistics.fmean(vals) if all(is_number(x) for x in vals) else None
    s['model_z'][dimension+'_agreement_abs']=abs(vals[0]-vals[1]) if all(is_number(x) for x in vals) else None
 return params

def repeat_rows(sections,dimension):
 return [{'track_id':item['song']['track_id'],'label':s.get('label'),'score':s['model_z'].get(dimension+'_consensus')}
  for item in sections.values() if item['song']['cohort']=='random' for s in item['sections'] if s.get('set')=='primary']
def trusts(sections):
 output={}
 for dimension,pair in PAIRS.items():
  rows=[s for item in sections.values() if item['song']['cohort']=='random' for s in item['sections']
        if s.get('set')=='primary' and all(is_number(s['model_z'].get(x)) for x in pair)]
  rho=correlation([(r['model_z'].get(pair[0]),r['model_z'].get(pair[1])) for r in rows])
  bylang={}
  for lang in sorted({r.get('language') for r in rows if r.get('language')}):
   bylang[lang]=correlation([(r['model_z'].get(pair[0]),r['model_z'].get(pair[1])) for r in rows if r.get('language')==lang])
  repeat=repeat_test(repeat_rows(sections,dimension),10000,20261112)
  ok=rho.get('rho') is not None and rho['rho']>=.4 and repeat.get('p') is not None and repeat['p']<.01
  output[dimension]={'models':pair,'rho':rho,'rho_by_language':bylang,'repeat_consistency':repeat,'trusted':bool(ok),
    'verdict':'trusted' if ok else 'not trusted / not evaluable','n':len(rows)}
 return output

def relation(a,b):
 if not is_number(a) or not is_number(b): return None
 if abs(a)>=.5 and abs(b)>=.5: return 'supports' if a*b>0 else 'contradicts'
 return 'neutral/ambiguous'
def coverage(s,ch):
 if ch=='text': return 1. if s.get('text_sha256') else 0.
 model_names=('m1','m2') if ch=='background' else ('voice',); values=[]
 for name in model_names:
  scores=s.get('audio_models',{}).get(name,{}).get('scores',{})
  shares=[v.get('frame_share') for v in scores.values() if is_number(v.get('frame_share'))]
  if shares: values.append(min(shares))
 return min(values) if values else 0.

def relations(sections,trust):
 pairs={'words_background':('text_valence','background_valence','text','background'),
        'words_voice':('text_valence','voice_valence','text','voice'),
        'voice_background':('voice_valence','background_valence','voice','background'),
        'voice_background_arousal':('voice_arousal','background_arousal','voice','background')}
 counts=defaultdict(Counter); profiles={}
 for item in sections.values():
  p_rows=[]
  for s in item['sections']:
   rs={}
   for key,(ld,rd,lc,rc) in pairs.items():
    if trust[ld]['trusted'] and trust[rd]['trusted']:
     a=s['model_z'].get(ld+'_consensus'); b=s['model_z'].get(rd+'_consensus'); label=relation(a,b)
     dis=[s['model_z'].get(ld+'_agreement_abs'),s['model_z'].get(rd+'_agreement_abs')]
     dis=[x for x in dis if is_number(x)]
     conf=min(coverage(s,lc),coverage(s,rc))*math.exp(-statistics.fmean(dis)) if label and dis else None
     rs[key]={'relation':label,'left_z':a,'right_z':b,'delta_z':a-b if is_number(a) and is_number(b) else None,'confidence':conf}
    else: rs[key]={'relation':None,'status':'experimental_only_untrusted_channel'}
   s['relations']=rs
   if s.get('set')=='primary' and rs['words_background'].get('relation'):
    p_rows.append((rs['words_background']['relation'],s['end_s']-s['start_s']))
   if s.get('set')=='primary':
    for pair,r in rs.items():
     if r.get('relation'):
      for kind,keyval in [('section_label',s.get('label') or 'Unlabeled'),('language',s.get('language') or 'missing'),
                          ('ring1_source',item['song'].get('ring1_source') or 'missing')]:
       counts[f"{item['song']['cohort']}:{pair}:{kind}:{keyval}"][r['relation']]+=1
  song=item['song']
  item['song_profile']=({'profile':'instrumental_only','n':0,'shares':None} if song.get('ring1_source')=='instrumental'
                        else profile(p_rows))
 for cohort in ('random','pilot','combined'):
  selected=[i for i in sections.values() if cohort=='combined' or i['song']['cohort']==cohort]
  profiles[cohort]=dict(Counter(i['song_profile']['profile'] for i in selected))
 return profiles,{k:dict(v) for k,v in counts.items()}

def separation(sections):
 out={}
 for dim in ('valence','arousal'):
  vals=[]
  for i in sections.values():
   if i['song']['cohort']!='random': continue
   for s in i['sections']:
    if s.get('set')=='primary':
     a=s['model_z'].get('M1_'+dim); b=s['model_z'].get('M1_full_mix_'+dim)
     if is_number(a) and is_number(b): vals.append((a,b))
  out[dim]=correlation(vals)
 return out

def normalize_curves(sections,section_params):
 # Frame values use the section-based parameters registered in the plan.
 frame_params={name:section_params.get(name) for name in FIELDS}
 for item in sections.values():
  for sid,frames in item['curves'].items():
   sec=next(s for s in item['sections'] if s['section_id']==sid)
   for frame in frames:
    for name in FIELDS: frame[name+'_z']=z(frame.get(name),frame_params[name])
    for name in ('T1','T2_valence','T2_arousal'):
     frame[name]=sec['scores_raw'].get(name); frame[name+'_z']=sec['model_z'].get(name)
 return frame_params

def csvwrite(path,rows):
 path.parent.mkdir(parents=True,exist_ok=True)
 if not rows: path.write_text('',encoding='utf-8'); return
 keys=list(dict.fromkeys(k for r in rows for k in r))
 with path.open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=keys); w.writeheader()
  for r in rows: w.writerow({k:json.dumps(v,ensure_ascii=False,allow_nan=False) if isinstance(v,(dict,list)) else v for k,v in r.items()})

def human_anchor_filled():
 rows=list(csv.DictReader((HERE/'HUMAN_ANCHOR.csv').open(encoding='utf-8-sig',newline='')))
 return any(r.get('my_relation(supports/neutral/contradicts)') or r.get('my_confidence_1to3') for r in rows)

def evaluate_human_anchor(sections):
 from sklearn.metrics import cohen_kappa_score
 rows=list(csv.DictReader((HERE/'HUMAN_ANCHOR.csv').open(encoding='utf-8-sig',newline='')))
 filled=[r for r in rows if r.get('my_relation(supports/neutral/contradicts)','').strip()]
 human=[]; model=[]; unmatched=0
 def timestamp(seconds):
  value=int(round(seconds)); return f'{value//60}:{value%60:02d}'
 for row in filled:
  item=sections.get(row['track_id']); candidates=[]
  if item:
   candidates=[s for s in item['sections'] if s.get('set')=='primary' and is_number(s.get('start_s'))
     and is_number(s.get('end_s')) and timestamp(s['start_s'])==row.get('start_mmss')
     and timestamp(s['end_s'])==row.get('end_mmss')
     and (s.get('display_label') or s.get('label'))==row.get('section_label')]
  expected=row['my_relation(supports/neutral/contradicts)'].strip().lower()
  predicted=candidates[0].get('relations',{}).get('words_background',{}).get('relation') if len(candidates)==1 else None
  if predicted=='neutral/ambiguous': predicted='neutral'
  if expected in ('supports','neutral','contradicts') and predicted in ('supports','neutral','contradicts'):
   human.append(expected);model.append(predicted)
  else: unmatched+=1
 kappa=float(cohen_kappa_score(human,model)) if len(human)>=2 else None
 if kappa is not None and not math.isfinite(kappa): kappa=None
 return {'filled_rows':len(filled),'matched_with_trusted_prediction':len(human),'unmatched_or_unavailable':unmatched,
         'cohens_kappa':kappa,'status':'no human labels yet' if not filled else ('evaluated' if kappa is not None else 'not evaluable')}

def write_schema():
 text='''# Task D output schema

All time values are seconds; z-values are unitless. Missing or unsupported scores are `null`, not zero. Lyric content is private and omitted; public outputs contain line counts and text SHA-256 hashes. Cohort is carried on every song, section and frame.

- `songs/<id>/sections.json`: one row per validated/secondary lyric span (or `instrumental-whole`); raw scores, normalized model scores, channel means/medians/standard deviations, frame coverage, relationships, confidence and top model-rating reasons.
- `songs/<id>/curves.parquet`: 4 s windows at 1 s hop; start/end seconds, frame scores, coverage, active-voice flag and prosody. Text scores are section-level and repeated across section windows.
- `songs/<id>/song.json`: cohort, language, ring-1 source, profile and plan commit.
- `sections.csv`: flattened section-level results; nested values are JSON strings. No lyric text.
- `songs.csv`: one row per song with duration-weighted profile shares.
- `validation/`: per-model normalization, model agreement by language, repeat consistency, channel trust, shuffle control, M1 separation check and profile/relation counts.
- `explanations/model_fits.json`: five-fold grouped-by-song Ridge fits (alpha 1) and within-test-fold permutation importance. Explanations describe model ratings, not ground truth or causation.

Normalization uses the mean and population standard deviation across available primary random-300 sections, separately per channel/model/dimension. `frame_share` is finite scored windows divided by valid possible windows. For each relation, confidence equals minimum channel frame coverage times the exponential of negative mean within-channel model disagreement. Text coverage is present only when attributable text is available.
'''
 (HERE/'SCHEMA.md').write_text(text,encoding='utf-8')

def write_report(sections,trust,profiles,separation_result,control,fits,integrity,gpu_hours,omitted_count):
 random=Counter(i['song_profile']['profile'] for i in sections.values() if i['song']['cohort']=='random')
 pilot=Counter(i['song_profile']['profile'] for i in sections.values() if i['song']['cohort']=='pilot')
 section_counts=Counter((i['song']['cohort'],s.get('set')) for i in sections.values() for s in i['sections'])
 failures=[]
 for name,result in trust.items():
  repeat=result['repeat_consistency']
  failures.append(f"| {name} | {result['verdict']} | {result['rho'].get('rho')} (n={result['rho'].get('n')}) | {repeat.get('difference')} | {repeat.get('p')} |")
 top=[]
 ablation=load(HERE/'validation'/'ablation.json')
 input_coverage=load(HERE/'validation'/'input_coverage.json')
 for target,result in fits['targets'].items():
  features=', '.join(x['feature'] for x in result.get('permutation_importance',[])[:3]) or 'not measured'
  top.append(f"- **{target}:** {result.get('status')}; grouped CV R²={result.get('grouped_cv_r2')}; leading features: {features}.")
 report=[
 '# Task D: Words, voice and background emotion congruence','',
 f'Preregistered plan and pinned model manifest commit: `{COMMIT}`.','',
 '## Trust verdicts and human-validation status','',
 '**No human raters exist for this analysis.** Emotion scores come from pretrained models; internal checks are model-based, not ground truth. Some model training used human-labeled data. Singing, lyric poetry, multilingual shifts and source-separation artifacts may be out of distribution.','',
 '| Channel/dimension | Verdict | Spearman ρ (n) | Repeat effect | Repeat p |','|---|---|---:|---:|---:|',*failures,'',
 f"The optional human anchor: {load(HERE/'validation'/'human_anchor_agreement.json')['status']}; matched n={load(HERE/'validation'/'human_anchor_agreement.json')['matched_with_trusted_prediction']}, Cohen's κ={load(HERE/'validation'/'human_anchor_agreement.json')['cohens_kappa']}. A trusted channel requires both ρ ≥ 0.40 and same-label repeat consistency p < 0.01.",'',
 '## Main results','',
 f"Random 300 profiles: `{dict(random)}`. Pilot 25 profiles (not a random sample): `{dict(pilot)}`. Combined cohort: `{profiles.get('combined')}`. Section counts by cohort/set: `{dict(section_counts)}`. Profile shares are duration-weighted, and categories require trusted words and background valence; otherwise profile is unavailable. Instrumental tracks are reported separately.",'',
 f"Words↔background shuffle control: n={control.get('n')}, observed supports-minus-contradictions={control.get('observed')}, random-other-song null mean={control.get('null_mean')}, two-sided p={control.get('p')}. The control is {'exploratory because one or both channels are untrusted' if not (trust['text_valence']['trusted'] and trust['background_valence']['trusted']) else 'primary'}. Nonsignificance means the method has not shown a relationship beyond this same-language shuffle.",'',
 f"The text-only ablation is {ablation.get('status')}; qualifying sections={ablation.get('qualifying_sections')}, share disagreeing with the two audio channels={ablation.get('wrong_relative_to_audio_share')}. This compares models, not errors against human labels.",'',
 f"M1 instrumental↔full-mix check: valence ρ={separation_result['valence'].get('rho')} (n={separation_result['valence'].get('n')}), arousal ρ={separation_result['arousal'].get('rho')} (n={separation_result['arousal'].get('n')}). High correlation means vocals barely change M1's estimate.",'',
 'The five selected known-answer probes and the alternate 24K Magic candidate were not present locally; `validation/missing_probes.csv` lists them. No probe scores are reported. Text arousal is low-confidence and has no two-model text-arousal consensus because T1 has no arousal output; words↔audio arousal congruence was not measured.','',
 '## What was measured','',
 'Words scores summarize attributable lyric sections; voice scores model vocal delivery and a fixed prosody composite; background scores come from the separated instrumental. This mirrors conversation analysis of what is said, how it is said, and the surrounding context. No lyric text is included in these public outputs.','',
 '## What explains the model ratings','',*top,'',
 'Feature importance and section reasons explain pretrained model ratings, not ground truth or causal effects. Untrusted-channel fits are not used as primary results.','',
 '## Coverage, exclusions and integrity','',
 f"All 325 cohort songs have text, separation, M1 and M2 score caches; voice has 306 caches, with 19 instrumental songs excluded. Of the primary sections, {input_coverage['all'].get('primary_without_text',0)} lack attributable lyric text. The emotion audit excluded {omitted_count} zero-length, zero-coverage windows with no score, caused by floating-point section-end rounding. Missing/unsupported score values remain null. Missing backend feature sources are listed in `validation/feature_source_availability.json`.",'',
 f"Snapshot integrity at end: v1 {integrity['v1']['unchanged']}/{integrity['v1']['total']} unchanged; MVP-325 {integrity['mvp_325']['unchanged']}/{integrity['mvp_325']['total']} unchanged.",'',
 f"The sum of recorded per-song wall times for GPU-using stages (Demucs, CLAP, voice model) is {gpu_hours:.3f} hours. It includes overhead and overlapping jobs; GPU utilization and elapsed end-to-end wall time were not measured. CPU stages run separately or concurrently. Stem model license is flagged scientific-use/noncommercial in `models.json`.",'',
 '## Output files','',
 '`SCHEMA.md`, `models.json`, `sections.csv`, `songs.csv`, `songs/<id>/sections.json`, `songs/<id>/curves.parquet`, `songs/<id>/song.json`, `validation/`, and `explanations/`.','']
 (HERE/'REPORT.md').write_text('\n'.join(report),encoding='utf-8')

def run():
 if sys.executable!='/home/usr/venvs/midi/bin/python': raise RuntimeError(f'Wrong interpreter {sys.executable}')
 verify(['T1','T2','M1','M2','V1','V2','source_separation'])
 if (HERE/'REPORT.md').exists(): raise FileExistsError('REPORT.md exists; refusing overwrite')
 if (HERE/'validation/integrity_end.json').exists(): raise FileExistsError('End audit exists; inspect before rerun')
 summary=list(csv.DictReader((ROOT/'mvp_325/summary.csv').open(encoding='utf-8-sig',newline='')))
 if len(summary)!=325 or len({s['track_id'] for s in summary})!=325: raise ValueError('Expected 325 unique cohort rows')
 language_rows=list(csv.DictReader((ROOT/'analysis_v2/language.csv').open(encoding='utf-8-sig',newline='')))
 language_by_song={row['track_id']:row.get('language') or None for row in language_rows}
 for s in summary:
  s['language']=language_by_song.get(s['track_id'])
 sections=load_all(summary)
 v2_params=add_v2(sections); norm=normalize(sections); trust=trusts(sections)
 distribution,breakdown=relations(sections,trust); sep=separation(sections)
 frame_params=normalize_curves(sections,norm)
 feature_missing=build_features(sections)
 explanations=fit_explanations(sections,trust)
 rows_song=[]; rows_section=[]; distributions=[]
 for track,item in sections.items():
  song=item['song']; songdir=HERE/'songs'/track; songdir.mkdir(parents=True,exist_ok=True); safe_sections=[]
  for s in item['sections']:
   clean={k:v for k,v in s.items() if k!='scores_raw'}
   clean['top_reasons']={target:explanations['section_reasons'].get(f'{track}:{s["section_id"]}:{target}',[])
                         for target in explanations['targets']}
   safe_sections.append(clean)
   rows_section.append({'track_id':track,'title':song['title'],'cohort':song['cohort'],'language':song.get('language'),
    'ring1_source':song.get('ring1_source'),'section_id':s['section_id'],'label':s.get('label'),'display_label':s.get('display_label'),
    'set':s.get('set'),'validated':s.get('validated'),'start_s':s.get('start_s'),'end_s':s.get('end_s'),
    'line_count':s.get('line_count'),'text_sha256':s.get('text_sha256'),'text_source':s.get('text_source'),
    'scores_raw':s['scores_raw'],'model_z':s['model_z'],'relations':s.get('relations'),
    'features':s.get('features'),'top_reasons':clean['top_reasons']})
  save(songdir/'sections.json',{'track_id':track,'title':song['title'],'cohort':song['cohort'],'language':song.get('language'),
    'plan_commit':COMMIT,'sections':safe_sections})
  frames=[f for fs in item['curves'].values() for f in fs]
  df=pd.DataFrame(frames)
  if len(df): df=df.sort_values(['section_id','start_s'])
  df.to_parquet(songdir/'curves.parquet',index=False,engine='pyarrow')
  p=item['song_profile']; rows_song.append({'track_id':track,'title':song['title'],'cohort':song['cohort'],
   'language':song.get('language'),'ring1_source':song.get('ring1_source'),'profile':p['profile'],
   'scored_sections':p['n'],'duration_weighted_shares':p['shares']})
  distributions.append({'track_id':track,'title':song['title'],'cohort':song['cohort'],'language':song.get('language'),
   'ring1_source':song.get('ring1_source'),'profile':p['profile'],'scored_sections':p['n'],'duration_weighted_shares':p['shares']})
  save(songdir/'song.json',{'track_id':track,'title':song['title'],'cohort':song['cohort'],'language':song.get('language'),
   'ring1_source':song.get('ring1_source'),'profile':p,'plan_commit':COMMIT,
   'strongest_contrast':max((s for s in item['sections']
     if s.get('relations',{}).get('words_background',{}).get('relation')=='contradicts'),
     key=lambda s:abs(s['relations']['words_background'].get('delta_z') or 0),default=None)})
 csvwrite(HERE/'sections.csv',rows_section); csvwrite(HERE/'songs.csv',rows_song)
 exploratory=[]
 for track,item in sections.items():
  for s in item['sections']:
   if s.get('set')!='primary': continue
   w=s['model_z'].get('text_valence_consensus'); b=s['model_z'].get('background_valence_consensus')
   v=s['model_z'].get('voice_valence_consensus')
   exploratory.append({'track_id':track,'cohort':item['song']['cohort'],'language':s.get('language'),
    'section_id':s['section_id'],'label':s.get('label'),'words_z':w,'background_z':b,'voice_z':v,
    'words_background_relation':relation(w,b),'words_voice_relation':relation(w,v),
    'voice_background_relation':relation(v,b),'status':'experimental; assess trust before interpretation'})
 csvwrite(HERE/'validation'/'exploratory_relations.csv',exploratory)
 for name,data in [('normalization',norm),('trust',trust),('profile_distribution',distribution),('relation_breakdown',breakdown),('separation_check',sep)]:
  save(HERE/'validation'/f'{name}.json',data)
 save(HERE/'explanations'/'model_fits.json',explanations)
 save(HERE/'explanations'/'feature_manifest.json',{'features':'tempo, instrument onset density, off-strong-beat share, pitch register, Krumhansl key/mode strength, pattern compression/repetition, chord-layer rate, instrumental RMS/brightness, vocal prosody',
  'note':'Features and contributions explain pretrained model ratings; they do not establish ground truth or causal effects.'})
 save(HERE/'validation'/'feature_source_availability.json',{'missing_by_stage':feature_missing,
  'note':'Missing stage-derived features remain unavailable; no values are fabricated.'})
 save(HERE/'validation'/'language_missing.json',{'source':'analysis_v2/language.csv',
  'songs':[{'track_id':s['track_id'],'cohort':s['cohort']} for s in summary if not s.get('language')]})
 omitted=[{'track_id':track,'section_id':s['section_id'],'model':model,'count':entry['excluded_zero_length_frames']}
  for track,item in sections.items() for s in item['sections'] for model,entry in s['audio_models'].items()
  if entry.get('excluded_zero_length_frames')]
 save(HERE/'validation'/'frame_omissions.json',{'reason':'Zero-duration, zero-coverage windows with no scores; floating-point boundary artifact',
  'excluded_frames':sum(x['count'] for x in omitted),'records':omitted})
 save(HERE/'validation'/'normalization.json',{'section_model':norm,'v2_features':v2_params,'frame_curve':frame_params})
 eligible=[{'track_id':i['song']['track_id'],'language':s.get('language'),
   'words':s['model_z'].get('text_valence_consensus'),
   'background':s['model_z'].get('background_valence_consensus')}
   for i in sections.values() if i['song']['cohort']=='random' for s in i['sections']
   if s.get('set')=='primary' and is_number(s['model_z'].get('text_valence_consensus'))
   and is_number(s['model_z'].get('background_valence_consensus'))]
 control=shuffle_control(eligible,1000,20261113) if eligible else {'n':0,'p':None}
 control['status']='primary' if trust['text_valence']['trusted'] and trust['background_valence']['trusted'] else 'experimental: untrusted channel'
 save(HERE/'validation'/'shuffle_control.json',control)
 if all(trust[k]['trusted'] for k in ('text_valence','voice_valence','background_valence')):
  qualified=[]
  for item in sections.values():
   if item['song']['cohort']!='random': continue
   for s in item['sections']:
    if s.get('set')!='primary': continue
    w=s['model_z'].get('text_valence_consensus'); v=s['model_z'].get('voice_valence_consensus'); b=s['model_z'].get('background_valence_consensus')
    if all(is_number(x) for x in (w,v,b)) and abs(v)>=.5 and abs(b)>=.5 and v*b>0:
     qualified.append({'track_id':item['song']['track_id'],'wrong_relative_to_audio':abs(w)>=.5 and w*v<0})
  bysong=defaultdict(list)
  for row in qualified: bysong[row['track_id']].append(row['wrong_relative_to_audio'])
  ablation={'status':'primary','qualifying_sections':len(qualified),
   'wrong_relative_to_audio_share':statistics.fmean(x['wrong_relative_to_audio'] for x in qualified) if qualified else None,
   'song_weighted_share':statistics.fmean(statistics.fmean(v) for v in bysong.values()) if bysong else None}
 else: ablation={'status':'unavailable: words, voice, or background valence untrusted','qualifying_sections':None,'wrong_relative_to_audio_share':None}
 save(HERE/'validation'/'ablation.json',ablation)
 save(HERE/'validation'/'human_anchor_agreement.json',evaluate_human_anchor(sections))
 write_schema()
 # Accumulated GPU compute from per-song device timings. Text and M1 stages ran on CPU.
 gpu_seconds=0.0
 for track in sections:
  for directory,name in ((HERE/'scratch'/'m2',f'{track}.json'),(HERE/'scratch'/'voice',f'{track}.json'),
                         (HERE/'stems'/track,'separation.json')):
   file=directory/name
   if file.is_file():
    timed=load(file).get('wall_seconds')
    if is_number(timed): gpu_seconds+=timed
 import audit
 if not audit.integrity('end'): raise RuntimeError('End-of-run snapshot verification failed')
 integrity=load(HERE/'validation'/'integrity_end.json')
 omitted_count=sum(r['excluded_zero_length_frames'] for track,item in sections.items() for s in item['sections']
                   for r in s['audio_models'].values() if r.get('excluded_zero_length_frames'))
 write_report(sections,trust,distribution,sep,control,explanations,integrity,gpu_seconds/3600,omitted_count)
 size=sum(p.stat().st_size for p in HERE.rglob('*') if p.is_file())
 save(HERE/'validation'/'output_size.json',{'bytes':size,'megabytes_decimal':size/1_000_000})
 return sections,trust,distribution,sep,control

if __name__=='__main__':
 result=run(); print(json.dumps({'songs':len(result[0]),'trust':{k:v['trusted'] for k,v in result[1].items()},
  'profiles':result[2],'separation_check':result[3],'shuffle':result[4]},indent=2))
