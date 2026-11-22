"""Recover only with both local Genius text and retained WhisperX word timing."""
import collections, csv, json, sys
from pathlib import Path
if sys.executable != '/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
HERE=Path(__file__).resolve().parent; ROOT=HERE.parent
def read(p): return json.loads(p.read_text(encoding='utf-8-sig')) if p.is_file() else {}
def count_words(obj):
    if isinstance(obj,list): return sum(count_words(x) for x in obj)
    if not isinstance(obj,dict): return 0
    count=0
    for key,val in obj.items():
        if key in ('words','word_segments') and isinstance(val,list):
            count+=sum(1 for word in val if isinstance(word,dict) and isinstance(word.get('start'),(float,int)) and isinstance(word.get('end'),(float,int)))
        elif isinstance(val,(list,dict)): count+=count_words(val)
    return count
def main():
    with (ROOT/'mvp_325/summary.csv').open(encoding='utf-8-sig',newline='') as f: songs={x['track_id']:x for x in csv.DictReader(f)}
    pending=[x for x in read(HERE/'recovery_results.json')['sections'] if x['status']=='unavailable']
    reasons=collections.Counter(); results=[]
    for item in pending:
        tid=item['track_id']; song=songs[tid]; folder=ROOT/('outputs' if song['cohort']=='random' else 'mvp_325/songs')/tid
        html=any(p.is_file() for p in (ROOT/'data/genius_html'/f'{tid}.html', HERE/'private/genius_html'/f'{tid}.html'))
        words=sum(count_words(read(folder/name)) for name in ('whisperx.json','pilot_whisperx.json'))
        if html and words:
            raise RuntimeError('Found word timing; implement alignment before declaring recovery complete')
        reason='no_cached_genius_html' if not html else 'no_retained_whisperx_word_timings'
        reasons[reason]+=1
        results.append({'track_id':tid,'cohort':song['cohort'],'section_id':item['section_id'],'recovered':False,'reason':reason,'cached_html':html,'timed_words':words})
    out={'attempted_where_both_exist':0,'pending_sections':len(pending),'recovered':0,'failure_reasons':dict(reasons),'sections':results}
    (HERE/'genius_word_recovery.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in out.items() if k!='sections'},indent=2))
if __name__=='__main__': main()
