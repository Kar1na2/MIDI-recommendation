"""Score the round-2 audio models: M3 (background) and V3 (voice).

Uses the round-1 stems and the identical 4 s / 1 s window definition. Round 1 is read-only;
all new outputs go under ``emotion_r2/scores/``.
"""
import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

if sys.executable != '/home/usr/venvs/midi/bin/python':
    raise RuntimeError('Wrong interpreter')

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
os.environ['HF_HOME'] = str(HERE / 'model_cache/hf')
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
os.environ.setdefault('XDG_CACHE_HOME', str(HERE / 'scratch/xdg'))
os.environ.setdefault('NUMBA_CACHE_DIR', str(HERE / 'scratch/numba'))
sys.path.insert(0, str(ROOT / 'emotion'))
sys.path.insert(0, str(ROOT / 'emotion/vendor'))
sys.path.append(str(HERE / 'vendor'))

import numpy as np
from audio_frames import read_mono, vocal_active, windows
from registration import verify_registration, verify_weights as verify_amended_weights

PLAN_COMMIT = 'e8127190bd82e2b631bb1e749874e73308e45a2c'
MANIFEST_SHA = 'bab8f1a06d8232a62a0e01bd61ea0991725a713d402498b2da53043615d52fae'
M3_RATE = 24000
V3_RATE = 16000


def verify_registered():
    digest = hashlib.sha256((HERE / 'models.json').read_bytes()).hexdigest()
    if digest != MANIFEST_SHA:
        raise RuntimeError('models.json differs from the preregistered manifest')
    for name in ('ANALYSIS_PLAN.md', 'models.json'):
        committed = subprocess.run(['git', 'show', PLAN_COMMIT + ':emotion_r2/' + name],
                                   cwd=ROOT, check=True, capture_output=True).stdout
        if committed != (HERE / name).read_bytes():
            raise RuntimeError(name + ' differs from the preregistered commit')
    return json.loads((HERE / 'models.json').read_text(encoding='utf-8'))


def verify_weights(manifest, role):
    item = next(x for x in manifest['models'] if x.get('role') == role)
    folder = HERE / 'model_cache' / role
    for record in item['files']:
        with (folder / record['path']).open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != record['sha256']:
                raise RuntimeError('Hash mismatch for ' + role + '/' + record['path'])


def verified_stem(track_id, name, cohort=None):
    if name == 'instrumental':
        folder = ROOT / 'emotion/stems' / track_id
        path = folder / 'instrumental.flac'
        separation = folder / 'separation.json'
    elif name == 'vocals':
        if cohort not in ('random', 'pilot'):
            raise ValueError('Unknown cohort for vocal stem: ' + str(cohort))
        folder = ROOT / ('outputs' if cohort == 'random' else 'mvp_325/songs') / track_id
        path = folder / 'stems' / 'vocals.wav'
        separation = folder / 'separation.json'
    else:
        raise ValueError('Unknown stem: ' + name)
    if not path.is_file() or not separation.is_file():
        return None
    data = json.loads(separation.read_text(encoding='utf-8'))
    if name == 'instrumental' and data.get('status') != 'complete':
        return None
    if name == 'vocals' and data.get('status') not in ('ok', 'ok_with_warning', 'complete'):
        return None
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    expected = (data.get('instrumental_sha256') if name == 'instrumental' else
                (data.get('data') or {}).get('stem_sha256', {}).get('vocals'))
    if not expected or expected != digest:
        raise RuntimeError('Unverified ' + name + ' stem for ' + track_id)
    return path


def score_m3(session, signal, section):
    values, coverage, frames = [], [], []
    for frame in windows(section['start_s'], section['end_s'], signal, M3_RATE):
        out = session.run(None, {'audio_waveform': frame['samples'][None, :]})[0]
        if out.shape[-1] != 2 or not np.isfinite(out).all():
            raise ValueError('M3 returned malformed values')
        values.append(out[0].tolist())
        coverage.append(frame['coverage'])
        frames.append({'start_s':frame['start_s'],'end_s':frame['end_s'],'coverage':frame['coverage'],
                       'valence':float(out[0,0]),'arousal':float(out[0,1])})
    if not values:
        return None
    array = np.asarray(values, dtype=np.float64)
    return {'valence_mean': float(array[:, 0].mean()), 'arousal_mean': float(array[:, 1].mean()),
            'valence_median': float(np.median(array[:, 0])),
            'arousal_median': float(np.median(array[:, 1])),
            'valence_sd': float(array[:, 0].std()), 'arousal_sd': float(array[:, 1].std()),
            'frame_count': len(values), 'frames':frames, 'mean_coverage': float(np.mean(coverage))}


def score_v3(model, processor, torch, signal, section, gates):
    values, coverage, active, frames = [], [], 0, []
    for frame in windows(section['start_s'], section['end_s'], signal, V3_RATE):
        gate=gates.get(round(frame['start_s'],6))
        if gate is None: raise ValueError('Missing registered pYIN gate')
        if not gate['active']:
            frames.append({'start_s':frame['start_s'],'end_s':frame['end_s'],'active':False,'valence':None,'arousal':None})
            continue
        active += 1
        inputs = processor(frame['samples'], sampling_rate=V3_RATE, return_tensors='pt',
                           return_attention_mask=True)
        with torch.inference_mode():
            out = model(**{k: v.to('cuda') for k, v in inputs.items()
                           if k in ('input_features', 'attention_mask')})['dims']
        row = out[0].float().cpu().numpy()
        if row.shape[-1] != 3 or not np.isfinite(row).all():
            raise ValueError('V3 returned malformed values')
        values.append(row.tolist())
        coverage.append(frame['coverage'])
        frames.append({'start_s':frame['start_s'],'end_s':frame['end_s'],'active':True,'valence':float(row[0]),'arousal':float(row[1]),'coverage':frame['coverage']})
    if not values:
        return {'status': 'no_active_voice_frames', 'frames': frames, 'active_frames': active}
    array = np.asarray(values, dtype=np.float64)
    return {'status': 'ok', 'valence_mean': float(array[:, 0].mean()),
            'arousal_mean': float(array[:, 1].mean()),
            'dominance_mean': float(array[:, 2].mean()),
            'valence_median': float(np.median(array[:, 0])),
            'arousal_median': float(np.median(array[:, 1])),
            'frame_count': len(values), 'frames':frames, 'active_frames': active,
            'mean_coverage': float(np.mean(coverage))}

def score_v1(model, extractor, torch, signal, section, gates):
    frames=[];values=[]
    for frame in windows(section['start_s'],section['end_s'],signal,V3_RATE):
        gate=gates.get(round(frame['start_s'],6))
        if gate is None: raise ValueError('Missing registered pYIN gate')
        result={'start_s':frame['start_s'],'end_s':frame['end_s'],'coverage':frame['coverage'],'active':gate['active'],'valence':None,'arousal':None}
        if gate['active']:
            inputs=extractor(frame['samples'],sampling_rate=V3_RATE,return_tensors='pt').input_values.to('cuda')
            with torch.inference_mode(): row=model(inputs)[0].float().cpu().numpy()
            if row.shape[-1]!=3 or not np.isfinite(row).all(): raise ValueError('Invalid V1 model output')
            result.update(valence=float(row[2]),arousal=float(row[0]));values.append((row[2],row[0]))
        frames.append(result)
    if not values: return {'status':'no_active_voice_frames','frames':frames,'active_frames':0}
    arr=np.asarray(values,dtype=float)
    return {'status':'ok','valence_mean':float(arr[:,0].mean()),'arousal_mean':float(arr[:,1].mean()),'frames':frames,'active_frames':len(values)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=['M3', 'V1', 'V3'])
    parser.add_argument('--limit', type=int)
    parser.add_argument('--song', action='append')
    args = parser.parse_args()

    registration=verify_registration()
    manifest=registration['manifest']
    item=next(m for m in manifest['models'] if m.get('role')==args.role)
    model_folder=(ROOT/'emotion/model_cache'/item['id'].replace('/','--')) if args.role=='V1' else HERE/'model_cache'/args.role
    verify_amended_weights(manifest,args.role,model_folder)

    dest = HERE / 'scores' / args.role
    dest.mkdir(parents=True, exist_ok=True)
    files = sorted((HERE / 'sections_input').glob('*.json'))
    with (ROOT/'mvp_325/summary.csv').open(encoding='utf-8-sig',newline='') as handle:
        sample={x['track_id']:x for x in csv.DictReader(handle)}
    if len(sample)!=325 or {p.stem for p in files}!=set(sample): raise RuntimeError('Section inputs differ from the registered 325-song sample')
    if args.song:
        files = [p for p in files if p.stem in set(args.song)]
    if args.limit:
        files = files[:args.limit]

    if args.role == 'M3':
        import onnxruntime as ort
        session = ort.InferenceSession(
            str(HERE / 'model_cache/M3/mert_emotion_int8.onnx'),
            providers=['CPUExecutionProvider'])
        model = processor = torch = None
    elif args.role=='V3':
        import torch
        from transformers import AutoModelForAudioClassification, AutoProcessor
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA unavailable')
        path = HERE / 'model_cache/V3'
        processor = AutoProcessor.from_pretrained(path, local_files_only=True)
        model = AutoModelForAudioClassification.from_pretrained(
            path, trust_remote_code=True, local_files_only=True).to('cuda').eval()
        session = None
    else:
        import torch
        from transformers import Wav2Vec2FeatureExtractor
        from voice_model import EmotionModel
        if not torch.cuda.is_available(): raise RuntimeError('CUDA unavailable')
        processor=Wav2Vec2FeatureExtractor.from_pretrained(model_folder,local_files_only=True)
        model,info=EmotionModel.from_pretrained(model_folder,local_files_only=True,output_loading_info=True)
        if info['missing_keys'] or info['unexpected_keys'] or info['mismatched_keys']: raise RuntimeError('Incomplete V1 model load')
        model=model.to('cuda').eval();session=None

    for path in files:
        track_id = path.stem
        target = dest / path.name
        public = json.loads(path.read_text(encoding='utf-8'))
        gate_path=HERE/'scores/voice_gate'/path.name
        stem_name = 'instrumental' if args.role == 'M3' else 'vocals'
        if args.role!='M3' and sample[track_id].get('ring1_source')=='instrumental':
            target.write_text(json.dumps({'track_id':track_id,'cohort':public.get('cohort'),'status':'not_applicable_instrumental','sections':[]})+'\n',encoding='utf8');continue
        stem = verified_stem(track_id, stem_name, public.get('cohort'))
        if stem is None:
            payload = {'track_id': track_id, 'cohort': public.get('cohort'), 'role': args.role, 'plan_commit': PLAN_COMMIT,
                       'status': stem_name + '_stem_unavailable', 'sections': []}
            target.write_text(json.dumps(payload, indent=2) + chr(10), encoding='utf-8')
            print(json.dumps({'track_id': track_id, 'status': payload['status']}), flush=True)
            continue

        gate_data=json.loads(gate_path.read_text(encoding='utf8')) if args.role!='M3' and gate_path.is_file() else None
        if args.role!='M3' and gate_data is None:
            # Missing preprocessing is an explicit missing result, never an
            # inactive-voice decision or a substituted model score.
            payload = {'track_id': track_id, 'cohort': public.get('cohort'),
                       'role': args.role, 'status': 'voice_gate_unavailable',
                       'amendment_commit': registration['amendment_commit'],
                       'sections': []}
            target.write_text(json.dumps(payload) + '\n', encoding='utf8')
            print(json.dumps({'track_id': track_id, 'status': payload['status']}), flush=True)
            continue

        with stem.open('rb') as stream:
            stem_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
        if gate_data and (gate_data.get('stem_sha256')!=stem_hash or gate_data.get('sections_sha256')!=hashlib.sha256(path.read_bytes()).hexdigest()): raise RuntimeError('Voice gate input hash mismatch')
        fingerprint = hashlib.sha256(path.read_bytes() + stem_hash.encode() + registration['amendment_commit'].encode() + (gate_path.read_bytes() if gate_data else b'')).hexdigest()
        if target.exists():
            cached = json.loads(target.read_text(encoding='utf-8'))
            if cached.get('input_sha256') == fingerprint and cached.get('amendment_commit') == registration['amendment_commit']:
                print(json.dumps({'track_id': track_id, 'status': 'cached'}), flush=True)
                continue

        rate = M3_RATE if args.role == 'M3' else V3_RATE
        signal = read_mono(stem, rate)
        results = []
        sections_gates={s['section_id']:{round(f['start_s'],6):f for f in s['frames']} for s in (gate_data or {}).get('sections',[])}
        for row in public['sections']:
            a,b=row.get('start_s'),row.get('end_s')
            if not isinstance(a,(int,float)) or not isinstance(b,(int,float)) or b<=a:
                results.append({'section_id':row['section_id'],'set':row.get('set'),'status':'missing_timing'})
                continue
            try:
                if args.role == 'M3':
                    scored = score_m3(session, signal, row)
                    record = {'section_id': row['section_id'], 'set': row.get('set'),
                              'status': 'ok' if scored else 'no_frames'}
                    if scored:
                        record.update(scored)
                else:
                    gate=sections_gates.get(row['section_id'],{})
                    scored = score_v3(model, processor, torch, signal, row, gate) if args.role=='V3' else score_v1(model,processor,torch,signal,row,gate)
                    record = {'section_id': row['section_id'], 'set': row.get('set')}
                    record.update(scored)
            except (ValueError, RuntimeError) as error:
                record = {'section_id': row['section_id'], 'set': row.get('set'),
                          'status': 'error', 'error': type(error).__name__ + ': ' + str(error)}
            results.append(record)

        payload = {'track_id': track_id, 'cohort': public.get('cohort'),
                   'language': public.get('language'), 'plan_commit': PLAN_COMMIT,
                   'amendment_commit':registration['amendment_commit'], 'manifest_sha256':registration['amended_manifest_sha256'], 'stem_sha256': stem_hash,
                   'input_sha256': fingerprint, 'role': args.role, 'sections': results}
        tmp = target.with_suffix('.tmp')
        tmp.write_text(json.dumps(payload, indent=2) + chr(10), encoding='utf-8')
        tmp.replace(target)
        ok = sum(1 for r in results if r.get('status') == 'ok')
        print(json.dumps({'track_id': track_id, 'sections': len(results), 'scored': ok}),
              flush=True)


if __name__ == '__main__':
    main()
