"""Capture model-card metadata before selecting and scoring models."""
import json
from pathlib import Path

from huggingface_hub import HfApi

IDS = [
    'clapAI/roberta-base-multilingual-sentiment',
    'intfloat/multilingual-e5-base',
    'laion/clap-htsat-unfused',
    'audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim',
]


def main():
    api = HfApi()
    results = []
    for model_id in IDS:
        info = api.model_info(model_id, files_metadata=True)
        files = []
        for item in info.siblings or []:
            lfs = item.lfs
            files.append({'name': item.rfilename, 'size': item.size,
                          'git_blob_id': item.blob_id,
                          'lfs_sha256': lfs.sha256 if lfs else None})
        results.append({'id': model_id, 'revision': info.sha,
                        'card_data': info.card_data.to_dict() if info.card_data else {},
                        'files': files})
        print(f'{model_id}: {info.sha}', flush=True)
    target = Path(__file__).resolve().parent / 'validation' / 'candidate_metadata.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(results, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
