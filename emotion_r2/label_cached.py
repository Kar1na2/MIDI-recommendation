"""Use existing local Genius headers and LRCLIB paragraph structure, without exporting lyrics."""
import collections
import csv
import json
import re
import sys
from pathlib import Path

if sys.executable != '/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
sys.path.insert(0,str(ROOT))
from pipeline.genius import parse_html
from pipeline.line_alignment import align_lines
from pipeline.lrclib import parse_lrc, parse_paragraphs
from pipeline.lyrics import paragraph_groups, timed_sections

def read(path): return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}

def assign_genius(sections, lines, genius, duration):
    source=[x for section in genius for x in section['lines']]
    matches=align_lines(source,[x['text'] for x in lines])
    times=timed_sections(genius,lines,matches,'lrclib',duration,confidence_threshold=.65)
    owner=[]
    for i,sec in enumerate(genius): owner.extend([i]*len(sec['lines']))
    bytarget={m['target_index']:owner[m['source_index']] for m in matches}
    results={}
    for sec in sections:
        a,b=sec.get('start_s'),sec.get('end_s')
        if not isinstance(a,(int,float)) or not isinstance(b,(int,float)) or b<=a: continue
        indices=[i for i,x in enumerate(lines) if a<=x['start_s']<b]
        hits=collections.Counter(bytarget[i] for i in indices if i in bytarget)
        if not hits or not indices: continue
        idx,count=hits.most_common(1)[0]; confidence=count/len(indices)
        if confidence < .65 or times[idx]['match_confidence'] < .65: continue
        results[sec['section_id']]={'label':genius[idx]['label'],'display_label':genius[idx]['display_label'],
                                    'genius_section_index':idx,'line_assignment_confidence':confidence,
                                    'genius_alignment_confidence':times[idx]['match_confidence']}
    return results

def main():
    if not read(HERE/'integrity_start.json').get('passed'): raise RuntimeError('Initial integrity check failed')
    (HERE/'labels').mkdir(exist_ok=True)
    with (ROOT/'mvp_325/summary.csv').open(encoding='utf-8-sig',newline='') as f: songs=list(csv.DictReader(f))
    counts=collections.Counter(); errors=collections.Counter()
    for song in songs:
        tid=song['track_id']; folder=ROOT/('outputs' if song['cohort']=='random' else 'mvp_325/songs')/tid
        stage=read(folder/'lyrics.json').get('data') or {}; lr=read(folder/'lrclib.json')
        sections=read(ROOT/'emotion/validation/input_sections'/f'{tid}.json').get('sections') or []
        isvalid=stage.get('source')=='lrclib' or any(x.get('source')=='lrclib' and x.get('status')=='synced_retrieved' for x in stage.get('attempts') or [])
        lines=parse_lrc(lr.get('syncedLyrics') or '') if isvalid else []
        groups=[]; labels={}; source=None
        if lines and lr.get('plainLyrics'):
            try:
                duration=float(lr['duration']); paragraphs=parse_paragraphs(lr['plainLyrics'],lines,duration)
                raw_count=len([x for x in re.split(r'\n\s*\n',lr['plainLyrics']) if x.strip()])
                if paragraphs and len(paragraphs)!=raw_count:
                    errors['paragraph_missing_alignment']+=1
                else:
                    for item in paragraph_groups(lr['plainLyrics'],paragraphs):
                        groups.append({k:item[k] for k in ('index','start_s','end_s','line_count','group','likely_chorus')})
            except Exception as error: errors['paragraph_'+type(error).__name__]+=1
        if lines:
            for p in (ROOT/'data/genius_html'/f'{tid}.html',HERE/'private/genius_html'/f'{tid}.html'):
                if not p.is_file(): continue
                try:
                    parsed=parse_html(p.read_text(encoding='utf-8-sig'))
                    labels=assign_genius(sections,lines,parsed,float(lr['duration'])); source=str(p.relative_to(ROOT))
                    break
                except Exception as error: errors['genius_'+type(error).__name__]+=1
        out=[]
        for sec in sections:
            label=sec.get('label'); is_primary=sec.get('set')=='primary'
            was_unlabeled=str(label or '').casefold() in ('unlabeled','unlabelled','')
            if is_primary:
                counts['primary']+=1
                if not was_unlabeled: counts['labelled_before']+=1
            inferred=None; a,b=sec.get('start_s'),sec.get('end_s')
            if groups and isinstance(a,(int,float)) and isinstance(b,(int,float)) and b>a:
                overlaps=[(max(0,min(b,p['end_s'])-max(a,p['start_s'])),p) for p in groups]
                best=max(overlaps,key=lambda x:x[0])
                if best[0]>0: inferred={'group':best[1]['group'],'likely_chorus':best[1]['likely_chorus'],
                                        'source':'lrclib_paragraph_repetition','verification':'inferred'}
            replacement=labels.get(sec['section_id']) if was_unlabeled else None
            if is_primary and (not was_unlabeled or replacement): counts['labelled_after']+=1
            if is_primary and replacement: counts['new_labels']+=1
            if is_primary and inferred: counts['inferred_groups']+=1
            out.append({'section_id':sec['section_id'],'set':sec.get('set'),'label':replacement['label'] if replacement else label,
                        'genius_alignment':replacement,'inferred_group':inferred})
        if source: counts['parseable_genius_songs']+=1
        (HERE/'labels'/f'{tid}.json').write_text(json.dumps({'track_id':tid,'sections':out,'paragraphs':groups,
                                                               'genius_html_source':source},indent=2)+'\n',encoding='utf-8')
    result={'counts':dict(counts),'errors':dict(errors),'genius_alignment_threshold':.65,
            'scope':'325-song development set; cached pages only',
            'privacy':'No lyric lines are present in these files; paragraph groups are inferred, not labels.'}
    (HERE/'label_cache_results.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2),flush=True)

if __name__=='__main__': main()
