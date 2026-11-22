"""Re-evaluate sample language with confidence-weighted section votes, privately."""
import collections, csv, json, math, sys
from pathlib import Path

if sys.executable != '/home/usr/venvs/midi/bin/python': raise RuntimeError('Use the specified interpreter')
HERE=Path(__file__).resolve().parent; ROOT=HERE.parent
sys.path.insert(0, str(HERE/'vendor'))
sys.path.append('/home/usr/venvs/whisperx/lib/python3.11/site-packages')
import langid

ALLOWED={'en','ko','ja','zh','es','fr','de','pt','it'}
MIN_CONFIDENCE=.8  # Inherited language-detection confidence threshold, fixed before this evaluation.
def read(path): return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
def write(path,value): path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')

def choose_vote(votes, section_count, conflicts, instrumental=False):
    best=max(votes,key=votes.get) if votes else None
    confidence=votes[best]/section_count if best and section_count else None
    eligible=bool(best and best in ALLOWED and confidence>=MIN_CONFIDENCE and not conflicts)
    return ('not_applicable' if instrumental else best if eligible else 'uncertain'), best, confidence

def main():
    with (ROOT/'mvp_325/summary.csv').open(encoding='utf-8-sig',newline='') as f: songs=list(csv.DictReader(f))
    if len(songs)!=325 or len({s['track_id'] for s in songs})!=325: raise RuntimeError('Unexpected sample')
    pilot={r['track_id']:r for r in read(ROOT/'pilot/selection.json')['songs']}
    original=HERE/'language_round2_prior.csv'
    if not original.exists(): original.write_bytes((HERE/'language.csv').read_bytes())
    with original.open(encoding='utf-8-sig',newline='') as f: prior={r['track_id']:r for r in csv.DictReader(f)}
    model=langid.langid.LanguageIdentifier.from_modelstring(langid.langid.model,norm_probs=True)
    sections=[]; rows=[]; comparisons=collections.Counter(); changed=collections.Counter()
    for i,song in enumerate(songs,1):
        tid=song['track_id']; private=read(HERE/'private'/f'{tid}.json')
        text_by_id={s['section_id']:s.get('text') or '' for s in private.get('sections',[])}
        source=read(ROOT/'emotion/validation/input_sections'/f'{tid}.json')
        if source.get('track_id') not in (None,tid): raise RuntimeError('Section identity mismatch')
        votes=collections.Counter(); section_count=0; disagreement=0
        for sec in source.get('sections',[]):
            text=text_by_id.get(sec['section_id'],'')
            if sec.get('set')!='primary' or not text.strip(): continue
            letters=[c for c in text if c.isalpha()]
            if not letters: continue
            lang,confidence=model.classify(text); confidence=float(confidence)
            hangul=sum('\uac00'<=c<='\ud7af' for c in letters)/len(letters)
            kana=sum('\u3040'<=c<='\u30ff' for c in letters)/len(letters)
            conflict=(hangul>=.20 and lang!='ko') or (kana>=.10 and lang!='ja')
            if conflict: disagreement+=1
            else: votes[lang]+=confidence
            section_count+=1
            sections.append({'track_id':tid,'cohort':song['cohort'],'section_id':sec['section_id'],'predicted_language':lang,'confidence':confidence,'script_conflict':conflict,'round1_language':sec.get('language')})
        instrumental=song.get('ring1_source')=='instrumental' or bool((pilot.get(tid) or {}).get('instrumental'))
        # A low-confidence single vote must not become a certain majority.
        language,best,confidence=choose_vote(votes,section_count,disagreement,instrumental)
        known=(pilot.get(tid) or {}).get('language')
        if known and not instrumental:
            comparisons['known_pilots']+=1
            if language=='uncertain': comparisons['uncertain']+=1
            elif language==known: comparisons['correct']+=1
            else:
                comparisons['incorrect']+=1
                # Preserve the error in validation metrics, but do not publish
                # a confident label that conflicts with known pilot metadata.
                language='uncertain'
        previous=prior.get(tid,{})
        for sec in source.get('sections',[]):
            if sec.get('language')!=language: changed[str(sec.get('language'))+' -> '+language]+=1
        row={'track_id':tid,'cohort':song['cohort'],'language':language,'method':'confidence_weighted_primary_section_votes',
             'confidence':confidence,'round1_language':previous.get('round1_language'),'prior_round2_language':previous.get('language'),
             'text_sections_voted':section_count,'script_conflicts':disagreement,'raw_majority':best,'pilot_known_language':known}
        rows.append(row)
        if i%25==0: print('Language votes',i,'/',len(songs),flush=True)
    with (HERE/'language.csv').open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    write(HERE/'section_language_votes.json',sections)
    write(HERE/'language_weighted_summary.json',{'sections':len(sections),'songs':len(rows),'pilot_validation':dict(comparisons),
          'pilot_accuracy_all_known': comparisons['correct']/comparisons['known_pilots'] if comparisons['known_pilots'] else None,
          'pilot_accuracy_decided': comparisons['correct']/(comparisons['correct']+comparisons['incorrect']) if comparisons['correct']+comparisons['incorrect'] else None,
          'languages':dict(collections.Counter(r['language'] for r in rows)),'section_changes_from_round1':dict(changed),
          'uncertain_songs':[{'track_id':r['track_id'],'cohort':r['cohort'],'raw_majority':r['raw_majority'],'confidence':r['confidence'],'script_conflicts':r['script_conflicts']} for r in rows if r['language']=='uncertain'],
          'threshold':MIN_CONFIDENCE,'allowed_languages':sorted(ALLOWED), 'pilot_mismatches_marked_uncertain_after_validation':True})
    print(json.dumps({'songs':len(rows),'sections':len(sections),'pilot_validation':dict(comparisons)},indent=2))

if __name__=='__main__': main()
