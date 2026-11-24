"""CPU-only core recomputation from verified cached note events; no onion work."""
from pathlib import Path
from collections import Counter
from . import beats, quantize, layers, patterns, join
from .common import read, save, digest, StageError, load_songs

ROOT = Path(__file__).resolve().parents[1]


def resolve_songs(selection):
    configured = {s['id']: s for s in load_songs(ROOT / 'songs.yaml')}
    sample = ROOT / 'mvp/sample.json'
    if selection == 'mvp':
        return read(sample)['songs']
    all_songs = dict(configured)
    manifest = ROOT / 'batch/manifest.json'
    if manifest.exists():
        all_songs.update({s['id']: s for s in read(manifest)['songs']})
    if sample.exists():
        all_songs.update({s['id']: s for s in read(sample)['songs']})
    ids = selection.split(',') if isinstance(selection, str) else selection
    missing = set(ids) - set(all_songs)
    if missing:
        raise StageError(f'Unknown song IDs: {sorted(missing)}')
    return [all_songs[tid] for tid in ids]


def data(path):
    value = read(path)
    if value.get('status') == 'failed':
        raise StageError(f'Cached stage failed: {path}')
    return value.get('data', value)


def publish(out, stage, payload):
    save(out / f'{stage}.json', {'schema_version': 2, 'stage': stage,
         'status': 'complete', 'computation_device': 'cpu', 'data': payload})
    return payload


def instrument_metrics(compressed):
    result = {}
    for instrument in sorted({p['instrument'] for p in compressed['patterns']}):
        definitions = [p for p in compressed['patterns'] if p['instrument'] == instrument]
        runs = [r for r in compressed['runs'] if r['instrument'] == instrument]
        bars = [r for r in compressed['bars'] if r['instrument'] == instrument and r.get('active')]
        result[instrument] = {'clusters': len(definitions), 'runs': len(runs),
                              'active_bars': len(bars),
                              'compression_ratio': len(bars)/len(runs) if runs else None}
    return result


def analysis(notes, grid, song):
    q = quantize.quantize(notes, grid, 16, (16, 24, 32, 48))
    lay = layers.split_notes(q['notes'], grid, q['instrument_steps_per_bar'])
    pat = patterns.run(q, grid, .5, .25, 2, 2, 4, 1, lay['pattern_notes'])
    return q, lay, pat


def timed_lyrics(lyrical):
    return {**lyrical, 'sections': [s for s in lyrical.get('sections', [])
            if s.get('validated', s.get('status') in ('complete', 'review'))
            and s.get('raw_start_s') is not None and s.get('raw_end_s') is not None
            and s.get('timing_source') != 'vocal_activity'
            and s.get('label') not in ('unaligned', 'vocals', 'no vocals')]}


def recompute(song, output_root=None, refresh_beats=True, evaluate_grids=True,
              use_routing=True, baseline_shuffles=1000):
    out = Path(output_root or ROOT / 'outputs') / song['id']
    trans = data(out / 'transcription.json')
    if not trans.get('notes'):
        raise StageError('CPU recompute requires cached transcription notes')
    routed = out / 'routing.json'
    routing_data = None
    notes = trans['notes']
    if use_routing and routed.exists() and read(routed).get('status') in ('complete', 'ok_with_warning'):
        routing_data = data(routed)
        notes = routing_data.get('merged_notes', notes)
        if routing_data.get('merged_notes') and not routing_data.get('routed_midi_path'):
            from .common import Note
            from .backends.muscriptor import MuScriptorBackend
            midi=out/'transcription_routed.mid'
            MuScriptorBackend._write_midi([Note(**n) for n in notes],midi)
            routing_data['routed_midi_path']=str(midi)
            record=read(routed); record['data']=routing_data
            save(routed,record)
    grid = data(out / 'beats.json')
    if not grid.get('raw_grid') or not grid.get('dbn_grid'):
        if not refresh_beats:
            raise StageError('Both meter grids are required before recompute')
        detected = beats.run(song['audio_path'], grid['duration_s'], 'cpu')
    else:
        detected = {k: grid[k] for k in ('raw_grid', 'dbn_grid', 'raw')}
    policy = song.get('beats', {}).get('meter_policy', 'strict')
    grid = beats.select_grid(detected, policy)
    grid['audio_sha256']=digest(song['audio_path'])
    publish(out, 'beats', grid)
    q, lay, pat = analysis(notes, grid, song)
    for name, payload in [('quantize',q), ('layers',lay), ('patterns',pat),
                          ('rhythm', {'sections':pat['rhythmic_sections'], 'grid_basis':'bars'})]:
        publish(out, name, payload)
    lyric_path=out/'lyrics.json'
    lyrical = data(lyric_path) if lyric_path.exists() and read(lyric_path).get('status')!='failed' else {}
    if song.get('instrumental') is True or song.get('language')=='N/A':
        lyrical={'status':'complete','source':'instrumental','sections':[],
                 'validated_section_count':0,'instrumental_evidence':'explicit song manifest'}
        publish(out,'lyrics',lyrical)
    report = {'schema_version':'core-mvp/1', 'song_id':song['id'], 'title':song['title'],
        'artist':song['artist'], 'source_sha256':digest(song['audio_path']),
        'status':'ok_with_warning' if trans.get('eos_warning') else 'ok',
        'transcription_source':'cached_full_mix_with_explicit_optional_piano_route',
        'transcription_midi_path':str(out/'transcription.mid'), 'eos_warning':trans.get('eos_warning',False),
        'routed_transcription_midi_path':routing_data.get('routed_midi_path') if routing_data else None,
        'pattern_note_source':'routed_merged_notes' if routing_data else 'full_mix',
        'estimated_bpm':grid['estimated_bpm'], 'meter_policy':policy, 'auto_choice':grid['auto_choice'],
        'instrument_steps_per_bar':q['instrument_steps_per_bar'], 'instrument_quantization':q['instrument_stats'],
        'grid_flags':q['flags'], 'grid_suspect':q['grid_suspect'],
        'instrument_pattern_metrics':instrument_metrics(pat), 'rhythmic_sections':pat['rhythmic_sections'],
        'patterns':pat['patterns'], 'pattern_runs':pat['runs'], 'lyric_sections':lyrical.get('sections',[]),
        'ring1_source':lyrical.get('source',lyrical.get('timing_source')), 'language':lyrical.get('language',song.get('language')),
        'sections_validated':lyrical.get('validated_section_count',0), 'sections_total':len(lyrical.get('sections',[])),
        'lyric_status':lyrical.get('status','pending'), 'boundary_evaluations':{}}
    if (out/'tuning.json').exists():
        report['tuning'] = data(out/'tuning.json')
    references = song.get('reference',{}).get('arpeggio_section',[])
    if isinstance(references,dict): references=[references]
    if references:
        report['arpeggio_acceptance']={'sections':[patterns.arpeggio_acceptance(r,grid,pat,
            quantized=q,transcription_notes=notes,layer_data=lay) for r in references]}
    timed = timed_lyrics(lyrical)
    if timed['sections']:
        joined = join.run(timed,pat,grid,q['notes'],baseline_shuffles=baseline_shuffles,
                          seed=int.from_bytes(song['id'].encode(),'little') % 2**32)
        publish(out,'join',joined)
        # Keep rejected sections visible alongside their source diagnostics.
        # The join and permutation test use only the validated timed subset.
        report.update({k:v for k,v in joined.items() if k != 'lyric_sections'})
        report['joined_lyric_sections'] = joined['lyric_sections']
        selected = iter(joined['lyric_sections'])
        report['lyric_sections'] = [next(selected) if timed_lyrics({'sections':[s]})['sections'] else s
                                    for s in lyrical.get('sections',[])]
    else:
        publish(out,'join',{'status':'not_applicable' if lyrical.get('source')=='instrumental' else 'pending',
                          'reason':'No validated timed lyric sections; excluded from boundary hypothesis test'})
    if evaluate_grids and timed['sections']:
        for name, selected in [('raw','as_detected'), ('auto','auto')]:
            test_grid = beats.select_grid(detected,selected)
            tq, tl, tp = analysis(notes,test_grid,song)
            joined = join.run(timed,tp,test_grid,tq['notes'],seed=int.from_bytes(song['id'].encode(),'little') % 2**32)
            folder=out/'grid_evaluations'/name; folder.mkdir(parents=True,exist_ok=True)
            publish(folder,'join',joined)
            report['boundary_evaluations'][name]=joined['boundary_agreement_baseline']
    save(out/'report.json',report)
    return report


def click(song, grid_name='both'):
    out=ROOT/'outputs'/song['id']; grid=data(out/'beats.json')
    for name in ('raw','dbn') if grid_name=='both' else (grid_name,):
        key=name+'_grid'
        if not grid.get(key): raise StageError('Recompute beats to store both grids first')
        path=out/('click.wav' if name=='raw' else 'click_dbn.wav')
        beats.write_click(song['audio_path'],grid[key],path)

