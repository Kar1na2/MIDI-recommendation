import json,csv,random,hashlib
from pathlib import Path
root=Path('.')
songs=sorted((root/'docs/data/instrumentals/songs').glob('*.json'))
man=json.loads((root/'docs/data/instrumentals/manifest.json').read_text(encoding='utf-8'))
ready=[t for t in man['tracks'] if t['status']=='ready']
print('songs',len(songs),'ready',len(ready),'total',man['total_tracks'])
assert {p.stem for p in songs}=={t['track_id'] for t in ready}
problems=[]
for p in songs:
    d=json.loads(p.read_text(encoding='utf-8'))
    v=d['views']
    if set(v)!={'instrumental','bass','drums','other'}: problems.append((p.name,'views'))
    if len(v['instrumental']['layers'])!=3: problems.append((p.name,'main-depth'))
    for s in ('bass','drums','other'):
        if len(v[s]['layers'])!=2: problems.append((p.name,s+'-depth'))
    dur=d['duration']
    for name,view in v.items():
        for layer in view['layers']:
            segs=layer['segments']
            if abs(segs[0]['start'])>1e-6 or abs(segs[-1]['end']-dur)>0.05: problems.append((p.name,name,'coverage'))
            if any(abs(a['end']-b['start'])>1e-6 for a,b in zip(segs,segs[1:])): problems.append((p.name,name,'gap'))
            if any(s['end']<=s['start'] for s in segs): problems.append((p.name,name,'empty'))
    if v['instrumental']['preview']['file']!='original.mp3': problems.append((p.name,'preview'))
    if set(d.get('audio',{}))!={'instrumental','bass','drums','other'}: problems.append((p.name,'audio'))
print('structure problems',len(problems),problems[:5])
rows=list(csv.DictReader(open('instrumental-audio-host/upload_manifest.csv',encoding='utf-8')))
print('upload rows',len(rows),'unique keys',len({r['object_key'] for r in rows}))
print('missing files',sum(1 for r in rows if not Path(r['local_path']).is_file()))
print('byte mismatches',sum(1 for r in rows if int(r['bytes'])!=Path(r['local_path']).stat().st_size))
print('total GiB',round(sum(int(r['bytes']) for r in rows)/1024**3,2))
sample=random.Random(7).sample(rows,5)
print('sample hashes ok',all(hashlib.sha256(Path(r['local_path']).read_bytes()).hexdigest()==r['sha256'] for r in sample))

