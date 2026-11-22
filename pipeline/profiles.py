"""Explicit isolated analysis profiles. The existing v1 path stays unchanged."""
import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from . import beats, core, join
from .common import StageError, read, save

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / 'analysis_v2'


def output_folder(output_root, song_id):
    root = Path(output_root).resolve()
    # Isolated writes must never be directed into the protected caches.
    protected = [ROOT / 'outputs', ROOT / 'mvp', ROOT / 'pilot', ROOT / 'docs']
    if any(root == p or p in root.parents for p in protected):
        raise StageError('Isolated recomputation cannot write to a v1 directory')
    return root / song_id


def code_stamp(song, profile):
    modules = sorted((ROOT / 'pipeline').rglob('*.py'))
    h = hashlib.sha256()
    for p in modules:
        h.update(p.relative_to(ROOT).as_posix().encode())
        h.update(p.read_bytes())
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout.strip()
    config = {'profile': profile, 'song': song,
              'grid_candidates': [16,24,32,48], 'pattern_threshold': .85,
              'onset_tolerance_steps': 1, 'section_window': 2, 'minimum_bars': 4}
    return {'code_commit': commit, 'working_tree_code_sha256': h.hexdigest(),
            'config_sha256': hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
            'profile': profile, 'device': 'cpu', 'config': config}


def resnap(lyrical, grid):
    """Preserve measured times and validation; update only grid coordinates."""
    result = copy.deepcopy(lyrical)
    for s in result.get('sections', []):
        for edge in ('start', 'end'):
            raw = s.get(f'raw_{edge}_s')
            snapped = beats.snap(grid, raw) if raw is not None else None
            s[f'snapped_{edge}_s'] = snapped
            s[f'{edge}_bar'] = round(beats.bar_position(grid, snapped)) if snapped is not None else None
        s['v2_snap_status'] = ('collapsed' if s.get('snapped_start_s') is not None and
            s.get('snapped_end_s') is not None and s['snapped_end_s'] <= s['snapped_start_s'] else 'ok')
    result['snap_policy'] = 'v2 auto grid; original raw times and v1 validation retained'
    return result


def recompute(song, profile='v1', output_root=None, source_root=None):
    if profile == 'v1' and output_root is None and source_root is None:
        return core.recompute(song)
    if profile not in ('v1', 'v2'):
        raise StageError(f'Unknown profile: {profile}')
    destination = output_root or V2 / 'songs'
    out = output_folder(destination, song['id'])
    src = Path(source_root or ROOT / 'outputs') / song['id']
    if profile == 'v1':
        # A complete upstream clone makes the original code execute unchanged.
        # copyfile creates independent files, never hardlinks or symlinks.
        out.mkdir(parents=True, exist_ok=True)
        for name in ('beats', 'transcription', 'routing', 'lyrics', 'tuning'):
            p = src / f'{name}.json'
            if p.exists():
                shutil.copyfile(p, out / p.name)
        return core.recompute(song, destination, refresh_beats=False)
    trans = core.data(src / 'transcription.json')
    if not trans.get('notes'):
        raise StageError('No successful cached transcription; no inference fallback')
    cached_grid = core.data(src / 'beats.json')
    if not cached_grid.get('raw_grid') or not cached_grid.get('dbn_grid'):
        raise StageError('Both cached meter grids are required; no GPU or beat inference allowed')
    notes = trans['notes']
    note_source = 'full_mix'
    routed = src / 'routing.json'
    if routed.exists() and read(routed).get('status') in ('complete', 'ok_with_warning'):
        route = core.data(routed)
        if route.get('merged_notes'):
            notes = route['merged_notes']
            note_source = 'cached_routed_merged_notes'
    grid = beats.select_grid(cached_grid, 'auto')
    q, lay, pat = core.analysis(notes, grid, song)
    lyrical = core.data(src / 'lyrics.json') if (src / 'lyrics.json').exists() else {
        'source': 'unavailable', 'sections': [], 'status': 'missing'}
    lyrical = resnap(lyrical, grid)
    stamp = code_stamp(song, profile)
    def publish(stage, value):
        save(out / f'{stage}.json', {'schema_version': 2, 'stage': stage,
            'status': 'complete', 'computation_device': 'cpu', 'provenance': stamp, 'data': value})
    for stage, value in [('beats', grid), ('quantize', q), ('layers', lay),
                         ('patterns', pat), ('rhythm', {'sections': pat['rhythmic_sections'], 'grid_basis': 'bars'}),
                         ('lyrics', lyrical)]:
        publish(stage, value)
    timed = core.timed_lyrics(lyrical)
    joined = join.run(timed, pat, grid, q['notes'], baseline_shuffles=1) if timed['sections'] else {
        'status': 'not_applicable', 'reason': 'No validated timed text lyric sections'}
    # The 1-draw legacy statistic is not evidence: the full pre-registered matrix
    # replaces it in a separate artifact with 10,000 draws.
    joined.pop('boundary_agreement_baseline', None)
    joined['hypothesis_result'] = 'See hypothesis.json; legacy baseline deliberately omitted'
    publish('join', joined)
    report = {'schema_version': 'analysis-v2/1', 'song_id': song['id'], 'title': song['title'],
        'status': 'complete', 'profile': 'v2', 'provenance': stamp,
        'auto_choice': grid['auto_choice'], 'chosen_grid': grid['chosen_grid'],
        'pattern_note_source': note_source, 'instrument_steps_per_bar': q['instrument_steps_per_bar'],
        'instrument_pattern_metrics': core.instrument_metrics(pat),
        'rhythmic_section_count': len(pat['rhythmic_sections']),
        'ring1_source': lyrical.get('source'), 'sections_validated': lyrical.get('validated_section_count',0),
        'sections_total': len(lyrical.get('sections', [])), 'source_root': str(src)}
    save(out / 'report.json', report)
    return report
