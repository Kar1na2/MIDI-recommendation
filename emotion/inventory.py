"""Inventory Task D inputs without scoring or exposing lyric text."""
import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'emotion' / 'validation'


def read_json(path):
    if not path.exists():
        return None
    with path.open(encoding='utf-8-sig') as stream:
        return json.load(stream)


def main():
    with (ROOT / 'mvp_325' / 'summary.csv').open(encoding='utf-8-sig', newline='') as stream:
        songs = list(csv.DictReader(stream))
    with (ROOT / 'analysis_v2' / 'language.csv').open(encoding='utf-8-sig', newline='') as stream:
        languages = {row['track_id']: row['language'] for row in csv.DictReader(stream)}
    rows = []
    for song in songs:
        track, cohort = song['track_id'], song['cohort']
        base = ROOT / ('outputs' if cohort == 'random' else 'mvp_325/songs') / track
        lyrics = read_json(base / 'lyrics.json') or {}
        data = lyrics.get('data') or {}
        sections = data.get('sections') or []
        lrclib = read_json(base / 'lrclib.json') or {}
        stems = base / 'stems'
        vocal_files = [p for p in stems.rglob('*') if p.is_file() and p.stem.lower() == 'vocals'] if stems.exists() else []
        rows.append(dict(track_id=track, title=song['title'], cohort=cohort,
                         language=languages.get(track, ''), ring1_source=song.get('ring1_source', ''),
                         lyrics_stage=bool(lyrics), sections=len(sections),
                         validated=sum(s.get('validated') is True for s in sections),
                         cached_lrclib_synced=bool(lrclib.get('syncedLyrics')),
                         cached_lrclib_plain=bool(lrclib.get('plainLyrics')),
                         vocal_stem_present=bool(vocal_files)))
    DEST.mkdir(parents=True, exist_ok=True)
    with (DEST / 'input_inventory.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    counts = {key: dict(Counter(str(row[key]) for row in rows)) for key in
              ('cohort', 'language', 'ring1_source', 'lyrics_stage', 'cached_lrclib_synced',
               'cached_lrclib_plain', 'vocal_stem_present')}
    counts['validated_sections'] = sum(row['validated'] for row in rows)
    (DEST / 'input_inventory_summary.json').write_text(json.dumps(counts, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == '__main__':
    main()
