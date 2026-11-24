"""Audit cached-source coverage without printing or exporting lyric text."""
import collections
import csv
import json
import sys
from pathlib import Path

if sys.executable != '/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
HERE=Path(__file__).resolve().parent
ROOT=HERE.parent

def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}

def main():
    if not load(HERE/'integrity_start.json').get('passed'): raise RuntimeError('Initial audit did not pass')
    with (ROOT/'emotion/sections.csv').open(encoding='utf-8-sig',newline='') as f:
        rows=[r for r in csv.DictReader(f) if r['set']=='primary' and r['text_source']=='missing_whisperx_lines']
    counts=collections.Counter(); details=[]; attempted=set(); rejections=collections.Counter()
    for row in rows:
        tid=row['track_id']; folder=ROOT/('outputs' if row['cohort']=='random' else 'mvp_325/songs')/tid
        lyr=load(folder/'lyrics.json').get('data') or {}; lrc=load(folder/'lrclib.json'); whisper=load(folder/'whisperx.json')
        genius=[load(folder/name) for name in ('genius_sections.json','pilot_genius.json','genius.json')]
        genius=[x.get('data',x) for x in genius if x]
        source=next((x for x in genius if x.get('sections') and x.get('source_url')==lyr.get('source_url')),None)
        section_index=int(row['section_id'].split('-')[1]); section=(lyr.get('sections') or [])[section_index]
        info={'track_id':tid,'section_id':row['section_id'],'source':lyr.get('source'),
              'source_url_present':bool(lyr.get('source_url')),'genius_section_index':section.get('genius_section_index'),
              'matching_genius_section_cache':bool(source),
              'lrclib_plain':bool(lrc.get('plainLyrics')),'lrclib_synced':bool(lrc.get('syncedLyrics')),
              'whisperx_line_count':len(whisper.get('lines') or []),
              'whisperx_section_count':len(whisper.get('sections') or [])}
        if tid not in attempted:
            attempted.add(tid)
            for attempt in lyr.get('attempts') or []:
                if attempt.get('source') == 'lrclib':
                    reason=str(attempt.get('reason') or '')
                    category=('duration_mismatch' if 'duration' in reason and 'match' in reason else
                              'no_synced_lyrics' if 'no synced lyrics' in reason else
                              'not_found' if '404' in reason else
                              'other_or_unknown')
                    rejections[category]+=1
        for k in ('matching_genius_section_cache','lrclib_plain','lrclib_synced','source_url_present'):
            if info[k]: counts[k]+=1
        counts['source_'+str(info['source'])]+=1
        details.append(info)
    result={'affected_sections':len(rows),'affected_songs':len({r['track_id'] for r in rows}),
            'coverage':dict(counts),'lrclib_rejection_categories_by_song':dict(rejections),'sections':details,
            'explanation':'Round-one recovery required whisperx.json lines, but the persisted alignment may contain sections only.'}
    (HERE/'recovery_diagnosis.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='sections'},indent=2))

if __name__=='__main__': main()
