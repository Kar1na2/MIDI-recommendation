"""Download revision-pinned candidate files; does not run inference."""
import hashlib
import json
from pathlib import Path

from huggingface_hub import snapshot_download

HERE = Path(__file__).resolve().parent
ALLOW = {
    'clapAI/roberta-base-multilingual-sentiment': ['*.json', '*.safetensors', '*.model', '*.txt'],
    'intfloat/multilingual-e5-base': ['*.json', '*.safetensors', '*.model', '*.txt'],
    'laion/clap-htsat-unfused': ['*.json', '*.bin', '*.txt'],
    'audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim': ['*.json', '*.safetensors', '*.txt'],
}


def main():
    candidates = json.loads((HERE / 'validation' / 'candidate_metadata.json').read_text(encoding='utf-8'))
    result = []
    for candidate in candidates:
        model_id = candidate['id']
        local = HERE / 'model_cache' / model_id.replace('/', '--')
        snapshot_download(model_id, revision=candidate['revision'], local_dir=local,
                          allow_patterns=ALLOW[model_id],
                          ignore_patterns=['onnx/*', 'openvino/*', 'tf_model*', 'flax_model*'])
        files = []
        for path in sorted(local.rglob('*')):
            if path.is_file() and '.cache' not in path.relative_to(local).parts:
                with path.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                files.append({'path': path.relative_to(local).as_posix(), 'sha256': digest,
                              'size': path.stat().st_size})
        if not any(f['path'] in ('model.safetensors', 'pytorch_model.bin') for f in files):
            raise RuntimeError(f'Weights missing for {model_id}')
        result.append({'id': model_id, 'revision': candidate['revision'], 'files': files})
        print(f'{model_id}: downloaded and hashed {len(files)} files', flush=True)
    (HERE / 'validation' / 'downloaded_models.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
