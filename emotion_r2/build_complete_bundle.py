"""Create a portable, lyric-free Task F results bundle from explicit paths."""
import csv, hashlib, json, sys, zipfile
from pathlib import Path

if sys.executable != '/home/usr/venvs/midi/bin/python': raise RuntimeError('Use specified interpreter')
HERE=Path(__file__).resolve().parent; ROOT=HERE.parent
dest=HERE/'TASK_F_COMPLETE.zip'
names=['REPORT.md','STATUS.md','CONTINUATION_STATUS.md','SCHEMA.md','ANALYSIS_PLAN.md','INHERITED_RUN_AUDIT.md',
       'models.json','models_amended.json','models_amended.sha256','l1_prompt.txt',
       'integrity_finish_start.json','integrity_finish_end.json','r1_snapshot.json',
       'round1_reuse_audit.json','voice_gate_validation.json','v3_real_smoke.json',
       'language_weighted_summary.json','language_revision_audit.json','language.csv',
       'genius_word_recovery.json','genius_manual_save.csv','label_provenance_audit.json',
       'explanation_phase1.json','compute_estimate.json','songs.csv','sections.csv',
       'finalize_data.py','complete_analyses.py','write_final_report.py','build_digest_r2.py',
       'verify_final_artifacts.py','build_complete_bundle.py']
files={HERE/n for n in names}
for folder in ('digest','songs','validation','sections_input'):
    files.update(p for p in (HERE/folder).rglob('*') if p.is_file())
for role in ('T1','T3','L1','M3','V1','V3','voice_gate'):
    files.update((HERE/'scores'/role).glob('*.json'))
files.update(HERE.glob('test_*.py'))
files.update([ROOT/'analysis_v2/v1_snapshot.json',ROOT/'mvp_325/snapshot.json',
              ROOT/'mvp_325/summary.csv',ROOT/'emotion/validation/trust.json',
              ROOT/'pilot/HAND_CHECK_SECTIONS.csv',ROOT/'docs/REPO_STATE.md'])
for p in files:
    if not p.is_file():raise FileNotFoundError(p)
forbidden={'text','lyrics','lyric','plainlyrics','syncedlyrics','content'}
def check(v,name):
    if isinstance(v,dict):
        for k,x in v.items():
            if k.casefold() in forbidden:raise ValueError('Potential lyric field '+name+': '+k)
            check(x,name)
    elif isinstance(v,list):
        for x in v:check(x,name)
manifest=[]
with zipfile.ZipFile(dest,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in sorted(files):
        name=p.relative_to(ROOT).as_posix()
        if any(part in ('private','model_cache','scratch') for part in p.parts):raise ValueError(name)
        data=p.read_bytes()
        if p.suffix=='.json' and (name.startswith(('emotion_r2/scores/','emotion_r2/sections_input/','emotion_r2/validation/','emotion_r2/digest/frontend_data/')) or name.endswith('genius_word_recovery.json')):
            check(json.loads(data),name)
        if p.suffix=='.csv':
            with p.open(encoding='utf-8-sig',newline='') as f: fields=next(csv.reader(f),[])
            if any(k.casefold() in forbidden for k in fields):raise ValueError('Potential lyric column '+name)
        z.writestr(name,data);manifest.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    z.writestr('BUNDLE_README.txt','Start with emotion_r2/REPORT.md or emotion_r2/digest/index.html (open locally). The report labels voice scores provisional. No lyric text, audio, model weights or credentials are included. All data are for research; see licensing caveats.\n')
    z.writestr('BUNDLE_MANIFEST.json',json.dumps({'files':manifest},indent=2)+'\n')
with zipfile.ZipFile(dest) as z:
    bad=z.testzip()
    if bad:raise RuntimeError('ZIP integrity failure: '+bad)
digest=hashlib.sha256(dest.read_bytes()).hexdigest()
(HERE/'TASK_F_COMPLETE.sha256').write_text(digest+'  '+dest.name+'\n',encoding='utf8')
print(json.dumps({'archive':str(dest),'files':len(manifest),'bytes':dest.stat().st_size,'sha256':digest,'zip_integrity':'passed'}))
