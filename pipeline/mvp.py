"""Seeded 300-song backend MVP, CPU lyric worker and measured reporting."""
from pathlib import Path
from collections import Counter
from statistics import median,mean
import datetime as dt
import json
import os
import random
import signal
import subprocess
import sys
import time
from zoneinfo import ZoneInfo
from .common import read,save,digest,atomic_write_bytes,StageError
from . import core, lyric_backend, retention

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'mvp'
SEED=20261107


def sample_rows(songs,count=300,seed=SEED):
    return random.Random(seed).sample(sorted(songs,key=lambda s:s['id']),count)


def distribution(songs):
    values=sorted(float(s['duration_s']) for s in songs)
    return {'count':len(values),'duration_mean_s':mean(values),'duration_median_s':median(values),
        'duration_min_s':min(values),'duration_max_s':max(values),
        'language_counts':dict(Counter(s.get('language') or 'unknown' for s in songs))}


def prepare():
    from .batch import atomic_csv
    from .handcheck import prepare as template
    FOLDER.mkdir(parents=True,exist_ok=True)
    progress=read(ROOT/'batch/progress.json')
    if progress['status']=='running': raise StageError('Stop the full-library batch before preparing the MVP')
    stop=ROOT/'batch/STOPPED_FOR_MVP.json'
    if not stop.exists():
        save(stop,{'stopped_at_utc':progress['updated_at'],
            'stopped_at_local':dt.datetime.fromisoformat(progress['updated_at']).astimezone(ZoneInfo('America/Los_Angeles')).isoformat(),
            'done':sum(s.get('status')=='done' for s in progress['songs'].values()),
            'failed':sum(s.get('status')=='failed' for s in progress['songs'].values()),
            'reason':'Full library deferred; backend fixes and 300-song random MVP requested'})
    base=read(ROOT/'batch/manifest.json')['songs']
    pilots=read(ROOT/'pilot/selection.json')['songs']
    known={s['track_id']:s.get('language') for s in pilots}
    for s in base:
        report=ROOT/'outputs'/s['id']/'report.json'
        if report.exists(): known[s['id']]=read(report).get('language')
        s['language']=known.get(s['id'])
        s['beats']={'meter_policy':'strict'}
        s['rhythm']={'grid_candidates':[16,24,32,48]}
        s['separation']={'keep_stems':['vocals','no_drums']}
    if (FOLDER/'sample.json').exists():
        sample=read(FOLDER/'sample.json')
    else:
        selected=sample_rows(base)
        finished=[s['id'] for s in selected if progress['songs'].get(s['id'],{}).get('status')=='done']
        import soundfile as sf
        full=list(base)
        for p in pilots:
            path=Path(p['audio_path'])
            if not path.is_absolute(): path=ROOT/path
            full.append({'id':p['track_id'],'duration_s':sf.info(path).duration,'language':p.get('language')})
        sample={'schema_version':1,'seed':SEED,'sampling':'uniform without replacement from 1028 existing nonpilot local MP3s',
            'songs':selected,'track_ids':[s['id'] for s in selected],
            'already_finished_batch_ids':finished,'already_finished_batch_count':len(finished),
            'comparison':{'sample':distribution(selected),'full_local_library':distribution(full),
                          'eligible_nonpilot_library':distribution(base)}}
        save(FOLDER/'sample.json',sample)
        atomic_csv(FOLDER/'sample.csv',[{k:s.get(k) for k in ('id','artist','title','duration_s','language')} for s in selected],
                   ['id','artist','title','duration_s','language'])
    # Preserve only proven successful original inferences across the EOS policy change.
    for s in sample['songs']:
        original=progress['songs'].get(s['id'],{})
        path=ROOT/'outputs'/s['id']/'transcription.json'
        if original.get('status')=='done' and original.get('source_sha256')==digest(s['audio_path']) and path.exists():
            record=read(path); artifacts=record.get('artifact_sha256',{})
            out=path.parent
            if record.get('status') in ('complete','ok_with_warning') and artifacts and all(
                    (out/key).is_file() and digest(out/key)==value for key,value in artifacts.items()):
                record['source_sha256']=original['source_sha256']
                record['cache_source_proof']='original full-library completed-song SHA-256 and MIDI artifact hashes'
                save(path,record)
    template()
    from .batch_report import write_report
    write_report(ROOT)
    return sample


def dataset():
    sample=read(FOLDER/'sample.json') if (FOLDER/'sample.json').exists() else prepare()
    songs=sorted(sample['songs'],key=lambda s:(s['duration_s'],s['id']))
    if len(songs)!=300 or not all(Path(s['audio_path']).is_file() for s in songs):
        raise StageError('MVP sample must contain exactly 300 existing audio files')
    manifest={'schema_version':1,'manifest_count':1128,'local_audio_count':1053,
        'pilot_skipped_count':25,'missing_audio_count':75,'batch_count':300,
        'seed':sample['seed'],'songs':songs,'order':'actual duration ascending; membership uniformly random'}
    save(FOLDER/'manifest.json',manifest)
    return songs,manifest


def worker():
    os.environ['CUDA_VISIBLE_DEVICES']=''
    songs=dataset()[0]; states={}; progress_file=FOLDER/'lyrics_progress.json'
    if progress_file.exists(): states=read(progress_file).get('songs',{})
    while True:
        changed=False
        for song in songs:
            tid=song['id']; out=ROOT/'outputs'/tid
            existing=states.get(tid,{})
            if existing.get('status') in ('complete','failed'): continue
            if existing.get('status')=='pending_genius' and existing.get('retry_at',0)>time.time(): continue
            if not (out/'beats.json').exists() or not (out/'stems/vocals.wav').exists(): continue
            try:
                result=lyric_backend.run(song,out,core.data(out/'beats.json'),{'vocals':str(out/'stems/vocals.wav')})
                states[tid]={'status':result['status'],'source':result['source'],'updated_at':time.time()}
                if result['status']=='pending_genius': states[tid]['retry_at']=time.time()+300
            except Exception as error:
                states[tid]={'status':'failed','failure_category':'lyrics_cpu','error':f'{type(error).__name__}: {error}',
                             'updated_at':time.time()}
            save(progress_file,{'songs':states,'updated_at':time.time()}); changed=True
            reason=cpu_failure_stop_reason(states)
            if reason:
                save(progress_file,{'status':'stopped_repeated_error','stop_reason':reason,
                                    'songs':states,'updated_at':time.time()})
                atomic_write_bytes(FOLDER/'stop.requested',reason.encode())
                return
        batch=read(FOLDER/'progress.json') if (FOLDER/'progress.json').exists() else {}
        # Non-piano songs have no routing stage. Once their CPU lyrics finish,
        # finalize their joins and retention without waiting for other songs.
        for song in songs:
            tid=song['id']; state=batch.get('songs',{}).get(tid,{})
            out=ROOT/'outputs'/tid
            if state.get('status')!='done' or states.get(tid,{}).get('status')!='complete': continue
            if (out/'retention.json').exists(): continue
            notes=core.data(out/'transcription.json').get('notes',[])
            if any(n['instrument']=='acoustic_piano' for n in notes) and state.get('pass2_status')!='complete': continue
            try:
                core.recompute(song,refresh_beats=False)
                retention.apply(out,song.get('separation',{}).get('keep_stems',['vocals','no_drums']))
            except Exception as error:
                states[tid]={'status':'failed','failure_category':'lyrics_or_retention_finalization',
                             'error':f'{type(error).__name__}: {error}'}
                save(progress_file,{'songs':states,'updated_at':time.time()})
        terminal=all(s.get('status') in ('done','failed','skipped') for s in batch.get('songs',{}).values()) and bool(batch.get('songs'))
        if terminal:
            # The 300-song report is not held hostage by an external 429 retry.
            # Pending API work remains in the durable queue and can resume later;
            # it is reported as pending_genius, never as a failure.
            break
        if (FOLDER/'stop.requested').exists():
            save(progress_file,{'status':'stopped','songs':states,'updated_at':time.time()})
            return
        if batch.get('status','running') not in ('running','starting','finalizing_cpu_lyrics_and_join'): break
        time.sleep(5 if not changed else 1)
    save(progress_file,{'status':'complete','songs':states,'updated_at':time.time()})


def cpu_failure_stop_reason(states):
    outcomes=sorted((s for s in states.values() if s.get('status') in ('complete','failed')),
                    key=lambda s:s.get('updated_at',0))
    first=outcomes[:100]
    if len(outcomes)<=100 and sum(s.get('status')=='failed' for s in first)>10:
        return 'lyrics_cpu_more_than_10_failures_in_first_100'
    last=outcomes[-5:]
    categories=[s.get('failure_category') if s.get('status')=='failed' else None for s in last]
    if len(last)==5 and categories[0] and len(set(categories))==1:
        return 'lyrics_cpu_five_consecutive_'+categories[0]
    return None


def start_lyrics():
    pid_file=FOLDER/'lyrics.pid'
    if pid_file.exists():
        try:
            pid=int(pid_file.read_text())
            cmdline=Path(f'/proc/{pid}/cmdline')
            if cmdline.exists() and b'pipeline.mvp' in cmdline.read_bytes():
                os.kill(pid,0); return
        except ProcessLookupError: pass
    log=(FOLDER/'lyrics.log').open('ab',buffering=0)
    process=subprocess.Popen(['/home/usr/venvs/midi/bin/python','-m','pipeline.mvp','lyrics-worker'],
        cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True,
        env={**os.environ,'CUDA_VISIBLE_DEVICES':'','OMP_NUM_THREADS':'4','MKL_NUM_THREADS':'4'})
    log.close(); atomic_write_bytes(pid_file,str(process.pid).encode())


def stop_lyrics():
    path=FOLDER/'lyrics.pid'
    if path.exists():
        try:
            pid=int(path.read_text())
            cmdline=Path(f'/proc/{pid}/cmdline')
            if cmdline.exists() and b'pipeline.mvp' in cmdline.read_bytes(): os.kill(pid,signal.SIGTERM)
        except ProcessLookupError: pass
        path.unlink(missing_ok=True)


def hypothesis(reports,policy):
    values=[r.get('boundary_evaluations',{}).get(policy) for r in reports]
    values=[v for v in values if v and v.get('observed_share_within_one_bar') is not None]
    if not values: return {'songs':0,'status':'unavailable','conclusion':'No songs with validated timed lyric sections yet'}
    observed=mean(v['observed_share_within_one_bar'] for v in values)
    count=min(len(v['shuffle_distribution']) for v in values)
    null=[mean(v['shuffle_distribution'][i] for v in values) for i in range(count)]
    p=(1+sum(x>=observed for x in null))/(1+count)
    lift=median(v['observed_minus_baseline'] for v in values)
    return {'songs':len(values),'median_per_song_lift':lift,
        'share_p_below_005':mean(v['one_sided_empirical_p']<.05 for v in values),
        'pooled_observed_mean':observed,'pooled_null_mean':mean(null),'pooled_permutation_p':p,
        'shuffles':count,'statistic':'equal-weight mean per-song boundary agreement; independent song seeds',
        'conclusion':'Lyric and rhythmic boundaries align more than chance' if p<.05 and observed>mean(null)
                     else 'No significant evidence that lyric and rhythmic boundaries align more than chance'}


def finish():
    path=FOLDER/'lyrics_progress.json'
    while not path.exists() or read(path).get('status')!='complete':
        if path.exists() and read(path).get('status','').startswith('stopped'):
            raise StageError('CPU lyrics worker stopped: '+str(read(path).get('stop_reason','stop requested')))
        pid_path=FOLDER/'lyrics.pid'
        if not pid_path.exists(): raise StageError('CPU lyrics worker PID missing before completion')
        try: os.kill(int(pid_path.read_text()),0)
        except ProcessLookupError: raise StageError('CPU lyrics worker exited before publishing completion; inspect mvp/lyrics.log')
        process_state=Path(f'/proc/{int(pid_path.read_text())}/stat')
        if process_state.exists() and process_state.read_text().split()[2]=='Z':
            raise StageError('CPU lyrics worker exited unexpectedly; inspect mvp/lyrics.log')
        time.sleep(2)
    stop_lyrics()
    progress=read(FOLDER/'progress.json')
    for song in dataset()[0]:
        state=progress['songs'].get(song['id'],{})
        if state.get('status')!='done': continue
        out=ROOT/'outputs'/song['id']
        try:
            core.recompute(song,refresh_beats=False)
            lyric=core.data(out/'lyrics.json') if (out/'lyrics.json').exists() else {}
            if lyric.get('status')=='complete' and not (out/'retention.json').exists():
                retention.apply(out,song.get('separation',{}).get('keep_stems',['vocals','no_drums']))
        except Exception as error:
            state['finalization_error']=f'{type(error).__name__}: {error}'
    save(FOLDER/'progress.json',progress)
    spot_check()
    write_report()


def spot_check():
    progress=read(FOLDER/'progress.json')
    complete=[s for s in dataset()[0] if progress['songs'].get(s['id'],{}).get('status')=='done'
              and progress['songs'][s['id']].get('pass2_status')!='failed'
              and not progress['songs'][s['id']].get('finalization_error')
              and (ROOT/'outputs'/s['id']/'transcription.mid').is_file()]
    from .batch import atomic_csv
    selected=random.Random(SEED+1).sample(complete,min(15,len(complete)))
    fields=['track_id','title','midi_path','rating_good_ok_bad','instrument_errors','timing_errors','notes']
    atomic_csv(FOLDER/'spot_check.csv',[{'track_id':s['id'],'title':s['title'],
        'midi_path':review_midi(ROOT/'outputs'/s['id']),**{k:'' for k in fields[3:]}} for s in selected],fields)
    return selected


def review_midi(out):
    path=Path(out)/'routing.json'
    if path.exists() and read(path).get('status') in ('complete','ok_with_warning'):
        routed=core.data(path).get('routed_midi_path')
        if routed and Path(routed).is_file(): return routed
    return str(Path(out)/'transcription.mid')


def write_report():
    if not (FOLDER/'progress.json').exists(): return
    from .batch import atomic_csv
    progress=read(FOLDER/'progress.json'); sample=read(FOLDER/'sample.json')
    lyric_progress=read(FOLDER/'lyrics_progress.json').get('songs',{}) if (FOLDER/'lyrics_progress.json').exists() else {}
    rows=[]; reports=[]; retained=[]; bytes_used=[]
    for song in sample['songs']:
        tid=song['id']; state=progress['songs'].get(tid,{}); out=ROOT/'outputs'/tid
        report=read(out/'report.json') if (out/'report.json').exists() else {}
        if state.get('status')=='done' and report.get('schema_version')=='core-mvp/1': reports.append(report)
        raw_auto=report.get('auto_choice',{})
        stats=report.get('instrument_quantization',[])
        metrics=report.get('instrument_pattern_metrics',{})
        boundary=report.get('boundary_evaluations',{}).get('raw',{})
        auto_boundary=report.get('boundary_evaluations',{}).get('auto',{})
        status=state.get('status','pending')
        if status=='done': status=report.get('status','ok')
        if state.get('finalization_error'): status='finalization_failed'
        lyric_state=lyric_progress.get(tid,{})
        if state.get('status')=='done' and lyric_state.get('status')=='pending_genius': status='pending_genius'
        failure_category=state.get('failure_category') or state.get('pass2_error_category') or (
            'finalization' if state.get('finalization_error') else lyric_state.get('failure_category'))
        if failure_category and state.get('status')=='done': status='partial_failure'
        rows.append({'track_id':tid,'title':song['title'],'status':status,
            'failure_category':failure_category or '',
            'tuning_offset_cents':report.get('tuning',{}).get('offset_cents'),
            'raw_irregular_bars':raw_auto.get('raw_irregular_bars'),'dbn_irregular_bars':raw_auto.get('dbn_irregular_bars'),
            'auto_would_choose':raw_auto.get('chosen_grid'),'bpm':report.get('estimated_bpm'),
            'grid_steps_per_instrument':json.dumps(report.get('instrument_steps_per_bar',{}),ensure_ascii=False),
            'median_within_30ms_share':median(s['within_30ms_share'] for s in stats) if stats else None,
            'instrument_pattern_metrics':json.dumps(metrics,ensure_ascii=False),
            'rhythmic_sections':len(report.get('rhythmic_sections',[])) if report else None,
            'ring1_source':report.get('ring1_source'),'sections_validated':report.get('sections_validated'),
            'sections_total':report.get('sections_total'),'language':report.get('language') or song.get('language') or 'unknown',
            'boundary_agreement':boundary.get('observed_share_within_one_bar'),
            'baseline_mean':boundary.get('baseline_mean_share'),'p':boundary.get('one_sided_empirical_p'),
            'auto_boundary_agreement':auto_boundary.get('observed_share_within_one_bar'),
            'auto_baseline_mean':auto_boundary.get('baseline_mean_share'),'auto_p':auto_boundary.get('one_sided_empirical_p'),
            'error':state.get('error') or state.get('pass2_error') or state.get('finalization_error') or lyric_state.get('error')})
        if state.get('status')=='done': bytes_used.append(sum(p.stat().st_size for p in out.rglob('*') if p.is_file()))
        if (out/'retention.json').exists():
            retained.append({**read(out/'retention.json'),'current_bytes':sum(p.stat().st_size for p in out.rglob('*') if p.is_file())})
    atomic_csv(FOLDER/'summary.csv',rows,list(rows[0]))
    tests={policy:hypothesis(reports,policy) for policy in ('raw','auto')}
    save(FOLDER/'hypothesis.json',tests)
    categories=Counter(row['failure_category'] for row in rows if row['failure_category'])
    gpu=Counter()
    log=FOLDER/'run_log.jsonl'
    if log.exists():
        for line in log.read_text().splitlines():
            try: event=json.loads(line)
            except ValueError: continue
            if event.get('stage') in ('beats','separation','transcription','routing') and event.get('status')!='cached':
                gpu[event.get('pass',1)]+=event.get('seconds',0)
    library_songs=read(ROOT/'batch/manifest.json')['songs']
    sample_by_id={s['id']:s for s in sample['songs']}
    library_duration=sum(s['duration_s'] for s in library_songs)
    # Exclude cache hits from the measured rates. The first 20 short songs can
    # be mostly previously completed work and give a misleading fresh-work ETA.
    stage_samples={}
    if log.exists():
        run_events=[]
        for line in log.read_text().splitlines():
            try: run_events.append(json.loads(line))
            except ValueError: pass
        for stage in ('beats','separation','transcription','routing'):
            events=[e for e in run_events if e.get('stage')==stage and e.get('status')!='cached']
            ids={e['song_id'] for e in events if e.get('song_id') in sample_by_id}
            seconds=sum(e.get('seconds',0) for e in events)
            audio=sum(sample_by_id[tid]['duration_s'] for tid in ids)
            stage_samples[stage]={'uncached_song_count':len(ids),'measured_seconds':seconds,
                'measured_audio_seconds':audio,
                'seconds_per_audio_second':seconds/audio if audio else None}
    for value in stage_samples.values():
        value['projected_full_library_seconds']=(value['seconds_per_audio_second']*library_duration
            if value['seconds_per_audio_second'] is not None else None)
    routing_value=stage_samples.get('routing')
    if not routing_value and progress.get('pass2_status')=='complete' and not progress.get('routing_triggered_count'):
        routing_value=stage_samples['routing']={'uncached_song_count':0,'measured_seconds':0,
            'measured_audio_seconds':0,'seconds_per_audio_second':0,
            'sample_trigger_share':0,'projected_full_library_seconds':0}
    if routing_value:
        finished_sample=sum(progress.get('songs',{}).get(s['id'],{}).get('status') in ('done','failed')
                            for s in sample['songs'])
        trigger_share=(routing_value['uncached_song_count']/finished_sample if finished_sample else None)
        routing_value['sample_trigger_share']=trigger_share
        if trigger_share is not None and routing_value['projected_full_library_seconds'] is not None:
            routing_value['projected_full_library_seconds']*=trigger_share
    projection=sum(v['projected_full_library_seconds'] for stage,v in stage_samples.items()
                   if stage!='routing' and v['projected_full_library_seconds'] is not None)/3600
    previous=read(ROOT/'batch/progress.json')
    already_done={tid for tid,state in previous.get('songs',{}).items() if state.get('status')=='done'}
    already_done|={tid for tid,state in progress.get('songs',{}).items() if state.get('status')=='done'}
    future_songs=[s for s in library_songs if s['id'] not in already_done]
    remaining_duration=sum(s['duration_s'] for s in future_songs)
    future_projection=sum((v['seconds_per_audio_second'] or 0)*
                          (v.get('sample_trigger_share',1) if stage=='routing' else 1)*remaining_duration
                          for stage,v in stage_samples.items())/3600
    disk_projection=mean(r['current_bytes'] for r in retained)*1028/1e9 if retained else None
    route_projection=stage_samples.get('routing',{}).get('projected_full_library_seconds')
    route_projection=route_projection/3600 if route_projection is not None else None
    eta20=read(FOLDER/'eta_after20.json') if (FOLDER/'eta_after20.json').exists() else 'not yet measured'
    full_total_projection=projection+route_projection if route_projection is not None else None
    forecasts={'gpu_stage_samples':stage_samples,
        'pass1_gpu_hours_for_1028_song_equivalents_at_uncached_stage_rates':projection,
        'gross_gpu_hours_for_1028_song_equivalents_at_uncached_stage_rates':full_total_projection,
        'estimated_remaining_full_run_gpu_hours_after_completed_batch_and_mvp_caches':future_projection,
        'remaining_full_run_song_count':len(future_songs),
        'remaining_full_run_audio_hours':remaining_duration/3600,
        'routing_gpu_hours_for_1028_at_measured_trigger_rate':route_projection,
        'total_gpu_hours':full_total_projection,
        'total_output_gb_with_retention':disk_projection,
        'caveat':'Each stage rate uses only sampled songs where that GPU stage actually ran. Gross projection covers 1,028 local nonpilot songs. Remaining projection excludes IDs already marked done in the stopped batch or MVP; partially cached stages are not subtracted, so it is conservative.'}
    save(FOLDER/'cost_projection.json',forecasts)
    failures='\n'.join(f"- {r['track_id']} — {r['failure_category']}: {r['error']}" for r in rows if r['failure_category']) or 'None recorded.'
    text=f'''# 300-song random backend MVP

Status: **{progress.get('status')}**. {'All GPU passes, CPU lyrics and final joins are finished; the numbers below are final for this sample.' if progress.get('status')=='complete' else 'This is a running snapshot until both GPU passes, CPU lyrics and final joins finish.'} Full-library processing remains stopped.

Seed: **{sample['seed']}**. Uniform sample without replacement, excluding 25 pilots. Already-finished full-batch songs sampled: **{sample['already_finished_batch_count']}**. Unsampled cached songs are excluded from all MVP statistics.

Sample/library comparison (unknown language is retained, not guessed):
```json
{json.dumps(sample['comparison'],ensure_ascii=False,indent=2)}
```

Song status counts: {dict(Counter(r['status'] for r in rows))}. Failures by category: {dict(categories)}.
Measured GPU-stage wall hours: Pass 1 {gpu[1]/3600:.3f}; Pass 2 {gpu[2]/3600:.3f}. Cached GPU computations cost zero new GPU hours in these measurements.
Measured ETA in hours: {(progress.get('measured_seconds_per_audio_second') or 0)*sum(s['duration_s'] for s in sample['songs'] if progress['songs'].get(s['id'],{}).get('status') not in ('done','failed','skipped'))/3600:.2f}; this is provisional, especially before 20 songs.
The recorded ETA after 20 outcomes is {eta20}. The fixed sample reaches 26 earlier batch-completed songs first, so this checkpoint is cache-heavy and is not used as the final fresh-work projection.

## Hypothesis test at scale

Only validated timed lyric sections are pooled. Vocal-activity boundaries are excluded. Each song uses an independent deterministic permutation seed. {'The sample is complete, so these are the final MVP figures.' if progress.get('status')=='complete' else 'Results remain provisional while the sample is incomplete.'}
```json
{json.dumps(tests,indent=2)}
```

## Storage and full-run projection

The previous batch did write click.wav and click_dbn.wav. MVP beat analysis writes neither; generate clicks on demand with the `pipeline click` command.
Pre-existing click caches are retained; none is newly generated. Retention runs only after each song's required stages are complete.
Retained songs measured: {len(retained)}. Mean MB/song before retention: {mean(r['bytes_before'] for r in retained)/1e6 if retained else 'unavailable'}; measured immediately after retention: {mean(r['bytes_after'] for r in retained)/1e6 if retained else 'unavailable'}; current retained footprint: {mean(r['current_bytes'] for r in retained)/1e6 if retained else 'unavailable'} MB/song.
Full 1,028-song projection from measured uncached per-stage MVP work (unavailable fields are not yet measured):
```json
{json.dumps(forecasts,indent=2)}
```

## Failures

{failures}

Flat song data: [summary.csv](summary.csv). Human review: [spot_check.csv](spot_check.csv), created after the run. The blind section-label template is ../pilot/HAND_CHECK_SECTIONS.csv. Empty human-rating fields are intentional.
'''
    atomic_write_bytes(FOLDER/'MVP_REPORT.md',text.encode())


if __name__=='__main__':
    if sys.argv[1:] == ['lyrics-worker']: worker()
    elif sys.argv[1:] == ['prepare']: print(json.dumps(prepare()['comparison'],indent=2))
    elif sys.argv[1:] == ['report']: write_report()
    elif sys.argv[1:] == ['spot-check']: spot_check()
