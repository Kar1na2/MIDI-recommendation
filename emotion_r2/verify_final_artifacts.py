"""Verify Task F delivery artifacts; this is not a change to the registered tests."""
import csv, json, math, re, sys
from html.parser import HTMLParser
from pathlib import Path

if sys.executable != '/home/usr/venvs/midi/bin/python':
    raise RuntimeError('Use the specified interpreter')
here=Path(__file__).resolve().parent
root=here.parent
def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
def validate(value, spec, path='$'):
    if 'oneOf' in spec:
        count=0
        for sub in spec['oneOf']:
            try: validate(value,sub,path);count+=1
            except ValueError: pass
        if count != 1: raise ValueError(f'{path}: oneOf matches {count}')
    if 'const' in spec and value != spec['const']: raise ValueError(f'{path}: const')
    if 'enum' in spec and value not in spec['enum']: raise ValueError(f'{path}: enum')
    types=spec.get('type',[])
    if isinstance(types,str): types=[types]
    def istype(t):
        return {'object':lambda:isinstance(value,dict),'array':lambda:isinstance(value,list),
                'string':lambda:isinstance(value,str),'null':lambda:value is None,
                'number':lambda:isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)}[t]()
    if types and not any(istype(t) for t in types): raise ValueError(f'{path}: expected {types}')
    if isinstance(value,dict):
        for key in spec.get('required',[]):
            if key not in value: raise ValueError(f'{path}: missing {key}')
        props=spec.get('properties',{})
        if spec.get('additionalProperties') is False and set(value)-set(props): raise ValueError(f'{path}: extra properties')
        for key,v in value.items():
            if key in props: validate(v,props[key],path+'.'+key)
    if isinstance(value,list):
        if 'maxItems' in spec and len(value)>spec['maxItems']:raise ValueError(f'{path}: maxItems')
        if 'items' in spec:
            for i,v in enumerate(value):validate(v,spec['items'],f'{path}[{i}]')
def scan(value, path='$'):
    forbidden={'text','lyrics','lyric','plainlyrics','syncedlyrics','content'}
    if isinstance(value,dict):
        for k,v in value.items():
            if k.casefold() in forbidden: raise ValueError(f'possible lyric field: {path}.{k}')
            scan(v,path+'.'+k)
    elif isinstance(value,list):
        for i,v in enumerate(value):scan(v,f'{path}[{i}]')
    elif isinstance(value,str) and len(value)>500:
        raise ValueError(f'long free-text field: {path}')
class Links(HTMLParser):
    def __init__(self): super().__init__(); self.external=[]
    def handle_starttag(self,tag,attrs):
        for key,val in attrs:
            if key in ('src','href','action') and val and re.match(r'(?i)\s*(https?:)?//',val):self.external.append(val)
with (root/'mvp_325/summary.csv').open(encoding='utf-8-sig',newline='') as f: songs=list(csv.DictReader(f))
ids={s['track_id'] for s in songs}
assert len(songs)==len(ids)==325
schema=load(here/'digest/frontend_data/schema.json')
frontend=here/'digest/frontend_data'
for song in songs:
    tid=song['track_id']; folder=here/'songs'/tid
    for name in ('song.json','sections.json','curves.parquet'):
        if not (folder/name).is_file():raise FileNotFoundError(folder/name)
    record=load(frontend/(tid+'.json')); validate(record,schema); scan(record)
    assert record['track_id']==tid and record['cohort'] in ('random','pilot')
corpus=load(frontend/'corpus.json');validate(corpus,schema);scan(corpus)
assert {p.stem for p in frontend.glob('*.json')}-{ 'schema','corpus'} == ids
charts=here/'digest/charts'
pngs={p.stem for p in charts.glob('*.png')}; jsons={p.stem for p in charts.glob('*.json')}
assert pngs==jsons and {'chorus_lift_distribution','trust_round1_vs_round2','feature_importance'} <= pngs
selection=load(here/'validation/card_selection.json')['track_ids']
assert len(selection)<=23 and set(selection)<=ids
assert {p.stem for p in (here/'digest/cards').glob('*.md')}==set(selection)
assert {'timeline_'+tid for tid in selection}<=pngs
html=(here/'digest/index.html').read_text(encoding='utf8'); parser=Links(); parser.feed(html)
assert not parser.external and "default-src 'none'" in html
masks=0
for tid in ids:
    one=load(here/'scores/V1'/f'{tid}.json');three=load(here/'scores/V3'/f'{tid}.json')
    def mask(d):
        return [(s['section_id'],[(x['start_s'],x['end_s'],x['active']) for x in s.get('frames',[])]) for s in d.get('sections',[])]
    if mask(one)!=mask(three): raise ValueError('V1/V3 window mismatch '+tid)
    masks+=1
result={'passed':True,'song_count':len(ids),'schema_valid_song_files':len(ids),'schema_validator':'local implementation of keywords used in schema.json, not a full JSON Schema implementation',
        'lyric_key_and_long_string_scan':'passed','card_count':len(selection),'png_and_json_chart_pairs':len(pngs),
        'html_no_remote_resources_and_csp':'passed','matching_V1_V3_window_masks':masks}
(here/'validation/artifact_verification.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
print(json.dumps(result,indent=2))
