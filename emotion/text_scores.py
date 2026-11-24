"""Score attributable lyric text from private inputs; persist only hashes/scores."""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer
from stems import library_active

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ANCHORS = {
    'en': ['happiness', 'sadness', 'excitement', 'calm'],
    'ja': ['幸せ', '悲しい', '興奮', '穏やか'],
    'ko': ['행복', '슬픔', '흥분', '차분하다'],
    'zh': ['快乐', '悲伤', '兴奋', '平静'],
}


def verify_registered():
    integrity = json.loads((HERE/'validation'/'integrity_start.json').read_text(encoding='utf-8'))
    if not integrity.get('passed'):
        raise RuntimeError('Starting snapshots are not verified')
    commit = (HERE/'validation'/'plan_commit.txt').read_text(encoding='utf-8').strip()
    for name in ('ANALYSIS_PLAN.md', 'models.json'):
        expected = subprocess.run(['git', 'show', f'{commit}:emotion/{name}'], cwd=ROOT,
                                  check=True, capture_output=True).stdout
        if expected != (HERE/name).read_bytes():
            raise RuntimeError(f'{name} differs from preregistered commit {commit}')
    manifest = json.loads((HERE/'models.json').read_text(encoding='utf-8'))
    for item in manifest['models']:
        if item.get('role') not in ('T1', 'T2'):
            continue
        cache = HERE/'model_cache'/item['id'].replace('/', '--')
        for record in item['files']:
            with (cache/record['path']).open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != record['sha256']:
                    raise RuntimeError(f'Hash mismatch for {item["id"]}/{record["path"]}')
    return manifest, commit


def encode(model, tokenizer, texts, device, embedding=False, batch_size=8):
    output, truncated = [], []
    for start in range(0, len(texts), batch_size):
        group = texts[start:start+batch_size]
        truncated.extend(len(tokenizer.encode(t, add_special_tokens=True, truncation=False)) > 512 for t in group)
        tokens = tokenizer(group, padding=True, truncation=True, max_length=512, return_tensors='pt').to(device)
        with torch.inference_mode():
            predicted = model(**tokens)
            if embedding:
                states = predicted.last_hidden_state.masked_fill(~tokens['attention_mask'][..., None].bool(), 0.)
                values = F.normalize(states.sum(dim=1)/tokens['attention_mask'].sum(dim=1)[..., None], p=2, dim=1)
            else:
                probs = predicted.logits.softmax(dim=-1)
                labels = {v.lower(): k for k, v in model.config.id2label.items()}
                values = probs[:, labels['positive']] - probs[:, labels['negative']]
        output.extend(values.cpu().tolist())
    return output, truncated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--device', choices=['cpu', 'cuda'], default='cpu')
    parser.add_argument('--song', action='append')
    args = parser.parse_args()
    manifest, commit = verify_registered()
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('CUDA requested but unavailable')
    torch.set_num_threads(2)
    t1_info = next(x for x in manifest['models'] if x['role'] == 'T1')
    t1_path = HERE/'model_cache'/t1_info['id'].replace('/', '--')
    t2_path = HERE/'model_cache'/'intfloat--multilingual-e5-base'
    t1_tokenizer = AutoTokenizer.from_pretrained(t1_path, local_files_only=True)
    t1 = AutoModelForSequenceClassification.from_pretrained(t1_path, local_files_only=True).to(args.device).eval()
    t2_tokenizer = AutoTokenizer.from_pretrained(t2_path, local_files_only=True)
    t2 = AutoModel.from_pretrained(t2_path, local_files_only=True).to(args.device).eval()
    anchor_vectors = {}
    for language, terms in ANCHORS.items():
        values, _ = encode(t2, t2_tokenizer, ['query: '+s for s in terms], args.device, embedding=True)
        anchor_vectors[language] = torch.tensor(values)
    dest = HERE/'scratch'/'text'
    dest.mkdir(parents=True, exist_ok=True)
    files = sorted((HERE/'validation'/'input_sections').glob('*.json'))
    if args.song:
        requested = set(args.song)
        files = [path for path in files if path.stem in requested]
        if {path.stem for path in files} != requested:
            raise ValueError('Requested IDs are outside prepared scope')
    for path in files:
        if library_active():
            raise RuntimeError('Library processing detected; emotion scoring paused until queue support is available')
        target = dest/path.name
        private_path = HERE/'private'/path.name
        fingerprint = hashlib.sha256(path.read_bytes() + private_path.read_bytes()).hexdigest()
        if target.exists():
            cached = json.loads(target.read_text(encoding='utf-8'))
            if cached.get('plan_commit') != commit or (cached.get('input_sha256') and cached['input_sha256'] != fingerprint):
                raise RuntimeError(f'Cached text scores have different plan or inputs: {path.stem}')
            if cached.get('input_sha256'):
                print(json.dumps({'track_id': path.stem, 'status': 'cached'}), flush=True)
                continue
            print(json.dumps({'track_id': path.stem, 'status': 'recomputing_unverified_cache'}), flush=True)
        public = json.loads(path.read_text(encoding='utf-8'))
        private = json.loads(private_path.read_text(encoding='utf-8'))
        private_by_id = {s['section_id']: s for s in private['sections']}
        results = []
        for row in public['sections']:
            record = {'section_id': row['section_id'], 'cohort': row['cohort'], 'track_id': row['track_id'],
                      'language': row['language'], 'text_sha256': row['text_sha256'], 'lines': [],
                      'T1': None, 'T2_valence': None, 'T2_arousal': None, 'status': 'missing_text'}
            content = private_by_id.get(row['section_id'])
            if not content or not content['text']:
                results.append(record)
                continue
            if hashlib.sha256(content['text'].encode('utf-8')).hexdigest() != row['text_sha256']:
                raise RuntimeError(f'Text hash mismatch for {row["track_id"]}/{row["section_id"]}')
            items = [content['text']] + content['lines']
            lang = row['language']
            t1_scores, t1_truncated = (encode(t1, t1_tokenizer, items, args.device)
                                       if lang in t1_info['languages'] else ([None]*len(items), [False]*len(items)))
            if lang in anchor_vectors:
                vectors, t2_truncated = encode(t2, t2_tokenizer, ['query: '+x for x in items], args.device, embedding=True)
                similarities = torch.tensor(vectors) @ anchor_vectors[lang].T
                valence = (similarities[:, 0] - similarities[:, 1]).tolist()
                arousal = (similarities[:, 2] - similarities[:, 3]).tolist()
            else:
                valence, arousal, t2_truncated = [None]*len(items), [None]*len(items), [False]*len(items)
            record.update(T1=t1_scores[0], T2_valence=valence[0], T2_arousal=arousal[0],
                          T1_truncated=t1_truncated[0], T2_truncated=t2_truncated[0],
                          status='ok' if any(x is not None for x in (t1_scores[0], valence[0])) else 'unsupported_or_missing_language')
            for index, line in enumerate(content['lines'], 1):
                record['lines'].append({'line_no': index, 'text_sha256': hashlib.sha256(line.encode('utf-8')).hexdigest(),
                                        'T1': t1_scores[index], 'T2_valence': valence[index], 'T2_arousal': arousal[index],
                                        'T1_truncated': t1_truncated[index], 'T2_truncated': t2_truncated[index]})
            results.append(record)
        tmp = target.with_suffix('.tmp')
        tmp.write_text(json.dumps({'track_id': public['track_id'], 'cohort': public['cohort'],
                                   'plan_commit': commit, 'input_sha256': fingerprint,
                                   'device': args.device, 'sections': results},
                                  ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        tmp.replace(target)
        print(json.dumps({'track_id': public['track_id'], 'cohort': public['cohort'], 'sections': len(results)}), flush=True)


if __name__ == '__main__':
    main()
