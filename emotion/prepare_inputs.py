"""Prepare private section text and public provenance; never run emotion models."""
import csv
import hashlib
import json
import math
from pathlib import Path

from pipeline.lrclib import parse_lrc

ROOT = Path(__file__).resolve().parents[1]
EMOTION = ROOT / 'emotion'


def load(path):
    if not path.is_file():
        return None
    with path.open(encoding='utf-8-sig') as stream:
        return json.load(stream)


def timed_text(rows, start, end):
    """Return only nonempty lines whose recorded onset lies in [start,end)."""
    return [str(line['text']).strip() for line in rows
            if line.get('text') and line.get('start_s') is not None
            and start <= float(line['start_s']) < end and str(line['text']).strip()]


def recover(section, source, folder):
    start, end = section['raw_start_s'], section['raw_end_s']
    timing = section.get('timing_source', '')
    if timing in ('lrclib', 'lrclib_paragraphs') and start is not None and end is not None:
        cached = load(folder / 'lrclib.json') or {}
        if cached.get('syncedLyrics'):
            return timed_text(parse_lrc(cached['syncedLyrics']), start, end), 'lrclib_synced'
        return [], 'missing_lrclib_synced'
    if timing.startswith('whisperx') and start is not None and end is not None:
        for name in ('whisperx.json', 'pilot_whisperx.json'):
            cached = load(folder / name) or {}
            if cached.get('lines'):
                return timed_text(cached['lines'], start, end), name
        return [], 'missing_whisperx_lines'
    index = section.get('genius_section_index')
    if isinstance(index, int) and source.get('source_url'):
        for name in ('pilot_genius.json', 'genius_sections.json', 'genius.json'):
            cached = load(folder / name) or {}
            cached = cached.get('data', cached)
            entries = cached.get('sections') or []
            if cached.get('source_url') == source['source_url'] and 0 <= index < len(entries):
                return [str(x).strip() for x in entries[index].get('lines', []) if str(x).strip()], name
    return [], 'text_provenance_unavailable'


def prepare(song, language, folder):
    payload = load(folder / 'lyrics.json') or {}
    data = payload.get('data') or {}
    track, cohort = song['track_id'], song['cohort']
    private, public = [], []
    for i, section in enumerate(data.get('sections') or []):
        a, b = section.get('raw_start_s'), section.get('raw_end_s')
        valid_time = (isinstance(a, (int, float)) and isinstance(b, (int, float))
                      and math.isfinite(a) and math.isfinite(b) and b > a)
        if not valid_time:
            a, b = None, None
            section = {**section, 'raw_start_s': None, 'raw_end_s': None}
        lines, provenance = recover(section, data, folder)
        text = '\n'.join(lines)
        section_id = f'lyric-{i:04d}'
        private.append({'section_id': section_id, 'lines': lines, 'text': text})
        public.append({'track_id': track, 'title': song['title'], 'cohort': cohort, 'language': language,
                       'section_id': section_id, 'label': section.get('label'),
                       'display_label': section.get('display_label'),
                       'start_s': a, 'end_s': b,
                       'set': 'primary' if section.get('validated') is True and valid_time else 'secondary',
                       'validated': section.get('validated') is True and valid_time,
                       'timing_source': section.get('timing_source'), 'text_source': provenance,
                       'line_count': len(lines), 'text_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest() if text else None})
    for i, segment in enumerate(data.get('ring1_segments') or []):
        if segment.get('source') not in ('unaligned', 'vocal_activity') or segment.get('label') == 'no vocals':
            continue
        a, b = segment.get('start'), segment.get('end')
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or not math.isfinite(a) or not math.isfinite(b) or b <= a:
            continue
        public.append({'track_id': track, 'title': song['title'], 'cohort': cohort, 'language': language,
                       'section_id': f'span-{i:04d}', 'label': segment.get('label'),
                       'display_label': segment.get('label'), 'start_s': a, 'end_s': b,
                       'set': 'secondary', 'validated': False, 'timing_source': segment.get('source'),
                       'text_source': 'text_provenance_unavailable', 'line_count': 0, 'text_sha256': None})
    return private, public


def main():
    with (ROOT/'mvp_325/summary.csv').open(encoding='utf-8-sig', newline='') as stream:
        songs = list(csv.DictReader(stream))
    with (ROOT/'analysis_v2/language.csv').open(encoding='utf-8-sig', newline='') as stream:
        languages = {row['track_id']: row['language'] for row in csv.DictReader(stream)}
    private_dir = EMOTION/'private'
    output_dir = EMOTION/'validation'/'input_sections'
    private_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    for song in songs:
        track = song['track_id']
        if not track.isalnum():
            raise ValueError(f'Unsafe track id: {track!r}')
        folder = ROOT/('outputs' if song['cohort'] == 'random' else 'mvp_325/songs')/track
        private, public = prepare(song, languages.get(track, ''), folder)
        (private_dir/f'{track}.json').write_text(json.dumps({'track_id': track, 'cohort': song['cohort'], 'sections': private}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        (output_dir/f'{track}.json').write_text(json.dumps({'track_id': track, 'cohort': song['cohort'], 'sections': public}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f'Prepared provenance for {len(songs)} songs; lyrics remain in emotion/private/.')


if __name__ == '__main__':
    main()
