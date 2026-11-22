"""Demucs CLI with its loader bound to the registered, hash-verified local bag."""
import hashlib
import json
from pathlib import Path

import yaml
from demucs import api
from demucs.apply import BagOfModels
from demucs.hf import load_safetensors_model

HERE = Path(__file__).resolve().parent


def get_pinned_model(name, repo=None):
    if name != 'htdemucs' or repo is not None:
        raise ValueError('Only the registered local htdemucs model is permitted')
    manifest = json.loads((HERE/'models.json').read_text(encoding='utf-8'))
    item = next(x for x in manifest['models'] if x['role'] == 'source_separation')
    for record in item['files']:
        path = HERE/'model_cache'/record['path']
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != record['sha256']:
                raise RuntimeError(f'HTDemucs file hash mismatch: {record["path"]}')
    root = HERE/'model_cache'/'demucs'
    bag = yaml.safe_load((root/'htdemucs.yaml').read_text(encoding='utf-8'))
    if bag.get('models') != ['955717e8']:
        raise ValueError('Unexpected HTDemucs bag definition')
    model = load_safetensors_model(root/'955717e8.safetensors')
    model.eval()
    return BagOfModels([model], bag.get('weights'), bag.get('segment')).eval()


def main():
    # Separator imports this symbol from demucs.api, so bind it before entry.
    api.get_model = get_pinned_model
    from demucs.separate import main as separate
    separate()


if __name__ == '__main__':
    main()
