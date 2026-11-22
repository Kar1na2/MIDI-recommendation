"""Read-only source integrity checks. Writes only beneath emotion/."""
import argparse
import csv
import hashlib
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / 'emotion'


def save(path, value):
    path = Path(path).resolve()
    if not path.is_relative_to(HOME.resolve()):
        raise ValueError('All new output must be under emotion/')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def verify(snapshot, root, extras=False):
    records = json.loads(snapshot.read_text(encoding='utf-8-sig'))['files']
    changed, missing = [], []
    for i, (name, record) in enumerate(records.items(), 1):
        target = root / name
        if not target.is_file():
            missing.append(name)
        elif target.stat().st_size != record['size']:
            changed.append(name)
        else:
            with target.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != record['sha256']:
                    changed.append(name)
        if i % 500 == 0:
            print(f'{snapshot.parent.name}: verified {i}/{len(records)}', flush=True)
    extra = (sorted(p.relative_to(root).as_posix() for p in root.rglob('*')
                    if p.is_file() and p != snapshot and p.relative_to(root).as_posix() not in records)
             if extras else [])
    return dict(total=len(records), unchanged=len(records)-len(changed)-len(missing),
                changed=changed, missing=missing, extra=extra,
                passed=not(changed or missing or extra))


def integrity(phase):
    started = time.time()
    result = {'v1': verify(ROOT/'analysis_v2/v1_snapshot.json', ROOT),
              'mvp_325': verify(ROOT/'mvp_325/snapshot.json', ROOT/'mvp_325', True)}
    result.update(passed=all(r['passed'] for r in result.values()), seconds=time.time()-started)
    save(HOME/f'validation/integrity_{phase}.json', result)
    print(json.dumps(result), flush=True)
    return result['passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['start', 'end'])
    args = parser.parse_args()
    raise SystemExit(0 if integrity(args.phase) else 1)
