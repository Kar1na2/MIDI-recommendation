"""Snapshot round one and verify the two existing protected snapshots."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True

if sys.executable != '/home/usr/venvs/midi/bin/python':
    raise RuntimeError('Use /home/usr/venvs/midi/bin/python')

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
R1 = ROOT / 'emotion'
EXCLUDED = {'private', 'model_cache', 'stems'}
sys.path.insert(0, str(R1))
from audit import verify

def files():
    return sorted(p for p in R1.rglob('*') if p.is_file() and p.relative_to(R1).parts[0] not in EXCLUDED)

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def create():
    path = HERE / 'r1_snapshot.json'
    if path.exists():
        raise RuntimeError('Snapshot already exists; refusing to replace the baseline')
    records = {}
    for i, p in enumerate(files(), 1):
        records[p.relative_to(R1).as_posix()] = {'size': p.stat().st_size, 'sha256': digest(p)}
        if i % 500 == 0: print(f'Round-one snapshot {i} files', flush=True)
    path.write_text(json.dumps({'files': records, 'excluded': sorted(EXCLUDED)}, indent=2) + '\n', encoding='utf-8')
    print(f'Round-one snapshot: {len(records)} files', flush=True)

def check(phase):
    start = time.monotonic()
    snapshot = HERE / 'r1_snapshot.json'
    result = {'round1': verify(snapshot, R1),
              'v1': verify(ROOT / 'analysis_v2/v1_snapshot.json', ROOT),
              'mvp_325': verify(ROOT / 'mvp_325/snapshot.json', ROOT / 'mvp_325', True)}
    recorded = set(json.loads(snapshot.read_text(encoding='utf-8'))['files'])
    result['round1']['extra'] = sorted(p.relative_to(R1).as_posix() for p in files() if p.relative_to(R1).as_posix() not in recorded)
    result['round1']['passed'] = result['round1']['passed'] and not result['round1']['extra']
    result['passed'] = all(result[k]['passed'] for k in ('round1', 'v1', 'mvp_325'))
    result['seconds'] = time.monotonic() - start
    (HERE / f'integrity_{phase}.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2), flush=True)
    return result['passed']

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['start', 'end'])
    phase = parser.parse_args().phase
    if phase == 'start' and not (HERE / 'r1_snapshot.json').exists(): create()
    raise SystemExit(0 if check(phase) else 1)
