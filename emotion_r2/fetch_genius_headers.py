"""Populate a round-two Genius queue, with no lyrics sent to any service."""
import csv
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

if sys.executable != '/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
sys.path.insert(0,str(ROOT))
from pipeline import genius, genius_matching, genius_queue
from pipeline.common import fingerprint

def read(p): return json.loads(p.read_text(encoding='utf-8-sig')) if p.is_file() else {}

def active_batch():
    import subprocess
    lines=subprocess.run(['ps','-eo','args='],capture_output=True,text=True,check=True).stdout.splitlines()
    return any('pipeline.batch' in line and 'python' in line for line in lines)

def write_manual(completed):
    with (HERE/'genius_manual_save.csv').open('w',encoding='utf-8',newline='') as out:
        w=csv.DictWriter(out,fieldnames=['track_id','title','artist','URL']); w.writeheader()
        for x in completed.values():
            if x.get('status')=='http_403': w.writerow({'track_id':x['track_id'],'title':x['title'],'artist':x['artist'],'URL':x.get('url')})

def main():
    if not read(HERE/'integrity_start.json').get('passed'): raise RuntimeError('Starting audit did not pass')
    token=genius._access_token(ROOT/'.env')
    if not token: raise RuntimeError('GENIUS_ACCESS_TOKEN is unavailable')
    if active_batch(): raise RuntimeError('Batch active: defer requests to preserve its global Genius throttle')
    with (ROOT/'emotion/songs.csv').open(encoding='utf-8-sig',newline='') as f: songs=list(csv.DictReader(f))
    targets=[]
    for song in songs:
        labeldata=read(HERE/'labels'/f"{song['track_id']}.json")
        if any(s.get('set')=='primary' and str(s.get('label') or '').casefold() in ('unlabeled','unlabelled','') for s in labeldata.get('sections',[])):
            targets.append(song)
    genius_queue.ROOT=HERE
    original_request=genius_queue.request
    def request_with_original_cache(endpoint,params,auth,cache=None):
        if active_batch(): raise RuntimeError('Batch started; round-two Genius queue stopped')
        key=fingerprint({'endpoint':endpoint,'params':params})
        cached=ROOT/'data/genius_api'/f'{key}.json'
        if cached.is_file(): return read(cached)['response']
        return original_request(endpoint,params,auth,cache)
    genius_queue.request=request_with_original_cache
    progress=HERE/'genius_header_progress.jsonl'; completed={}
    if progress.exists():
        for line in progress.read_text(encoding='utf-8').splitlines():
            x=json.loads(line); completed[x['track_id']]=x
    private=HERE/'private/genius_html'; private.mkdir(parents=True,exist_ok=True)
    last_page_request=0.0
    with progress.open('a',encoding='utf-8') as log:
        for i,song in enumerate(targets,1):
            tid=song['track_id']; prior=completed.get(tid)
            if prior and prior.get('status') in ('page_saved','already_parseable','http_403','no_match'): continue
            if prior and prior.get('status')=='page_rate_limited' and prior.get('retry_at',0)>time.time(): continue
            if active_batch(): raise RuntimeError('Batch started; queue stopped without interfering')
            folder=ROOT/('outputs' if song['cohort']=='random' else 'mvp_325/songs')/tid
            local=ROOT/'data/genius_html'/f'{tid}.html'; cached=read(folder/'genius_candidates.json')
            stage=read(folder/'lyrics.json').get('data') or {}; url=cached.get('source_url') or stage.get('source_url')
            outcome={'track_id':tid,'title':song['title'],'artist':song['artist'],'url':url,'status':None}
            previous_403=any('403' in str(x.get('reason') or '') for x in stage.get('attempts') or [] if isinstance(x,dict))
            if local.is_file():
                try:
                    genius.parse_html(local.read_text(encoding='utf-8-sig')); outcome['status']='already_parseable'
                except Exception: pass
            try:
                if outcome['status'] is None and url is None:
                    lookup=genius_matching.lookup({'artist':song['artist'],'title':song['title']},token,folder/'genius_search_cache')
                    url=lookup.get('source_url'); outcome['url']=url
                    if lookup.get('status')=='pending_genius': outcome.update(status='pending_genius',pending=lookup.get('pending'))
                    elif not url: outcome['status']='no_match'
                if outcome['status'] is None and url:
                    if urlparse(url).hostname not in ('genius.com','www.genius.com'): raise ValueError('Non-Genius URL rejected')
                    denied=ROOT/'data/genius_api/page_denials'/f'{fingerprint(url)}.json'
                    if previous_403 or denied.exists(): outcome['status']='http_403'
                    else:
                        time.sleep(max(0,5-(time.monotonic()-last_page_request)))
                        response=requests.get(url,headers={'User-Agent':'MIDI-Recommendation/0.1 (local research)'},timeout=25)
                        last_page_request=time.monotonic()
                        if response.status_code==403: outcome['status']='http_403'
                        elif response.status_code==429:
                            retry_after=response.headers.get('Retry-After')
                            outcome.update(status='page_rate_limited',retry_after=retry_after,
                                           retry_at=time.time()+genius_queue.retry_delay(retry_after))
                        elif response.status_code!=200: outcome.update(status='page_error',http_status=response.status_code)
                        else:
                            genius.parse_html(response.text)
                            (private/f'{tid}.html').write_text(response.text,encoding='utf-8')
                            outcome['status']='page_saved'
            except Exception as error:
                outcome.update(status='error',error=type(error).__name__+': '+str(error))
            completed[tid]=outcome; log.write(json.dumps(outcome,ensure_ascii=False)+'\n'); log.flush(); write_manual(completed)
            if i%10==0: print(f'Genius queue {i}/{len(targets)}',flush=True)
    write_manual(completed)
    from collections import Counter
    print(json.dumps({'target_songs':len(targets),'statuses':dict(Counter(x['status'] for x in completed.values()))}),flush=True)

if __name__=='__main__': main()
