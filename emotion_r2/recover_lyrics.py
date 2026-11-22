"""Recover attributable text from validated cached LRCLIB alignments; keep text private."""
import collections
import csv
import json
import subprocess
import sys
from pathlib import Path

if sys.executable != '/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
sys.path.insert(0,str(ROOT))
from pipeline.line_alignment import align_lines
from pipeline.lrclib import parse_lrc

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}

def match_plain_to_synced(plain, synced):
    """Pair source and timed lyric lines in order, accepting the existing 0.65 threshold."""
    lines=[x.strip() for x in (plain or '').splitlines() if x.strip()]
    matches=align_lines(lines,[row['text'] for row in synced],min_similarity=.65)
    return {m['target_index']:m for m in matches}

def main():
    if not read(HERE/'integrity_start.json').get('passed'): raise RuntimeError('Initial integrity check failed')
    ignore=subprocess.run(['git','check-ignore','-q','emotion_r2/private/probe.json'],cwd=ROOT)
    if ignore.returncode: raise RuntimeError('Private lyrics directory is not git ignored')
    with (ROOT/'mvp_325/summary.csv').open(encoding='utf-8-sig',newline='') as f: songs=list(csv.DictReader(f))
    out=HERE/'private'; out.mkdir(exist_ok=True)
    results=[]
    for song in songs:
        tid=song['track_id']; original=read(ROOT/'emotion/private'/f'{tid}.json')
        folder=ROOT/('outputs' if song['cohort']=='random' else 'mvp_325/songs')/tid
        stage=read(folder/'lyrics.json').get('data') or {}; lr=read(folder/'lrclib.json')
        valid_lrc=any(x.get('source')=='lrclib' and x.get('status')=='synced_retrieved' for x in stage.get('attempts') or [])
        synced=parse_lrc(lr.get('syncedLyrics') or '') if valid_lrc else []
        matches=match_plain_to_synced(lr.get('plainLyrics'),synced) if synced and lr.get('plainLyrics') else {}
        sections=read(ROOT/'emotion/validation/input_sections'/f'{tid}.json').get('sections') or []
        by_id={x.get('section_id'):x for x in original.get('sections') or []}
        private_rows=[]
        for sec in sections:
            sid=sec['section_id']; existing=by_id.get(sid) or {'section_id':sid,'lines':[],'text':''}
            lines=existing.get('lines') or []; text=existing.get('text') or ''
            status='existing_text' if text else 'unavailable'
            reason=None; matched=0; candidates=0
            if not text and sec.get('text_source')=='missing_whisperx_lines' and sec.get('set')=='primary':
                a,b=sec.get('start_s'),sec.get('end_s')
                if not valid_lrc:
                    reason='no_validated_synced_lrclib_cache'
                elif not lr.get('plainLyrics'):
                    reason='no_plain_lyrics_for_order_alignment'
                elif not matches:
                    reason='plain_to_synced_alignment_unavailable'
                else:
                    indexes=[i for i,line in enumerate(synced) if a <= line['start_s'] < b]
                    candidates=len(indexes); accepted=[i for i in indexes if i in matches]; matched=len(accepted)
                    if not indexes: reason='no_synced_lines_in_validated_section'
                    elif not accepted: reason='no_order_aligned_synced_lines_in_section'
                    else:
                        lines=[synced[i]['text'] for i in accepted]; text='\n'.join(lines)
                        status='recovered' if matched==candidates else 'recovered_partial'
                        reason=None if matched==candidates else 'some_synced_lines_failed_0_65_alignment'
                results.append({'track_id':tid,'section_id':sid,'status':status,'reason':reason,
                                'source':'validated_lrclib_plain_to_synced' if text else None,
                                'matched_lines':matched,'timed_lines_in_section':candidates,
                                'timing_caveat':'LRCLIB timestamps mapped to the validated WhisperX section span; transcription words were not retained.'})
            private_rows.append({'section_id':sid,'lines':lines,'text':text})
        (out/f'{tid}.json').write_text(json.dumps({'track_id':tid,'cohort':song['cohort'],'sections':private_rows},ensure_ascii=False)+'\n',encoding='utf-8')
    counts=collections.Counter(r['status'] for r in results); reasons=collections.Counter(r['reason'] for r in results if r['reason'])
    report={'affected_sections':len(results),'status_counts':dict(counts),'unavailable_reasons':dict(reasons),'sections':results,
            'privacy':'Lyric text written only under ignored emotion_r2/private/',
            'selection':'Only source LRCLIB caches previously marked synced_retrieved; original duration rejection remains binding.',
            'alignment':'Existing order-preserving matcher with 0.65 minimum line similarity; section spans retain existing timings.'}
    (HERE/'recovery_results.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='sections'},indent=2))

if __name__=='__main__': main()
