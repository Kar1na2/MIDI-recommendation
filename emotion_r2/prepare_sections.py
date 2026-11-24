"""Build round-2 public section tables from round-1 sections plus Phase 1 fixes.

Reads round 1 read-only. Writes public section metadata (no lyric text) to
``emotion_r2/sections_input/`` and verifies that every text hash still matches the
private text under ``emotion_r2/private/``.
"""
import csv
import hashlib
import json
import sys
from pathlib import Path

if sys.executable != '/home/usr/venvs/midi/bin/python':
    raise RuntimeError('Wrong interpreter')

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
R1_SECTIONS = ROOT / 'emotion/validation/input_sections'
DEST = HERE / 'sections_input'


def load_language():
    rows = {}
    with (HERE / 'language.csv').open(encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            rows[row['track_id']] = row
    return rows


def load_labels(track_id):
    path = HERE / 'labels' / (track_id + '.json')
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    return {s['section_id']: s for s in data.get('sections', [])}


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    languages = load_language()
    summary = {'tracks': 0, 'sections': 0, 'with_text': 0, 'without_text': 0,
               'label_changed': 0, 'language_changed': 0, 'inferred_group': 0,
               'hash_mismatch': 0, 'recovered_text': 0}

    for path in sorted(R1_SECTIONS.glob('*.json')):
        track_id = path.stem
        public = json.loads(path.read_text(encoding='utf-8'))
        private_path = HERE / 'private' / (track_id + '.json')
        private = ({s["section_id"]: s for s in
                    json.loads(private_path.read_text(encoding="utf-8")).get("sections", [])}
                   if private_path.is_file() else {})
        labels = load_labels(track_id)
        language_row = languages.get(track_id, {})
        language = language_row.get('language') or 'uncertain'

        rows = []
        for row in public['sections']:
            new = dict(row)
            if row.get('language') != language:
                summary['language_changed'] += 1
            new['language'] = language
            new['language_method'] = language_row.get('method')
            new['language_confidence'] = language_row.get('confidence')
            new['round1_language'] = row.get('language')

            label_row = labels.get(row['section_id'])
            if label_row:
                if label_row.get('label') and label_row['label'] != row.get('label'):
                    summary['label_changed'] += 1
                new['label'] = label_row.get('label') or row.get('label')
                new['genius_alignment'] = label_row.get('genius_alignment')
                group = label_row.get('inferred_group')
                new['inferred_group'] = (group or {}).get('group')
                new['inferred_likely_chorus'] = (group or {}).get('likely_chorus')
                if group:
                    summary['inferred_group'] += 1
            else:
                new['genius_alignment'] = None
                new['inferred_group'] = None
                new['inferred_likely_chorus'] = None

            content = private.get(row['section_id'])
            text = (content or {}).get('text') or ''
            if text:
                digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
                if row.get('text_sha256') and digest != row['text_sha256']:
                    summary['hash_mismatch'] += 1
                    new['text_recovered'] = True
                    summary['recovered_text'] += 1
                else:
                    new['text_recovered'] = False
                new['text_sha256'] = digest
                new['line_count'] = len(content.get('lines') or [])
                new['has_text'] = True
                summary['with_text'] += 1
            else:
                new['has_text'] = False
                new['text_recovered'] = False
                summary['without_text'] += 1
            rows.append(new)

        out = {'track_id': track_id, 'cohort': public.get('cohort'),
               'language': language, 'sections': rows}
        (DEST / (track_id + '.json')).write_text(
            json.dumps(out, ensure_ascii=False, indent=2) + chr(10), encoding='utf-8')
        summary['tracks'] += 1
        summary['sections'] += len(rows)

    (HERE / 'sections_input_summary.json').write_text(
        json.dumps(summary, indent=2) + chr(10), encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
