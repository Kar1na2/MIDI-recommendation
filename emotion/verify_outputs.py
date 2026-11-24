"""Read-only QA of Task D public outputs; never reads emotion/private."""
import csv
import json
from pathlib import Path

import pyarrow.parquet as pq

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
FORBIDDEN={'text','lyrics','plainlyrics','syncedlyrics','lyric_text','audio'}

def forbidden_fields(obj,where=''):
 found=[]
 if isinstance(obj,dict):
  for key,value in obj.items():
   label=f'{where}.{key}' if where else key
   if key.casefold() in FORBIDDEN: found.append(label)
   found.extend(forbidden_fields(value,label))
 elif isinstance(obj,list):
  for i,value in enumerate(obj): found.extend(forbidden_fields(value,f'{where}[{i}]'))
 return found

def verify():
 with (ROOT/'mvp_325'/'summary.csv').open(encoding='utf-8-sig',newline='') as f:
  songs=list(csv.DictReader(f))
 errors=[]
 for song in songs:
  track=song['track_id']; dest=HERE/'songs'/track
  for name in ('sections.json','song.json','curves.parquet'):
   if not (dest/name).is_file(): errors.append(f'{track}: missing {name}')
  for name in ('sections.json','song.json'):
   path=dest/name
   if not path.is_file(): continue
   data=json.loads(path.read_text(encoding='utf-8'))
   if data.get('track_id')!=track or data.get('cohort')!=song['cohort']:
    errors.append(f'{track}: {name} identity/cohort mismatch')
   fields=forbidden_fields(data)
   if fields: errors.append(f'{track}: forbidden content fields in {name}: {fields[:5]}')
  path=dest/'curves.parquet'
  if path.is_file():
   try:
    schema=pq.read_schema(path)
    for name in schema.names:
     if name.casefold() in FORBIDDEN: errors.append(f'{track}: forbidden parquet field {name}')
   except Exception as exc: errors.append(f'{track}: unreadable parquet: {exc}')
 for name in ('REPORT.md','SCHEMA.md','models.json','sections.csv','songs.csv'):
  if not (HERE/name).is_file(): errors.append(f'missing {name}')
 for name in ('integrity_start.json','integrity_end.json','trust.json','shuffle_control.json','separation_check.json'):
  path=HERE/'validation'/name
  if not path.is_file(): errors.append(f'missing validation/{name}')
  elif name.startswith('integrity_') and not json.loads(path.read_text(encoding='utf-8')).get('passed'):
   errors.append(f'validation/{name} failed')
 return {'songs_checked':len(songs),'passed':not errors,'error_count':len(errors),'errors':errors}

if __name__=='__main__':
 result=verify(); print(json.dumps(result,indent=2)); raise SystemExit(0 if result['passed'] else 1)
