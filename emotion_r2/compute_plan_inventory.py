"""Count registered sample inputs for runtime planning, without model scoring."""
import collections,csv,json,math,sys
from pathlib import Path
if sys.executable!='/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
here=Path(__file__).resolve().parent;root=here.parent
with (root/'mvp_325/summary.csv').open(encoding='utf-8-sig',newline='') as f: songs=list(csv.DictReader(f))
counts=collections.Counter()
for song in songs:
    path=here/'sections_input'/f"{song['track_id']}.json"; data=json.loads(path.read_text(encoding='utf8'))
    for sec in data['sections']:
        counts['sections']+=1
        if sec.get('has_text'):counts['text_sections']+=1
        a,b=sec.get('start_s'),sec.get('end_s')
        if isinstance(a,(int,float)) and isinstance(b,(int,float)) and b>a:
            counts['all_timed_windows']+=math.ceil(b-a)
            if song.get('ring1_source')!='instrumental':counts['vocal_song_timed_windows']+=math.ceil(b-a)
            if sec.get('set')=='primary':counts['primary_timed_windows']+=math.ceil(b-a)
out={'songs':len(songs),'counts':dict(counts),'note':'Counts are upper bounds before stem availability and voice-activity gating; no model scores computed.'}
(here/'compute_plan_inventory.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf8');print(json.dumps(out,indent=2))
