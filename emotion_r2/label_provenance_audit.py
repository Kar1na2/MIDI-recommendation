"""Summarize section-label provenance without exposing lyric text."""
import collections,csv,json,re,sys
from pathlib import Path
if sys.executable!='/home/usr/venvs/midi/bin/python':raise RuntimeError('Wrong interpreter')
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent
def read(p):return json.loads(p.read_text(encoding='utf-8-sig')) if p.is_file() else {}
with (ROOT/'mvp_325/summary.csv').open(encoding='utf-8-sig',newline='') as f:songs=list(csv.DictReader(f))
counts=collections.Counter()
for song in songs:
    tid=song['track_id'];folder=ROOT/('outputs' if song['cohort']=='random' else 'mvp_325/songs')/tid
    orig=(read(folder/'lyrics.json').get('data') or {}).get('sections') or []
    for sec in read(HERE/'sections_input'/f'{tid}.json').get('sections',[]):
        if sec.get('set')!='primary':continue
        label=str(sec.get('label') or '').strip().casefold()
        if not re.fullmatch(r'(chorus|verse)(?:\s+\d+)?',label):continue
        match=re.fullmatch(r'lyric-(\d+)',sec['section_id']);idx=int(match.group(1)) if match else -1
        source=orig[idx] if 0<=idx<len(orig) else {}
        direct=isinstance(source.get('genius_section_index'),int) or bool(sec.get('genius_alignment'))
        counts['genius_proven' if direct else 'no_genius_index']+=1
        counts['source_'+str(source.get('timing_source'))]+=1
out={'counts':dict(counts),'policy':'Only explicitly indexed Genius or validated Genius-aligned labels qualify for H1.'}
(HERE/'label_provenance_audit.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf8');print(json.dumps(out,indent=2))
