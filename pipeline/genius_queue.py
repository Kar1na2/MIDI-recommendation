"""Permanent API response cache, shared throttle and resumable rate-limit queue."""
from pathlib import Path
import time
import datetime as dt
from email.utils import parsedate_to_datetime
from contextlib import contextmanager
from .common import read, save, fingerprint, StageError
from .config import GENIUS_THROTTLE_SECONDS

ROOT=Path(__file__).resolve().parents[1]


class GeniusPending(StageError):
    def __init__(self, key, retry_at, reason):
        self.key, self.retry_at=key,retry_at
        super().__init__(reason)


@contextmanager
def locked(path):
    import fcntl
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX)
        try: yield
        finally: fcntl.flock(stream,fcntl.LOCK_UN)


def retry_delay(value, now=None):
    now=time.time() if now is None else now
    try: return max(0.,float(value))
    except (TypeError,ValueError):
        try: return max(0.,parsedate_to_datetime(value).timestamp()-now)
        except (TypeError,ValueError,OverflowError): return 60.


def request(endpoint, params, token, cache=None):
    import requests
    key=fingerprint({'endpoint':endpoint,'params':params})
    base=ROOT/'data/genius_api'; permanent=base/f'{key}.json'
    legacy=Path(cache)/f'{key}.json' if cache else None
    if permanent.exists(): return read(permanent)['response']
    if legacy and legacy.exists():
        value=read(legacy); save(permanent,value); return value['response']
    queue_path=ROOT/'data/genius_queue.json'
    with locked(base/'api.lock'):
        queue=read(queue_path) if queue_path.exists() else {'schema_version':1,'requests':{}}
        prior=queue['requests'].get(key,{})
        if prior.get('status')=='waiting' and prior.get('retry_at',0)>time.time():
            raise GeniusPending(key,prior['retry_at'],'Genius API request is queued until Retry-After expires')
        throttle=base/'throttle.json'
        last=read(throttle).get('last_request_at',0) if throttle.exists() else 0
        time.sleep(max(0.,GENIUS_THROTTLE_SECONDS-(time.time()-last)))
        response=requests.get('https://api.genius.com'+endpoint,params=params,
            headers={'Authorization':'Bearer '+token,'Accept':'application/json',
                     'User-Agent':'MIDI-Recommendation/0.1'},timeout=25)
        now=time.time(); save(throttle,{'last_request_at':now})
        try: body=response.json()
        except ValueError: body={'text':response.text}
        saved={'endpoint':endpoint,'params':params,'http_status':response.status_code,
               'received_at':now,'retry_after':response.headers.get('Retry-After'),'body':body}
        save(base/'responses'/key/f'{time.time_ns()}.json',saved)
        if response.status_code==429 or response.status_code>=500:
            retry_at=now+retry_delay(response.headers.get('Retry-After'))
            queue['requests'][key]={'endpoint':endpoint,'params':params,'status':'waiting',
                'retry_at':retry_at,'http_status':response.status_code,'attempts':prior.get('attempts',0)+1}
            save(queue_path,queue)
            raise GeniusPending(key,retry_at,f'Genius HTTP {response.status_code}; queued for a later retry')
        if response.status_code in (401,403):
            raise StageError(f'Genius API HTTP {response.status_code}; no alternate access path attempted')
        response.raise_for_status()
        payload=body.get('response',{})
        save(permanent,{'endpoint':endpoint,'params':params,'response':payload})
        if key in queue['requests']:
            queue['requests'][key].update(status='complete',completed_at=now); save(queue_path,queue)
        return payload
