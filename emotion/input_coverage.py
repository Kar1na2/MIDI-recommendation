"""Count prepared section provenance without loading or emitting private text."""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    counts = Counter()
    by_cohort = {}
    for path in (ROOT/'validation'/'input_sections').glob('*.json'):
        song = json.loads(path.read_text(encoding='utf-8'))
        cohort = song['cohort']
        cohort_counts = by_cohort.setdefault(cohort, Counter())
        for row in song['sections']:
            prefix = row['set']
            keys = [prefix + '_sections', prefix + ('_with_text' if row['line_count'] else '_without_text'),
                    prefix + '_source:' + row['text_source']]
            for key in keys:
                counts[key] += 1
                cohort_counts[key] += 1
    result = {'all': dict(counts), 'cohorts': {key: dict(value) for key, value in by_cohort.items()}}
    (ROOT/'validation'/'input_coverage.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
