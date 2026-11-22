"""Enforce original registration plus the committed Amendment 1."""
import hashlib, json, subprocess
from pathlib import Path
HERE=Path(__file__).resolve().parent; ROOT=HERE.parent
ORIGINAL='e8127190bd82e2b631bb1e749874e73308e45a2c'
ORIGINAL_MANIFEST='bab8f1a06d8232a62a0e01bd61ea0991725a713d402498b2da53043615d52fae'
def git(*args): return subprocess.run(['git',*args],cwd=ROOT,check=True,capture_output=True).stdout
def verify_registration():
    original_plan=git('show',ORIGINAL+':emotion_r2/ANALYSIS_PLAN.md')
    plan=(HERE/'ANALYSIS_PLAN.md').read_bytes()
    if not plan.startswith(original_plan): raise RuntimeError('Original preregistered plan was altered')
    if hashlib.sha256((HERE/'models.json').read_bytes()).hexdigest()!=ORIGINAL_MANIFEST: raise RuntimeError('Original manifest changed')
    if git('show',ORIGINAL+':emotion_r2/models.json')!=(HERE/'models.json').read_bytes(): raise RuntimeError('Original manifest commit mismatch')
    amended=(HERE/'models_amended.json').read_bytes(); digest=hashlib.sha256(amended).hexdigest()
    if digest not in plan.decode('utf8'): raise RuntimeError('Plan does not bind amended manifest')
    if not (HERE/'models_amended.sha256').read_text().startswith(digest): raise RuntimeError('Amended manifest hash mismatch')
    for name in ('ANALYSIS_PLAN.md','models_amended.json','models_amended.sha256'):
        if git('show','HEAD:emotion_r2/'+name)!=(HERE/name).read_bytes(): raise RuntimeError('Amendment is uncommitted or modified: '+name)
    commit=git('log','-1','--format=%H','--','emotion_r2/ANALYSIS_PLAN.md').decode().strip()
    if commit==ORIGINAL: raise RuntimeError('Amendment is not committed')
    return {'original_plan_commit':ORIGINAL,'amendment_commit':commit,'original_manifest_sha256':ORIGINAL_MANIFEST,'amended_manifest_sha256':digest,'manifest':json.loads(amended)}

def verify_weights(manifest,role,folder):
    item=next(x for x in manifest['models'] if x.get('role')==role)
    for record in item['files']:
        path=folder/record['path']
        with path.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest!=record['sha256']: raise RuntimeError('Weight or metadata hash mismatch: '+role+'/'+record['path'])
