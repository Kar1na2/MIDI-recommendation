"""Core lyric timing: manual, LRCLIB, CPU WhisperX, explicit activity fallback."""
from pathlib import Path
import os
import subprocess
import sys
from . import genius, genius_matching, lrclib, lyrics
from .common import read, save, StageError
from .config import LYRIC_ALIGNMENT_CONFIDENCE
from .line_alignment import align_lines, normalize
from .lyric_validation import diagnose_sections, per_section_ring, vocal_activity

MIDI_PYTHON='/home/usr/venvs/midi/bin/python'
WHISPERX_DEPENDENCIES='/home/usr/venvs/whisperx/lib/python3.11/site-packages'
ROOT=Path(__file__).resolve().parents[1]


def cpu_alignment(payload, output):
    request=Path(output)/'whisperx_request.json'; target=Path(output)/'whisperx.json'
    payload={**payload,'device':'cpu'}
    if request.exists() and target.exists() and read(request)==payload:
        return read(target)
    save(request,payload)
    env={**os.environ,'CUDA_VISIBLE_DEVICES':'',
         'PYTHONPATH':str(ROOT)+os.pathsep+WHISPERX_DEPENDENCIES}
    process=subprocess.run([MIDI_PYTHON,'-S','-m','pipeline.whisperx_runner',
        '--request',str(request),'--output',str(target)],cwd=ROOT,env=env,
        capture_output=True,text=True,timeout=1200)
    if process.returncode:
        raise StageError('CPU WhisperX failed: '+process.stderr[-1200:])
    return read(target)


def cached_source(song, folders):
    for folder in folders:
        for name in ('pilot_genius.json','genius_sections.json','genius.json'):
            path=folder/name
            if path.exists():
                value=read(path); value=value.get('data',value)
                if value.get('sections') and any(s.get('lines') for s in value['sections']):
                    return value
    if song.get('genius_url'):
        return genius.fetch(song,folders[0])
    return None


def valid_lrc_sections(source,lrc,duration,language):
    flat=[line for s in source for line in s['lines']]
    matches=align_lines(flat,[l['text'] for l in lrc['lines']],romanize=True,language=language)
    sections=lyrics.timed_sections(source,lrc['lines'],matches,'lrclib',duration,LYRIC_ALIGNMENT_CONFIDENCE)
    for s in sections:
        s['validated']=not s['flags'] and s['raw_end_s'] is not None and s['raw_start_s'] is not None
        s['failure_reasons']=list(s['flags']); s['status']='complete' if s['validated'] else 'review'
        s['display_label']=f"{s['label']} {s['number']}"
    return sections


def run(song,output,grid,stems,cache_dirs=()):
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    stems=stems or {'vocals':str(output/'stems/vocals.wav')}
    if 'stems' in stems: stems=stems['stems']
    folders=[output]+[Path(p) for p in cache_dirs]
    duration=grid['duration_s']; attempts=[]; source=None; lrc=None; pending=False
    language=song.get('language'); sections=[]; source_url=None
    if language in ('N/A','unknown','und'): language=None
    if song.get('manual_sections_csv'):
        # Invalid manual input is a hard error, not a reason to try a lower source.
        sections=lyrics.manual_sections(song['manual_sections_csv'],None,duration)
        try:
            parsed=cached_source(song,folders)
            if parsed:
                source=parsed['sections']; source_url=parsed.get('source_url')
                sections=lyrics.manual_sections(song['manual_sections_csv'],source,duration)
        except Exception as error:
            attempts.append({'source':'genius','status':'rejected','reason':str(error)})
        for s in sections: s.update(validated=True,status='complete',display_label=f"{s['label']} {s['number']}")
        attempts.append({'source':'manual','status':'accepted'})
        result_source='manual'
    else:
        attempts.append({'source':'manual','status':'not_configured'})
        lrc_folder=next((p for p in folders if (p/'lrclib.json').exists()),output)
        try:
            lrc=lrclib.fetch(song,duration,lrc_folder)
            attempts.append({'source':'lrclib','status':'synced_retrieved','line_count':len(lrc['lines'])})
        except Exception as error:
            attempts.append({'source':'lrclib','status':'rejected','reason':f'{type(error).__name__}: {error}'})
            if (lrc_folder/'lrclib.json').exists() and read(lrc_folder/'lrclib.json').get('instrumental') is True:
                result={'status':'complete','source':'instrumental','sections':[],
                    'validated_section_count':0,'attempts':attempts,'language':language,
                    'instrumental_evidence':'LRCLIB instrumental=true'}
                save(output/'lyrics.json',{'status':'complete','data':result}); return result
        try:
            source=cached_source(song,folders)
            if source:
                attempts.append({'source':'genius','status':'cached_source'})
            else:
                matching=genius_matching.lookup(song,genius._access_token())
                save(output/'genius_candidates.json',matching)
                pending=matching['status']=='pending_genius'
                attempts.append({'source':'genius','status':matching['status'],'reason':matching.get('reason')})
                if matching.get('source_url'):
                    source=genius.fetch({**song,'genius_url':matching['source_url']},output)
        except Exception as error:
            attempts.append({'source':'genius','status':'rejected','reason':f'{type(error).__name__}: {error}'})
        if source:
            source_url=source.get('source_url')
            source=source['sections']
            for i,s in enumerate(source):
                s.setdefault('genius_section_index',i); s.setdefault('number',i+1); s.setdefault('performers',[])
        if lrc and source:
            sections=valid_lrc_sections(source,lrc,duration,language)
            attempts.append({'source':'lrclib','status':'accepted' if all(s['validated'] for s in sections) else 'partially_accepted',
                'sections_validated':sum(s['validated'] for s in sections),'sections_total':len(sections),
                'rejections':[{'index':i,'reason':s['failure_reasons']} for i,s in enumerate(sections) if not s['validated']]})
        elif lrc and lrc['lrclib_paragraphs']:
            groups=lyrics.paragraph_groups(lrc.get('plain_lyrics',''),lrc['lrclib_paragraphs'])
            sections=[{'label':'Unlabeled','number':p['index']+1,'display_label':f"Paragraph {p['index']+1}",
                'raw_start_s':p['start_s'],'raw_end_s':p['end_s'],'timing_source':'lrclib_paragraphs',
                'validated':p['end_s']>p['start_s'],'status':'complete','labels':'unverified',
                'match_confidence':None,'flags':[],'genius_section_index':None,
                'group':groups[p['index']]['group'],'likely_chorus':groups[p['index']]['likely_chorus']} for p in lrc['lrclib_paragraphs']]
            attempts.append({'source':'lrclib','status':'accepted_paragraph_timing','labels':'unverified'})
        elif lrc:
            attempts.append({'source':'lrclib','status':'rejected','reason':'No paragraph breaks and no usable Genius section text'})
        result_source='lrclib' if sections else None
        # A queued Genius lookup remains pending; it is never reported as failed.
        if not pending and (not sections or not all(s['validated'] for s in sections)):
            try:
                aligned=None; flat=[line for s in (source or []) for line in s['lines']]
                for folder in folders:
                    for name in ('pilot_whisperx.json','whisperx.json'):
                        path=folder/name
                        if path.exists():
                            value=read(path)
                            if flat and [normalize(x) for x in flat]==[normalize(r.get('text','')) for r in value.get('lines',[])]:
                                aligned=value; break
                    if aligned: break
                if aligned is None:
                    aligned=cpu_alignment({'vocals':stems['vocals'],'lines':flat,'language':language},output)
                language=aligned.get('language',language)
                if source:
                    wx=diagnose_sections(source,aligned['lines'],duration)
                else:
                    wx=aligned.get('sections',[])
                if sections:
                    sections=[s if s['validated'] else wx[i] for i,s in enumerate(sections)]
                    result_source='lrclib'
                else:
                    sections=wx; result_source='whisperx'
                attempts.append({'source':'whisperx','status':'accepted' if any(s['validated'] for s in sections) else 'rejected',
                    'device':'cpu','sections_validated':sum(s['validated'] for s in sections),
                    'rejections':[s.get('failure_reasons',[]) for s in sections if not s['validated']]})
            except Exception as error:
                attempts.append({'source':'whisperx','status':'rejected','device':'cpu','reason':f'{type(error).__name__}: {error}'})
    valid=sum(s.get('validated',False) for s in sections)
    if pending:
        result_source='pending_genius'; status='pending_genius'; ring={}
    elif valid:
        status='complete'; ring=per_section_ring(sections,duration,stems['vocals'])
    else:
        if not Path(stems['vocals']).exists():
            save(output/'lyric_attempts.json',{'attempts':attempts,
                'warnings':[a.get('reason','') for a in attempts if a.get('status')=='rejected'],
                'next_source':'whisperx'})
            raise StageError('WhisperX/activity fallback requires successful source separation and its vocals stem')
        activity=vocal_activity(stems['vocals'],duration)
        result_source='vocal_activity'; status='complete'; ring={'ring1_segments':activity['segments']}
        attempts.append({'source':'vocal_activity','status':'accepted','reason':'No higher-priority source validated a section'})
    agreement=lyrics.paragraph_agreement(sections,lrc['lrclib_paragraphs'],lrc['lines']) if lrc and source and sections else None
    # Snapped coordinates are diagnostics only. They never invalidate valid raw timing.
    if sections: lyrics.decorate(sections,grid)
    result={**ring,'status':status,'source':result_source,'timing_source':result_source,
        'labels':'verified' if source else 'unverified',
        'sections':sections,'validated_section_count':valid,'genius_section_count':len(source) if source else None,
        'source_url':source_url,'language':language,'attempts':attempts,
        'timing_sources_used':sorted({s.get('timing_source','unknown') for s in sections}),
        'paragraph_agreement':agreement,'lrclib_paragraphs':lrc['lrclib_paragraphs'] if lrc else [],
        'confidence_threshold':LYRIC_ALIGNMENT_CONFIDENCE}
    save(output/'lyric_attempts.json',{'attempts':attempts,
        'warnings':[a.get('reason','') for a in attempts if a.get('status')=='rejected']})
    save(output/'lyrics.json',{'schema_version':2,'status':status,'data':result,
        'labels':result['labels'],'lrclib_paragraphs':result['lrclib_paragraphs']})
    return result
