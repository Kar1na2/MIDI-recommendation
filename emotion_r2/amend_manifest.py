"""Produce a transparent amendment without changing the historical manifest."""
import hashlib, json, sys
from pathlib import Path
if sys.executable != '/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
here=Path(__file__).resolve().parent
original=here/'models.json'
old_hash=hashlib.sha256(original.read_bytes()).hexdigest()
if old_hash!='bab8f1a06d8232a62a0e01bd61ea0991725a713d402498b2da53043615d52fae': raise RuntimeError('Original manifest mismatch')
manifest=json.loads(original.read_text(encoding='utf-8'))
manifest['amendment']='Amendment 1; original manifest preserved unchanged'
manifest['original_manifest_sha256']=old_hash
for item in manifest['models']:
    if item.get('role')=='T3':
        item['family']='mmBERT-base, supervised three-class sentiment student'
        item['related_family']='L1: training labels generated using a Qwen teacher; L1 is Qwen'
    elif item.get('role')=='L1': item['related_family']='T3: T3 labels generated using a Qwen teacher'
    elif item.get('role')=='M3':
        item['possible_shared_training_data']=True
        item['distinct_from_M1']='Different backbone; overlap in MediaEval/DEAM training tracks not established.'
        item['license']='Conflicting: prior manifest CC-BY-NC-4.0; model card Apache-2.0; underlying permissions unresolved'
        item['license_conflict']=True
        item['noncommercial']=True
        item['license_evidence']=['emotion_r2/models.json','https://huggingface.co/huyleit/mert-v1-95m-music-emotion-int8']
dest=here/'models_amended.json'
dest.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
digest=hashlib.sha256(dest.read_bytes()).hexdigest()
(here/'models_amended.sha256').write_text(digest+'  models_amended.json\n',encoding='utf8')
print('amended_manifest_sha256='+digest)
