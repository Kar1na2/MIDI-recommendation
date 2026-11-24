"""Preregistered sample statistics; no lyric text is accepted or stored here."""
import itertools
import math
from collections import defaultdict

import numpy as np
from scipy.stats import wilcoxon
from stat_methods import correlation, repeat_test


def finite(x):
    return isinstance(x, (int, float, np.number)) and math.isfinite(x)


def trust(rows, models, dimension, *, iterations=10000, words=False):
    """Rows have model z scores and an explicitly sourced label, if any."""
    pairs = {}
    for a, b in itertools.combinations(models, 2):
        pairs[f'{a}--{b}'] = correlation([(r.get('model_z', {}).get(a),
                                            r.get('model_z', {}).get(b)) for r in rows])
    correlations = [p['rho'] for p in pairs.values()]
    median = float(np.median(correlations)) if correlations and all(finite(x) for x in correlations) else None
    repeat = repeat_test([{'track_id': r['track_id'], 'label': r.get('genius_label'),
                           'score': r.get('consensus', {}).get(dimension)} for r in rows],
                         iterations=iterations, seed=20261112)
    independent_pair = (any(p['rho'] is not None and p['rho'] >= .4
                            for key, p in pairs.items() if 'T1' in key) if words else None)
    correlation_pass = median is not None and median >= .4 and (not words or independent_pair)
    return {'pairs': pairs, 'median_rho': median, 't1_pair_pass': independent_pair,
            'repeat': repeat, 'trusted': bool(correlation_pass and repeat['p'] is not None and repeat['p'] < .01)}


def chorus_lift(rows, score_key, *, iterations=10000, seed=20261116):
    """Within-song Chorus minus Verse, requiring exact proven label categories."""
    grouped = defaultdict(lambda: defaultdict(list))
    for row in rows:
        label = row.get('genius_category')
        score = row.get('consensus', {}).get(score_key)
        if label in ('verse', 'chorus') and finite(score):
            grouped[row['track_id']][label].append(float(score))
    values = [{'track_id': track, 'lift': float(np.mean(v['chorus']) - np.mean(v['verse'])),
               'chorus_sections': len(v['chorus']), 'verse_sections': len(v['verse'])}
              for track, v in sorted(grouped.items()) if v['chorus'] and v['verse']]
    diffs = np.asarray([v['lift'] for v in values], dtype=float)
    if not len(diffs):
        return {'n': 0, 'median': None, 'share_above_zero': None, 'p': None,
                'bootstrap_95_ci': None, 'songs': values}
    rng = np.random.default_rng(seed)
    medians = np.median(rng.choice(diffs, size=(iterations, len(diffs)), replace=True), axis=1)
    # All-zero differences have no Wilcoxon information and are not a pass.
    p = float(wilcoxon(diffs, alternative='greater').pvalue) if np.any(diffs != 0) else None
    return {'n': len(diffs), 'median': float(np.median(diffs)),
            'share_above_zero': float(np.mean(diffs > 0)), 'p': p,
            'bootstrap_95_ci': [float(x) for x in np.quantile(medians, [.025, .975])],
            'iterations': iterations, 'seed': seed, 'songs': values}
