"""Pre-registered boundary nulls; deliberately separate from v1 statistics."""
import hashlib
from collections import defaultdict

import numpy as np

from . import core, patterns

SHUFFLES = 10_000


def seed_for(track_id, *parts):
    value = '|'.join(map(str, (20261110, track_id, *parts)))
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], 'little')


def voice_sections(pat, grid, mode):
    if mode == 'included':
        return pat['rhythmic_sections']
    features = defaultdict(set)
    active = defaultdict(set)
    selected_instrument_has_notes = False
    for row in pat['bars']:
        is_voice = row['instrument'].split('__', 1)[0] == 'voice'
        keep = (not is_voice) if mode == 'excluded' else is_voice
        if not keep:
            continue
        selected_instrument_has_notes |= bool(row.get('active'))
        if row.get('pattern_type') != 'regular_bar':
            continue
        features[row['bar']].update((row['onset_pattern_id'], row['pitch_pattern_id']))
        if row.get('active'):
            active[row['bar']].add(row['pattern_id'])
    irregular = {row['bar']: row for row in pat.get('irregular_bars', [])}
    settings = pat.get('sectioning', {})
    sections, _ = patterns.novelty_sections(grid, features, irregular,
        settings.get('window_bars', 2), settings.get('minimum_section_bars', 4),
        settings.get('novelty_threshold', .5))
    # With no selected instrument, there is no rhythmic boundary evidence.
    # Mandatory odd-meter cuts alone do not become a synthetic voice result.
    if not selected_instrument_has_notes:
        return []
    for section in sections:
        section['active_pattern_ids'] = sorted(set().union(*(active[i] for i in
            range(section['start_bar'], section['end_bar']))))
    return sections


def boundary_indices(grid, lyrical, sections):
    db = np.array(grid['downbeat_times'], dtype=float)
    # Song-start downbeat and final measured downbeat are endpoint controls.
    # Internal downbeats are all measured downbeats after the first, excluding
    # an endpoint at/after duration; the final measured downbeat normally starts
    # the trailing partial bar and remains a valid internal boundary.
    internal = db[(db > db[0]) & (db < grid['duration_s'])]
    eligible = [s for s in core.timed_lyrics(lyrical)['sections']
                if s.get('label', '').casefold() != 'instrumental']
    ordered = sorted(eligible, key=lambda s: s['raw_start_s'])
    starts = [s['raw_start_s'] for s in ordered[1:]]
    def nearest(t):
        return int(np.argmin(abs(internal - t)))
    if not len(internal):
        return internal, [], []
    # Retain the edge exclusion before snapping, so pickups cannot become
    # the first internal boundary by an artificial nearest-point assignment.
    lyric = sorted({nearest(t) for t in starts if db[0] < t < grid['duration_s']})
    rhythm = sorted({nearest(s['start_s']) for s in sections
                     if db[0] < s['start_s'] < grid['duration_s'] and
                     min(abs(db - s['start_s'])) < 1e-6})
    return internal, lyric, rhythm


def draw_boundaries(count, population, null, seed, shuffles=SHUFFLES, own=None, phrase=None):
    rng = np.random.default_rng(seed)
    if count == 0:
        return np.empty((shuffles, 0), dtype=int), None
    if null == 'circular':
        shifts = rng.integers(0, population, size=shuffles)
        return (np.array(own)[None, :] + shifts[:, None]) % population, None
    pool = np.arange(population) if null == 'uniform' else np.array(phrase, dtype=int)
    if count > len(pool):
        return None, f'{count} boundaries exceed {len(pool)} eligible {null} locations'
    # Random ranks yield a uniform subset without replacement on each row.
    if count == len(pool):
        return np.broadcast_to(pool, (shuffles, len(pool))), None
    ranks = rng.random((shuffles, len(pool)))
    selected = np.argpartition(ranks, count - 1, axis=1)[:, :count]
    return pool[selected], None


def agreement(lyric, rhythmic, tolerance):
    if not len(lyric):
        raise ValueError('No evaluable lyric boundaries')
    if not len(rhythmic):
        return 0.
    return float((np.min(abs(np.array(lyric)[:, None] - np.array(rhythmic)[None, :]), axis=1) <= tolerance).mean())


def null_rates(lyric, draws, tolerance):
    if draws.shape[1] == 0:
        return np.zeros(draws.shape[0])
    rates = np.zeros(draws.shape[0])
    # Loop over lyric boundaries instead of allocating S x K x L.
    for boundary in lyric:
        rates += (abs(draws - boundary) <= tolerance).any(axis=1)
    return rates / len(lyric)


def song_matrix(track_id, grids, pats, lyrics):
    records, distributions, variants = [], {}, {}
    for grid_name in ('raw', 'auto'):
        grid, pat = grids[grid_name], pats[grid_name]
        # Candidate index i corresponds to absolute downbeat number i+1.
        for voice in ('included', 'excluded', 'only'):
            sections = voice_sections(pat, grid, voice)
            variants[f'{grid_name}_{voice}'] = sections
            internal, lyric, own = boundary_indices(grid, lyrics[grid_name], sections)
            for null in ('uniform', 'circular', 'phrase'):
                child_seed = seed_for(track_id, grid_name, voice, null)
                phrase = [i for i in range(len(internal)) if (i + 1) % 4 == 0]
                reason = None
                if not lyric:
                    reason = 'No validated internal text lyric boundaries'
                elif not len(internal):
                    reason = 'No internal measured downbeats'
                if reason is None:
                    draws, reason = draw_boundaries(len(own), len(internal), null,
                        child_seed, own=own, phrase=phrase)
                for tolerance, label in ((1, 'one_bar'), (0, 'exact')):
                    cell = f'{grid_name}__{voice}__{null}__{label}'
                    row = {'cell': cell, 'track_id': track_id, 'grid': grid_name,
                        'voice': voice, 'null': null, 'tolerance': label,
                        'song_seed': seed_for(track_id), 'shuffle_seed': child_seed,
                        'shuffles': SHUFFLES, 'lyric_boundaries': len(lyric),
                        'rhythmic_boundaries': len(own), 'candidate_downbeats': len(internal),
                        'status': 'excluded' if reason else 'complete', 'reason': reason}
                    if reason is None:
                        observed = agreement(lyric, own, tolerance)
                        rates = null_rates(lyric, draws, tolerance)
                        row.update(observed=observed, null_mean=float(rates.mean()),
                            lift=observed - float(rates.mean()),
                            p=float((1 + np.count_nonzero(rates >= observed - 1e-12)) / (SHUFFLES + 1)))
                        distributions[cell] = rates
                    records.append(row)
    return records, distributions, variants


def pooled(records, distributions):
    """Distributions are a list in the same order as complete records."""
    if not records:
        return {'songs': 0, 'status': 'unavailable'}
    observed = float(np.mean([r['observed'] for r in records]))
    null = np.mean(np.array(distributions, dtype=float), axis=0)
    lifts = np.array([r['lift'] for r in records])
    rng = np.random.default_rng(20261111)
    bootstrap = np.median(lifts[rng.integers(0, len(lifts), size=(10_000, len(lifts)))], axis=1)
    ci = np.quantile(bootstrap, [.025, .975]).tolist()
    return {'songs': len(records), 'pooled_observed_mean': observed,
        'pooled_null_mean': float(null.mean()),
        'pooled_p': float((1 + np.count_nonzero(null >= observed - 1e-12)) / (len(null) + 1)),
        'median_lift': float(np.median(lifts)), 'median_lift_ci95': ci,
        'share_song_p_below_005': float(np.mean([r['p'] < .05 for r in records])),
        'shuffles': len(null), 'bootstrap_resamples': 10_000, 'bootstrap_seed': 20261111}
