"""Audit cached frame timing without reading lyric text."""
import json
from pathlib import Path

root = Path(__file__).resolve().parent
invalid = []
for stage in ('m1', 'm2', 'voice'):
    for path in sorted((root/'scratch'/stage).glob('*.json')):
        payload = json.loads(path.read_text(encoding='utf-8'))
        for section in payload.get('sections', []):
            for frame in section.get('frames', []):
                start, end = frame.get('start_s'), frame.get('end_s')
                if not isinstance(start, (float, int)) or not isinstance(end, (float, int)) or end <= start:
                    invalid.append({'stage': stage, 'track_id': path.stem,
                                    'section_id': section.get('section_id'), 'start_s': start, 'end_s': end})
print(json.dumps({'invalid_count': len(invalid), 'examples': invalid[:20]}, indent=2))
