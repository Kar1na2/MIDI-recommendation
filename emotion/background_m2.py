"""Score instrumental windows with the preregistered fixed CLAP prompts."""
import argparse
import hashlib
import itertools
import json
import subprocess
import time
from pathlib import Path

import numpy as np
import torch
from transformers import ClapModel, ClapProcessor

from audio_frames import read_mono, windows
from guards import verify
from stems import library_active, songs

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROMPTS = ['cheerful, bright music', 'sad, dark music', 'intense, energetic music', 'calm, quiet music']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--song', action='append')
    parser.add_argument('--device', choices=['cpu', 'cuda'], default='cuda')
    parser.add_argument('--wait-for-stems', action='store_true')
    args = parser.parse_args()
    commit = verify(['M2'])
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('CUDA requested but unavailable')
    torch.set_num_threads(2)
    model_path = HERE/'model_cache'/'laion--clap-htsat-unfused'
    processor = ClapProcessor.from_pretrained(model_path, local_files_only=True)
    model = ClapModel.from_pretrained(model_path, local_files_only=True).to(args.device).eval()
    text_input = processor(text=PROMPTS, return_tensors='pt', padding=True).to(args.device)
    with torch.inference_mode():
        prompts = model.get_text_features(**text_input)
    selected = songs()
    if args.song:
        requested = set(args.song)
        selected = [s for s in selected if s['track_id'] in requested]
        if {s['track_id'] for s in selected} != requested:
            raise ValueError('Requested song is outside registered cohort')
    outdir = HERE/'scratch'/'m2'
    outdir.mkdir(parents=True, exist_ok=True)
    for song in selected:
        if library_active():
            raise RuntimeError('Library processing detected; emotion work paused')
        track = song['track_id']
        section_path = HERE/'validation'/'input_sections'/f'{track}.json'
        stem = HERE/'stems'/track/'instrumental.flac'
        metadata = stem.parent/'separation.json'
        started_wait = time.monotonic()
        while args.wait_for_stems and not metadata.exists():
            if library_active():
                raise RuntimeError('Library processing detected; emotion work paused')
            processes = subprocess.run(['ps', '-eo', 'args='], check=True, capture_output=True, text=True).stdout
            if not any('emotion/stems.py' in line and 'python' in line for line in processes.splitlines()):
                break
            if time.monotonic()-started_wait > 3600:
                raise TimeoutError(f'Waited more than an hour for {track} stem')
            time.sleep(5)
        if not stem.exists() or not metadata.exists():
            print(json.dumps({'track_id': track, 'cohort': song['cohort'], 'status': 'instrumental_stem_unavailable'}), flush=True)
            continue
        meta = json.loads(metadata.read_text(encoding='utf-8'))
        with stem.open('rb') as stream:
            stem_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
        if meta.get('status') != 'complete' or meta.get('instrumental_sha256') != stem_hash:
            raise RuntimeError(f'Unverified instrumental stem for {track}')
        fingerprint = hashlib.sha256(section_path.read_bytes()+stem_hash.encode()).hexdigest()
        target = outdir/f'{track}.json'
        if target.exists():
            saved = json.loads(target.read_text(encoding='utf-8'))
            if saved.get('plan_commit') != commit or saved.get('input_sha256') != fingerprint:
                raise RuntimeError(f'M2 cache mismatch for {track}')
            print(json.dumps({'track_id': track, 'cohort': song['cohort'], 'status': 'cached'}), flush=True)
            continue
        started = time.monotonic()
        try:
            audio = read_mono(stem, 48000)
            sections = json.loads(section_path.read_text(encoding='utf-8'))['sections']
            if song['ring1_source'] == 'instrumental' and not sections:
                sections = [{'section_id': 'instrumental-whole', 'set': 'instrumental', 'start_s': 0., 'end_s': len(audio)/48000}]
            results = []
            for section in sections:
                sec = {'section_id': section['section_id'], 'set': section['set'], 'frames': [], 'status': 'ok'}
                a, b = section['start_s'], section['end_s']
                if a is None or b is None:
                    sec['status'] = 'missing_raw_timing'
                    results.append(sec)
                    continue
                frames = iter(windows(a, b, audio, 48000))
                while True:
                    group = list(itertools.islice(frames, 4))
                    if not group:
                        break
                    if library_active():
                        raise RuntimeError('Library processing detected while scoring')
                    valid = [frame for frame in group if frame['actual_seconds']]
                    scores = []
                    if valid:
                        inputs = processor(audio=[frame['samples'] for frame in valid], sampling_rate=48000,
                                           return_tensors='pt', padding=True).to(args.device)
                        with torch.inference_mode():
                            similarity = model.get_audio_features(**inputs) @ prompts.T
                        if not torch.isfinite(similarity).all():
                            raise ValueError('CLAP returned nonfinite similarities')
                        scores = similarity.cpu().numpy().tolist()
                    index = 0
                    for frame in group:
                        row = {'start_s': frame['start_s'], 'end_s': frame['end_s'],
                               'instrumental_coverage': frame['coverage'], 'M2_valence': None, 'M2_arousal': None}
                        if frame['actual_seconds']:
                            sim = scores[index]
                            row['M2_valence'] = sim[0]-sim[1]
                            row['M2_arousal'] = sim[2]-sim[3]
                            index += 1
                        sec['frames'].append(row)
                results.append(sec)
            data = {'track_id': track, 'cohort': song['cohort'], 'status': 'complete', 'plan_commit': commit,
                    'input_sha256': fingerprint, 'device': args.device, 'wall_seconds': time.monotonic()-started,
                    'sections': results}
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps(data, ensure_ascii=False)+'\n', encoding='utf-8')
            temporary.replace(target)
            print(json.dumps({'track_id': track, 'cohort': song['cohort'], 'status': 'complete',
                              'sections': len(results), 'seconds': data['wall_seconds']}), flush=True)
        except Exception as exc:
            print(json.dumps({'track_id': track, 'cohort': song['cohort'], 'status': 'failed',
                              'error': f'{type(exc).__name__}: {exc}'}), flush=True)
            if 'Library processing detected' in str(exc):
                raise


if __name__ == '__main__':
    main()
