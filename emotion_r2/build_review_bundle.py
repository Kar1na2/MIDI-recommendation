"""Build a lyric-free, explicitly scoped Task F review bundle."""
import csv
import hashlib
import json
import sys
import zipfile
from pathlib import Path

if sys.executable != '/home/usr/venvs/midi/bin/python':
    raise RuntimeError('Use /home/usr/venvs/midi/bin/python')
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEST = HERE/'TASK_F_PRELIMINARY_REVIEW.zip'
explicit = [
    'emotion_r2/REVIEW_README.md', 'emotion_r2/ANALYSIS_PLAN.md', 'emotion_r2/STATUS.md',
    'emotion_r2/INHERITED_RUN_AUDIT.md', 'emotion_r2/CONTINUATION_STATUS.md',
    'emotion_r2/l1_prompt.txt',
    'emotion_r2/models.json', 'emotion_r2/models_amended.json', 'emotion_r2/models_amended.sha256',
    'emotion_r2/integrity_continuation_start.json', 'emotion_r2/r1_snapshot.json',
    'analysis_v2/v1_snapshot.json', 'mvp_325/snapshot.json', 'mvp_325/summary.csv',
    'emotion/sections.csv', 'emotion/validation/trust.json', 'emotion/stat_methods.py',
    'emotion/aggregate_scores.py', 'emotion/audio_frames.py', 'emotion/voice_model.py',
    'docs/REPO_STATE.md', 'emotion_r2/round1_reuse_audit.json',
    'emotion_r2/voice_gate_validation.json', 'emotion_r2/v3_real_smoke.json',
    'emotion_r2/language_weighted_summary.json', 'emotion_r2/language_revision_audit.json',
    'emotion_r2/language.csv', 'emotion_r2/genius_word_recovery.json',
    'emotion_r2/genius_manual_save.csv', 'emotion_r2/label_provenance_audit.json',
    'emotion_r2/explanation_phase1.json', 'emotion_r2/compute_estimate.json',
    'emotion_r2/prepare_statistics_r2.py', 'emotion_r2/statistics_r2.py',
    'emotion_r2/hypotheses_r2.py', 'emotion_r2/explanations_r2.py',
    'emotion_r2/explanation_repair.py', 'emotion_r2/score_text_model.py',
    'emotion_r2/audio_scores_r2.py', 'emotion_r2/build_voice_gates.py',
    'emotion_r2/voicing.py', 'emotion_r2/registration.py',
    'emotion_r2/audit_round1_reuse.py', 'emotion_r2/language_weighted.py',
    'emotion_r2/label_provenance_audit.py', 'emotion_r2/genius_word_recovery.py',
    'emotion_r2/prepare_sections.py',
    'emotion_r2/verify_score_checkpoints.py', 'emotion_r2/build_review_bundle.py',
]
files = {ROOT/name for name in explicit}
for pattern in ('emotion_r2/validation/*.json', 'emotion_r2/sections_input/*.json',
                'emotion_r2/test_*.py'):
    files.update(ROOT.glob(pattern))
for role in ('T1', 'T3', 'L1', 'M3', 'V1', 'V3', 'voice_gate'):
    files.update((HERE/'scores'/role).glob('*.json'))
missing = [str(p) for p in files if not p.is_file()]
if missing:
    raise FileNotFoundError('Missing required bundle files: '+repr(missing))

forbidden = {'text', 'lyrics', 'lyric', 'plainlyrics', 'syncedlyrics', 'content'}
def check_keys(value, name):
    if isinstance(value, dict):
        for key, val in value.items():
            if key.casefold() in forbidden:
                raise ValueError('Potential lyric-bearing field in '+name+': '+key)
            check_keys(val, name)
    elif isinstance(value, list):
        for val in value:
            check_keys(val, name)

for path in sorted(files):
    relative = path.relative_to(ROOT).as_posix()
    if '/private/' in '/'+relative or 'model_cache' in relative:
        raise ValueError('Forbidden path: '+relative)
    if path.suffix == '.json' and (relative.startswith('emotion_r2/scores/') or
                                   relative.startswith('emotion_r2/sections_input/') or
                                   relative.startswith('emotion_r2/validation/') or
                                   relative == 'emotion_r2/genius_word_recovery.json'):
        check_keys(json.loads(path.read_text(encoding='utf-8-sig')), relative)
    if path.suffix == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as source:
            fields = next(csv.reader(source), [])
        if any(field.casefold() in forbidden for field in fields):
            raise ValueError('Potential lyric-bearing CSV column: '+relative)

manifest = []
with zipfile.ZipFile(DEST, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for path in sorted(files):
        name = path.relative_to(ROOT).as_posix()
        data = path.read_bytes()
        archive.writestr(name, data)
        manifest.append({'path':name, 'bytes':len(data), 'sha256':hashlib.sha256(data).hexdigest()})
    archive.writestr('BUNDLE_MANIFEST.json', json.dumps({'files':manifest}, indent=2)+'\n')
with zipfile.ZipFile(DEST) as archive:
    bad = archive.testzip()
    if bad:
        raise RuntimeError('Archive verification failed: '+bad)
digest = hashlib.sha256(DEST.read_bytes()).hexdigest()
(HERE/'TASK_F_PRELIMINARY_REVIEW.sha256').write_text(digest+'  '+DEST.name+'\n',encoding='utf8')
print(json.dumps({'archive':str(DEST), 'files':len(manifest), 'bytes':DEST.stat().st_size,
                  'sha256':digest, 'zip_integrity':'passed'}))
