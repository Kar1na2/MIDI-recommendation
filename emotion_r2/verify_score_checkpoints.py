"""Check sample coverage before starting the next long-running local model."""
import csv
import hashlib
import json
import sys
from pathlib import Path

if sys.executable != '/home/usr/venvs/midi/bin/python':
    raise RuntimeError('Use the required WSL interpreter')
here = Path(__file__).resolve().parent
root = here.parent
role = sys.argv[1]
if role not in ('L1', 'M3', 'V1', 'V3', 'voice_gate'):
    raise ValueError('Unknown role')
with (root / 'mvp_325/summary.csv').open(encoding='utf-8-sig', newline='') as stream:
    songs = list(csv.DictReader(stream))
if len(songs) != 325 or len({s['track_id'] for s in songs}) != 325:
    raise RuntimeError('Sample does not have exactly 325 unique tracks')
missing = []
statuses = {}
for song in songs:
    track = song['track_id']
    if role == 'voice_gate':
        folder = root / ('outputs' if song['cohort'] == 'random' else 'mvp_325/songs') / track
        eligible = (song.get('ring1_source') != 'instrumental' and
                    (folder / 'stems/vocals.wav').is_file() and
                    (folder / 'transcription.json').is_file())
        if not eligible:
            continue
    path = here / 'scores' / role / (track + '.json')
    if not path.is_file():
        missing.append(track)
        continue
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('track_id') != track or data.get('cohort') != song['cohort']:
        raise RuntimeError('Invalid checkpoint identity: ' + str(path))
    status = data.get('status', 'checkpoint')
    section_path = here / 'sections_input' / (track + '.json')
    if role == 'voice_gate':
        if data.get('sections_sha256') != hashlib.sha256(section_path.read_bytes()).hexdigest():
            raise RuntimeError('Stale gate section input: ' + track)
    elif status == 'checkpoint':
        commit = '7484df243f1e43b82249c4f9a3b26ad7e02c0aa3'
        if data.get('amendment_commit') != commit or data.get('role') != role or not data.get('input_sha256'):
            raise RuntimeError('Stale model checkpoint: ' + track + '/' + role)
        if role == 'L1':
            private = here / 'private' / (track + '.json')
            private_bytes = private.read_bytes() if private.is_file() else b''
            prompt_hash = '46f33c7d62256022323d6de55f3f821a8a955af72ea94ef0c6b711bd7fff4571'
            expected = hashlib.sha256(section_path.read_bytes()+private_bytes+commit.encode()+role.encode()+prompt_hash.encode()).hexdigest()
            if data['input_sha256'] != expected:
                raise RuntimeError('Stale L1 input: ' + track)
    statuses[status] = statuses.get(status, 0) + 1
if missing:
    raise RuntimeError(f'{role}: {len(missing)} missing checkpoints: {missing[:10]}')
print(json.dumps({'role': role, 'status': 'coverage_verified', 'counts': statuses}), flush=True)
