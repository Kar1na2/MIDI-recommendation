"""Grouped out-of-fold explanation fits for trusted channels only."""
import json
import sys
from pathlib import Path

if sys.executable != '/home/usr/venvs/midi/bin/python':
    raise RuntimeError('Use the required WSL interpreter')
import numpy as np
from explanation_repair import cross_validate, family_features, finite

HERE = Path(__file__).resolve().parent
BASELINES = ('instrumental_rms','tempo_bpm','onset_density_total','instrumental_brightness_hz')


def fit(rows, channel):
    selected = [r for r in rows if r['cohort']=='random' and r['set']=='primary'
                and finite(r['consensus'].get(channel))]
    groups = np.array([r['track_id'] for r in selected])
    if len(set(groups)) < 5:
        return {'status':'not evaluable: fewer than five songs'}
    y = np.array([r['consensus'][channel] for r in selected], dtype=float)
    rebuilt = []
    unknown = set()
    for row in selected:
        features = row.get('features') or {}
        families, unmapped = family_features(features); unknown.update(unmapped)
        rebuilt.append({**{k:v for k,v in features.items() if not k.startswith('onset_density__')}, **families})
    if unknown:
        raise ValueError('Unmapped instrument features: '+', '.join(sorted(unknown)))
    names = sorted({k for row in rebuilt for k in row})
    coverage = {k: sum(finite(r.get(k)) for r in rebuilt)/len(rebuilt) for k in names}
    names = [k for k in names if coverage[k]>=.05]
    if not names:
        return {'status':'not evaluable: no features meet coverage'}
    X = np.array([[r.get(k, np.nan) if finite(r.get(k)) else np.nan for k in names] for r in rebuilt])
    full = cross_validate(X,y,groups,names,True,True)
    if full['oof_r2'] <= 0:
        full['permutation_importance'] = []
    baselines = {}
    for name in BASELINES:
        if name in names:
            col = names.index(name)
            baselines[name] = cross_validate(X[:,[col]],y,groups,[name],True)
        else:
            baselines[name] = {'status':'dropped below 5% coverage'}
    return {'status':'completed', 'n_sections':len(selected), 'n_songs':len(set(groups)),
            'coverage':coverage, 'full':full, 'single_feature_baselines':baselines,
            'drivers_shown':full['oof_r2']>0}


def main():
    folder = HERE/'validation'
    rows = json.loads((folder/'sections_analysis.json').read_text(encoding='utf8'))
    verdicts = json.loads((folder/'trust.json').read_text(encoding='utf8'))
    result = {channel: fit(rows,channel) if verdict['overall']['trusted'] else {'status':'not run: channel not trusted'}
              for channel, verdict in verdicts.items()}
    (folder/'explanations.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf8')
    print('Grouped explanations completed for evaluable trusted channels.')


if __name__ == '__main__':
    main()
