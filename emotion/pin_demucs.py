"""Fetch one explicit HTDemucs HF revision and hash it; do not process audio."""
import hashlib
import importlib.metadata
import json
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

HERE = Path(__file__).resolve().parent


def main():
    name = 'adefossez/HTDemucs'
    info = HfApi().model_info(name, files_metadata=True)
    dest = HERE/'model_cache'/'demucs'
    snapshot_download(name, revision=info.sha, local_dir=dest,
                      allow_patterns=['htdemucs.yaml', '955717e8.safetensors', '955717e8.json', 'README.md'])
    files = []
    upstream = {f.rfilename: f for f in info.siblings}
    for path in sorted(dest.iterdir()):
        if path.is_file():
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            meta = upstream[path.name]
            if meta.lfs and meta.lfs.sha256 != digest:
                raise RuntimeError(f'Checkpoint hash mismatch for {path.name}')
            files.append({'path': 'demucs/'+path.name, 'size': path.stat().st_size, 'sha256': digest})
    if not {'955717e8.safetensors', 'htdemucs.yaml'} <= {p.name for p in dest.iterdir()}:
        raise RuntimeError('Pinned Demucs checkpoint or bag definition missing')
    result = {'id': name, 'role': 'source_separation', 'channel': 'background', 'revision': info.sha,
              'software_version': importlib.metadata.version('demucs'),
              'license': 'scientific purposes only (maintainer statement); code MIT', 'noncommercial': True,
              'license_source': 'https://github.com/facebookresearch/demucs/issues/327',
              'languages': ['audio, language independent'], 'card': 'https://huggingface.co/adefossez/HTDemucs',
              'coverage_note': 'Music source separation; artifacts and vocal leakage are possible.', 'files': files}
    (HERE/'validation'/'demucs_model.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
