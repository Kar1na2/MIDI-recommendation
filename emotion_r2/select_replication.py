"""Choose a fixed-seed sample from completed non-MVP library tracks."""
import csv
import json
import random
import sys
from collections import Counter
from pathlib import Path

if sys.executable != '/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
SEED=20261115

def read(path): return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}

def main():
    if not read(HERE/'integrity_start.json').get('passed'): raise RuntimeError('Initial audit failed')
    with (ROOT/'mvp_325/summary.csv').open(encoding='utf-8-sig',newline='') as f:
        excluded={r['track_id'] for r in csv.DictReader(f)}
    manifest=read(ROOT/'library/manifest.json').get('songs',[])
    eligible=[]; rejected=Counter()
    required=('tuning','beats','separation','transcription','quantize','layers','patterns','rhythm')
    for song in manifest:
        tid=song['track_id']
        if tid in excluded: rejected['mvp_or_pilot']+=1; continue
        folder=ROOT/'outputs'/tid
        if not Path(song['audio_path']).is_file(): rejected['missing_audio']+=1; continue
        if any(read(folder/f'{name}.json').get('status') not in ('ok','ok_with_warning','complete') for name in required):
            rejected['missing_or_failed_pass1_stage']+=1; continue
        lyrics=read(folder/'lyrics.json'); data=lyrics.get('data') or {}
        if lyrics.get('status') not in ('ok','ok_with_warning','complete') or not data.get('sections'):
            rejected['missing_lyric_sections']+=1; continue
        eligible.append({'track_id':tid,'title':song['title'],'artist':song.get('artist'),
                         'audio_path':song['audio_path'],'duration_s':song.get('duration_s')})
    eligible.sort(key=lambda x:x['track_id'])
    chosen=random.Random(SEED).sample(eligible,min(300,len(eligible)))
    output={'seed':SEED,'eligible_count':len(eligible),'selected_count':len(chosen),'rejected_counts':dict(rejected),
            'sampling':'Uniform random sample without replacement from eligible completed non-MVP tracks sorted by track_id',
            'songs':chosen}
    (HERE/'replication_sample.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in output.items() if k!='songs'},indent=2))

if __name__=='__main__': main()
