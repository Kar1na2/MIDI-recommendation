"""Download public score MIDI and screen adjacent arpeggios by their apex contour."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import html
import json
from pathlib import Path
import re
import statistics
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.webexport.build_arpeggio_experiment_page_data import read_midi

OUT = ROOT / 'output/analysis/arpeggio_experiment/classical_apex'
BASE = 'https://www.mutopiaproject.org'


def rising_runs(path: Path, min_note: int, max_note: int, max_gap: float) -> list:
    events = sorted((start, pitch) for start, end, pitch, velocity in read_midi(path)
                    if min_note <= pitch <= max_note)
    groups = []
    for start, pitch in events:
        if not groups or start - groups[-1][0] > .055:
            groups.append([start, pitch])
        else:
            groups[-1][1] = max(groups[-1][1], pitch)
    runs, current = [], []
    for event in groups:
        if current and not (0 < event[0] - current[-1][0] <= max_gap and 0 < event[1] - current[-1][1] <= 12):
            runs.append(current)
            current = []
        current.append(event)
    if current:
        runs.append(current)
    return runs


def triples(runs: list, max_gap: float) -> list[dict]:
    results = []
    for index in range(len(runs) - 2):
        selected = runs[index:index + 3]
        if any(len(run) < 3 or run[-1][1] - run[0][1] < 5 for run in selected):
            continue
        gaps = [selected[i+1][0][0] - selected[i][-1][0] for i in range(2)]
        if max(gaps) > max_gap:
            continue
        rates = [statistics.median(b[0] - a[0] for a,b in zip(run, run[1:])) for run in selected]
        results.append({'start': selected[0][0][0], 'end': selected[-1][-1][0],
                        'counts': [len(run) for run in selected], 'total_notes': sum(map(len, selected)),
                        'run_starts': [run[0][0] for run in selected], 'run_ends': [run[-1][0] for run in selected],
                        'pitch_runs': [[pitch for start,pitch in run] for run in selected],
                        'ioi_medians': rates, 'ioi_cv': statistics.pstdev(rates) / max(statistics.mean(rates), 1e-6), 'gaps': gaps})
    return results


def fetch(url: str, path: Path) -> bytes:
    if path.exists():
        return path.read_bytes()
    data = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'ArpeggioResearch/1.0'}), timeout=45).read()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def text(fragment: str) -> str:
    return html.unescape(re.sub('<[^>]+>', '', fragment)).strip()


def catalogue(limit: int, selected_instrument: str) -> list[dict]:
    pieces = []
    for start in range(0, limit * 10, 10):
        cached = f'index-{start}.html' if selected_instrument == 'Piano' else f'index-{selected_instrument}-{start}.html'
        page = fetch(f'{BASE}/cgibin/make-table.cgi?Instrument={selected_instrument}&startat={start}', OUT / cached).decode('utf-8')
        blocks = re.findall(r'<table class="table-bordered result-table">(.*?)</table>', page, re.S)
        for block in blocks:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', block, re.S)
            if len(cells) < 12:
                continue
            instrument = text(cells[4])
            if selected_instrument not in instrument or re.search(r'Voice|Soprano|Tenor|Choir|Chorus|Baritone', instrument):
                continue
            midi = re.search(r'href="([^"]+\.mid)"', block)
            info = re.search(r'piece-info\.cgi\?id=(\d+)', block)
            if not midi or not info:
                continue
            pieces.append({'id': info[1], 'title': text(cells[0]), 'composer': text(cells[1]).removeprefix('by '),
                           'style': text(cells[6]), 'instrument': instrument, 'license': text(cells[9]),
                           'source_url': f'{BASE}/cgibin/piece-info.cgi?id={info[1]}',
                           'midi_url': html.unescape(midi[1]), 'path': str(OUT / f'{info[1]}.mid')})
        print(f'index {start}: {len(pieces)} instrumental scores with piano', flush=True)
        if 'Next 10' not in page:
            break
    return pieces


def download(piece: dict) -> dict:
    try:
        fetch(piece['midi_url'], Path(piece['path']))
        return piece
    except Exception as exc:
        return {**piece, 'error': str(exc)}


def arpeggio(pitches: list[int]) -> bool:
    steps = [b-a for a,b in zip(pitches, pitches[1:])]
    return 2 <= len({p % 12 for p in pitches}) <= 5 and sum(step >= 3 for step in steps) >= 2


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--download', action='store_true')
    ap.add_argument('--page-limit', type=int, default=100)
    ap.add_argument('--min-note', type=int, default=21)
    ap.add_argument('--max-note', type=int, default=108)
    ap.add_argument('--instrument', choices=['Piano', 'Guitar'], default='Piano')
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    metadata = OUT / 'catalogue.json'
    if args.download:
        pieces = catalogue(args.page_limit, args.instrument)
        with ThreadPoolExecutor(max_workers=4) as pool:
            pieces = list(pool.map(download, pieces))
        if metadata.exists():
            previous = json.loads(metadata.read_text(encoding='utf-8'))
            pieces = list({p['id']: p for p in previous + pieces}.values())
        metadata.write_text(json.dumps(pieces, ensure_ascii=False, indent=2), encoding='utf-8')
    else:
        pieces = json.loads(metadata.read_text(encoding='utf-8'))
    matches = []
    for piece in pieces:
        if 'error' in piece:
            continue
        try:
            runs = rising_runs(Path(piece['path']), args.min_note, args.max_note, .42)
            onsets = {run[0][0]: [t for t,p in run] for run in runs}
            for match in triples(runs, .60):
                apex = [r[-1] for r in match['pitch_runs']]
                if apex[0] < apex[2] < apex[1]:
                    matches.append({**piece, **match, 'apex': apex,
                                    'onset_runs': [onsets[start] for start in match['run_starts']],
                                    'equal_counts': len(set(match['counts'])) == 1})
        except Exception as exc:
            print(f'Cannot scan {piece["id"]}: {exc}', flush=True)
    matches.sort(key=lambda r: (not r['equal_counts'], r['ioi_cv'], sum(r['gaps']), -min(r['counts']), r['title']))
    best = []
    seen = set()
    for item in matches:
        key = (item['composer'], item['title'])
        if key not in seen:
            seen.add(key)
            best.append(item)
    (OUT / 'matches.json').write_text(json.dumps(matches, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / 'best.json').write_text(json.dumps(best, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'scanned': len(pieces), 'triples': len(matches), 'pieces': len(best), 'top': best[:20]}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
