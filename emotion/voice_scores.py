"""Compute dimensional speech-model and interpretable vocal-window features."""
import argparse
import hashlib
import json
import math
import os
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault('NUMBA_CACHE_DIR', str(HERE/'scratch'/'numba'))
(HERE/'scratch'/'numba').mkdir(parents=True, exist_ok=True)

import librosa
import numpy as np
import torch
from scipy import signal as scipy_signal
from transformers import Wav2Vec2FeatureExtractor

from audio_frames import read_mono, vocal_active, windows
from guards import verify
from stems import library_active, songs
from voice_model import EmotionModel

ROOT = HERE.parent
SR = 16000
HOP = 320


def prosody(frame, pitches, onset_count):
    usable = frame['samples'][:round(frame['actual_seconds']*SR)]
    blocks = usable[:(len(usable)//1600)*1600].reshape(-1, 1600)
    rms = np.sqrt(np.mean(blocks.astype(np.float64)**2, axis=1)) if len(blocks) else np.array([])
    rms = rms[rms > 0.01]
    if len(usable) >= 1024:
        freqs, _, spectrum = scipy_signal.stft(usable, fs=SR, nperseg=1024, noverlap=704, boundary=None)
        magnitude = np.abs(spectrum)
        total = magnitude.sum(axis=0)
        centroids = np.divide((magnitude*freqs[:, None]).sum(axis=0), total,
                              out=np.full_like(total, np.nan), where=total > 1e-10)
        centroids = centroids[np.isfinite(centroids)]
    else:
        centroids = np.array([])
    voiced = pitches[np.isfinite(pitches) & (pitches > 0)]
    result = {'f0_median_hz': float(np.median(voiced)) if voiced.size else None,
              'f0_range_semitones': float(12*np.log2(voiced.max()/voiced.min())) if voiced.size >= 2 else None,
              'voiced_ratio': float(voiced.size/len(pitches)) if len(pitches) else None,
              'vibrato_rate_hz': None, 'vibrato_extent_semitones': None,
              'rms_mean': float(np.mean(rms)) if rms.size else None,
              'rms_variance': float(np.var(rms)) if rms.size else None,
              'brightness_hz': float(np.mean(centroids)) if centroids.size else None,
              'voice_note_onset_rate_hz': onset_count/frame['actual_seconds'] if onset_count is not None and frame['actual_seconds'] else None}
    # Use the longest continuous voiced run to avoid fabricating samples across gaps.
    good = np.isfinite(pitches) & (pitches > 0)
    changes = np.diff(np.r_[False, good, False].astype(int))
    starts, ends = np.where(changes == 1)[0], np.where(changes == -1)[0]
    if len(starts):
        j = int(np.argmax(ends-starts))
        continuous = pitches[starts[j]:ends[j]]
        if len(continuous) >= SR//HOP:
            semitones = 12*np.log2(continuous/np.median(continuous))
            sos = scipy_signal.butter(3, [3, 8], btype='bandpass', fs=SR/HOP, output='sos')
            filtered = scipy_signal.sosfiltfilt(sos, semitones)
            frequencies = np.fft.rfftfreq(len(filtered), HOP/SR)
            power = np.abs(np.fft.rfft(filtered))**2
            mask = (frequencies >= 3) & (frequencies <= 8)
            if np.any(mask) and np.ptp(filtered) > 1e-6:
                result['vibrato_rate_hz'] = float(frequencies[mask][np.argmax(power[mask])])
                result['vibrato_extent_semitones'] = float((np.percentile(filtered, 95)-np.percentile(filtered, 5))/2)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--song', action='append')
    parser.add_argument('--device', choices=['cpu', 'cuda'], default='cuda')
    args = parser.parse_args()
    commit = verify(['V1'])
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('CUDA requested but unavailable')
    torch.set_num_threads(2)
    model_path = HERE/'model_cache'/'audeering--wav2vec2-large-robust-12-ft-emotion-msp-dim'
    extractor = Wav2Vec2FeatureExtractor.from_pretrained(model_path, local_files_only=True)
    model, info = EmotionModel.from_pretrained(model_path, local_files_only=True, output_loading_info=True)
    if info['missing_keys'] or info['unexpected_keys'] or info['mismatched_keys']:
        raise RuntimeError(f'Incomplete V1 load: {info}')
    model.to(args.device).eval()
    selected = songs()
    if args.song:
        requested = set(args.song)
        selected = [s for s in selected if s['track_id'] in requested]
        if {s['track_id'] for s in selected} != requested:
            raise ValueError('Requested song outside registered cohort')
    outdir = HERE/'scratch'/'voice'
    outdir.mkdir(parents=True, exist_ok=True)
    for song in selected:
        if library_active():
            raise RuntimeError('Library processing detected; emotion work paused')
        track, cohort = song['track_id'], song['cohort']
        if song['ring1_source'] == 'instrumental':
            print(json.dumps({'track_id': track, 'cohort': cohort, 'status': 'instrumental_only'}), flush=True)
            continue
        folder = ROOT/('outputs' if cohort == 'random' else 'mvp_325/songs')/track
        vocal = folder/'stems'/'vocals.wav'
        transcription = folder/'transcription.json'
        section_path = HERE/'validation'/'input_sections'/f'{track}.json'
        try:
            if not vocal.is_file() or not transcription.is_file():
                raise FileNotFoundError('Vocal stem or transcription missing')
            with vocal.open('rb') as stream:
                vocal_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
            sep = json.loads((folder/'separation.json').read_text(encoding='utf-8'))
            expected = (sep.get('data') or {}).get('stem_sha256', {}).get('vocals')
            if not expected or expected != vocal_hash:
                raise RuntimeError('Vocal stem does not match cached separation hash')
            trans_bytes = transcription.read_bytes()
            fingerprint = hashlib.sha256(section_path.read_bytes()+vocal_hash.encode()+trans_bytes).hexdigest()
            target = outdir/f'{track}.json'
            if target.exists():
                saved = json.loads(target.read_text(encoding='utf-8'))
                if saved.get('plan_commit') != commit or saved.get('input_sha256') != fingerprint:
                    raise RuntimeError('Voice cache mismatch')
                print(json.dumps({'track_id': track, 'cohort': cohort, 'status': 'cached'}), flush=True)
                continue
            started = time.monotonic()
            audio = read_mono(vocal, SR)
            trans = json.loads(trans_bytes).get('data') or {}
            notes = trans.get('notes') or []
            voice_track_present = any(note.get('instrument') == 'voice' for note in notes)
            onsets = [note['onset_s'] for note in notes if note.get('instrument') == 'voice' and isinstance(note.get('onset_s'), (float, int))]
            sections = json.loads(section_path.read_text(encoding='utf-8'))['sections']
            results = []
            for section in sections:
                if library_active():
                    raise RuntimeError('Library processing detected while scoring')
                a, b = section['start_s'], section['end_s']
                sec = {'section_id': section['section_id'], 'set': section['set'], 'frames': [], 'status': 'ok'}
                if a is None or b is None:
                    sec['status'] = 'missing_raw_timing'
                    results.append(sec)
                    continue
                crop = audio[round(a*SR):min(round(b*SR), len(audio))]
                if not len(crop):
                    sec['status'] = 'outside_vocal_stem'
                    results.append(sec)
                    continue
                f0, _, _ = librosa.pyin(crop, fmin=librosa.note_to_hz('C2'), fmax=librosa.note_to_hz('C6'),
                                        sr=SR, frame_length=2048, hop_length=HOP, center=True)
                times = np.arange(len(f0))*HOP/SR
                for frame in windows(a, b, audio, SR):
                    row = {'start_s': frame['start_s'], 'end_s': frame['end_s'], 'coverage': frame['coverage'],
                           'active': vocal_active(frame['samples'], SR), 'V1_valence': None, 'V1_arousal': None,
                           'prosody': None}
                    if row['active'] and frame['actual_seconds']:
                        if library_active():
                            raise RuntimeError('Library processing detected while scoring')
                        values = extractor(frame['samples'], sampling_rate=SR, return_tensors='pt').input_values.to(args.device)
                        with torch.inference_mode():
                            scores = model(values)[0].cpu().numpy()
                        if not np.isfinite(scores).all():
                            raise ValueError('V1 returned nonfinite output')
                        row['V1_arousal'], row['V1_valence'] = float(scores[0]), float(scores[2])
                        selected_f0 = f0[(times >= frame['start_s']-a) & (times < frame['end_s']-a)]
                        count = sum(frame['start_s'] <= onset < frame['end_s'] for onset in onsets) if voice_track_present else None
                        row['prosody'] = prosody(frame, selected_f0, count)
                    sec['frames'].append(row)
                results.append(sec)
            data = {'track_id': track, 'cohort': cohort, 'status': 'complete', 'plan_commit': commit,
                    'input_sha256': fingerprint, 'voice_track_present': voice_track_present,
                    'device': args.device, 'wall_seconds': time.monotonic()-started, 'sections': results}
            temp = target.with_suffix('.tmp')
            temp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False)+'\n', encoding='utf-8')
            temp.replace(target)
            print(json.dumps({'track_id': track, 'cohort': cohort, 'status': 'complete',
                              'sections': len(results), 'seconds': data['wall_seconds']}), flush=True)
        except Exception as exc:
            print(json.dumps({'track_id': track, 'cohort': cohort, 'status': 'failed',
                              'error': f'{type(exc).__name__}: {exc}'}), flush=True)
            if 'Library processing detected' in str(exc):
                raise


if __name__ == '__main__':
    main()
