"""Run preregistered Essentia background/full-mix models on CPU."""
import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

os.environ['CUDA_VISIBLE_DEVICES'] = ''
os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '2')
os.environ.setdefault('OMP_NUM_THREADS', '2')
os.environ.setdefault('TF_NUM_INTRAOP_THREADS', '2')
os.environ.setdefault('TF_NUM_INTEROP_THREADS', '2')

import numpy as np
import soundfile as sf
from essentia.standard import TensorflowPredict2D, TensorflowPredictMusiCNN

from audio_frames import read_mono, windows
from guards import verify
from stems import library_active, songs

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def model_score(frame, embedder, predictor):
    embeddings = embedder(frame)
    predictions = predictor(embeddings)
    if predictions.size == 0 or predictions.shape[-1] != 2 or not np.isfinite(predictions).all():
        raise ValueError('Essentia returned empty, malformed, or nonfinite values')
    return predictions.mean(axis=0).tolist()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--song', action='append')
    parser.add_argument('--wait-for-stems', action='store_true')
    args = parser.parse_args()
    commit = verify(['M1'])
    paths = HERE/'model_cache'/'essentia'
    embedder = TensorflowPredictMusiCNN(graphFilename=str(paths/'msd-musicnn-1.pb'), output='model/dense/BiasAdd')
    predictor = TensorflowPredict2D(graphFilename=str(paths/'emomusic-msd-musicnn-2.pb'), output='model/Identity')
    selected = songs()
    if args.song:
        requested = set(args.song)
        selected = [s for s in selected if s['track_id'] in requested]
        if {s['track_id'] for s in selected} != requested:
            raise ValueError('Requested song is outside registered cohort')
    output_dir = HERE/'scratch'/'m1'
    output_dir.mkdir(parents=True, exist_ok=True)
    for song in selected:
        if library_active():
            raise RuntimeError('Library processing detected; emotion work paused')
        track = song['track_id']
        target = output_dir/f'{track}.json'
        section_path = HERE/'validation'/'input_sections'/f'{track}.json'
        stem_path = HERE/'stems'/track/'instrumental.flac'
        separation_path = stem_path.parent/'separation.json'
        waiting_since = time.monotonic()
        while args.wait_for_stems and not separation_path.exists():
            if library_active():
                raise RuntimeError('Library processing detected; emotion work paused')
            processes = subprocess.run(['ps', '-eo', 'args='], check=True, capture_output=True, text=True).stdout
            if not any('emotion/stems.py' in line and 'python' in line for line in processes.splitlines()):
                break
            if time.monotonic() - waiting_since > 3600:
                raise TimeoutError(f'Waited more than an hour for {track} stem')
            time.sleep(5)
        if not stem_path.exists() or not separation_path.exists():
            print(json.dumps({'track_id': track, 'cohort': song['cohort'], 'status': 'instrumental_stem_unavailable'}), flush=True)
            continue
        separation = json.loads(separation_path.read_text(encoding='utf-8'))
        with stem_path.open('rb') as stream:
            stem_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
        if separation.get('status') != 'complete' or separation.get('instrumental_sha256') != stem_hash:
            raise RuntimeError(f'Unverified instrumental stem for {track}')
        original = Path(song['audio_path']).resolve()
        if not original.is_relative_to((ROOT/'liked-audio').resolve()) or not original.is_file():
            print(json.dumps({'track_id': track, 'cohort': song['cohort'], 'status': 'original_audio_unavailable'}), flush=True)
            continue
        with original.open('rb') as stream:
            audio_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
        fingerprint = hashlib.sha256(section_path.read_bytes()+stem_hash.encode()+audio_hash.encode()).hexdigest()
        if target.exists():
            cached = json.loads(target.read_text(encoding='utf-8'))
            if cached.get('plan_commit') != commit or (cached.get('audio_sha256') and cached.get('input_sha256') != fingerprint):
                raise RuntimeError(f'M1 cache mismatch for {track}')
            if cached.get('audio_sha256'):
                print(json.dumps({'track_id': track, 'cohort': song['cohort'], 'status': 'cached'}), flush=True)
                continue
            print(json.dumps({'track_id': track, 'cohort': song['cohort'], 'status': 'recomputing_unverified_cache'}), flush=True)
        begin = time.monotonic()
        try:
            instrumental = read_mono(stem_path, 16000)
            mix = read_mono(original, 16000)
            sections = json.loads(section_path.read_text(encoding='utf-8'))['sections']
            if song['ring1_source'] == 'instrumental' and not sections:
                sections = [{'section_id': 'instrumental-whole', 'set': 'instrumental', 'start_s': 0.,
                             'end_s': min(len(instrumental), len(mix))/16000}]
            result = []
            for section in sections:
                if library_active():
                    raise RuntimeError('Library processing detected while scoring')
                sec = {'section_id': section['section_id'], 'set': section['set'], 'frames': [], 'status': 'ok'}
                a, b = section['start_s'], section['end_s']
                if a is None or b is None:
                    sec['status'] = 'missing_raw_timing'
                    result.append(sec)
                    continue
                frame_pairs = zip(windows(a, b, instrumental, 16000), windows(a, b, mix, 16000))
                for inst, full in frame_pairs:
                    v1 = model_score(inst['samples'], embedder, predictor) if inst['actual_seconds'] else [None, None]
                    v2 = model_score(full['samples'], embedder, predictor) if full['actual_seconds'] else [None, None]
                    sec['frames'].append({'start_s': inst['start_s'], 'end_s': inst['end_s'],
                        'instrumental_coverage': inst['coverage'], 'mix_coverage': full['coverage'],
                        'M1_valence': v1[0], 'M1_arousal': v1[1],
                        'M1_full_mix_valence': v2[0], 'M1_full_mix_arousal': v2[1]})
                result.append(sec)
            data = {'track_id': track, 'cohort': song['cohort'], 'status': 'complete', 'plan_commit': commit,
                    'input_sha256': fingerprint, 'audio_sha256': audio_hash,
                    'device': 'cpu', 'wall_seconds': time.monotonic()-begin, 'sections': result}
            temp = target.with_suffix('.tmp')
            temp.write_text(json.dumps(data, ensure_ascii=False)+'\n', encoding='utf-8')
            temp.replace(target)
            print(json.dumps({'track_id': track, 'cohort': song['cohort'], 'status': 'complete',
                              'sections': len(result), 'seconds': data['wall_seconds']}), flush=True)
        except Exception as exc:
            print(json.dumps({'track_id': track, 'cohort': song['cohort'], 'status': 'failed',
                              'error': f'{type(exc).__name__}: {exc}'}), flush=True)
            if 'Library processing detected' in str(exc):
                raise


if __name__ == '__main__':
    main()
