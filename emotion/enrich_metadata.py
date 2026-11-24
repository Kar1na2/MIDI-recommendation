"""Add audited source metadata to completed Task D public song artifacts."""
import csv
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent

def load(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def main():
 if not (HERE/'REPORT.md').is_file(): raise RuntimeError('Task D report is not complete')
 if not load(HERE/'validation'/'integrity_end.json').get('passed'):
  raise RuntimeError('Task D end-integrity verification did not pass')
 sources=load(ROOT/'mvp'/'sample.json')['songs']+load(ROOT/'pilot'/'selection.json')['songs']
 metadata={row['track_id']:row for row in sources}
 if len(metadata)!=325: raise ValueError('Expected 325 unique manifest rows')
 csv_path=HERE/'songs.csv'
 with csv_path.open(encoding='utf-8-sig',newline='') as stream:
  reader=csv.DictReader(stream); fields=list(reader.fieldnames or []); rows=list(reader)
 if len(rows)!=325 or set(r['track_id'] for r in rows)!=set(metadata):
  raise ValueError('Song output and manifest IDs disagree')
 for field in ('artist','duration_s'):
  if field not in fields: fields.append(field)
 missing=[]
 for row in rows:
  track=row['track_id']; source=metadata[track]; path=HERE/'songs'/track/'song.json'; item=load(path)
  if item.get('track_id')!=track or item.get('cohort')!=row.get('cohort'):
   raise ValueError(f'Output identity mismatch: {track}')
  duration=load(HERE/'stems'/track/'separation.json').get('audio_duration_s')
  artist=source.get('artist') or None
  if artist is None or not isinstance(duration,(float,int)): missing.append(track)
  item['artist']=artist; item['duration_s']=duration
  item['status']='instrumental_only' if item.get('ring1_source')=='instrumental' else 'scored'
  temp=path.with_suffix('.json.tmp'); temp.write_text(json.dumps(item,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); temp.replace(path)
  row['artist']=artist; row['duration_s']=duration
 temp=csv_path.with_suffix('.csv.tmp')
 with temp.open('w',encoding='utf-8',newline='') as stream:
  writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)
 temp.replace(csv_path)
 output={'songs':len(rows),'missing_artist_or_duration':missing,'artist_source':'mvp/sample.json and pilot/selection.json',
         'duration_source':'emotion/stems/<id>/separation.json'}
 (HERE/'validation'/'metadata_enrichment.json').write_text(json.dumps(output,indent=2)+'\n',encoding='utf-8')
 print(json.dumps(output))

if __name__=='__main__': main()
