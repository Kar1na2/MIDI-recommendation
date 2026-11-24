"""Diagnose and repair the round-one explanation fit; never score round two."""
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import r2_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

if sys.executable != '/home/usr/venvs/midi/bin/python':
    raise RuntimeError('Use the required WSL interpreter')
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FAMILIES = {
    'drums': ('drums', 'timpani', 'chromatic_percussion'),
    'bass': ('acoustic_bass', 'electric_bass', 'contrabass'),
    'keys': ('acoustic_piano', 'electric_piano', 'organ'),
    'guitar': ('acoustic_guitar', 'clean_electric_guitar', 'distorted_electric_guitar'),
    'strings': ('cello', 'viola', 'violin', 'string_ensemble', 'orchestral_harp'),
    'brass_wind': ('baritone_sax', 'bassoon', 'brass_section', 'clarinet', 'flutes', 'french_horn',
                   'oboe', 'soprano_and_alto_sax', 'tenor_sax', 'trombone', 'trumpet', 'tuba'),
    'synth': ('synth_lead', 'synth_pad', 'synth_strings'),
    'voice': ('voice',),
}

def finite(x):
    return isinstance(x, (int, float)) and math.isfinite(x)

def family_features(features):
    """Missing individual instrument keys mean zero only when quantization exists."""
    result = {}
    observed = {k[len('onset_density__'):] for k in features if k.startswith('onset_density__')}
    unknown = observed - {name for names in FAMILIES.values() for name in names}
    quantized = finite(features.get('onset_density_total'))
    for family, names in FAMILIES.items():
        vals = [features.get('onset_density__' + name, 0.0) for name in names]
        result['family_' + family] = sum(vals) if quantized and all(finite(v) for v in vals) else np.nan
    return result, unknown

def model(indicators):
    return make_pipeline(SimpleImputer(strategy='median', add_indicator=indicators, keep_empty_features=True),
                         StandardScaler(), Ridge(alpha=1.0))

def cross_validate(X, y, groups, columns, indicators, importance=False):
    folds = list(GroupKFold(n_splits=5).split(X, y, groups))
    oof = np.full(len(y), np.nan)
    reports = []
    permutation = defaultdict(list)
    scaling = []
    for fold, (train, test) in enumerate(folds, 1):
        fitted = model(indicators).fit(X[train], y[train])
        prediction = fitted.predict(X[test]); oof[test] = prediction
        fold_score = r2_score(y[test], prediction)
        reports.append({'fold': fold, 'train_sections': len(train), 'test_sections': len(test),
                        'train_songs': len(set(groups[train])), 'test_songs': len(set(groups[test])),
                        'r2': float(fold_score)})
        scaler = fitted.named_steps['standardscaler']
        scaling.append({'fold': fold, 'zero_variance_columns': int((scaler.var_ == 0).sum()),
                        'scaled_columns': len(scaler.scale_)})
        if importance:
            rng = np.random.default_rng(20261115 + fold)
            for j, name in enumerate(columns):
                drops = []
                for _ in range(10):
                    shuffled = X[test].copy(); shuffled[:, j] = rng.permutation(shuffled[:, j])
                    drops.append(fold_score - r2_score(y[test], fitted.predict(shuffled)))
                permutation[name].append(float(np.mean(drops)))
    ranks = [{'feature': name, 'mean_r2_drop': float(np.mean(values)),
              'fold_r2_drops': values, 'positive_folds': sum(v > 0 for v in values),
              'fold_std_r2_drop': float(np.std(values))} for name, values in permutation.items()]
    ranks.sort(key=lambda x: x['mean_r2_drop'], reverse=True)
    return {'oof_r2': float(r2_score(y, oof)), 'folds': reports, 'scaling': scaling, 'permutation_importance': ranks}

def main():
    if not json.loads((HERE/'integrity_start.json').read_text(encoding='utf-8'))['passed']:
        raise RuntimeError('Starting integrity audit has not passed')
    rows = []
    with (ROOT/'emotion/sections.csv').open(encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            if row['cohort'] != 'random' or row['set'] != 'primary': continue
            z = json.loads(row['model_z']).get('background_arousal_consensus')
            if finite(z): rows.append((row['track_id'], json.loads(row['features']), z))
    groups = np.array([x[0] for x in rows]); y = np.array([x[2] for x in rows], dtype=float)
    oldcols = sorted({k for _, features, _ in rows for k,v in features.items() if finite(v)})
    oldX = np.array([[features.get(c, np.nan) if finite(features.get(c)) else np.nan for c in oldcols] for _,features,_ in rows])
    old_coverage = {c: float(np.isfinite(oldX[:, j]).mean()) for j,c in enumerate(oldcols)}
    rebuilt = []; unmapped = set()
    for _, f, _ in rows:
        family, unknown = family_features(f); unmapped.update(unknown)
        rebuilt.append({**{k:v for k,v in f.items() if not k.startswith('onset_density__')}, **family})
    if unmapped: raise ValueError('Unmapped instrument features: ' + ', '.join(sorted(unmapped)))
    allcols = sorted({k for row in rebuilt for k in row})
    coverage = {c: sum(finite(row.get(c)) for row in rebuilt) / len(rebuilt) for c in allcols}
    cols = [c for c in allcols if coverage[c] >= .05]
    X = np.array([[row.get(c, np.nan) if finite(row.get(c)) else np.nan for c in cols] for row in rebuilt])
    old = cross_validate(oldX,y,groups,oldcols,False)
    fixed = cross_validate(X,y,groups,cols,True,True)
    baselines = {}
    for col in ('instrumental_rms','tempo_bpm','onset_density_total','instrumental_brightness_hz'):
        j = cols.index(col) if col in cols else None
        baselines[col] = cross_validate(X[:, [j]],y,groups,[col],True) if j is not None else {'status':'dropped_below_5_percent_coverage'}
    result = {'status':'completed', 'source':'emotion/sections.csv', 'target':'round-one background arousal consensus',
              'no_round2_scoring':True, 'n_sections':len(rows), 'n_songs':len(set(groups)),
              'model':'Ridge(alpha=1), five-fold GroupKFold by song',
              'old':old, 'old_feature_nonnull_coverage':old_coverage,
              'fix':{'family_mapping':FAMILIES,'minimum_nonnull_coverage':.05,'feature_coverage':coverage,
                     'included_features':cols,'dropped_features':[c for c in allcols if c not in cols],
                     'imputation':'training-fold median with missing-value indicators',
                     'scaling':'training-fold standardization after imputation','evaluation':fixed},
              'single_feature_baselines':baselines}
    (HERE/'explanation_phase1.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'sections':len(rows),'songs':len(set(groups)),'old_r2':old['oof_r2'],
                      'fixed_r2':fixed['oof_r2'],'top_features':fixed['permutation_importance'][:5]},indent=2),flush=True)

if __name__ == '__main__': main()
