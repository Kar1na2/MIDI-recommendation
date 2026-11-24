"""Score section lyric text with T3 (supervised sentiment) and L1 (local instruct LLM).

Everything runs locally. Lyric text is read from the git-ignored private folder and never
written to any output: only scores and hashes are persisted.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

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
os.environ.setdefault('TORCHINDUCTOR_CACHE_DIR', str(HERE / 'scratch/torchinductor'))
os.environ.setdefault('TRITON_CACHE_DIR', str(HERE / 'scratch/triton'))
os.environ.setdefault('NUMBA_CACHE_DIR', str(HERE / 'scratch/numba'))
sys.path.insert(0, str(ROOT / 'emotion/vendor'))
sys.path.append(str(HERE / 'vendor'))

PLAN_COMMIT = 'e8127190bd82e2b631bb1e749874e73308e45a2c'
MANIFEST_SHA = 'bab8f1a06d8232a62a0e01bd61ea0991725a713d402498b2da53043615d52fae'
SEED = 20261115


def verify_registered():
    digest = hashlib.sha256((HERE / 'models.json').read_bytes()).hexdigest()
    if digest != MANIFEST_SHA:
        raise RuntimeError('models.json differs from the preregistered manifest')
    for name in ('ANALYSIS_PLAN.md', 'models.json', 'l1_prompt.txt'):
        committed = subprocess.run(['git', 'show', PLAN_COMMIT + ':emotion_r2/' + name],
                                   cwd=ROOT, check=True, capture_output=True).stdout
        if committed != (HERE / name).read_bytes():
            raise RuntimeError(name + ' differs from the preregistered commit')
    return json.loads((HERE / 'models.json').read_text(encoding='utf-8'))


def verify_weights(manifest, roles):
    for item in manifest['models']:
        if item.get('role') not in roles:
            continue
        folder = HERE / 'model_cache' / item['role']
        for record in item['files']:
            with (folder / record['path']).open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != record['sha256']:
                    raise RuntimeError('Hash mismatch for ' + item['role'] + '/' + record['path'])


def parse_l1(reply):
    text = reply.strip()
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    try:
        valence = float(parsed['valence'])
        arousal = float(parsed['arousal'])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-1.0 <= valence <= 1.0 and -1.0 <= arousal <= 1.0):
        return None
    return {'valence': valence, 'arousal': arousal,
            'uncertain': bool(parsed.get('uncertain', False))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int)
    parser.add_argument('--song', action='append')
    args = parser.parse_args()

    manifest = verify_registered()
    verify_weights(manifest, {'T3', 'L1'})

    import torch
    from transformers import (AutoModelForCausalLM, AutoModelForSequenceClassification,
                              AutoTokenizer)
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA unavailable')
    torch.manual_seed(SEED)

    prompt_text = (HERE / 'l1_prompt.txt').read_text(encoding='utf-8')
    prompt_sha = hashlib.sha256(prompt_text.encode('utf-8')).hexdigest()

    t3_path = HERE / 'model_cache/T3'
    t3_tokenizer = AutoTokenizer.from_pretrained(t3_path, local_files_only=True)
    t3 = AutoModelForSequenceClassification.from_pretrained(
        t3_path, local_files_only=True).to('cuda').eval()
    labels = {v.lower(): k for k, v in t3.config.id2label.items()}

    l1_path = HERE / 'model_cache/L1'
    l1_tokenizer = AutoTokenizer.from_pretrained(l1_path, local_files_only=True)
    l1 = AutoModelForCausalLM.from_pretrained(
        l1_path, local_files_only=True, torch_dtype=torch.float16,
        device_map='cuda').eval()

    dest = HERE / 'scores/text'
    dest.mkdir(parents=True, exist_ok=True)
    files = sorted((HERE / 'sections_input').glob('*.json'))
    if args.song:
        files = [p for p in files if p.stem in set(args.song)]
    if args.limit:
        files = files[:args.limit]

    for path in files:
        target = dest / path.name
        private_path = HERE / 'private' / path.name
        fingerprint = hashlib.sha256(
            path.read_bytes() + (private_path.read_bytes() if private_path.is_file() else b'')
            + prompt_sha.encode()).hexdigest()
        if target.exists():
            cached = json.loads(target.read_text(encoding='utf-8'))
            if cached.get('input_sha256') == fingerprint and cached.get('plan_commit') == PLAN_COMMIT:
                print(json.dumps({'track_id': path.stem, 'status': 'cached'}), flush=True)
                continue

        public = json.loads(path.read_text(encoding='utf-8'))
        private = ({s["section_id"]: s for s in
                    json.loads(private_path.read_text(encoding="utf-8")).get("sections", [])}
                   if private_path.is_file() else {})

        results = []
        for row in public['sections']:
            record = {'section_id': row['section_id'], 'set': row.get('set'),
                      'language': row.get('language'), 'text_sha256': row.get('text_sha256'),
                      'T3_valence': None, 'L1_valence': None, 'L1_arousal': None,
                      'L1_uncertain': None, 'status': 'missing_text'}
            content = private.get(row['section_id'])
            text = (content or {}).get('text') or ''
            if not text:
                results.append(record)
                continue
            if hashlib.sha256(text.encode('utf-8')).hexdigest() != row.get('text_sha256'):
                raise RuntimeError('Text hash mismatch for ' + path.stem + '/' + row['section_id'])

            with torch.inference_mode():
                tokens = t3_tokenizer(text, truncation=True, max_length=512,
                                      return_tensors='pt').to('cuda')
                probs = t3(**tokens).logits.softmax(dim=-1)[0]
                record['T3_valence'] = float(probs[labels['positive']] - probs[labels['negative']])
                record['T3_truncated'] = len(t3_tokenizer.encode(text, add_special_tokens=True,
                                                                 truncation=False)) > 512

                messages = [{'role': 'user', 'content': prompt_text + chr(10) + text}]
                prompt_ids = l1_tokenizer.apply_chat_template(
                    messages, tokenize=True, add_generation_prompt=True,
                    return_tensors='pt').to('cuda')
                attention = torch.ones_like(prompt_ids)
                generated = l1.generate(prompt_ids, attention_mask=attention,
                                        max_new_tokens=64, do_sample=False,
                                        pad_token_id=l1_tokenizer.eos_token_id)
                reply = l1_tokenizer.decode(generated[0, prompt_ids.shape[1]:],
                                            skip_special_tokens=True)
            parsed = parse_l1(reply)
            if parsed is None:
                record['status'] = 'l1_unparseable'
            else:
                record['L1_valence'] = parsed['valence']
                record['L1_arousal'] = parsed['arousal']
                record['L1_uncertain'] = parsed['uncertain']
                record['status'] = 'ok'
            results.append(record)

        payload = {'track_id': public['track_id'], 'cohort': public.get('cohort'),
                   'language': public.get('language'), 'plan_commit': PLAN_COMMIT,
                   'manifest_sha256': MANIFEST_SHA, 'l1_prompt_sha256': prompt_sha,
                   'input_sha256': fingerprint, 'sections': results}
        tmp = target.with_suffix('.tmp')
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + chr(10),
                       encoding='utf-8')
        tmp.replace(target)
        scored = sum(1 for r in results if r['status'] == 'ok')
        print(json.dumps({'track_id': path.stem, 'sections': len(results), 'scored': scored}),
              flush=True)


if __name__ == '__main__':
    main()
