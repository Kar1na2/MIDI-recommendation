"""Check the public display export against read-only registered records."""
from collections import Counter
import json
from pathlib import Path
import sys
import zipfile
from export_readings import DISPLAY, R2, TOLERANCE, READING_LABEL, READING_NOTICE, file_sha, inputs, overlapping, read, sample, save, sha

if sys.executable!='/home/usr/venvs/midi/bin/python':raise RuntimeError('Wrong interpreter')
expected=set(sample());prob_paths=list((DISPLAY/'text_model_probs').glob('*.json'))
if {p.stem for p in prob_paths}!=expected:raise RuntimeError('Probability song coverage mismatch')
counts={role:Counter() for role in ('T1','T3')};errors={role:0. for role in counts};sets=Counter()
reading_counts=Counter();reading_count=0
part_a_preserved=0
old_bundle=DISPLAY/'TASK_I_DISPLAY.zip'
if old_bundle.is_file():
    with zipfile.ZipFile(old_bundle) as archive: manifest=json.loads(archive.read('MANIFEST.json'))['files']
    for record in manifest:
        if record['path'].startswith('text_model_probs/'):
            if file_sha(DISPLAY/record['path'])!=record['sha256']:raise RuntimeError('Part A export changed')
            part_a_preserved+=1
for path in sorted(prob_paths):
    data=read(path);tid=path.stem;public,texts,_,_=inputs(tid)
    if data['track_id']!=tid or data['cohort']!=public['cohort']:raise RuntimeError('Metadata mismatch')
    if [r['section_id'] for r in data['sections']]!=[r['section_id'] for r in public['sections']]:raise RuntimeError('Section mismatch')
    for role in counts:
        checkpoint=read(R2/'scores'/role/path.name)
        if data['models'][role]['registered_checkpoint_sha256']!=file_sha(R2/'scores'/role/path.name):raise RuntimeError('Source changed')
        for row,old in zip(data['sections'],checkpoint['sections']):
            result=row[role];counts[role][result['status']]+=1
            if result['status']!=old['status']:raise RuntimeError('Registered status changed')
            probabilities=result['probabilities']
            if old['status']=='ok':
                if any(v is None for v in probabilities.values()):raise RuntimeError('Missing eligible probabilities')
                delta=abs(probabilities['positive']-probabilities['negative']-old['valence'])
                if delta>TOLERANCE:raise RuntimeError('Valence mismatch')
                errors[role]=max(errors[role],delta)
            elif any(v is not None for v in probabilities.values()) or result['valence'] is not None or result['registered_valence'] is not None:
                raise RuntimeError('Registered skipped section was scored')
    for row in data['sections']:sets[row['set']]+=1
    reading_path=DISPLAY/'l1_readings'/path.name
    if reading_path.is_file():
        reading_count+=1;reading=read(reading_path)
        if reading['track_id']!=tid or reading['cohort']!=public['cohort']:raise RuntimeError('Reading metadata mismatch')
        if reading['registered_checkpoint_sha256']!=file_sha(R2/'scores/L1'/path.name):raise RuntimeError('L1 source changed')
        old_l1={r['section_id']:r for r in read(R2/'scores/L1'/path.name)['sections']}
        if reading['reading_label']!=READING_LABEL or reading['interpretation_notice']!=READING_NOTICE:raise RuntimeError('Missing model attribution')
        if reading['prompt_sha256']!=sha((DISPLAY/'l1_reading_prompt.txt').read_bytes()):raise RuntimeError('Prompt hash mismatch')
        if [r['section_id'] for r in reading['sections']]!=[r['section_id'] for r in public['sections']]:raise RuntimeError('Reading section mismatch')
        for row in reading['sections']:
            if row['reading_label']!=READING_LABEL or row['interpretation_notice']!=READING_NOTICE:raise RuntimeError('Missing section attribution')
            if row['registered_status']!=old_l1[row['section_id']]['status']:raise RuntimeError('L1 registered status changed')
            if not texts[row['section_id']] and (row['status']!='missing_text' or row['reading'] is not None):raise RuntimeError('Missing-text section was interpreted')
            reading_counts[row['status']]+=1
            if row['reading'] is not None:
                if any(overlapping(row['reading'],texts[row['section_id']]).values()):raise RuntimeError('Published reading overlaps source')
                if row['status']!='ok':raise RuntimeError('Failed reading not blanked')
            elif row['status']=='ok':raise RuntimeError('Missing successful reading')
            if 'text' in row or 'lyrics' in row:raise RuntimeError('Source text leaked')
if reading_count not in (0,325):raise RuntimeError('Incomplete optional reading export')
result={'passed':True,'song_files':325,'section_sets':dict(sets),'registered_status_counts':{r:dict(v) for r,v in counts.items()},
    'maximum_absolute_valence_error':errors,'absolute_tolerance':TOLERANCE,'reading_song_files':reading_count,'reading_status_counts':dict(reading_counts),
    'all_published_readings_overlap_free':True if reading_count else None,'registered_skips_preserved_with_null_probabilities':True,'no_lyric_text_fields':True,
    'part_a_files_unchanged_from_previous_bundle':part_a_preserved}
save(DISPLAY/'export_verification.json',result)
print(json.dumps(result,indent=2))
