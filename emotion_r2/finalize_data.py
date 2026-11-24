"""Finalize cached Task F data and requested diagnostics, without model inference."""
import collections,csv,hashlib,json,math,sys
from pathlib import Path
if sys.executable!='/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'emotion'))
import numpy as np
import pandas as pd
from stat_methods import correlation,standardization,z
from explanation_repair import family_features,cross_validate

def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def save(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
def finite(x):return isinstance(x,(int,float,np.number)) and math.isfinite(x)
def csvout(p,rows,fields=None):
    p.parent.mkdir(parents=True,exist_ok=True)
    fields=fields or sorted(set().union(*(r.keys() for r in rows)))
    with p.open('w',encoding='utf8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for row in rows:w.writerow({k:json.dumps(v,ensure_ascii=False,allow_nan=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})

def gate_audit(songs,rows):
    indexed=collections.defaultdict(dict)
    for r in rows:indexed[r['track_id']][r['section_id']]=r
    result=[];section_records=[];grouped={k:collections.Counter() for k in ('random','pilot','combined')}
    for song in songs:
        tid=song['track_id'];p=HERE/'scores/voice_gate'/f'{tid}.json'
        counts=collections.Counter();sections=[];lyric_sections=[]
        if p.is_file():
            for sec in read(p)['sections']:
                source=indexed[tid].get(sec['section_id'],{})
                if sec['section_id'].startswith('lyric-'):
                    frames=sec['frames'];active=sum(f['active'] for f in frames)
                    entry={'track_id':tid,'title':song['title'],'cohort':song['cohort'],'section_id':sec['section_id'],'set':source.get('set'),
                           'windows':len(frames),'active_windows':active,'active_share':active/len(frames) if frames else None}
                    lyric_sections.append(entry);section_records.append(entry)
                if source.get('set')!='primary':continue
                frames=sec['frames']
                for frame in frames:
                    gate=frame['active'];note=frame['muscriptor_voice_note_active']
                    counts['both_active' if gate and note else 'detector_only' if gate else 'transcription_only' if note else 'neither']+=1
                sections.append({'section_id':sec['section_id'],'windows':len(frames),'active_windows':sum(f['active'] for f in frames)})
        total=sum(counts.values());timed=[s for s in sections if s['windows']]
        absent=sum(s['active_windows']==0 for s in timed)
        record={'track_id':tid,'title':song['title'],'cohort':song['cohort'],
                'status':'instrumental_not_applicable' if song.get('ring1_source')=='instrumental' else 'available' if p.is_file() else 'gate_unavailable',
                **{k:counts[k] for k in ('both_active','detector_only','transcription_only','neither')},
                'windows':total,'agreement':(counts['both_active']+counts['neither'])/total if total else None,
                'primary_sections':len(sections),'timed_primary_sections':len(timed),'sections_without_active_windows':absent,
                'no_active_section_share':absent/len(timed) if timed else None,
                'majority_sections_without_active_windows':bool(timed and absent>len(timed)/2),
                'detector_only_share':counts['detector_only']/total if total else None,
                'transcription_only_share':counts['transcription_only']/total if total else None}
        timed_lyrics=[s for s in lyric_sections if s['windows']]
        record.update(timed_lyric_sections=len(timed_lyrics),zero_active_lyric_sections=sum(s['active_windows']==0 for s in timed_lyrics),
                      lyric_sections_with_majority_inactive_windows=sum(s['active_windows']<s['windows']/2 for s in timed_lyrics))
        result.append(record)
        for cohort in (song['cohort'],'combined'):grouped[cohort].update(counts)
    overall={}
    for cohort,counts in grouped.items():
        total=sum(counts.values());overall[cohort]={**dict(counts),'windows':total,'agreement':(counts['both_active']+counts['neither'])/total if total else None,
                  'detector_only_share':counts['detector_only']/total if total else None,'transcription_only_share':counts['transcription_only']/total if total else None}
    output={'definition':'Primary lyric-section windows; overlapping windows are not independent. Detector-only is pYIN active and MuScriptor inactive; transcription-only is the reverse. Neither is human truth.',
            'majority_definition':'Strictly more than half of timed primary lyric sections contain zero pYIN-active windows; this describes the detector, not absence of singing.',
            'overall':overall,'seeded_ten':read(HERE/'voice_gate_validation.json'),'songs':result}
    save(HERE/'validation/voice_gate_audit.json',output);csvout(HERE/'validation/voice_gate_audit.csv',result)
    csvout(HERE/'validation/voice_gate_sections.csv',section_records)
    csvout(HERE/'validation/voice_gate_majority_misses.csv',[s for s in result if s['majority_sections_without_active_windows']])
    return output

def acoustic_audit(rows):
    subset=[r for r in rows if r['cohort']=='random' and r['set']=='primary'];out={}
    for role in ('V1','V3'):
        out[role]={}
        for dim in ('arousal','valence'):
            out[role][dim]={feature:correlation([(r['raw'].get(role+'_'+dim),r['features'].get(feature)) for r in subset])
                           for feature in ('voice_brightness_hz','voice_f0_median_hz','voice_rms_mean')}
    result={'analysis':'Post hoc descriptive acoustic diagnostic; pairwise-complete random primary sections, no new trust decision or gate change.',
            'feature_source':'Round-one section prosody features. Feature aggregation is not restricted to the new pYIN-selected model windows; unequal frame selection limits interpretation.',
            'correlations':out,'interpretation':'Agreement may reflect shared responses to acoustics rather than emotion. These correlations cannot establish or rule out that shortcut; no human emotion ground truth is supplied.'}
    save(HERE/'validation/voice_acoustic_correlations.json',result);return result

def explanation_checks(rows):
    existing=read(HERE/'validation/explanations.json');out={};foldrows=[];importance=[]
    for channel,data in existing.items():
        if data.get('status')!='completed':out[channel]=data;continue
        evaluation=data['full']
        if not evaluation['permutation_importance']:
            # Preserve the registered estimator/folds/seeds; retain diagnostic
            # importance even for R²<=0, without presenting any drivers.
            selected=[r for r in rows if r['cohort']=='random' and r['set']=='primary' and finite(r['consensus'].get(channel))]
            features=[]
            for r in selected:
                f=r['features'];fam,unknown=family_features(f)
                if unknown:raise ValueError('Unknown instruments '+str(unknown))
                features.append({**{k:v for k,v in f.items() if not k.startswith('onset_density__')},**fam})
            names=sorted(k for k,v in data['coverage'].items() if v>=.05)
            X=np.array([[f.get(k,np.nan) if finite(f.get(k)) else np.nan for k in names] for f in features])
            evaluation=cross_validate(X,np.array([r['consensus'][channel] for r in selected]),np.array([r['track_id'] for r in selected]),names,True,True)
            if abs(evaluation['oof_r2']-data['full']['oof_r2'])>1e-12:raise ValueError('Explanation reproduction changed R²')
        out[channel]={**data,'full':evaluation,'drivers_allowed':evaluation['oof_r2']>0,
                      'importance_note':'Retained for fold-stability audit only when R²<=0; not interpreted as drivers.'}
        for model,d in [('full',evaluation)]+list(data['single_feature_baselines'].items()):
            for f in d.get('folds',[]):foldrows.append({'channel':channel,'model':model,'oof_r2':d.get('oof_r2'),**f})
        for imp in evaluation['permutation_importance']:importance.append({'channel':channel,**imp})
    save(HERE/'validation/explanation_checks.json',out)
    csvout(HERE/'validation/explanation_folds.csv',foldrows);csvout(HERE/'validation/explanation_importance_stability.csv',importance)
    return out

def export_songs(songs,rows,gate):
    params=read(HERE/'validation/normalization.json');trust=read(HERE/'validation/trust.json')
    status={};by=collections.defaultdict(list)
    for row in rows:by[row['track_id']].append(row)
    old={}
    with (ROOT/'emotion/sections.csv').open(encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):old[(r['track_id'],r['section_id'])]=r
    for row in rows:
        r=old.get((row['track_id'],row['section_id']),{});raw=json.loads(r.get('scores_raw','{}'))
        same=r.get('text_sha256')==row.get('text_sha256') and r.get('language')==row.get('language') and bool(row.get('text_sha256'))
        row['references_raw']={'L1_arousal':row['raw'].get('L1_arousal'),'T2_arousal':raw.get('T2_arousal') if same else None,'T2_valence':raw.get('T2_valence') if same else None}
        row['reference_t2_status']='verified_same_text_hash_and_language' if same else 'missing_changed_or_absent_text_or_language'
    refparams={k:standardization([r['references_raw'][k] for r in rows if r['cohort']=='random' and r['set']=='primary']) for k in ('L1_arousal','T2_arousal','T2_valence')}
    save(HERE/'validation/reference_normalization.json',refparams)
    gates={g['track_id']:g for g in gate['songs']};summary=[]
    membership={'words_valence':['T1_valence','T3_valence','L1_valence'],'background_arousal':['M1_arousal','M2_arousal','M3_arousal'],
                'background_valence':['M1_valence','M2_valence','M3_valence'],'voice_valence':['V1_valence','V3_valence'],'voice_arousal':['V1_arousal','V3_arousal']}
    for i,song in enumerate(songs,1):
        tid=song['track_id'];sections=by[tid];sectionmap={r['section_id']:r for r in sections};folder=HERE/'songs'/tid;folder.mkdir(parents=True,exist_ok=True)
        for row in sections:
            row['channel_status']={ch:'PROVISIONAL' if ch.startswith('voice_') else 'trusted' if v['overall']['trusted'] else 'experimental_untrusted' for ch,v in trust.items()}
            row['references_z']={k:z(v,refparams[k]) for k,v in row['references_raw'].items()}
            row['relations']={'words_background':{'status':'not_available_background_valence_untrusted','relation':None}}
            row['confidence']=None;row['confidence_note']='Not calibrated; model counts and missingness are supplied instead.'
            row['top_reasons']=[];row['reasons_status']='Local section attributions not measured; global permutation importance is not a local explanation.'
            row['available_models']={ch:sum(finite(row['model_z'].get(m)) for m in members) for ch,members in membership.items()}
        frames={}
        def frame(sid,start,end):
            key=(sid,round(float(start),6))
            if key not in frames:frames[key]={'track_id':tid,'cohort':song['cohort'],'language':sectionmap[sid]['language'],'section_id':sid,'set':sectionmap[sid]['set'],'start_s':float(start),'end_s':float(end)}
            return frames[key]
        prior=ROOT/'emotion/songs'/tid/'curves.parquet'
        if prior.is_file():
            for r in pd.read_parquet(prior).to_dict('records'):
                sid=r['section_id']
                if sid not in sectionmap:continue
                end=r.get('m1_end_s') if finite(r.get('m1_end_s')) else r.get('m2_end_s')
                if not finite(end):continue
                out=frame(sid,r['start_s'],end)
                for key in ('M1_valence','M1_arousal','M2_valence','M2_arousal'):out[key]=r.get(key) if finite(r.get(key)) else None
        for role in ('M3','V1','V3'):
            payload=read(HERE/'scores'/role/f'{tid}.json')
            status.setdefault(role,collections.Counter()).update([payload.get('status','checkpoint')])
            for sec in payload.get('sections',[]):
                if sec['section_id'] not in sectionmap:raise ValueError('Unexpected section')
                for f in sec.get('frames',[]):
                    out=frame(sec['section_id'],f['start_s'],f['end_s'])
                    for dim in ('valence','arousal'):out[role+'_'+dim]=f.get(dim)
                    if role.startswith('V'):out[role+'_active']=f.get('active')
        output=[]
        for (sid,_),r in sorted(frames.items()):
            for channel,members in membership.items():
                values=[]
                for m in members:
                    value=sectionmap[sid]['raw'].get(m) if m.startswith(('T1_','T3_','L1_')) else r.get(m)
                    r[m+'_z']=z(value,params[m])
                    if finite(r[m+'_z']):values.append(r[m+'_z'])
                r[channel+'_consensus_z']=float(np.mean(values)) if values else None
                r[channel+'_models']=len(values)
            output.append(r)
        cols=['track_id','cohort','language','section_id','set','start_s','end_s']
        pd.DataFrame(output,columns=sorted(set(cols).union(*(r.keys() for r in output)))).to_parquet(folder/'curves.parquet',index=False)
        save(folder/'sections.json',{'track_id':tid,'cohort':song['cohort'],'sections':sections})
        primary=[r for r in sections if r['set']=='primary']
        meta={'track_id':tid,'title':song['title'],'cohort':song['cohort'],'language':sections[0]['language'] if sections else song.get('language'),
              'sampling_note':'random primary sample' if song['cohort']=='random' else 'not a random sample',
              'sections':len(sections),'primary_sections':len(primary),'has_registered_sections':bool(sections),
              'voice_gate':gates[tid],'channel_status':sections[0]['channel_status'] if sections else {},
              'relation_profile':None,'profile_status':'not run: background valence untrusted',
              'confidence':None,'top_reasons_status':'Local reasons not measured'}
        save(folder/'song.json',meta);summary.append({k:v for k,v in meta.items() if k!='voice_gate'})
        if i%50==0:print('Exported',i,'/325',flush=True)
    for role in ('T1','T3','L1'):
        top=collections.Counter();secstatus=collections.Counter();seconds=0.
        for song in songs:
            d=read(HERE/'scores'/role/(song['track_id']+'.json'));top[d.get('status','checkpoint')]+=1
            secstatus.update(r.get('status','missing') for r in d.get('sections',[]));seconds+=d.get('seconds',0)
        status[role]={'top':dict(top),'sections':dict(secstatus),'checkpoint_inference_seconds':seconds}
    save(HERE/'validation/model_inventory.json',{k:dict(v) for k,v in status.items()})
    csvout(HERE/'songs.csv',summary);csvout(HERE/'sections.csv',rows)
    return summary

def main():
    with (ROOT/'mvp_325/summary.csv').open(encoding='utf-8-sig',newline='') as f:songs=list(csv.DictReader(f))
    rows=read(HERE/'validation/sections_analysis.json')
    if len(songs)!=325 or {r['track_id'] for r in rows}- {s['track_id'] for s in songs}:raise ValueError('Scope mismatch')
    gate=gate_audit(songs,rows);acoustic_audit(rows);explanation_checks(rows);export_songs(songs,rows,gate)
    # Existing language/inferred analyses are immutable inputs; expose tidy tables.
    trust=read(HERE/'validation/trust.json');lang=[]
    for channel,v in trust.items():
        for language,d in v['by_language'].items():
            lang.append({'channel':channel,'language':language,'median_rho':d['median_rho'],'repeat_p':d['repeat']['p'],'repeat_songs':d['repeat']['songs'],
                         'registered_pass':d['trusted'],'verdict':'PROVISIONAL' if channel.startswith('voice_') else 'trusted' if d['trusted'] else 'not trusted / not evaluable','pairs':d['pairs']})
    csvout(HERE/'validation/trust_by_language.csv',lang)
    h=read(HERE/'validation/h1.json');csvout(HERE/'validation/h1_summary.csv',[{'cohort':cohort,'analysis':kind,**{k:v for k,v in d.items() if k!='songs'}} for cohort,v in h.items() for kind,d in v.items() if kind in ('round2','round1_models','inferred_group_exploratory')])
    save(HERE/'validation/finalization_inventory.json',{'songs':len(songs),'sections':len(rows),'cohorts':dict(collections.Counter(s['cohort'] for s in songs)),
         'primary_sections':dict(collections.Counter(r['cohort'] for r in rows if r['set']=='primary')),'no_model_inference':True,'registered_tests_unchanged':True})
    print('Cached-data finalization complete',flush=True)
if __name__=='__main__':main()
