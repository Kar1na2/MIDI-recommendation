"""Sample a blank human-review anchor without reading lyrics or model scores."""
import csv
import json
import random
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def timestamp(seconds):
    value = int(round(seconds))
    return f'{value//60}:{value%60:02d}'


def main():
    rng = random.Random(20261114)
    groups = defaultdict(list)
    for path in sorted((HERE/'validation'/'input_sections').glob('*.json')):
        song = json.loads(path.read_text(encoding='utf-8'))
        rows = [r for r in song['sections'] if r['set'] == 'primary' and r['language']
                and r['start_s'] is not None and r['end_s'] is not None]
        if rows:
            chosen = rng.choice(rows)
            groups[chosen['language']].append(chosen)
    if sum(map(len, groups.values())) < 30:
        raise RuntimeError('Fewer than 30 eligible songs with known language')
    for rows in groups.values():
        rng.shuffle(rows)
    languages = sorted(groups)
    quota = {lang: min(len(groups[lang]), 30//len(languages)) for lang in languages}
    remaining = 30-sum(quota.values())
    capacity = {lang: len(groups[lang])-quota[lang] for lang in languages}
    total = sum(capacity.values())
    ideal = {lang: remaining*capacity[lang]/total for lang in languages}
    for lang in languages:
        quota[lang] += int(ideal[lang])
    left = 30-sum(quota.values())
    for lang in sorted(languages, key=lambda item: (-(ideal[item] % 1), item))[:left]:
        quota[lang] += 1
    chosen = [row for lang in languages for row in groups[lang][:quota[lang]]]
    rng.shuffle(chosen)
    fields = ['track_id', 'title', 'section_label', 'start_mmss', 'end_mmss',
              'my_relation(supports/neutral/contradicts)', 'my_confidence_1to3', 'notes', 'cohort']
    target = HERE/'HUMAN_ANCHOR.csv'
    if target.exists():
        raise FileExistsError('Refusing to overwrite a potentially completed human anchor')
    with target.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in chosen:
            writer.writerow({'track_id': row['track_id'], 'title': row['title'],
                             'section_label': row['display_label'] or row['label'],
                             'start_mmss': timestamp(row['start_s']), 'end_mmss': timestamp(row['end_s']),
                             'cohort': row['cohort']})
    (HERE/'validation'/'human_anchor_sampling.json').write_text(json.dumps({'seed': 20261114,
        'eligible_songs_by_language': {lang: len(groups[lang]) for lang in languages},
        'selected_by_language': quota, 'selected': len(chosen)}, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'selected': len(chosen), 'languages': quota}))


if __name__ == '__main__':
    main()
