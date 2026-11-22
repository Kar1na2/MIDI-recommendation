"""Export only existing background-model window values to frontend scratch storage."""
import argparse
import json
import math
from pathlib import Path
import sys

sys.dont_write_bytecode = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--destination', required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    destination = Path(args.destination).resolve()
    if sys.executable != '/home/usr/venvs/midi/bin/python':
        raise RuntimeError('Use /home/usr/venvs/midi/bin/python')
    if not destination.is_relative_to(root / 'frontend'):
        raise RuntimeError('Window display exports must stay under frontend/')
    sys.path.insert(0, str(root / 'emotion/vendor'))
    try:
        import pyarrow.parquet as parquet
    except ImportError as error:
        print(json.dumps({'availableSongs': 0, 'readerError': str(error), 'failedSongs': {}, 'windows': 0}))
        return
    destination.mkdir(parents=True, exist_ok=True)
    wanted = ['section_id', 'start_s', 'end_s', 'background_arousal_consensus_z']
    wanted += [f'{model}_{dimension}{suffix}' for model in ('M1', 'M2', 'M3')
               for dimension in ('arousal', 'valence') for suffix in ('', '_z')]
    summary = {'availableSongs': 0, 'failedSongs': {}, 'windows': 0}
    for song in sorted((root / 'emotion_r2/digest/frontend_data').glob('*.json')):
        if song.stem in {'corpus', 'schema'}:
            continue
        try:
            path = root / 'emotion_r2/songs' / song.stem / 'curves.parquet'
            columns = set(parquet.read_schema(path).names)
            records = parquet.read_table(path, columns=[name for name in wanted if name in columns]).to_pylist()
            sections = {}
            for record in records:
                section_id = record.get('section_id')
                if not isinstance(section_id, str):
                    continue
                point = {name: value if isinstance(value, (int, float)) and math.isfinite(value) else None
                         for name in wanted if name != 'section_id' for value in [record.get(name)]}
                sections.setdefault(section_id, []).append(point)
            result = {'status': 'available', 'sections': sections}
            summary['availableSongs'] += 1
            summary['windows'] += len(records)
        except Exception as error:
            summary['failedSongs'][song.stem] = str(error)
            result = {'status': 'unavailable', 'reason': str(error), 'sections': {}}
        (destination / song.name).write_text(json.dumps(result, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
