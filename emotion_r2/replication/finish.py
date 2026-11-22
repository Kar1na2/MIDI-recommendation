"""Verify and freeze scores; permit registered statistics only after freeze commit."""
import argparse
from collections import Counter,defaultdict
import hashlib
import itertools
import math
import statistics
import sys
from .audit import ROOT,R2,DEST,read,save,sha,now,rows as csv_rows
from .controls import pre_scoring,frozen,lock_sources,git
from .worker import selected,inputs,verified_stem

ROLES=('T1','T3','L1','M1','M2','M3','V1','V3','voice_gate')
CHANNELS={'words_valence':['T1_valence','T3_valence','L1_valence'],
          'background_valence':['M1_valence','M2_valence','M3_valence'],
          'background_arousal':['M1_arousal','M2_arousal','M3_arousal'],
          'voice_valence':['V1_valence','V3_valence'],'voice_arousal':['V1_arousal','V3_arousal']}

def freeze():
    pre_scoring()
    from registration import verify_registration
    reg=verify_registration();amend=reg['amendment_commit'].encode()
    from .worker import model_path
    from guards import verify as verify_round1_weights
    verify_round1_weights(['M1','M2','source_separation'])
    for role in ('T1','T3','L1','M3','V1','V3'):model_path(reg,role)
    prompt=hashlib.sha256((R2/'l1_prompt.txt').read_text(encoding='utf8').encode()).hexdigest().encode();files={};coverage={};failures={};statuses={}
    songs=selected()
    manifest=read(DEST/'inventory.json')
    if len(manifest['songs'])!=len({s['track_id'] for s in manifest['songs']}):raise RuntimeError('Repeated inventory ID')
    protected={s['track_id'] for s in csv_rows(ROOT/'mvp_325/summary.csv')}
    if protected & {s['track_id'] for s in manifest['songs']}:raise RuntimeError('Protected ID in replication')
    def bind(p):
        files[p.relative_to(ROOT).as_posix()]=sha(p)
    for p in (DEST/'inventory.json',DEST/'replication_sample.json',DEST/'prepared_songs.json',DEST/'near_duplicates.csv',DEST/'registered_source_hashes.json'):
        bind(p)
    for p in DEST.glob('*.py'):bind(p)
    bind(R2/'REPLICATION_NOTES.md')
    for song in songs:
        tid=song['track_id'];public,_,pb,qb=inputs(tid);expected={s['section_id'] for s in public['sections']}
        coverage[tid]={};statuses[tid]={};failures[tid]={}
        for name,digest in public['source_hashes'].items():
            p=ROOT/name
            if sha(p)!=digest:raise RuntimeError('Backend source changed before freeze: '+name)
            bind(p)
        for p in (DEST/'sections_input'/f'{tid}.json',DEST/'private'/f'{tid}.json'):
            bind(p)
        bind(__import__('pathlib').Path(song['audio_path']))
        stem_hashes={}
        for name in ('instrumental','vocals'):
            p=verified_stem(tid,name)
            stem_hashes[name]=sha(p) if p else ''
            if p:bind(p)
        for meta in (DEST/'stems'/tid/'separation.json',ROOT/'outputs'/tid/'separation.json'):
            if meta.is_file():bind(meta)
        gp=DEST/'scores/voice_gate'/f'{tid}.json'
        for role in ROLES:
            p=DEST/'scores'/role/f'{tid}.json'
            if not p.is_file():raise RuntimeError('Unattempted model: '+tid+'/'+role)
            payload=read(p);bind(p)
            if payload.get('track_id')!=tid or payload.get('cohort')!='replication_library':raise RuntimeError('Checkpoint identity mismatch')
            if role in ('T1','T3','L1','M3','V1','V3') and (payload.get('amendment_commit')!=reg['amendment_commit'] or payload.get('manifest_sha256')!=reg['amended_manifest_sha256']):
                raise RuntimeError('Checkpoint registration mismatch: '+tid+'/'+role)
            if role in ('T1','T3','L1'):
                fingerprint=hashlib.sha256(pb+qb+amend+role.encode()+prompt).hexdigest()
            elif role in ('M1','M2'):
                fingerprint=hashlib.sha256(pb+stem_hashes['instrumental'].encode()+(sha(__import__('pathlib').Path(song['audio_path'])).encode() if role=='M1' else b'')).hexdigest()
            elif role=='voice_gate':
                fingerprint=hashlib.sha256(pb+stem_hashes['vocals'].encode()+sha(ROOT/'outputs'/tid/'transcription.json').encode()).hexdigest()
            else:
                stem_hash=stem_hashes['instrumental' if role=='M3' else 'vocals']
                fingerprint=hashlib.sha256(pb+stem_hash.encode()+amend+(gp.read_bytes() if role!='M3' else b'')).hexdigest()
            if payload.get('input_sha256')!=fingerprint:raise RuntimeError('Checkpoint input hash mismatch: '+tid+'/'+role)
            actual=[s['section_id'] for s in payload.get('sections',[])]
            top=payload.get('status','checkpoint');statuses[tid][role]=top
            if len(actual)!=len(set(actual)):raise RuntimeError('Repeated checkpoint section')
            unavailable=top in ('instrumental_stem_unavailable','vocals_stem_unavailable','voice_gate_unavailable','not_applicable_instrumental')
            if top not in ('complete','checkpoint','instrumental_stem_unavailable','vocals_stem_unavailable','voice_gate_unavailable','not_applicable_instrumental'):
                raise RuntimeError('Unrecognized checkpoint status: '+tid+'/'+role+'/'+str(top))
            if unavailable:
                if actual:raise RuntimeError('Unavailable model has substituted sections')
            elif role in ('M1','M2') and song['ring1_source']=='instrumental' and not expected:
                if actual!=['instrumental-whole']:raise RuntimeError('Instrumental whole-window mismatch')
            elif set(actual)!=expected:raise RuntimeError('Checkpoint section coverage mismatch: '+tid+'/'+role)
            counts=Counter(s.get('status','unknown') for s in payload.get('sections',[]))
            allowed_statuses=({'ok','missing_text','unsupported_or_uncertain_language'} if role=='T1' else
                {'ok','missing_text'} if role=='T3' else {'ok','missing_text','unparseable_response'} if role=='L1' else
                {'ok','missing_raw_timing','error'} if role in ('M1','M2') else
                {'ok','missing_timing','no_frames','error'} if role=='M3' else
                {'ok','missing_timing','error'} if role=='voice_gate' else {'ok','missing_timing','no_active_voice_frames','error'})
            if set(counts)-allowed_statuses:raise RuntimeError('Unrecognized section status: '+tid+'/'+role)
            if role in ('T1','T3','L1'):
                for sec in payload.get('sections',[]):
                    if sec['status'] not in ('ok',) and any(sec.get(k) is not None for k in ('valence','arousal')):
                        raise RuntimeError('Missing text-model score was filled: '+tid+'/'+role)
            coverage[tid][role]=dict(counts)
            errors=[{'section_id':s['section_id'],'error':s.get('error')} for s in payload.get('sections',[]) if s.get('status')=='error']
            if errors:failures[tid][role]=errors
            if top not in ('complete','checkpoint','not_applicable_instrumental'):failures[tid][role]={'status':top}
            if role in ('V1','V3') and top=='complete':
                mask={s['section_id']:[(round(f['start_s'],6),f['active']) for f in s['frames']] for s in read(gp)['sections']}
                for sec in payload['sections']:
                    if sec.get('status') in ('ok','no_active_voice_frames'):
                        actual_mask=[(round(f['start_s'],6),f['active']) for f in sec.get('frames',[])]
                        if actual_mask!=mask.get(sec['section_id']):raise RuntimeError('V1/V3 shared gate mask mismatch')
        for folder in ('text_model_probs','l1_readings'):
            p=R2/'display'/folder/f'{tid}.json'
            if not p.is_file():raise RuntimeError('Missing display output: '+tid+'/'+folder)
            bind(p)
    result={'schema_version':1,'frozen_utc':now(),'scoring_complete':True,'registered_draw_sha256':sha(DEST/'replication_sample.json'),
            'normalization_sha256':sha(R2/'validation/normalization.json'),'trust_sha256':sha(R2/'validation/trust.json'),
            'library_total':len(manifest['songs']),'backend_passed':len(songs),'scored_ids':[s['track_id'] for s in songs],
            'failures':failures,'checkpoint_statuses':statuses,'coverage':coverage,'files':files,
            'no_interim_h1_computed':True,'h2_h3':'not run (development precondition failed)'}
    target=DEST/'frozen_manifest.json'
    if target.exists():
        old=read(target)
        if old['files']!=files:raise RuntimeError('Scored set changed after initial freeze')
    else:save(target,result)
    print('Frozen checkpoint coverage and hashes; no hypothesis statistics computed.',flush=True)

def finite(v):return isinstance(v,(int,float)) and math.isfinite(v)

def analysis_rows():
    frozen()
    from stat_methods import z
    from prepare_statistics_r2 import source_label
    params=read(R2/'validation/normalization.json');reference_params=read(R2/'validation/reference_normalization.json');result=[]
    for song in selected():
        tid=song['track_id'];public,_,_,_=inputs(tid)
        source=(read(ROOT/'outputs'/tid/'lyrics.json').get('data') or {}).get('sections') or []
        models={r:{s['section_id']:s for s in read(DEST/'scores'/r/f'{tid}.json')['sections']} for r in ROLES if r!='voice_gate'}
        for section in public['sections']:
            row=dict(section);raw={}
            for role,mapping in models.items():
                scored=mapping.get(section['section_id'],{})
                for dim in ('valence','arousal'):
                    raw[role+'_'+dim]=scored.get(dim if role in ('T1','T3','L1') else dim+'_mean')
            row['raw']=raw;row['model_z']={k:z(raw.get(k),params[k]) for k in set(itertools.chain.from_iterable(CHANNELS.values()))}
            row['references_raw']={'L1_arousal':raw.get('L1_arousal'),'T2_arousal':None,'T2_valence':None}
            row['references_z']={k:z(v,reference_params[k]) for k,v in row['references_raw'].items()}
            row['reference_t2_status']='not_scored_in_registered_round2_model_set'
            row['consensus']={}
            for channel,keys in CHANNELS.items():
                values=[row['model_z'][k] for k in keys if finite(row['model_z'][k])]
                row['consensus'][channel]=statistics.fmean(values) if values else None
            row['genius_label'],row['genius_category']=source_label(section,source)
            result.append(row)
    return result

def gate_summary(songs):
    summary=[];total=Counter()
    for song in songs:
        tid=song['track_id'];gate=read(DEST/'scores/voice_gate'/f'{tid}.json');counts=Counter();lyric=0;absent=0;primary_lyric=0;primary_absent=0
        for sec in gate.get('sections',[]):
            frames=sec.get('frames',[])
            if sec['section_id'].startswith('lyric-') and frames:
                lyric+=1;absent+=not any(f['active'] for f in frames)
            if sec.get('set')!='primary':continue
            if sec['section_id'].startswith('lyric-') and frames:
                primary_lyric+=1;primary_absent+=not any(f['active'] for f in frames)
            for frame in frames:
                detector=frame['active'];note=frame['muscriptor_voice_note_active']
                counts['both_active' if detector and note else 'detector_only' if detector else 'transcription_only' if note else 'neither']+=1
        n=sum(counts.values());total.update(counts)
        summary.append({'track_id':tid,'title':song['title'],'cohort':song['cohort'],'counts':dict(counts),'windows':n,
                        'agreement':(counts['both_active']+counts['neither'])/n if n else None,
                        'detector_only_share':counts['detector_only']/n if n else None,'transcription_only_share':counts['transcription_only']/n if n else None,
                        'timed_lyric_sections':lyric,'zero_active_lyric_sections':absent,'majority_lyric_sections_no_singing':bool(lyric and absent>lyric/2)})
        summary[-1].update(timed_primary_lyric_sections=primary_lyric,zero_active_primary_lyric_sections=primary_absent,
                           majority_primary_lyric_sections_without_active_windows=bool(primary_lyric and primary_absent>primary_lyric/2))
    n=sum(total.values())
    return {'songs':summary,'overall_counts':dict(total),'windows':n,
            'agreement':(total['both_active']+total['neither'])/n if n else None,
            'detector_only_share':total['detector_only']/n if n else None,'transcription_only_share':total['transcription_only']/n if n else None,
            'voice_verdict':'PROVISIONAL; no re-decision','comparison_is_not_ground_truth':True}

def alignment(songs):
    from pipeline import core,v2_stats
    records=[];arrays=[];excluded=[]
    for song in songs:
        tid=song['track_id'];folder=ROOT/'outputs'/tid
        try:
            grid=core.data(folder/'beats.json');pat=core.data(folder/'patterns.json');lyric=core.data(folder/'lyrics.json')
            sections=v2_stats.voice_sections(pat,grid,'excluded')
            internal,text,own=v2_stats.boundary_indices(grid,lyric,sections)
            if not text or not len(internal):excluded.append({'track_id':tid,'reason':'No evaluable validated internal lyric/downbeat boundaries'});continue
            seed=v2_stats.seed_for(tid,'raw','excluded','circular')
            draws,error=v2_stats.draw_boundaries(len(own),len(internal),'circular',seed,own=own)
            if error:raise ValueError(error)
            observed=v2_stats.agreement(text,own,1);rates=v2_stats.null_rates(text,draws,1)
            import numpy as np
            records.append({'track_id':tid,'observed':observed,'null_mean':float(rates.mean()),'lift':observed-float(rates.mean()),
                            'p':float((1+np.count_nonzero(rates>=observed-1e-12))/(len(rates)+1)),'seed':seed})
            arrays.append(rates)
        except Exception as error:excluded.append({'track_id':tid,'reason':type(error).__name__+': '+str(error)})
    return {'status':'exploratory; analysis_v2 plan has no replication section','grid':'raw strict v1 cached outputs',
            'method':'Accompaniment-only; circular-shift null, 10,000 shuffles, ±1 bar; original v2_stats kernels/seeds; no auto/v2 recompute',
            'pooled':v2_stats.pooled(records,arrays),'songs':records,'excluded':excluded}

def analyze():
    frozen();lock_sources()
    from statistics_r2 import chorus_lift
    from stat_methods import correlation,repeat_test
    from .features import extract,scoped
    from explanations_r2 import fit
    rows=analysis_rows();songs=selected();primary=[r for r in rows if r['set']=='primary']
    draw=read(DEST/'replication_sample.json');ids={s['track_id'] for s in draw['songs']}
    duplicates={s['track_id'] for s in csv_rows(DEST/'near_duplicates.csv')}
    registered=chorus_lift([r for r in primary if r['track_id'] in ids],'background_arousal',iterations=10000,seed=20261116)
    registered['replicates']=bool(registered['p'] is not None and registered['p']<.01)
    sensitivity=chorus_lift([r for r in primary if r['track_id'] in ids and r['track_id'] not in duplicates],'background_arousal',iterations=10000,seed=20261116)
    sensitivity['label']='Sensitivity only; same registered draw excluding preflagged near-duplicates; no redraw'
    save(DEST/'validation/h1.json',{'registered':registered,'sensitivity':sensitivity,'alpha':.01,
        'h2':'not run (development precondition failed)','h3':'not run (development precondition failed)',
        'registered_selected_count':len(ids),'no_replacement_after_scoring_failure':True})
    comparisons={}
    development=read(R2/'validation/trust.json')
    for channel,keys in CHANNELS.items():
        pairs={a+'--'+b:correlation([(r['model_z'].get(a),r['model_z'].get(b)) for r in primary]) for a,b in itertools.combinations(keys,2)}
        values=[v['rho'] for v in pairs.values()]
        comparisons[channel]={'development':development[channel]['overall'],'library_pairs':pairs,
                              'library_median_rho':statistics.median(values) if values and all(finite(v) for v in values) else None,
                              'development_verdict_unchanged':True,'analysis':'descriptive only; no new trust verdict'}
        comparisons[channel]['library_repeat']=repeat_test([{'track_id':r['track_id'],'label':r.get('genius_label'),'score':r['consensus'].get(channel)} for r in primary],iterations=10000,seed=20261112)
        comparisons[channel]['by_language']={}
        for language in sorted({r.get('language') or 'uncertain' for r in primary}):
            subset=[r for r in primary if (r.get('language') or 'uncertain')==language]
            pairs_by={a+'--'+b:correlation([(r['model_z'].get(a),r['model_z'].get(b)) for r in subset]) for a,b in itertools.combinations(keys,2)}
            vals=[v['rho'] for v in pairs_by.values()]
            comparisons[channel]['by_language'][language]={'pairs':pairs_by,'median_rho':statistics.median(vals) if vals and all(finite(v) for v in vals) else None,
                'development':development[channel].get('by_language',{}).get(language),'descriptive_only':True}
    save(DEST/'validation/descriptive_correlations.json',comparisons)
    gate=gate_summary(songs)
    gate['development']=read(R2/'validation/voice_gate_audit.json')['overall']
    save(DEST/'validation/voice_gate.json',gate)
    rows=extract(songs,rows)
    # Select the new library cohort while preserving every numerical part of fit.
    scoped_fit=scoped(fit,[("r['cohort']=='random' and ","")])
    explanations={ch:scoped_fit(rows,ch) if dev['overall']['trusted'] else {'status':'not run: development channel untrusted'} for ch,dev in development.items()}
    # Final Task F retained importance for fold-stability auditing even where
    # OOF R² <= 0, without interpreting it as a driver. Reproduce that check.
    from explanation_repair import family_features,cross_validate
    import numpy as np
    for channel,fit_result in explanations.items():
        if fit_result.get('status')!='completed':continue
        if not fit_result['full']['permutation_importance']:
            subset=[r for r in rows if r['set']=='primary' and finite(r['consensus'].get(channel))]
            names=sorted(k for k,v in fit_result['coverage'].items() if v>=.05);feature_rows=[]
            for r in subset:
                fam,unknown=family_features(r['features'])
                if unknown:raise RuntimeError('Unknown explanation instrument')
                feature_rows.append({**{k:v for k,v in r['features'].items() if not k.startswith('onset_density__')},**fam})
            X=np.array([[f.get(k,np.nan) if finite(f.get(k)) else np.nan for k in names] for f in feature_rows])
            audited=cross_validate(X,np.array([r['consensus'][channel] for r in subset]),np.array([r['track_id'] for r in subset]),names,True,True)
            if abs(audited['oof_r2']-fit_result['full']['oof_r2'])>1e-12:raise RuntimeError('Explanation R² reproduction mismatch')
            fit_result['full']=audited
        fit_result['drivers_allowed']=fit_result['full']['oof_r2']>0
    save(DEST/'validation/explanations.json',explanations)
    acoustic={role:{dim:{f:correlation([(r['raw'].get(role+'_'+dim),r.get('features',{}).get(f)) for r in rows if r['set']=='primary']) for f in ('voice_brightness_hz','voice_f0_median_hz','voice_rms_mean')} for dim in ('valence','arousal')} for role in ('V1','V3')}
    save(DEST/'validation/voice_acoustic_correlations.json',{'library':acoustic,'development':read(R2/'validation/voice_acoustic_correlations.json'),
        'caveat':'May reflect a shared acoustic shortcut; not emotion ground truth. Feature frames preserve development diagnostic selection; model frames use the fixed pYIN gate.'})
    save(DEST/'validation/sections_analysis.json',rows)
    save(DEST/'validation/alignment_exploratory.json',alignment(songs))
    by=defaultdict(list)
    for row in rows:by[row['track_id']].append(row)
    for song in songs:
        tid=song['track_id'];sections=by[tid]
        for sec in sections:sec['channel_status']={c:'PROVISIONAL' if c.startswith('voice_') else 'trusted' if dev['overall']['trusted'] else 'experimental_untrusted' for c,dev in development.items()}
        save(DEST/'songs'/tid/'sections.json',{'track_id':tid,'cohort':song['cohort'],'sections':sections})
        save(DEST/'songs'/tid/'song.json',{**song,'sections':len(sections),'normalization':'fixed development parameters',
             'voice_verdict':'PROVISIONAL','h2_h3':'not run (development precondition failed)'})
        export_curves(tid,sections)
    prepared=read(DEST/'prepared_songs.json')['songs']
    for song in prepared:
        if not song['backend_ok']:
            save(DEST/'songs'/song['track_id']/'song.json',{**song,'status':'failed','failure_category':song.get('eligibility_reason'),
                'sections':0,'scores_substituted':False})
    save(DEST/'analysis_complete.json',{'utc':now(),'registered_draw_count':len(ids),'scored_library_songs':len(songs),'status':'complete'})

def export_curves(tid,sections):
    from stat_methods import z
    import pandas as pd
    params=read(R2/'validation/normalization.json');by={s['section_id']:s for s in sections};frames={}
    for role in ('M1','M2','M3','V1','V3'):
        for sec in read(DEST/'scores'/role/f'{tid}.json')['sections']:
            sid=sec['section_id']
            if sid not in by:continue
            for f in sec.get('frames',[]):
                key=(sid,round(f['start_s'],6))
                row=frames.setdefault(key,{'track_id':tid,'cohort':'replication_library','language':by[sid].get('language'),
                    'section_id':sid,'set':by[sid].get('set'),'start_s':float(f['start_s']),'end_s':float(f['end_s'])})
                for dim in ('valence','arousal'):row[role+'_'+dim]=f.get(role+'_'+dim) if role in ('M1','M2') else f.get(dim)
                if role.startswith('V'):row[role+'_active']=f.get('active')
    result=[]
    for (sid,_),row in sorted(frames.items()):
        for channel,keys in CHANNELS.items():
            values=[]
            for key in keys:
                raw=by[sid]['raw'].get(key) if key.startswith(('T1_','T3_','L1_')) else row.get(key)
                row[key+'_z']=z(raw,params[key])
                if finite(row[key+'_z']):values.append(row[key+'_z'])
            row[channel+'_consensus_z']=statistics.fmean(values) if values else None;row[channel+'_models']=len(values)
        result.append(row)
    cols=sorted(set(('track_id','cohort','language','section_id','set','start_s','end_s')).union(*(r.keys() for r in result)))
    pd.DataFrame(result,columns=cols).to_parquet(DEST/'songs'/tid/'curves.parquet',index=False)

def report():
    frozen()
    inventory=read(DEST/'inventory.json');preflight=read(DEST/'preflight_inventory.json');h1=read(DEST/'validation/h1.json')
    integrity=read(DEST/'integrity_end.json');freeze_data=read(DEST/'frozen_manifest.json')
    backend=read(ROOT/'library/progress.json');model_counts={role:dict(Counter(s[role] for s in freeze_data['checkpoint_statuses'].values())) for role in ROLES}
    original=h1['registered'];sensitivity=h1['sensitivity'];comparisons=read(DEST/'validation/descriptive_correlations.json')
    explanations=read(DEST/'validation/explanations.json');gate=read(DEST/'validation/voice_gate.json')
    failed=Counter(s.get('failure_category','unspecified') for s in backend['songs'].values() if s.get('status')=='failed')
    failed['missing_audio']=inventory['missing_audio']
    prepared=read(DEST/'prepared_songs.json')['songs']
    source_mix=dict(Counter(s.get('ring1_source') or 'unavailable' for s in prepared))
    language_mix=dict(Counter(s.get('language') or 'uncertain' for s in prepared))
    lines=['# Task K — full-library registered replication',
           '\nH1 **'+('replicates' if original['replicates'] else 'does not replicate')+'** by the registered one-sided Wilcoxon rule, alpha 0.01.',
           '\nDevelopment trust verdicts are unchanged. Voice verdicts remain **PROVISIONAL**. H2/H3 were **not run (precondition failed)**; this is not a negative result.',
           '\nOriginal plan: `e8127190`; Amendment 1: `7484df2`. Operational decisions: [REPLICATION_NOTES.md](REPLICATION_NOTES.md).',
           '\n## Counts and failures',f'\nLibrary IDs outside the protected 325: {inventory["total"]}; local audio {inventory["audio_available"]}; missing audio {inventory["missing_audio"]}.',
           f'Backend status counts: {dict(Counter(s.get("status") for s in backend["songs"].values()))}. Failure categories: {dict(failed)}.',
           f'Registered pre-scoring random draw: {h1["registered_selected_count"]}; final Chorus-and-Verse qualifying songs: {original["n"]}. No replacement after failures.',
           f'\nLyric-source mix across all non-MVP IDs (including unavailable inputs): {source_mix}. Language mix: {language_mix}.',
           '\nModel checkpoint status counts: `'+__import__('json').dumps(model_counts,sort_keys=True)+'`.',
           '\nSection-level statuses/errors and coverage are fully recorded in `replication/frozen_manifest.json`; backend examples and details remain in `library/FULL_RUN_REPORT.md` and `library/progress.json`.',
           '\n## H1',f'\nMedian per-song Chorus minus Verse background-arousal consensus: {original["median"]}; share above zero {original["share_above_zero"]}; one-sided Wilcoxon p {original["p"]}; 95% bootstrap CI {original["bootstrap_95_ci"]}. 10,000 bootstrap resamples, seed 20261116.',
           '\nEligibility is conditional on available local audio, completed backend stages and lyric sections; H1 additionally requires scored, proven Genius Chorus and Verse. Missing labels or scores are not manufactured, and the draw is not conditioned on label balance or effect size.',
           f'\nSensitivity excluding the preflagged near-duplicates, without redraw: n={sensitivity["n"]}, median={sensitivity["median"]}, positive share={sensitivity["share_above_zero"]}, p={sensitivity["p"]}, CI={sensitivity["bootstrap_95_ci"]}.',
           '\nThe registered draw and scored checkpoints were frozen and committed before any new-song H1/chorus–verse statistics. Commit: `'+git('log','-1','--format=%H','--','emotion_r2/replication/frozen_manifest.json').decode().strip()+'`.',
           '\n## Descriptive comparisons — no verdict changes',
           '\n| Channel | Development median ρ | Library median ρ |\n|---|---:|---:|']
    for channel,item in comparisons.items():lines.append(f'| {channel} | {item["development"]["median_rho"]} | {item["library_median_rho"]} |')
    lines+=['\nEvery pair and sample size: `replication/validation/descriptive_correlations.json`. These are all-new-library primary sections, not a new trust decision or a random population sample.',
            f'\nVoice-gate agreement: {gate["agreement"]}; detector-only share {gate["detector_only_share"]}; transcription-only share {gate["transcription_only_share"]}. MuScriptor activity is not ground truth. Per-song counts and songs with no active singing windows in most lyric sections: `replication/validation/voice_gate.json`.',
            f'\nDevelopment primary random-300 gate comparison: {gate["development"].get("random")}. The per-song/library comparison is descriptive, not a validation against human truth.',
            '\nVoice acoustic-shortcut diagnostics, with development comparisons, are in `replication/validation/voice_acoustic_correlations.json`. Shared responses to brightness, pitch or loudness may explain model agreement; they do not establish emotional validity.',
            '\nExplanation fits retain the registered estimator, folds, feature definitions, coverage rule and seeds. Full results (including fold R² and all single-feature baselines): `replication/validation/explanations.json`.',
            '\n| Channel | Library OOF R² | Development OOF R² | Best single-feature OOF R² |\n|---|---:|---:|---:|']
    dev_explanation=read(R2/'validation/explanation_checks.json')
    baseline_notes=[]
    for channel,ex in explanations.items():
        if ex.get('status')!='completed':lines.append(f'| {channel} | {ex.get("status")} | — | — |');continue
        base=[(name,v.get('oof_r2')) for name,v in ex['single_feature_baselines'].items() if finite(v.get('oof_r2'))]
        best=max(base,key=lambda x:x[1]) if base else None
        lines.append(f'| {channel} | {ex["full"]["oof_r2"]} | {dev_explanation.get(channel,{}).get("full",{}).get("oof_r2")} | {best} |')
        if best is not None:
            baseline_notes.append(f'\n{channel}: full OOF R² minus the best single-feature baseline is {ex["full"]["oof_r2"]-best[1]}; the full fit is not evidence of many distinct emotional drivers.')
    lines+=baseline_notes
    lines+=['\nPermutation-importance fold stability is retained for diagnostics. R² ≤ 0 permits no driver claims. Model-rating predictability is not emotion ground truth or causation.',
            '\nLyric/rhythm alignment is **exploratory** because the v2 plan defines no replication. It uses raw/strict v1 cached sections, the existing accompaniment-only circular-shift kernels and ±1-bar tolerance; it does not rerun v2 or change meter defaults. Results/exclusions: `replication/validation/alignment_exploratory.json`.',
            '\n## Caveats and operational decisions',
            '\nAll decisions are in the dated notes: full library versus seeded ≤300 confirmatory draw; fixed input/status eligibility; no resampling; metadata-only duplicate flags; v1 strict backend with routing held; isolated outputs; same local text provenance/language/model kernels; fixed development normalization; same prompts/display-overlap scan; terminal failure recording; freeze barrier; unchanged development trust; descriptive fits and exploratory alignment; checkpoint supervision and compute accounting.',
            '\nM1–M3 share MediaEval/DEAM provenance; exact training-track/split overlap remains unresolved. M3 model-card Apache-2.0 versus manifest CC-BY-NC-4.0 conflict remains unresolved. T3 and L1 are a related family. No independence or commercial-use permission is newly claimed.',
            '\nDevelopment chorus lift was a development robustness check, not confirmation. This report’s H1 alone is the registered held-out replication. Thresholds and tests were not changed after seeing results.',
            '\n## Compute, storage, tests and integrity',f'\nBackend GPU-stage wall hours: {backend.get("gpu_seconds_pass1",0)/3600}. This is stage wall time, not GPU utilization; it includes earlier reused library work where the original progress did so. New model worker wall times are in `replication/stage_times.jsonl`; score checkpoints retain per-song timings.',
            f'\nPlanning footprint from the actual 325: {preflight["mean_325_bytes"]}; projected additional bytes {preflight["projected_additional_bytes"]}. Timings, cache and coverage limits are documented in `replication/preflight_inventory.json`.',
            '\nTests: see `replication/test_results.json`; no registered tests were changed.',
            '\n| Snapshot | Start | End |\n|---|---|---|']
    initial=read(DEST/'integrity_start.json')
    for key in ('round1','v1','mvp_325','round2_325'):
        a,b=initial[key],integrity[key];lines.append(f'| {key} | unchanged: {a["unchanged"]}/{a["total"]} | unchanged: {b["unchanged"]}/{b["total"]} |')
    lines+=['\n## Items needing human review',
            '\n- Review failed/missing-audio tracks; no missing input was substituted.\n- Voice-gate validity and shared acoustic shortcuts remain provisional and require listening review.\n- Exact M1/M3 training overlap and the M3 license conflict require authoritative manifests/clarification.\n- Metadata-only near-duplicate flags may miss undocumented versions.\n- Model interpretations and class probabilities are display-only, not songwriter intent or explanations of registered score computation.\n- No routing, full-library H2/H3, site work, push or history rewriting was performed.']
    lines+=['\n## Measured new worker wall time', '\n| Worker | Wall hours |\n|---|---:|']
    times=DEST/'stage_times.jsonl'
    if times.exists():
        for line in times.read_text(encoding='utf8').splitlines():
            entry=__import__('json').loads(line)
            lines.append(f'| {entry["module"]} {" ".join(entry["arguments"])} | {entry["wall_seconds"]/3600} |')
    from .audit import size
    backend_bytes=sum(size(ROOT/'outputs'/s['track_id']) for s in inventory['songs'])
    emotion_bytes=size(DEST)
    lines.append(f'\nMeasured library backend artifacts: {backend_bytes} bytes (including reused non-MVP caches); new replication tree: {emotion_bytes} bytes (including stems/checkpoints/curves). Display exports are additional. These are file bytes, not filesystem allocation or whole-drive growth attributable solely to this task.')
    lines+=['\n## Checkpoint and failure detail','\n| Role | Section statuses | Failed/unavailable examples |\n|---|---|---|']
    for role in ROLES:
        count=Counter()
        for song_coverage in freeze_data['coverage'].values():count.update(song_coverage[role])
        examples=[tid for tid,entry in freeze_data['failures'].items() if role in entry][:3]
        lines.append(f'| {role} | {dict(count)} | {examples} |')
    if original['n']==0:lines.append('\nH1 was not evaluable because no drawn song had both scored, proven Genius Chorus and Verse sections. The registered replication criterion is not met; this absence is not evidence of a zero or negative effect.')
    (R2/'REPLICATION_REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf8')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['freeze','analyze','report']);a=p.parse_args()
    {'freeze':freeze,'analyze':analyze,'report':report}[a.action]()
