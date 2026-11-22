"""Prepare new-song sections using the final development provenance/language rules."""
from collections import Counter
import hashlib
import random
import sys
from .audit import ROOT, R2, DEST, read, save, sha, now
from .controls import pre_scoring

sys.path[:0]=[str(R2),str(ROOT/'emotion'),str(R2/'vendor')]

def maybe(path):return read(path) if path.is_file() else {}

def prepare():
    pre_scoring()
    from emotion.prepare_inputs import prepare as prepare_source, timed_text
    from pipeline.lrclib import parse_lrc
    from pipeline.line_alignment import align_lines
    from recover_lyrics import match_plain_to_synced
    from language_weighted import choose_vote
    from langid.langid import LanguageIdentifier, model
    classifier=LanguageIdentifier.from_modelstring(model,norm_probs=True)
    songs=read(DEST/'inventory.json')['songs']
    progress=read(ROOT/'library/progress.json')
    prepared=[];eligibility=[];rejected=Counter()
    required=('tuning','beats','separation','transcription','quantize','layers','patterns','rhythm')
    for song in songs:
        tid=song['track_id'];folder=ROOT/'outputs'/tid
        stages={s:maybe(folder/(s+'.json')) for s in required}
        lyric=maybe(folder/'lyrics.json');data=lyric.get('data') or {}
        backend_ok=all(stages[s].get('status') in ('ok','ok_with_warning','complete') for s in required)
        reason=('missing_audio' if not song['audio_exists'] else
                'missing_or_failed_pass1_stage' if not backend_ok else
                'pending_genius' if lyric.get('status')=='pending_genius' else
                'missing_lyric_sections' if lyric.get('status') not in ('ok','ok_with_warning','complete') or not data.get('sections') else None)
        # This is exactly the original selection's eligibility, before emotion scoring.
        if reason:rejected[reason]+=1
        else:eligibility.append({k:song.get(k) for k in ('track_id','title','artist','audio_path','duration_s')})
        source_hashes={str(p.relative_to(ROOT)):sha(p) for p in [folder/(s+'.json') for s in required]+[folder/'lyrics.json',folder/'lrclib.json',folder/'whisperx.json',folder/'genius.json',folder/'genius_sections.json'] if p.is_file()}
        identity={'track_id':tid,'cohort':'replication_library','title':song.get('title'),'audio_path':song.get('audio_path'),
                  'ring1_source':data.get('source'),'source_hashes':source_hashes,'backend_ok':backend_ok,
                  'eligibility_reason':reason,'backend_failure':progress.get('songs',{}).get(tid,{}).get('failure_category')}
        target=DEST/'sections_input'/f'{tid}.json'
        if target.exists():
            cached=read(target)
            if cached.get('source_hashes')!=source_hashes:raise RuntimeError('Prepared backend input changed: '+tid)
            prepared.append({**identity,'language':cached.get('language')});continue
        if not backend_ok:
            save(target,{**identity,'status':'failed','failure_category':reason,'language':'uncertain','sections':[]})
            save(DEST/'private'/f'{tid}.json',{'track_id':tid,'cohort':'replication_library','sections':[]})
            prepared.append({**identity,'language':'uncertain'});continue
        private,public=prepare_source(song,'uncertain',folder)
        byid={s['section_id']:s for s in private}
        # Same development repair: retained, valid LRCLIB line timing plus a
        # source-line alignment; never place plain text using time alone.
        lrc=maybe(folder/'lrclib.json')
        lrc_valid=data.get('source')=='lrclib' or any(a.get('source')=='lrclib' and a.get('status')=='synced_retrieved' for a in data.get('attempts',[]))
        lines=parse_lrc(lrc.get('syncedLyrics') or '') if lrc_valid else []
        aligned=match_plain_to_synced(lrc.get('plainLyrics'),lines) if lines and lrc.get('plainLyrics') else {}
        originals=data.get('sections') or []
        for sec in public:
            original=next((s for i,s in enumerate(originals) if sec['section_id']==f'lyric-{i:04d}'),{})
            sec.update(has_text=bool(byid.get(sec['section_id'],{}).get('text')),genius_alignment=None,
                       inferred_group=original.get('group'),inferred_likely_chorus=original.get('likely_chorus'))
            if aligned and not sec['has_text'] and sec.get('text_source')=='missing_whisperx_lines' and sec.get('set')=='primary':
                recovered=[line['text'] for i,line in enumerate(lines) if i in aligned and sec['start_s']<=line['start_s']<sec['end_s']]
                if recovered:
                    text='\n'.join(recovered)
                    byid[sec['section_id']]={'section_id':sec['section_id'],'lines':recovered,'text':text}
                    sec.update(has_text=True,text_sha256=hashlib.sha256(text.encode()).hexdigest(),line_count=len(recovered),text_source='validated_lrclib_plain_to_synced')
        votes=Counter();count=0;conflicts=0
        for sec in public:
            text=byid.get(sec['section_id'],{}).get('text') or ''
            letters=[c for c in text if c.isalpha()]
            if sec.get('set')!='primary' or not letters:continue
            lang,confidence=classifier.classify(text);confidence=float(confidence)
            hangul=sum('\uac00'<=c<='\ud7af' for c in letters)/len(letters)
            kana=sum('\u3040'<=c<='\u30ff' for c in letters)/len(letters)
            conflict=(hangul>=.20 and lang!='ko') or (kana>=.10 and lang!='ja')
            if conflict:conflicts+=1
            else:votes[lang]+=confidence
            count+=1
        language,best,confidence=choose_vote(votes,count,conflicts,data.get('source')=='instrumental')
        for sec in public:
            sec.update(language=language,language_method='confidence_weighted_primary_section_votes',language_confidence=confidence)
        payload={**identity,'language':language,'language_votes':dict(votes),'text_sections_voted':count,'script_conflicts':conflicts,
                 'status':'complete' if public else 'no_sections','sections':public}
        save(target,payload)
        save(DEST/'private'/f'{tid}.json',{'track_id':tid,'cohort':'replication_library','sections':list(byid.values())})
        prepared.append({**identity,'language':language})
        print(tid+': input prepared (no model scores)',flush=True)
    eligibility.sort(key=lambda s:s['track_id'])
    selected=random.Random(20261115).sample(eligibility,min(300,len(eligibility)))
    draw={'seed':20261115,'selection_utc':now(),'eligible_count':len(eligibility),'selected_count':len(selected),
          'sampling':'Original selection: sort eligible track IDs, uniformly sample without replacement; no emotion-conditioned exclusion or resampling',
          'eligible_ids':[s['track_id'] for s in eligibility],'rejected_counts':dict(rejected),'songs':selected}
    target=DEST/'replication_sample.json'
    if target.exists():
        existing=read(target)
        if existing['songs']!=draw['songs'] or existing['eligible_ids']!=draw['eligible_ids']:raise RuntimeError('Frozen pre-scoring draw changed')
    else:save(target,draw)
    save(DEST/'prepared_songs.json',{'songs':prepared,'created_utc':now()})

if __name__=='__main__':prepare()
