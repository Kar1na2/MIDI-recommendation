"""Resume interrupted Task D scoring in detached, cache-aware workers.

Run only in WSL with the registered interpreter. Existing logs are appended.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from stems import library_active


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PYTHON = '/home/usr/venvs/midi/bin/python'
JOBS = [
    ('separation', 'stems.py', []),
    ('text_scores', 'text_scores.py', ['--device', 'cpu']),
    ('m1_scores', 'background_m1.py', ['--wait-for-stems']),
    ('m2_scores', 'background_m2.py', ['--wait-for-stems', '--device', 'cuda']),
    ('voice_scores', 'voice_scores.py', ['--device', 'cuda']),
]


def main():
    if Path(sys.executable).absolute() != Path(PYTHON):
        raise RuntimeError('Run with the requested MIDI interpreter')
    if library_active():
        raise RuntimeError('Library processing detected; GPU work cannot be started outside its queue')
    listing = subprocess.run(['ps', '-eo', 'args='], check=True, capture_output=True, text=True).stdout
    for _, script, _ in JOBS:
        if any('python' in line and f'emotion/{script}' in line for line in listing.splitlines()):
            raise RuntimeError(f'Existing worker detected for {script}; refusing duplicate launch')
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
           'PYTHONPATH': os.pathsep.join([str(HERE/'vendor'), str(ROOT)])}
    processes = []
    for log, script, args in JOBS:
        with (HERE/f'{log}.log').open('a', encoding='utf-8') as output:
            output.write('\n' + json.dumps({'event': 'resumed', 'utc': datetime.now(timezone.utc).isoformat()}) + '\n')
            output.flush()
            proc = subprocess.Popen([PYTHON, '-u', f'emotion/{script}', *args], cwd=ROOT, env=env,
                                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        processes.append({'script': script, 'pid': proc.pid, 'log': f'emotion/{log}.log'})
    result = {'started_utc': datetime.now(timezone.utc).isoformat(), 'workers': processes}
    path = HERE/'validation'/'resume_workers.json'
    path.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
