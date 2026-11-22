"""Prepare registered sample statistics after every model checkpoint exists."""
import csv
import itertools
import json
import re
import statistics
import sys
from pathlib import Path

if sys.executable != '/home/usr/venvs/midi/bin/python':
    raise RuntimeError('Use the required WSL interpreter')
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / 'emotion'))
from stat_methods import standardization, z
from statistics_r2 import chorus_lift, finite, trust

CHANNELS = {'words_valence': ['T1_valence', 'T3_valence', 'L1_valence'],
            'background_valence': ['M1_valence', 'M2_valence', 'M3_valence'],
            'background_arousal': ['M1_arousal', 'M2_arousal', 'M3_arousal'],
            'voice_valence': ['V1_valence', 'V3_valence'],
            'voice_arousal': ['V1_arousal', 'V3_arousal']}
COMMIT = '7484df243f1e43b82249c4f9a3b26ad7e02c0aa3'


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def source_label(section, original):
    m = re.fullmatch(r'lyric-(\d+)', section['section_id'])
    idx = int(m.group(1)) if m else -1
    source = original[idx] if 0 <= idx < len(original) else {}
    proven = isinstance(source.get('genius_section_index'), int) or bool(section.get('genius_alignment'))
    label = str(section.get('label') or '').strip()
    if not proven or label.casefold().startswith(('unlabeled', 'unknown', 'unaligned')):
        return None, None
    match = re.fullmatch(r'(chorus|verse)(?:\s+\d+)?', label, re.IGNORECASE)
    return label, match.group(1).lower() if match else None


def main():
    with (ROOT / 'mvp_325/summary.csv').open(encoding='utf-8-sig', newline='') as f:
        songs = list(csv.DictReader(f))
    ids = {s['track_id'] for s in songs}
    if len(songs) != 325 or len(ids) != 325:
        raise ValueError('Unexpected sample identity')
    audit = {s['track_id']: s for s in load(HERE/'round1_reuse_audit.json')['songs']}
    old = {}
    with (ROOT/'emotion/sections.csv').open(encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            if row['track_id'] in ids:
                old[(row['track_id'], row['section_id'])] = row
    records = []
    for song in songs:
        tid = song['track_id']
        if audit.get(tid, {}).get('m1') != 'verified' or audit.get(tid, {}).get('m2') != 'verified':
            raise ValueError('Round-one reuse not verified for ' + tid)
        public = load(HERE/'sections_input'/f'{tid}.json')
        if public['cohort'] != song['cohort']:
            raise ValueError('Cohort mismatch: ' + tid)
        folder = ROOT / ('outputs' if song['cohort'] == 'random' else 'mvp_325/songs') / tid
        source = folder/'lyrics.json'
        original = (load(source).get('data') or {}).get('sections') or [] if source.is_file() else []
        by_model = {}
        for role in ('T1', 'T3', 'L1', 'M3', 'V1', 'V3'):
            payload = load(HERE/'scores'/role/f'{tid}.json')
            if payload.get('track_id') != tid or payload.get('cohort') != song['cohort']:
                raise ValueError('Model identity mismatch: '+tid+'/'+role)
            if payload.get('status') not in ('not_applicable_instrumental', 'instrumental_stem_unavailable', 'vocals_stem_unavailable') and payload.get('amendment_commit') != COMMIT:
                raise ValueError('Model checkpoint is not from the amendment: '+tid+'/'+role)
            by_model[role] = {x['section_id']: x for x in payload.get('sections', [])}
        for sec in public['sections']:
            row = dict(sec)
            row.pop('text', None)
            prior = old.get((tid, sec['section_id']))
            if prior:
                old_times = [float(prior[k]) if prior[k] else None for k in ('start_s','end_s')]
                if old_times != [sec.get('start_s'), sec.get('end_s')]:
                    raise ValueError('Round-one section timing differs: '+tid+'/'+sec['section_id'])
            previous = json.loads(prior['scores_raw']) if prior else {}
            raw = {k: previous.get(k) for k in ('M1_valence','M1_arousal','M2_valence','M2_arousal')}
            for role in ('T1', 'T3', 'L1', 'M3', 'V1', 'V3'):
                scored = by_model[role].get(sec['section_id'], {})
                for dim in ('valence', 'arousal'):
                    raw[role+'_'+dim] = scored.get(dim if role in ('T1','T3','L1') else dim+'_mean')
            row['raw'] = raw
            row['features'] = json.loads(prior['features']) if prior else {}
            row['genius_label'], row['genius_category'] = source_label(sec, original)
            records.append(row)
    keys = sorted(set(itertools.chain.from_iterable(CHANNELS.values())))
    params = {key: standardization([r['raw'].get(key) for r in records
                                   if r['cohort']=='random' and r['set']=='primary']) for key in keys}
    for row in records:
        row['model_z'] = {k: z(row['raw'].get(k), params[k]) for k in keys}
        row['consensus'] = {}
        for channel, models in CHANNELS.items():
            values = [row['model_z'][m] for m in models if finite(row['model_z'][m])]
            row['consensus'][channel] = statistics.fmean(values) if values else None
        pair = [row['model_z'][m] for m in ('M1_arousal','M2_arousal') if finite(row['model_z'][m])]
        row['consensus']['background_arousal_round1_models'] = statistics.fmean(pair) if pair else None
    primary = [r for r in records if r['cohort']=='random' and r['set']=='primary']
    verdicts = {}
    for channel, models in CHANNELS.items():
        verdicts[channel] = {'overall': trust(primary, models, channel, words=channel=='words_valence'), 'by_language': {}}
        for language in sorted({r.get('language') or 'uncertain' for r in primary}):
            subset = [r for r in primary if (r.get('language') or 'uncertain') == language]
            verdicts[channel]['by_language'][language] = trust(subset, models, channel, words=channel=='words_valence')
        if channel.startswith('background_'):
            pairs = verdicts[channel]['overall']['pairs']
            keep = [v['rho'] for k,v in pairs.items() if not ('M1_' in k and 'M3_' in k)]
            verdicts[channel]['without_m1_m3_correlation_pass'] = (statistics.median(keep)>=.4 if len(keep)==2 and all(finite(x) for x in keep) else None)
    hypotheses = {}
    for cohort in ('random','pilot','combined'):
        subset = [r for r in records if r['set']=='primary' and (cohort=='combined' or r['cohort']==cohort)]
        # Paragraph clusters do not identify verses.  The exploratory contrast
        # is explicitly likely-chorus versus other inferred groups, never Genius.
        inferred = []
        for row in subset:
            flag = row.get('inferred_likely_chorus')
            if row.get('inferred_group') and flag in (True, False):
                inferred.append({**row, 'genius_category': 'chorus' if flag else 'verse'})
        hypotheses[cohort] = {'round2': chorus_lift(subset,'background_arousal'),
                              'round1_models': chorus_lift(subset,'background_arousal_round1_models'),
                              'inferred_group_exploratory': chorus_lift(inferred,'background_arousal'),
                              'inferred_contrast': 'likely-chorus paragraph group versus all other inferred paragraph groups; not verse labels',
                              'by_language': {language: chorus_lift([r for r in subset if (r.get('language') or 'uncertain') == language], 'background_arousal')
                                              for language in sorted({r.get('language') or 'uncertain' for r in subset})}}
    dest = HERE/'validation'; dest.mkdir(exist_ok=True)
    for name, value in (('normalization.json',params),('trust.json',verdicts),('h1.json',hypotheses)):
        (dest/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf8')
    (dest/'sections_analysis.json').write_text(json.dumps(records,allow_nan=False)+'\n',encoding='utf8')
    print(json.dumps({'sections':len(records),'random_primary':len(primary),'status':'statistics_prepared'}))


if __name__ == '__main__':
    main()
