"""Conditional development analyses; input contains metadata and scores only."""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

if sys.executable != '/home/usr/venvs/midi/bin/python':
    raise RuntimeError('Use the required WSL interpreter')
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent/'emotion'))
from stat_methods import correlation, finite, profile, relation, shuffle_control


def h2(rows):
    usable = []
    for row in rows:
        words = row['consensus'].get('words_valence')
        background = row['consensus'].get('background_valence')
        if finite(words) and finite(background):
            start, end = row.get('start_s'), row.get('end_s')
            usable.append({'track_id': row['track_id'], 'cohort': row['cohort'],
                           'language': row.get('language'), 'words': words,
                           'background': background, 'duration': end-start if finite(start) and finite(end) and end>start else None})
    counts = Counter(relation(r['words'],r['background']) for r in usable)
    by_song = defaultdict(list)
    for row in usable:
        by_song[row['track_id']].append(row)
    song_results = {}
    for tid, entries in sorted(by_song.items()):
        corr = correlation([(x['words'],x['background']) for x in entries])
        song_results[tid] = {'cohort': entries[0]['cohort'], 'correlation': corr,
                             'profile': profile([(relation(x['words'],x['background']), x['duration']) for x in entries])}
    return {'n_sections': len(usable), 'relation_counts': dict(counts),
            'relation_shares': {k:v/len(usable) for k,v in counts.items()} if usable else {},
            'same_language_shuffle': shuffle_control([r for r in usable if r['language'] not in (None,'','uncertain')],iterations=10000,seed=20261113),
            'songs': song_results}


def h3(rows):
    eligible = opposite = 0
    for row in rows:
        c = row['consensus']; voice = c.get('voice_valence'); background = c.get('background_valence'); words = c.get('words_valence')
        if not all(finite(x) for x in (voice,background,words)):
            continue
        if abs(voice)>=.5 and abs(background)>=.5 and voice*background>0:
            eligible += 1
            opposite += bool(abs(words)>=.5 and words*voice<0)
    return {'eligible_sections': eligible, 'opposite_words': opposite,
            'share': opposite/eligible if eligible else None}


def main():
    folder = HERE/'validation'
    rows = json.loads((folder/'sections_analysis.json').read_text(encoding='utf8'))
    verdicts = json.loads((folder/'trust.json').read_text(encoding='utf8'))
    trusted = lambda channel: verdicts[channel]['overall']['trusted']
    results = {}
    for cohort in ('random','pilot','combined'):
        sample = [r for r in rows if r['set']=='primary' and (cohort=='combined' or r['cohort']==cohort)]
        results[cohort] = {
            'h2': h2(sample) if trusted('words_valence') and trusted('background_valence') else {'status':'not run (precondition failed)'},
            'h3': h3(sample) if trusted('words_valence') and trusted('background_valence') and trusted('voice_valence') else {'status':'not run (precondition failed)'},
            'sampling_note': 'random primary sample' if cohort=='random' else 'not a random sample'}
    (folder/'conditional_hypotheses.json').write_text(json.dumps(results,indent=2,allow_nan=False)+'\n',encoding='utf8')
    print('Conditional hypotheses evaluated according to registered preconditions.')


if __name__ == '__main__':
    main()
