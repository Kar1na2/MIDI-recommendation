"""Fixed, preregistered statistics; missing data never count as passes."""
import math
from collections import defaultdict

import numpy as np
from scipy.stats import spearmanr


def finite(value):
    return value is not None and math.isfinite(value)


def standardization(values):
    valid = np.asarray([x for x in values if finite(x)], dtype=float)
    if not valid.size:
        return None
    mean, std = float(valid.mean()), float(valid.std())
    return {'n': len(valid), 'mean': mean, 'std': std} if std > 0 else None


def z(value, params):
    return (value-params['mean'])/params['std'] if finite(value) and params else None


def consensus(left, right):
    return (left+right)/2 if finite(left) and finite(right) else None


def relation(left, right):
    if not finite(left) or not finite(right):
        return None
    if abs(left) >= .5 and abs(right) >= .5:
        return 'supports' if left*right > 0 else 'contradicts'
    return 'neutral/ambiguous'


def profile(pairs):
    """pairs are (relation, positive duration) for scored primary sections."""
    pairs = [(r, d) for r, d in pairs if r is not None and finite(d) and d > 0]
    if not pairs:
        return {'profile': 'unavailable', 'n': 0, 'shares': None}
    total = sum(d for _, d in pairs)
    labels = ('supports', 'contradicts', 'neutral/ambiguous')
    shares = {label: sum(d for r, d in pairs if r == label)/total for label in labels}
    if len(pairs) < 3:
        label = 'flat'
    elif shares['supports'] >= .6:
        label = 'congruent'
    elif shares['contradicts'] >= .4:
        label = 'contrasting'
    else:
        label = 'mixed'
    return {'profile': label, 'n': len(pairs), 'shares': shares}


def correlation(pairs):
    data = [(a, b) for a, b in pairs if finite(a) and finite(b)]
    if len(data) < 3:
        return {'rho': None, 'n': len(data)}
    rho = float(spearmanr([a for a, _ in data], [b for _, b in data]).statistic)
    return {'rho': rho if math.isfinite(rho) else None, 'n': len(data)}


def repeat_test(rows, iterations=10000, seed=20261112):
    groups = defaultdict(list)
    for row in rows:
        label = (row.get('label') or '').strip().casefold()
        if finite(row.get('score')) and label not in ('', 'unlabeled', 'unknown', 'unaligned', 'vocals', 'no vocals'):
            groups[row['track_id']].append((label, row['score']))
    rng = np.random.default_rng(seed)
    deltas, null = [], np.zeros(iterations, dtype=float)
    same_pairs = different_pairs = 0
    for values in groups.values():
        if len(values) < 3:
            continue
        labels = np.array([item[0] for item in values])
        scores = np.array([item[1] for item in values], dtype=float)
        i, j = np.triu_indices(len(values), 1)
        same = labels[i] == labels[j]
        if not same.any() or same.all():
            continue
        distances = np.abs(scores[i]-scores[j])
        deltas.append(float(distances[~same].mean()-distances[same].mean()))
        same_pairs += int(same.sum())
        different_pairs += int((~same).sum())
        shuffled = rng.permuted(np.tile(labels, (iterations, 1)), axis=1)
        perm_same = shuffled[:, i] == shuffled[:, j]
        same_sum = (perm_same*distances).sum(axis=1)
        null += (distances.sum()-same_sum)/(~same).sum()-same_sum/same.sum()
    if not deltas:
        return {'p': None, 'difference': None, 'songs': 0, 'same_pairs': 0, 'different_pairs': 0}
    observed = float(np.mean(deltas))
    null /= len(deltas)
    return {'p': float((1+np.count_nonzero(null >= observed))/(iterations+1)),
            'difference': observed, 'songs': len(deltas), 'same_pairs': same_pairs,
            'different_pairs': different_pairs, 'iterations': iterations, 'seed': seed}


def shuffle_control(rows, iterations=1000, seed=20261113):
    eligible = [r for r in rows if r.get('language') and finite(r.get('words')) and finite(r.get('background'))]
    by_language = defaultdict(list)
    for row in eligible:
        by_language[row['language']].append(row)
    accepted, pools = [], []
    for row in eligible:
        candidates = [x['background'] for x in by_language[row['language']] if x['track_id'] != row['track_id']]
        if candidates:
            accepted.append(row)
            pools.append(np.array(candidates))
    if not accepted:
        return {'n': 0, 'excluded_no_partner': len(eligible), 'observed': None, 'null_mean': None, 'p': None}
    def contribution(a, b):
        label = relation(a, b)
        return 1 if label == 'supports' else -1 if label == 'contradicts' else 0
    observed = np.mean([contribution(row['words'], row['background']) for row in accepted])
    rng = np.random.default_rng(seed)
    null = np.zeros(iterations)
    for row, pool in zip(accepted, pools):
        sampled = pool[rng.integers(len(pool), size=iterations)]
        word = row['words']
        strong = (np.abs(sampled) >= .5) & (abs(word) >= .5)
        null += np.where(strong & (sampled*word > 0), 1, np.where(strong & (sampled*word < 0), -1, 0))
    null /= len(accepted)
    mean = float(null.mean())
    p = (1+np.count_nonzero(np.abs(null-mean) >= abs(observed-mean)))/(iterations+1)
    return {'n': len(accepted), 'excluded_no_partner': len(eligible)-len(accepted),
            'observed': float(observed), 'null_mean': mean, 'p': float(p),
            'iterations': iterations, 'seed': seed}
