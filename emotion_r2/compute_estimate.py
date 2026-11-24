"""Transparent workload extrapolations from synthetic tests, not measured song throughput."""
import csv,json,sys
from pathlib import Path
if sys.executable!='/home/usr/venvs/midi/bin/python':raise RuntimeError('Wrong interpreter')
here=Path(__file__).resolve().parent;root=here.parent
def read(p):return json.loads(p.read_text(encoding='utf-8-sig')) if p.is_file() else {}
with (root/'mvp_325/summary.csv').open(encoding='utf8',newline='') as f:songs=list(csv.DictReader(f))
changed=0
for song in songs:
    tid=song['track_id'];new=read(here/'sections_input'/f'{tid}.json');old=read(root/'emotion/validation/input_sections'/f'{tid}.json')
    old_by={s['section_id']:s for s in old.get('sections',[])}
    for section in new.get('sections',[]):
        original=old_by.get(section['section_id']) or {}
        if section.get('has_text') and (section.get('text_sha256')!=original.get('text_sha256') or section.get('language')!=original.get('language')):changed+=1
counts=read(here/'compute_plan_inventory.json')['counts'];gate=read(here/'voice_gate_validation.json')
active=(gate['confusion']['both_active']+gate['confusion']['gate_only'])/gate['windows']
voice_windows=counts['all_timed_windows']*active
bench={r:read(here/'scratch'/f'benchmark_{r}.json') for r in ('T3','L1','M3','V3','V1')}
if any(not x for x in bench.values()):raise RuntimeError('Synthetic benchmarks are incomplete')
rates={r:(x.get('inference_seconds') if r=='V1' else x['result']['inference_seconds']) for r,x in bench.items()}
estimates={'T1':{'input_count':changed,'basis':'T3 synthetic rate used as an unvalidated proxy; only changed sections','hours':changed*rates['T3']/3600},
 'T3':{'input_count':counts['text_sections'],'hours':counts['text_sections']*rates['T3']/3600},
 'L1':{'input_count':counts['text_sections'],'hours':counts['text_sections']*rates['L1']/3600},
 'M3':{'input_count':counts['all_timed_windows'],'hours':counts['all_timed_windows']*rates['M3']/3600},
 'V1':{'estimated_active_windows':voice_windows,'hours':voice_windows*rates['V1']/3600},
 'V3':{'estimated_active_windows':voice_windows,'hours':voice_windows*rates['V3']/3600},
 'pYIN_gate':{'songs':gate['eligible_songs'],'hours':gate['seconds']/10*gate['eligible_songs']/3600},
 'M1_M2':{'hours':0,'basis':'Reuse only after input-hash verification; excludes hash-reading time'}}
out={'sample_text_sections':counts['text_sections'],'all_timed_windows':counts['all_timed_windows'],'t1_changed_text_sections':changed,
 'synthetic_inference_seconds':rates,'validation_active_window_share':active,'estimates':estimates,
 'caveat':'Single cold synthetic inference per model, scaled linearly; text lengths, first-call overhead, window overlap, stem availability, failure handling, device contention and GPU I/O make these highly uncertain. Counts include secondary sections. This is not measured end-to-end throughput.'}
(here/'compute_estimate.json').write_text(json.dumps(out,indent=2,allow_nan=False)+'\n',encoding='utf8');print(json.dumps(out,indent=2))
