"""Audit the existing 923 changed section languages before rebuilding inputs."""
import collections,csv,json,sys
from pathlib import Path
if sys.executable!='/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
HERE=Path(__file__).resolve().parent
with (HERE/'language.csv').open(encoding='utf8',newline='') as f: new={r['track_id']:r for r in csv.DictReader(f)}
transitions=collections.Counter(); future=collections.Counter(); mismatches=[]; counts=collections.Counter()
for p in sorted((HERE/'sections_input').glob('*.json')):
    data=json.loads(p.read_text(encoding='utf8')); tid=data['track_id']
    if tid not in new: raise RuntimeError('Section input outside the sample')
    n=new[tid]
    if n['pilot_known_language'] and n['language'] not in (n['pilot_known_language'],'uncertain','not_applicable'):
        mismatches.append({'track_id':tid,'cohort':n['cohort'],'pilot_language':n['pilot_known_language'],'detected_language':n['language']})
    for sec in data.get('sections',[]):
        old=sec.get('round1_language'); prior=sec.get('language'); current=n['language']
        if old!=prior:
            counts['prior_changed_sections']+=1
            transitions[f'{old} -> {prior}']+=1
            future[f'{prior} -> {current}']+=1
        if old!=current: counts['new_changed_sections']+=1
out={'counts':dict(counts),'prior_round1_to_prior_r2':dict(transitions.most_common()),'prior_r2_to_weighted_for_previously_changed_sections':dict(future.most_common()),'pilot_misclassified':mismatches}
(HERE/'language_revision_audit.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
print(json.dumps({'counts':out['counts'],'pilot_misclassified':mismatches},indent=2))
