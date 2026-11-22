"""Commit allowlist, fixed development parameters and the no-peeking barrier."""
import json
import subprocess
from pathlib import Path
from .audit import ROOT, R2, DEST, read, sha, save

TASK_PATHS = ('emotion_r2/replication/', 'emotion_r2/REPLICATION_NOTES.md',
              'emotion_r2/REPLICATION_PROGRESS.md', 'emotion_r2/REPLICATION_REPORT.md',
              'emotion_r2/display/.gitignore',
              'emotion_r2/replication_development_snapshot.json')

def git(*args):
    return subprocess.run(['git',*args],cwd=ROOT,check=True,capture_output=True).stdout

def allowed(path):
    if '..' in Path(path).parts:return False
    return any(path.startswith(p) if p.endswith('/') else path==p for p in TASK_PATHS)

def commit(paths, message):
    if git('branch','--show-current').decode().strip()!='codex/library-replication':raise RuntimeError('Wrong branch; no staging or commit performed')
    if not paths or not all(allowed(p) for p in paths):raise RuntimeError('Out-of-scope commit paths')
    current=git('diff','--cached','--name-only','-z').decode().split('\0')
    if any(p and not allowed(p) for p in current):raise RuntimeError('Foreign paths already staged; index left unchanged')
    git('add','--',*paths)
    listing=git('diff','--cached','--name-status').decode()
    print('git diff --cached --name-status\n'+listing,flush=True)
    staged=git('diff','--cached','--name-only','-z').decode().split('\0')
    if any(p and not allowed(p) for p in staged):raise RuntimeError('Foreign staged paths; abort commit')
    if listing:git('commit','-m',message)
    return git('rev-parse','HEAD').decode().strip()

def registered_sources():
    names=['emotion_r2/ANALYSIS_PLAN.md','emotion_r2/models.json','emotion_r2/models_amended.json',
           'emotion_r2/models_amended.sha256','emotion_r2/l1_prompt.txt',
           'emotion_r2/validation/normalization.json','emotion_r2/validation/trust.json',
           'emotion_r2/validation/reference_normalization.json',
           'emotion_r2/audio_scores_r2.py','emotion_r2/voicing.py','emotion_r2/score_text_model.py',
           'emotion_r2/language_weighted.py','emotion_r2/statistics_r2.py',
           'emotion_r2/explanation_repair.py','emotion_r2/display/l1_reading_prompt.txt',
           'emotion_r2/display/export_readings.py','emotion/audio_frames.py',
           'emotion/background_m1.py','emotion/background_m2.py','emotion/feature_explanations.py',
           'emotion/pinned_demucs.py','emotion/models.json','emotion/prepare_inputs.py',
           'emotion_r2/recover_lyrics.py','emotion_r2/label_cached.py','emotion_r2/registration.py',
           'emotion/voice_scores.py','emotion/aggregate_scores.py',
           'analysis_v2/ANALYSIS_PLAN.md','pipeline/v2_stats.py']
    return {name:sha(ROOT/name) for name in names}

def lock_sources():
    target=DEST/'registered_source_hashes.json'
    actual=registered_sources()
    if target.exists():
        if read(target)!=actual:raise RuntimeError('Registered source or parameter changed')
    else:save(target,actual)

def pre_scoring():
    if git('branch','--show-current').decode().strip()!='codex/library-replication':raise RuntimeError('Wrong branch for Task K')
    if not read(DEST/'integrity_start.json')['passed']:raise RuntimeError('Initial snapshots failed')
    if git('show','HEAD:emotion_r2/REPLICATION_NOTES.md')!=(R2/'REPLICATION_NOTES.md').read_bytes():
        raise RuntimeError('Operational decisions must be committed before scoring')
    if git('show','HEAD:emotion_r2/replication/registered_source_hashes.json')!=(DEST/'registered_source_hashes.json').read_bytes():
        raise RuntimeError('Source/parameter lock is not committed')
    lock_sources()

def frozen():
    path=DEST/'frozen_manifest.json'
    if not path.is_file():raise RuntimeError('No peeking: scoring has not been frozen')
    if git('show','HEAD:emotion_r2/replication/frozen_manifest.json')!=path.read_bytes():
        raise RuntimeError('No peeking: freeze must be committed before analysis')
    data=read(path)
    for name,digest in data['files'].items():
        if sha(ROOT/name)!=digest:raise RuntimeError('Frozen artifact changed: '+name)
    return data
