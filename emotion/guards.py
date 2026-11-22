"""Preregistration and model-file guards for Task D inference."""
import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def verify(roles):
    if not json.loads((HERE/'validation'/'integrity_start.json').read_text(encoding='utf-8')).get('passed'):
        raise RuntimeError('Starting snapshot checks have not passed')
    commit = (HERE/'validation'/'plan_commit.txt').read_text(encoding='utf-8').strip()
    for name in ('ANALYSIS_PLAN.md', 'models.json'):
        blob = subprocess.run(['git', 'show', f'{commit}:emotion/{name}'], cwd=ROOT, check=True,
                              capture_output=True).stdout
        if blob != (HERE/name).read_bytes():
            raise RuntimeError(f'{name} does not match preregistered commit')
    manifest = json.loads((HERE/'models.json').read_text(encoding='utf-8'))
    chosen = [entry for entry in manifest['models'] if entry['role'] in roles]
    if {entry['role'] for entry in chosen} != set(roles):
        raise RuntimeError('Model role missing from manifest')
    for entry in chosen:
        for record in entry.get('files', []):
            if entry['role'] == 'M1':
                path = HERE/'model_cache'/'essentia'/record['path']
            elif entry['role'] == 'source_separation':
                path = HERE/'model_cache'/record['path']
            else:
                path = HERE/'model_cache'/entry['id'].replace('/', '--')/record['path']
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest != record['sha256']:
                raise RuntimeError(f'Hash mismatch: {entry["role"]}/{record["path"]}')
    return commit
