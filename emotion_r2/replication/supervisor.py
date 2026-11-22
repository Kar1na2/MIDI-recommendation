"""Detached Task K state machine. No statistics until a committed full freeze."""
import argparse
from collections import Counter
import os
import subprocess
import sys
import time
from .audit import ROOT,R2,DEST,read,save,now,integrity
from .controls import pre_scoring,commit

def maybe(p):return read(p) if p.is_file() else {}

def update(phase,**extra):
    state={'utc':now(),'phase':phase,'no_interim_statistics':True,**extra}
    save(DEST/'progress.json',state)
    print(str(state),flush=True)

def append_progress(phase):
    backend=maybe(ROOT/'library/progress.json');states=backend.get('songs',{})
    counts=dict(Counter(v.get('status','unknown') for v in states.values()))
    failures=dict(Counter(v.get('failure_category') for v in states.values() if v.get('status')=='failed'))
    stages={}
    for role in ('T1','T3','L1','M1','M2','M3','voice_gate','V1','V3'):
        stages[role]=len(list((DEST/'scores'/role).glob('*.json')))
    with (R2/'REPLICATION_PROGRESS.md').open('a',encoding='utf8') as f:
        f.write(f'\n- {now()} — {phase}; backend {counts}; failures {failures}; emotion checkpoints {stages}; backend ETA hours {backend.get("remaining_eta_hours", "not yet measured")}. No interim H1/chorus–verse statistics.\n')

def alive(pid_path):
    try:
        pid=int(pid_path.read_text())
        state=__import__('pathlib').Path('/proc')/str(pid)/'stat'
        if not state.is_file() or state.read_text().split()[2]=='Z':return False
        cmd=state.with_name('cmdline').read_bytes()
        return b'pipeline.batch' in cmd or b'pipeline.library' in cmd
    except (ValueError,OSError):return False

def library_pass():
    state=maybe(ROOT/'library/progress.json')
    if state.get('status')=='complete':return
    if str(state.get('status','')).startswith('stopped'):
        raise RuntimeError('Existing library stopped: '+str(state.get('stop_reason'))+'; explicit resume required')
    if not alive(ROOT/'library/batch.pid'):
        # Existing Task B launcher and progress are retained, never reconstructed.
        subprocess.run([sys.executable,'-B','-m','pipeline.batch','resume','--set','library'],cwd=ROOT,check=True)
    update('backend_pass1',checkpoint='library/progress.json')
    last=0
    while True:
        state=maybe(ROOT/'library/progress.json')
        if state.get('status')=='complete':break
        if time.monotonic()-last>=10800:append_progress('Pass 1');last=time.monotonic()
        if str(state.get('status','')).startswith('stopped'):
            raise RuntimeError('Library stop condition: '+str(state.get('stop_reason')))
        if (ROOT/'library/stop.requested').exists():raise RuntimeError('Stop requested; not overridden')
        if not alive(ROOT/'library/batch.pid'):
            raise RuntimeError('Library worker exited; checkpoints retained; inspect library/daemon.log before resume')
        time.sleep(30)

def worker(module,*args):
    dest=DEST/'logs';dest.mkdir(parents=True,exist_ok=True)
    name='_'.join(args) or module.rsplit('.',1)[-1]
    started=time.monotonic();started_utc=now()
    update('worker',module=module,arguments=list(args),started_utc=started_utc)
    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','OMP_NUM_THREADS':'2','MKL_NUM_THREADS':'2'}
    with (dest/(name+'.log')).open('ab',buffering=0) as f:
        process=subprocess.Popen([sys.executable,'-B','-m',module,*args],cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT)
        save(DEST/'active_worker.json',{'pid':process.pid,'module':module,'arguments':list(args),'started_utc':now()})
        last=0
        while process.poll() is None:
            if time.monotonic()-last>=10800:append_progress(name);last=time.monotonic()
            time.sleep(30)
        if process.returncode:raise RuntimeError('Worker failed: '+module+' '+name+'; see '+str(dest/(name+'.log')))
    with (DEST/'stage_times.jsonl').open('a',encoding='utf8') as f:
        f.write(__import__('json').dumps({'module':module,'arguments':list(args),'started_utc':started_utc,'finished_utc':now(),'wall_seconds':time.monotonic()-started})+'\n')

def run():
    pre_scoring()
    library_pass()
    worker('emotion_r2.replication.inputs')
    commit(['emotion_r2/replication/replication_sample.json'],'Record registered pre-scoring replication draw')
    for role in ('separate','T1','T3','L1','M1','M2','M3','gate','V1','V3'):
        worker('emotion_r2.replication.worker',role)
    worker('emotion_r2.replication.display','T1')
    worker('emotion_r2.replication.display','T3')
    worker('emotion_r2.replication.display','combine')
    worker('emotion_r2.replication.display','L1')
    worker('emotion_r2.replication.finish','freeze')
    commit(['emotion_r2/replication/frozen_manifest.json'],'Freeze scored replication library before registered statistics')
    worker('emotion_r2.replication.finish','analyze')
    integrity('end')
    worker('emotion_r2.replication.finish','report')
    commit(['emotion_r2/REPLICATION_REPORT.md','emotion_r2/REPLICATION_PROGRESS.md',
            'emotion_r2/replication/integrity_end.json'],'Report registered library replication and protected integrity')
    update('complete')
    append_progress('Complete')
    (DEST/'keep_awake.stop').touch()

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['start','run','status']);a=p.parse_args()
    if a.action=='status':print(maybe(DEST/'progress.json'));return
    if a.action=='start':
        pid=DEST/'supervisor.pid'
        if pid.exists():
            try:os.kill(int(pid.read_text()),0)
            except (ProcessLookupError,ValueError):pass
            else:raise RuntimeError('Task K supervisor already running')
        with (DEST/'supervisor.log').open('ab',buffering=0) as log:
            proc=subprocess.Popen([sys.executable,'-B','-m','emotion_r2.replication.supervisor','run'],cwd=ROOT,
                stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,
                env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        pid.write_text(str(proc.pid));print('Started Task K supervisor PID '+str(proc.pid));return
    try:run()
    except Exception as error:
        update('stopped_error',error=type(error).__name__+': '+str(error))
        append_progress('STOPPED: '+str(error))
        raise
    finally:(DEST/'supervisor.pid').unlink(missing_ok=True)

if __name__=='__main__':main()
