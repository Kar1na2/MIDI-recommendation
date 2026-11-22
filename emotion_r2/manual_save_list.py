"""Rank sample songs by unlabeled primary sections without transmitting lyrics."""
import csv, json, sys
from pathlib import Path
from urllib.parse import quote
if sys.executable != '/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
HERE=Path(__file__).resolve().parent; ROOT=HERE.parent
def read(path): return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
with (ROOT/'mvp_325/summary.csv').open(encoding='utf-8-sig',newline='') as f: songs=list(csv.DictReader(f))
rows=[]
for song in songs:
    tid=song['track_id']; labels=read(HERE/'labels'/f'{tid}.json').get('sections') or []
    count=sum(1 for row in labels if row.get('set')=='primary' and (not str(row.get('label') or '').strip() or str(row.get('label')).strip().casefold().startswith('unlabel')))
    if not count: continue
    folder=ROOT/('outputs' if song['cohort']=='random' else 'mvp_325/songs')/tid
    report=read(folder/'report.json'); artist=report.get('artist') or ''; title=report.get('title') or song.get('title') or ''
    rows.append({'track_id':tid,'cohort':song['cohort'],'title':title,'artist':artist,'unlabeled_sections':count,
                 'genius_search_url':'https://genius.com/search?q='+quote(' '.join((artist,title)).strip(),safe='')})
rows.sort(key=lambda x:(-x['unlabeled_sections'],x['track_id']))
with (HERE/'genius_manual_save.csv').open('w',encoding='utf-8',newline='') as f:
    fields=['track_id','cohort','title','artist','unlabeled_sections','genius_search_url']; w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
print(json.dumps({'songs':len(rows),'unlabeled_sections':sum(r['unlabeled_sections'] for r in rows)}))
