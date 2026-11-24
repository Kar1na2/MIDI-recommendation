"""Fetch the documented MusiCNN and emoMusic graphs and record their hashes."""
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
URLS = [
    'https://essentia.upf.edu/models/feature-extractors/musicnn/msd-musicnn-1.pb',
    'https://essentia.upf.edu/models/feature-extractors/musicnn/msd-musicnn-1.json',
    'https://essentia.upf.edu/models/classification-heads/emomusic/emomusic-msd-musicnn-2.pb',
    'https://essentia.upf.edu/models/classification-heads/emomusic/emomusic-msd-musicnn-2.json',
]


def main():
    root = HERE / 'model_cache' / 'essentia'
    root.mkdir(parents=True, exist_ok=True)
    result = []
    for url in URLS:
        data = urlopen(url, timeout=90).read()
        target = root / url.rsplit('/', 1)[-1]
        target.write_bytes(data)
        result.append({'url': url, 'path': target.name, 'size': len(data),
                       'sha256': hashlib.sha256(data).hexdigest()})
        print(f'{target.name}: {len(data)} bytes', flush=True)
    (HERE / 'validation' / 'downloaded_essentia.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
