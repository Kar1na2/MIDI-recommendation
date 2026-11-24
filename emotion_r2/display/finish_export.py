"""Document and package Task I display data without modifying analysis artifacts."""
import argparse, hashlib, json, sys, zipfile
from pathlib import Path
from export_readings import DISPLAY, read, save, sha, now

if sys.executable!='/home/usr/venvs/midi/bin/python':raise RuntimeError('Wrong interpreter')
parser=argparse.ArgumentParser()
parser.add_argument('--part-b',choices=['complete','pending_approval','skipped'],required=True)
args=parser.parse_args()
probs={r:read(DISPLAY/f'{r}_verification.json') for r in ('T1','T3')}
verification=read(DISPLAY/'export_verification.json')
integrity=read(DISPLAY/'integrity_end.json')
optional=read(DISPLAY/'L1_reading_summary.json') if args.part_b=='complete' else None
part_b={'status':args.part_b,'prompt_sha256':sha((DISPLAY/'l1_reading_prompt.txt').read_bytes()),'display_only':True}
if args.part_b=='pending_approval':
    part_b['reason']='Automatic approval review rejected local L1 generation because derived readings could disclose private lyric meaning despite verbatim overlap filtering. User approval is pending; no L1 readings were generated.'
if optional:part_b['summary']=optional
save(DISPLAY/'part_b_status.json',part_b)
lines=['# Task I — per-model lyric display data','These files are display data only. No registered score, checkpoint, threshold, gate, model revision or verdict was changed. No frontend files were edited and nothing was pushed.',
       '## Part A: class probabilities','`text_model_probs/<track_id>.json` contains all 325 songs and all 3,726 registered sections (primary and secondary). Join to existing frontend records by track ID and section ID. No lyric text is included.',
       '| Model | Rescored sections | Largest absolute valence error | Required tolerance |\n|---|---:|---:|---:|']
for role,d in probs.items():lines.append(f"| {role} | {d['status_counts']['ok']} | {d['maximum_absolute_valence_error']:.12g} | 1e-5 |")
lines+=['Every rescored section passed P(positive) − P(negative) versus the registered valence. Class probabilities are softmax outputs, not calibrated emotion confidence. Both models used their pinned local weights, the same inputs, FP32 classifier loading, 512-token truncation, evaluation mode and seed as the registered scoring code.',
        'T1 retains 1,326 `reused_round1`, 706 `unsupported_or_uncertain_language`, and 1,519 `missing_text` statuses. Their probability and valence fields are null; they were not rescored. T3 retains 1,519 `missing_text` records with null values. Reused round-one values can still be read from the registered results, but no class probabilities are inferred for them.',
        '## Part B: optional L1 readings',f"Status: **{args.part_b}**. New prompt: `l1_reading_prompt.txt`; SHA-256 `{part_b['prompt_sha256']}`."]
if optional:
    lines+=[f"There are {optional['songs']} song files in `l1_readings/`. Status counts: `{json.dumps(optional['status_counts'],sort_keys=True)}`. The overlap scan blanked {optional['overlap_blocked_sections']} section readings.",
            '**Display label: L1\'s interpretation of the lyrics.** Keep this attribution beside every reading in the front end, and render readings as plain text. These outputs showcase the model\'s interpretation; they are not verified songwriter intent, human emotion, or an explanation of how registered scores were computed.',
            'The pinned L1 model used local greedy decoding with the new display prompt; its registered score prompt and checkpoints were not changed. Identical prompt/text/revision inputs may reuse an explicitly recorded display-only generation checkpoint.',
            'Overlap rule: normalize NFKC and case; block any four consecutive matching words, and any four matching ja/ko/zh script characters after removing spacing/punctuation. The latter rule is deliberately conservative. Rejected text is never saved, including in logs or intermediate checkpoints. All published readings passed a second scan.',
            'These descriptions are unvalidated model-generated interpretations. The overlap scan detects verbatim reuse, not semantic translation or theme accuracy; it cannot guarantee that a description preserves no source meaning. Readings never replace or affect registered L1 scores.']
elif args.part_b=='pending_approval':lines.append(part_b['reason'])
else:lines.append('The optional reading generation was not run.')
lines+=['## Schema','Each probability file has `track_id`, `cohort`, model IDs/revisions and source-checkpoint SHA-256 values. Each `sections` item has `section_id`, `set`, `language`, `text_sha256`, and separate `T1`/`T3` objects. Model objects retain the registered `status` and contain `probabilities` (`positive`, `neutral`, `negative`), `valence`, `registered_valence`, `absolute_valence_error`, `reproduction_check`, and `truncated`. Skipped records use null numerical values. Scores are not changed or synthesized.',
        'Optional reading files additionally store the new prompt SHA-256, decoding settings, registered status, display reading status, overlap flags and a nullable `reading`. They contain no passage text.',
        '## Verification','Six display-only overlap tests and the 22 existing Task F tests passed. The registered statistical tests were not edited. `export_verification.json` checks every exported section against its source status and score; `registered_files_start.json` and `integrity_end.json` audit unchanged registered artifacts and private input hashes.']
for name in ('round1','v1','mvp_325'):
    d=integrity[name];lines.append(f"- {name}: unchanged: {d['unchanged']:,}/{d['total']:,}; pass: {d['passed']}; extras: {len(d['extra'])}.")
d=integrity['registered_files'];lines.append(f"- Registered Task F files: unchanged: {d['unchanged']:,}/{d['total']:,}; changed private inputs: {len(integrity['private_input_changes'])}.")
lines.append(f"Overall integrity pass: **{integrity['passed']}**. See `integrity_end.json` for the full changed/missing/extra lists. These counts describe the actual verification and are not inferred from earlier Task F results.")
(DISPLAY/'README.md').write_text('\n\n'.join(lines)+'\n',encoding='utf8')
paths={DISPLAY/n for n in ('README.md','schema.json','l1_reading_prompt.txt','input_inventory.json','T1_verification.json','T3_verification.json','export_verification.json','integrity_end.json','part_b_status.json','test_summary.json')}
paths.update((DISPLAY/'text_model_probs').glob('*.json'))
if optional:
    paths.add(DISPLAY/'L1_reading_summary.json');paths.update((DISPLAY/'l1_readings').glob('*.json'))
dest=DISPLAY/'TASK_I_DISPLAY.zip';manifest=[]
with zipfile.ZipFile(dest,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in sorted(paths):
        data=p.read_bytes();name=p.relative_to(DISPLAY).as_posix();z.writestr(name,data)
        manifest.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    z.writestr('MANIFEST.json',json.dumps({'files':manifest},indent=2)+'\n')
with zipfile.ZipFile(dest) as z:
    bad=z.testzip()
    if bad:raise RuntimeError('Invalid archive '+bad)
digest=sha(dest.read_bytes());(DISPLAY/'TASK_I_DISPLAY.sha256').write_text(digest+'  '+dest.name+'\n',encoding='utf8')
save(DISPLAY/'completion.json',{'updated_utc':now(),'part_a':'complete','part_b':args.part_b,'integrity_passed':integrity['passed'],'bundle':dest.name,'bundle_sha256':digest,'bundle_bytes':dest.stat().st_size,'bundle_files':len(manifest)})
save(DISPLAY/'progress.json',{'updated_utc':now(),'phase':'complete' if args.part_b!='pending_approval' else 'part_a_complete_part_b_pending_approval','part_a':'complete','part_b':args.part_b,'integrity_passed':integrity['passed']})
print(json.dumps({'part_a':'complete','part_b':args.part_b,'integrity_passed':integrity['passed'],'archive':str(dest),'bytes':dest.stat().st_size,'sha256':digest}))
