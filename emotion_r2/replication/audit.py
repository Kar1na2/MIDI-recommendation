"""Read-only cohort inventory, storage/timing audit, and four protected snapshots."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import statistics
import sys
import unicodedata

if sys.executable != '/home/usr/venvs/midi/bin/python':
    raise RuntimeError('Use /home/usr/venvs/midi/bin/python')
ROOT = Path(__file__).resolve().parents[2]
R2 = ROOT / 'emotion_r2'
DEST = R2 / 'replication'

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf8')
    temp.replace(path)

def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def now():
    return datetime.now(timezone.utc).isoformat()

def rows(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def snapshot4():
    target = R2 / 'replication_development_snapshot.json'
    if target.exists():
        return
    paths = [R2 / 'REPORT.md']
    for name in ('scores', 'songs', 'validation', 'digest'):
        paths += [p for p in (R2/name).rglob('*') if p.is_file()]
    def record(p):
        return p.relative_to(R2).as_posix(), {'size':p.stat().st_size, 'sha256':sha(p)}
    with ThreadPoolExecutor(max_workers=8) as pool:
        files = dict(pool.map(record, sorted(paths)))
    save(target, {'algorithm':'sha256', 'created_utc':now(), 'scope':'Completed 325-song round-2 outputs: scores/, songs/, validation/, REPORT.md, digest/',
                  'track_ids':sorted(r['track_id'] for r in rows(ROOT/'mvp_325/summary.csv')), 'files':files})

def verify(snapshot, base):
    data = read(snapshot)
    def check(item):
        name, expected = item
        p = base/name
        if not p.is_file(): return name, 'missing'
        return name, 'unchanged' if p.stat().st_size == expected['size'] and sha(p) == expected['sha256'] else 'changed'
    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(check, data['files'].items()))
    counts = Counter(status for _,status in result)
    return {'total':len(result), 'unchanged':counts['unchanged'],
            'changed':[name for name,status in result if status=='changed'],
            'missing':[name for name,status in result if status=='missing'],
            'passed':counts['unchanged']==len(result)}

def integrity(phase):
    snapshot4()
    specs = [('round1',R2/'r1_snapshot.json',ROOT/'emotion'),
             ('v1',ROOT/'analysis_v2/v1_snapshot.json',ROOT),
             ('mvp_325',ROOT/'mvp_325/snapshot.json',ROOT/'mvp_325'),
             ('round2_325',R2/'replication_development_snapshot.json',R2)]
    result = {'checked_utc':now()}
    for key, snapshot, base in specs:
        print('Verifying '+key, flush=True)
        result[key] = verify(snapshot, base)
        print(key+': unchanged: '+str(result[key]['unchanged'])+'/'+str(result[key]['total']), flush=True)
    result['passed'] = all(result[key]['passed'] for key,_,_ in specs)
    save(DEST/('integrity_'+phase+'.json'), result)
    if not result['passed']:
        raise RuntimeError('Protected snapshot mismatch; see integrity_'+phase+'.json')
    return result

def normalize(text):
    text = unicodedata.normalize('NFKC',str(text or '')).casefold()
    return re.sub(r'[^\w]+',' ',text).strip()

def title_core(text):
    text = re.sub(r'\([^)]*\)|\[[^]]*\]', '', text or '')
    text = re.split(r'\s+-\s+',text)[0]
    return normalize(text)

def size(path):
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file()) if path.is_dir() else 0

def inventory():
    selected = rows(ROOT/'mvp_325/summary.csv')
    ids = {s['track_id'] for s in selected}
    if len(ids)!=325 or len(selected)!=325: raise RuntimeError('Invalid protected cohort')
    source = read(ROOT/'batch/manifest.json')['songs']
    # The existing batch manifest contains local audio only. Missing tracks are
    # a separate inventory and must remain visible rather than disappearing.
    for s in rows(ROOT/'batch/missing_audio.csv'):
        if s['track_id'] not in {x['track_id'] for x in source}:
            source.append({**s,'audio_path':'','duration_s':float(s['duration_s']) if s.get('duration_s') else None})
    metadata = {s['track_id']:s for s in read(ROOT/'mvp/sample.json')['songs']+read(ROOT/'pilot/selection.json')['songs']}
    if set(metadata)!=ids: raise RuntimeError('Library exclusion IDs do not equal protected 325')
    library = []
    duplicates = []
    for s in source:
        tid=s['track_id']
        if tid in ids: continue
        s={**s, 'cohort':'replication_library', 'audio_exists':bool(s.get('audio_path') and Path(s['audio_path']).is_file())}
        library.append(s)
        for dev in metadata.values():
            artist = normalize(s.get('artist'))
            same_artist = bool(artist and artist==normalize(dev.get('artist')))
            if same_artist and title_core(s.get('title'))==title_core(dev.get('title')):
                duplicates.append({'track_id':tid,'title':s.get('title'),'artist':s.get('artist'),
                                   'development_track_id':dev['track_id'],'development_title':dev.get('title'),
                                   'reason':'same normalized artist and base title (version suffix/bracket removed)',
                                   'action':'retain; exclude only in predeclared sensitivity analysis'})
    library.sort(key=lambda s:s['track_id'])
    if len(library)!=len({s['track_id'] for s in library}): raise RuntimeError('Duplicate track IDs in source')
    save(DEST/'inventory.json', {'created_utc':now(),'excluded_count':325, 'total':len(library),
                              'audio_available':sum(s['audio_exists'] for s in library),
                              'missing_audio':sum(not s['audio_exists'] for s in library),'songs':library})
    fields=['track_id','title','artist','development_track_id','development_title','reason','action']
    with (DEST/'near_duplicates.csv').open('w',encoding='utf8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(duplicates)
    footprints=[]
    for s in selected:
        tid=s['track_id']
        folder=ROOT/('outputs' if s['cohort']=='random' else 'mvp_325/songs')/tid
        footprints.append({'track_id':tid,'backend_bytes':size(folder),'emotion_stem_bytes':size(ROOT/'emotion/stems'/tid),
                           'r2_score_bytes':sum((R2/'scores'/role/f'{tid}.json').stat().st_size for role in ('T1','T3','L1','M3','V1','V3','voice_gate') if (R2/'scores'/role/f'{tid}.json').is_file())})
    means={k:statistics.mean(s[k] for s in footprints) for k in ('backend_bytes','emotion_stem_bytes','r2_score_bytes')}
    free=shutil.disk_usage(ROOT).free
    pending=sum(s.get('status') not in ('done','failed','skipped') for s in read(ROOT/'library/progress.json')['songs'].values())
    projected=pending*means['backend_bytes']+sum(s['audio_exists'] for s in library)*(means['emotion_stem_bytes']+means['r2_score_bytes'])
    measured=defaultdict(list)
    for p in (ROOT/'mvp/run_log.jsonl',ROOT/'pilot/run_log.jsonl'):
        if not p.exists():continue
        for line in p.read_text(encoding='utf8').splitlines():
            try:r=json.loads(line)
            except ValueError:continue
            if r.get('song_id') in ids and r.get('status') in ('ok','complete') and r.get('seconds') is not None:
                measured[r['stage']].append(float(r['seconds']))
    for role in ('T1','T3','L1','M3','V1','V3'):
        p=R2/(role.lower()+'_run.log')
        if not p.exists():continue
        for line in p.read_text(encoding='utf8').splitlines():
            try:r=json.loads(line)
            except ValueError:continue
            if r.get('seconds') is not None: measured[role].append(float(r['seconds']))
    for role,name in (('M1','m1_scores.log'),('M2','m2_scores.log')):
        p=ROOT/'emotion'/name
        if p.exists():
            for line in p.read_text(encoding='utf8').splitlines():
                try:r=json.loads(line)
                except ValueError:continue
                if r.get('seconds') is not None:measured[role].append(float(r['seconds']))
    for tid in ids:
        p=ROOT/'emotion/stems'/tid/'separation.json'
        if p.is_file() and read(p).get('wall_seconds') is not None:
            measured['emotion_separation'].append(read(p)['wall_seconds'])
    timings={k:{'observations':len(v),'mean_seconds':statistics.mean(v),'total_logged_seconds':sum(v),
                'projected_library_hours':statistics.mean(v)*sum(s['audio_exists'] for s in library)/3600} for k,v in measured.items() if v}
    worker_wall={'M3':13404,'V1':660,'V3':3603}
    for k,v in worker_wall.items():
        timings[k]={'basis':'Actual completed 325-run worker start/end timestamps in continuation_run.log; includes overhead, not utilization',
                    'development_wall_seconds':v,'projected_library_hours':v/325*sum(s['audio_exists'] for s in library)/3600}
    gate=read(R2/'voice_gate_validation.json')
    timings['pYIN_gate']={'basis':'Actual seeded ten-song development validation; extrapolation only',
                         'development_wall_seconds':gate['seconds'],
                         'projected_library_hours':gate['seconds']/10*sum(s['audio_exists'] for s in library)/3600}
    display=read(R2/'display/L1_reading_summary.json')
    timings['L1_display']={'basis':'Actual 325-run display summary; includes identical-input reuse',
                          'development_wall_seconds':display['seconds_this_run'],
                          'projected_library_hours':display['seconds_this_run']/325*sum(s['audio_exists'] for s in library)/3600}
    result={'created_utc':now(),'library_count':len(library),'audio_available':sum(s['audio_exists'] for s in library),
            'missing_audio':sum(not s['audio_exists'] for s in library),'pending_backend':pending,
            'near_duplicate_ids':len({r['track_id'] for r in duplicates}), 'mean_325_bytes':means,
            'free_bytes':free,'projected_additional_bytes':projected,'margin':1.3,'disk_passed':free>=1.3*projected,
            'stage_timings_from_actual_logs':timings,
            'timing_limitations':'Inference logs omit some startup/verification/interruption overhead. T1 development timings include round-one reuse, so do not provide a reliable full-rescoring estimate. Lyrics and input preparation are unlogged; zero is not inferred. Backend routing timings are reported separately and are not part of Task B Pass 1. Projections use mean song durations and are refreshed from this run.'}
    save(DEST/'preflight_inventory.json',result)
    print(json.dumps(result,indent=2),flush=True)
    if not result['disk_passed']:raise RuntimeError('Insufficient disk with 1.3x measured projection')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['start','end','inventory']);a=p.parse_args()
    inventory() if a.action=='inventory' else integrity(a.action)
