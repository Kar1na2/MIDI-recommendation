"""Find three consecutive rising-note bursts in existing MIDI trees and score.

This purposely ignores chord, key, and exact pitch equality. It favors a
three-run note-count shape already observed in the pedal-aware target MIDI;
then it lists all other three-rise matches. It is a review queue, not a
semantic similarity verdict.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path

import pretty_midi

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "output/analysis/arpeggio_experiment/other_bytedance.mid"
TARGET_SONG = "1nonly - Meaningless Love (Official Music Video)"
OUT = ROOT / "output/analysis/arpeggio_experiment/arpeggio_matches"
EXTENSIONS = {".mid", ".midi"}


def rising_runs(path: Path, min_note: int, max_note: int, max_gap: float):
    midi = pretty_midi.PrettyMIDI(str(path))
    events = sorted((float(n.start), int(n.pitch)) for i in midi.instruments for n in i.notes
                    if min_note <= n.pitch <= max_note)
    # Collapse near-simultaneous chord-tone attacks to the highest newly
    # attacked pitch: preserve melodic contour, discount sustained pedal tails.
    groups = []
    for t, pitch in events:
        if not groups or t - groups[-1][0] > .055:
            groups.append([t, pitch])
        else:
            groups[-1][1] = max(groups[-1][1], pitch)
    runs, current = [], []

    def finish():
        # Keep even short/non-rising fragments. Otherwise a triple could jump
        # over unrelated notes and falsely pass the "nothing in between" test.
        if current:
            runs.append(current.copy())

    for event in groups:
        if current:
            dt = event[0] - current[-1][0]
            dp = event[1] - current[-1][1]
            if not (0 < dt <= max_gap and 0 < dp <= 12):
                finish()
                current = []
        current.append(tuple(event))
    finish()
    return runs


def triples(runs, max_gap):
    out = []
    for i in range(len(runs)-2):
        rs = runs[i:i+3]
        # Every collapsed note-on group must belong to one of the three
        # immediately adjacent ascending runs. No skipped intervening note
        # fragment is allowed. A run has >=3 notes and spans >=5 semitones.
        if any(len(r) < 3 or r[-1][1] - r[0][1] < 5 for r in rs):
            continue
        gaps = [rs[k+1][0][0]-rs[k][-1][0] for k in range(2)]
        if max(gaps) > max_gap:
            continue
        counts = [len(r) for r in rs]
        rates = [statistics.median([r[j+1][0]-r[j][0] for j in range(len(r)-1)]) for r in rs]
        rate_cv = statistics.pstdev(rates) / max(statistics.mean(rates), 1e-6)
        out.append({
            "start": rs[0][0][0], "end": rs[-1][-1][0],
            "counts": counts, "total_notes": sum(counts),
            "run_starts": [r[0][0] for r in rs],
            "run_ends": [r[-1][0] for r in rs],
            "pitch_runs": [[p for t,p in r] for r in rs],
            "ioi_medians": rates, "ioi_cv": rate_cv,
            "gaps": gaps,
        })
    return out


def discover_candidates(include_external: Path | None):
    paths = [(p.parent.name, p.resolve(), "project") for p in (ROOT / "midi").glob("*/other.mid")]
    if include_external and include_external.exists():
        paths += [(p.stem, p.resolve(), "external_reference") for p in include_external.rglob("*") if p.suffix.lower() in EXTENSIONS]
    return paths


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--include-external',type=Path)
    ap.add_argument('--min-note',type=int,default=48)
    ap.add_argument('--max-note',type=int,default=88)
    ap.add_argument('--max-ioi',type=float,default=.42)
    ap.add_argument('--max-run-gap',type=float,default=.60)
    ap.add_argument('--limit',type=int)
    args=ap.parse_args()
    target_runs=rising_runs(TARGET,args.min_note,args.max_note,args.max_ioi)
    target_shapes=[r['counts'] for r in triples(target_runs,args.max_run_gap)]
    # Keep observed target shapes, but only shapes with three short arpeggio
    # runs. The primary tier is identical attack count in all three runs.
    preferred=sorted({tuple(s) for s in target_shapes if len(s)==3 and all(3<=n<=7 for n in s)})
    paths=discover_candidates(args.include_external)
    if args.limit: paths=paths[:args.limit]
    match_rows=[]
    for song,path,source in paths:
        if source == "project" and song == TARGET_SONG: continue
        if path.resolve()==TARGET.resolve(): continue
        try:
            rs=rising_runs(path,args.min_note,args.max_note,args.max_ioi)
            ts=triples(rs,args.max_run_gap)
        except Exception:
            continue
        for match in ts:
            shape=tuple(match['counts'])
            match_rows.append({"song":song,"source":source,"path":str(path.relative_to(ROOT)),
                               **match,"same_count_shape":shape in preferred,
                               "equal_count_runs":len(set(shape)) == 1,
                               "target_shapes":preferred})
    # First tier: each of the three arpeggios has the same number of attacks.
    # Second: a three-run count shape seen in the target transcription.
    # Third: any three consecutive rises. Within tiers prefer even timing,
    # shorter gaps, and shorter spans; chord/key/pitch terms are not used.
    match_rows.sort(key=lambda r:(not r['equal_count_runs'],
        not r['same_count_shape'],
        min((sum(a!=b for a,b in zip(r['counts'],s)) + abs(len(r['counts'])-len(s)) for s in preferred),default=99),
        r['ioi_cv'],sum(r['gaps']),r['end']-r['start'],r['song'].casefold()))
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'target_summary.json').write_text(json.dumps({"target_midi":str(TARGET.relative_to(ROOT)),
        "rising_runs":len(target_runs),"three_run_shapes":target_shapes,"preferred_exact_shapes":preferred,
        "parameters":{"min_note":args.min_note,"max_note":args.max_note,"max_ioi":args.max_ioi,
                       "max_inter_run_gap":args.max_run_gap}},indent=2),encoding='utf-8')
    fields=['rank','tier','song','source','path','start','end','counts','run_starts','run_ends',
            'pitch_runs','ioi_medians','ioi_cv','gaps','same_count_shape']
    fields.append('equal_count_runs')
    with (OUT/'three_arpeggio_candidates.csv').open('w',newline='',encoding='utf-8') as fp:
        w=csv.DictWriter(fp,fieldnames=fields);w.writeheader()
        for idx,r in enumerate(match_rows,1):
            tier = "equal_count_runs" if r['equal_count_runs'] else ("target_count_shape" if r['same_count_shape'] else "any_three_rises")
            w.writerow({"rank":idx,"tier":tier,
                **{k:(json.dumps(r[k]) if isinstance(r[k],(list,dict)) else r[k]) for k in fields if k in r}})
    # One best match per song keeps repeat passages from dominating review.
    best=[];seen=set()
    for row in match_rows:
        if row['song'] not in seen:
            seen.add(row['song']);best.append(row)
    with (OUT/'top_per_song.csv').open('w',newline='',encoding='utf-8') as fp:
        w=csv.DictWriter(fp,fieldnames=fields);w.writeheader()
        for idx,r in enumerate(best,1):
            tier = "equal_count_runs" if r['equal_count_runs'] else ("target_count_shape" if r['same_count_shape'] else "any_three_rises")
            w.writerow({"rank":idx,"tier":tier,
                **{k:(json.dumps(r[k]) if isinstance(r[k],(list,dict)) else r[k]) for k in fields if k in r}})
    print(json.dumps({"songs_scanned":len(paths),"target_shapes":preferred,
        "total_three_run_matches":len(match_rows),"songs_with_match":len(best),
        "same_count_matches":sum(r['same_count_shape'] for r in match_rows),
        "equal_count_run_matches":sum(r['equal_count_runs'] for r in match_rows),
        "top":[{"song":r['song'],"tier":"equal_count_runs" if r['equal_count_runs'] else ("target_count_shape" if r['same_count_shape'] else "any_three_rises"),
                "counts":r['counts'],"start":round(r['start'],2),"end":round(r['end'],2),
                "ioi_cv":round(r['ioi_cv'],2)} for r in best[:20]]},indent=2),flush=True)

if __name__=='__main__': main()
