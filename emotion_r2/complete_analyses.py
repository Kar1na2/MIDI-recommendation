"""Finish descriptive exports and registered exploratory text comparison."""
import collections,csv,json,sys
from pathlib import Path
if sys.executable!='/home/usr/venvs/midi/bin/python':raise RuntimeError('Wrong interpreter')
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent
sys.path.insert(0,str(ROOT/'emotion'))
from finalize_data import read,save,csvout,gate_audit
from statistics_r2 import chorus_lift

def main():
    with (ROOT/'mvp_325/summary.csv').open(encoding='utf-8-sig',newline='') as f:songs=list(csv.DictReader(f))
    rows=read(HERE/'validation/sections_analysis.json');gate=gate_audit(songs,rows)
    for g in gate['songs']:
        p=HERE/'songs'/g['track_id']/'song.json';meta=read(p);meta['voice_gate']=g;save(p,meta)
    text={}
    for cohort in ('random','pilot','combined'):
        subset=[r for r in rows if r['set']=='primary' and (cohort=='combined' or r['cohort']==cohort)]
        text[cohort]={'analysis':'Registered exploratory Chorus–Verse words-valence comparison, not confirmatory',
                     'overall':chorus_lift(subset,'words_valence'),
                     'by_language':{lang:chorus_lift([r for r in subset if (r.get('language') or 'uncertain')==lang],'words_valence') for lang in sorted({r.get('language') or 'uncertain' for r in subset})}}
    save(HERE/'validation/exploratory_text_chorus_lift.json',text)
    langs={}
    for cohort in ('random','pilot','combined'):
        selected=[s for s in songs if cohort=='combined' or s['cohort']==cohort]
        langs[cohort]={'sampling_note':'random primary sample' if cohort=='random' else 'not a random sample',
                       'song_languages':dict(collections.Counter(read(HERE/'songs'/s['track_id']/'song.json')['language'] for s in selected)),
                       'primary_section_languages':dict(collections.Counter(r.get('language') or 'uncertain' for r in rows if r['set']=='primary' and (cohort=='combined' or r['cohort']==cohort)))}
    save(HERE/'validation/language_by_cohort.json',langs)
    print('Gate, text exploratory, and language summaries finalized',flush=True)
if __name__=='__main__':main()
