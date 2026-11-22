"""Search every local MP3 filename and title/artist tags for specified probes."""
import csv
import json
import unicodedata
from pathlib import Path

import mutagen

ROOT = Path(__file__).resolve().parents[1]
PROBES = [
    ('YOASOBI', 'Yoru ni Kakeru', ('yoru ni kakeru', 'yoru ni kakeru racing into the night', '夜に駆ける')),
    ('Foster the People', 'Pumped Up Kicks', ('pumped up kicks',)),
    ('OutKast', 'Hey Ya!', ('hey ya',)),
    ('Bruno Mars', 'Uptown Funk', ('uptown funk',)),
    ('Bruno Mars', '24K Magic', ('24k magic',)),
    ('Adele', 'Someone Like You', ('someone like you',)),
]


def normalize(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().replace('_', ' ').replace('-', ' ').split())


def main():
    found = {title: [] for _, title, _ in PROBES}
    errors = []
    files = list((ROOT / 'liked-audio').rglob('*.mp3'))
    for file in files:
        text = file.name
        try:
            tags = mutagen.File(file, easy=True)
            if tags:
                text += ' ' + ' '.join(tags.get('title', [])) + ' ' + ' '.join(tags.get('artist', []))
        except Exception as exc:
            errors.append({'path': file.relative_to(ROOT).as_posix(), 'error': str(exc)})
        value = normalize(text)
        for artist, title, variants in PROBES:
            if any(normalize(v) in value for v in variants):
                found[title].append({'path': file.relative_to(ROOT).as_posix(), 'expected_artist': artist})
    dest = ROOT / 'emotion' / 'validation'
    dest.mkdir(parents=True, exist_ok=True)
    (dest / 'probe_inventory.json').write_text(json.dumps({'files_scanned': len(files), 'matches': found,
                                                            'tag_read_errors': errors}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with (dest / 'missing_probes.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['cohort', 'artist', 'title', 'status'])
        writer.writeheader()
        for artist, title, _ in PROBES:
            if not found[title]:
                writer.writerow({'cohort': 'probe', 'artist': artist, 'title': title,
                                 'status': 'no filename or title-tag match' if not errors else 'not found; tag errors require review'})
    print(json.dumps({'files_scanned': len(files), 'matches': found, 'tag_error_count': len(errors)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
