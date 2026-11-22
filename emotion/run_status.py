"""Read-only progress inventory for the preregistered scoring run.

This is not an analysis: it does not normalize, judge trust, or interpret scores.
"""
import csv
import json
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STAGES = {'text': 'text_scores.log', 'm1': 'm1_scores.log',
          'm2': 'm2_scores.log', 'voice': 'voice_scores.log'}


def log_status(path):
    result = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get('track_id'):
            result[row['track_id']] = row
    return result


def inventory():
    with (ROOT/'mvp_325'/'summary.csv').open(encoding='utf-8-sig', newline='') as stream:
        songs = list(csv.DictReader(stream))
    ids = {row['track_id'] for row in songs}
    stages = {}
    for stage, filename in STAGES.items():
        logged = log_status(HERE/filename)
        files = {p.stem for p in (HERE/'scratch'/stage).glob('*.json')}
        failed = {track: row.get('error', row.get('status')) for track, row in logged.items()
                  if row.get('status') in ('failed', 'instrumental_stem_unavailable', 'original_audio_unavailable')}
        excluded = {row['track_id'] for row in songs if stage == 'voice' and row.get('ring1_source') == 'instrumental'}
        stages[stage] = {'cache_files': len(files & ids), 'expected_cache_files': len(ids-excluded),
                         'instrumental_excluded': len(excluded), 'failed_logged': failed,
                         'remaining_without_cache': len(ids-excluded-files),
                         'unexpected_cache_files': sorted(files-ids)}
    separation = Counter()
    for track in ids:
        path = HERE/'stems'/track/'separation.json'
        if path.is_file():
            separation[json.loads(path.read_text(encoding='utf-8')).get('status', 'missing_status')] += 1
        else:
            separation['pending_or_unrecorded'] += 1
    return {'cohorts': dict(Counter(song['cohort'] for song in songs)),
            'separation': dict(separation), 'stages': stages,
            'note': 'Cache counts are progress indicators, not validation of completion or results.'}


if __name__ == '__main__':
    print(json.dumps(inventory(), indent=2, sort_keys=True))
