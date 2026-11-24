"""Task I: local display-only exports. Never write registered inputs or scores."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import unicodedata

if sys.executable != '/home/usr/venvs/midi/bin/python':
    raise RuntimeError('Use /home/usr/venvs/midi/bin/python')
DISPLAY=Path(__file__).resolve().parent
R2=DISPLAY.parent
ROOT=R2.parent
os.environ.update(HF_HOME=str(DISPLAY/'cache/hf'), HF_HUB_OFFLINE='1',
    TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', TOKENIZERS_PARALLELISM='false',
    XDG_CACHE_HOME=str(DISPLAY/'cache/xdg'), NUMBA_CACHE_DIR=str(DISPLAY/'cache/numba'),
    TORCH_HOME=str(DISPLAY/'cache/torch'), CUDA_CACHE_PATH=str(DISPLAY/'cache/cuda'),
    TORCHINDUCTOR_CACHE_DIR=str(DISPLAY/'cache/torchinductor'),TRITON_CACHE_DIR=str(DISPLAY/'cache/triton'),
    TORCH_EXTENSIONS_DIR=str(DISPLAY/'cache/torch_extensions'),HF_MODULES_CACHE=str(DISPLAY/'cache/hf_modules'),
    TMPDIR=str(DISPLAY/'tmp'), PYTHONDONTWRITEBYTECODE='1')
for name in ('cache','tmp'):(DISPLAY/name).mkdir(parents=True,exist_ok=True)
sys.path[:0]=[str(R2),str(ROOT/'emotion/vendor')]
sys.path.append(str(R2/'vendor'))
from registration import verify_registration, verify_weights

TOLERANCE=1e-5  # User-specified reproduction requirement; no tuning.
SKIPPED={'reused_round1','missing_text','unsupported_or_uncertain_language'}
READING_LABEL="L1's interpretation of the lyrics"
READING_NOTICE='A model-generated interpretation, not verified songwriter intent or emotion, and not an explanation of how registered scores were computed.'

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))
def sha(data):return hashlib.sha256(data).hexdigest()
def file_sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def save(path,value):
    if not path.resolve().is_relative_to(DISPLAY.resolve()):raise RuntimeError('Write outside display')
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf8')
    temporary.replace(path)
def now():return datetime.now(timezone.utc).isoformat()
def progress(phase,**fields):
    record={'updated_utc':now(),'phase':phase,'display_only':True,**fields}
    save(DISPLAY/'progress.json',record)
    print(json.dumps(record),flush=True)
def fail(kind,**fields):
    save(DISPLAY/'FAILURE.json',{'kind':kind,'utc':now(),**fields})
    progress('stopped',reason=kind,**fields)
    raise RuntimeError(kind)
def sample():
    with (ROOT/'mvp_325/summary.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    result={r['track_id']:r for r in rows}
    if len(result)!=325 or len(rows)!=325:raise RuntimeError('Expected exactly 325 sample songs')
    if {p.stem for p in (R2/'sections_input').glob('*.json')}!=set(result):raise RuntimeError('Section scope mismatch')
    return result
def protected_files():
    paths=set()
    for folder in ('scores','validation','sections_input'):
        paths.update(p for p in (R2/folder).rglob('*') if p.is_file())
    for name in ('ANALYSIS_PLAN.md','models.json','models_amended.json','models_amended.sha256','l1_prompt.txt','r1_snapshot.json','REPORT.md'):
        paths.add(R2/name)
    paths.update([ROOT/'analysis_v2/v1_snapshot.json',ROOT/'mvp_325/snapshot.json',ROOT/'mvp_325/summary.csv'])
    return paths
def hash_paths(paths):
    def record(path):return path.relative_to(ROOT).as_posix(),{'size':path.stat().st_size,'sha256':file_sha(path)}
    with ThreadPoolExecutor(max_workers=12) as pool:return dict(pool.map(record,sorted(paths)))
def inputs(tid):
    path=R2/'sections_input'/f'{tid}.json';public_bytes=path.read_bytes();public=json.loads(public_bytes)
    priv_path=R2/'private'/path.name;private_bytes=priv_path.read_bytes() if priv_path.is_file() else b''
    private={r['section_id']:r for r in json.loads(private_bytes).get('sections',[])} if private_bytes else {}
    texts={}
    for row in public['sections']:
        content=(private.get(row['section_id']) or {}).get('text') or ''
        if content and sha(content.encode())!=row['text_sha256']:raise RuntimeError('Input text hash mismatch '+tid+'/'+row['section_id'])
        texts[row['section_id']]=content
    return public,texts,public_bytes,private_bytes
def registered(tid,role,reg,public_bytes,private_bytes):
    result=read(R2/'scores'/role/f'{tid}.json')
    old_prompt_sha=sha((R2/'l1_prompt.txt').read_text(encoding='utf8').encode())
    expected=sha(public_bytes+private_bytes+reg['amendment_commit'].encode()+role.encode()+old_prompt_sha.encode())
    if result['input_sha256']!=expected or result['manifest_sha256']!=reg['amended_manifest_sha256']:
        raise RuntimeError('Registered checkpoint input mismatch '+role+'/'+tid)
    return result
def identity(reg,role):
    item=next(m for m in reg['manifest']['models'] if m.get('role')==role)
    folder=ROOT/'emotion/model_cache'/item['id'].replace('/','--') if role=='T1' else R2/'model_cache'/role
    verify_weights(reg['manifest'],role,folder)
    return item,folder
def inventory():
    sample();reg=verify_registration()
    baseline=DISPLAY/'registered_files_start.json'
    if baseline.is_file():raise RuntimeError('Baseline already exists; preserve it and use existing checkpoints to resume')
    counts={r:Counter() for r in ('T1','T3','L1')};sets=Counter();private_hashes={}
    for tid in sorted(sample()):
        pub,texts,pb,qb=inputs(tid);private_hashes[tid]=sha(qb)
        for row in pub['sections']:sets[row['set']]+=1
        for role in counts:
            data=registered(tid,role,reg,pb,qb)
            if [s['section_id'] for s in data['sections']]!=[s['section_id'] for s in pub['sections']]:raise RuntimeError('Section order mismatch')
            for row in data['sections']:
                counts[role][row['status']]+=1
                if row['status']=='ok' and not texts[row['section_id']]:raise RuntimeError('Scored section without text')
    save(baseline,{'utc':now(),'files':hash_paths(protected_files()),'private_input_sha256':private_hashes})
    save(DISPLAY/'input_inventory.json',{'utc':now(),'songs':325,'section_sets':dict(sets),'registered_statuses':{k:dict(v) for k,v in counts.items()},
        'amendment_commit':reg['amendment_commit'],'manifest_sha256':reg['amended_manifest_sha256'],
        'probability_rule':'Score only registered status ok; retain all other registered statuses with null probability fields.',
        'valence_absolute_tolerance':TOLERANCE,'display_only':True})
    progress('inventory_complete',songs=325,registered_statuses={k:dict(v) for k,v in counts.items()})
def probabilities(role):
    reg=verify_registration();item,folder=identity(reg,role)
    import torch
    from transformers import AutoTokenizer,AutoModelForSequenceClassification
    if not torch.cuda.is_available():raise RuntimeError('CUDA unavailable; no silent fallback')
    torch.manual_seed(20261115)
    tokenizer=AutoTokenizer.from_pretrained(folder,local_files_only=True)
    model=AutoModelForSequenceClassification.from_pretrained(folder,local_files_only=True).to('cuda').eval()
    labels={v.lower():k for k,v in model.config.id2label.items()}
    if set(labels)!={'positive','neutral','negative'}:raise RuntimeError('Unexpected labels')
    totals=Counter();max_error=0.;started=time.monotonic()
    for n,tid in enumerate(sorted(sample()),1):
        public,texts,pb,qb=inputs(tid);old=registered(tid,role,reg,pb,qb)
        target=DISPLAY/'checkpoints'/role/f'{tid}.json'
        source_sha=file_sha(R2/'scores'/role/f'{tid}.json')
        if target.is_file():
            cached=read(target)
            if cached['registered_checkpoint_sha256']!=source_sha:raise RuntimeError('Display checkpoint source changed')
            rows=cached['sections']
        else:
            rows=[]
            for original in old['sections']:
                sid=original['section_id'];status=original['status']
                result={k:original.get(k) for k in ('section_id','set','language','text_sha256','status')}
                result.update(probabilities={'positive':None,'neutral':None,'negative':None},valence=None,registered_valence=None,
                    absolute_valence_error=None,reproduction_check='not_scored_registered_status',truncated=original.get('truncated'))
                if status=='ok':
                    content=texts[sid]
                    with torch.inference_mode():
                        encoded=tokenizer(content,truncation=True,max_length=512,return_tensors='pt').to('cuda')
                        probs=model(**encoded).logits.softmax(dim=-1)[0]
                    values={label:float(probs[i]) for label,i in labels.items()}
                    if not all(math.isfinite(p) and 0<=p<=1 for p in values.values()) or abs(sum(values.values())-1)>1e-5:fail('invalid_probabilities',role=role,track_id=tid,section_id=sid)
                    value=values['positive']-values['negative'];error=abs(value-original['valence'])
                    truncated=len(tokenizer.encode(content,add_special_tokens=True,truncation=False))>512
                    if truncated!=original['truncated']:fail('truncation_mismatch',role=role,track_id=tid,section_id=sid)
                    if error>TOLERANCE:fail('valence_reproduction_mismatch',role=role,track_id=tid,section_id=sid,registered_valence=original['valence'],reproduced_valence=value,absolute_error=error,tolerance=TOLERANCE)
                    result.update(probabilities=values,valence=value,registered_valence=original['valence'],absolute_valence_error=error,reproduction_check='passed')
                elif status not in SKIPPED:raise RuntimeError('Unknown registered classifier status '+status)
                rows.append(result)
            save(target,{'track_id':tid,'cohort':public['cohort'],'role':role,'model_id':item['id'],'revision':item['revision'],
                'registered_checkpoint_sha256':source_sha,'max_length':512,'truncation':True,'display_only':True,'sections':rows})
        for row in rows:
            totals[row['status']]+=1
            max_error=max(max_error,row['absolute_valence_error'] or 0.)
        if n%25==0 or n==325:progress('probabilities',role=role,songs_done=n,songs_total=325,status_counts=dict(totals),maximum_absolute_valence_error=max_error)
    save(DISPLAY/f'{role}_verification.json',{'role':role,'songs':325,'status_counts':dict(totals),'maximum_absolute_valence_error':max_error,
        'absolute_tolerance':TOLERANCE,'all_scored_sections_pass':True,'seconds':time.monotonic()-started,'model_id':item['id'],'revision':item['revision']})
def combine():
    for tid in sorted(sample()):
        roles={r:read(DISPLAY/'checkpoints'/r/f'{tid}.json') for r in ('T1','T3')}
        if [x['section_id'] for x in roles['T1']['sections']]!=[x['section_id'] for x in roles['T3']['sections']]:raise RuntimeError('Display section order mismatch')
        rows=[]
        for a,b in zip(roles['T1']['sections'],roles['T3']['sections']):
            rows.append({'section_id':a['section_id'],'set':a['set'],'language':a['language'],'text_sha256':a['text_sha256'],
                'T1':{k:v for k,v in a.items() if k not in ('section_id','set','language','text_sha256')},
                'T3':{k:v for k,v in b.items() if k not in ('section_id','set','language','text_sha256')}})
        save(DISPLAY/'text_model_probs'/f'{tid}.json',{'schema_version':1,'track_id':tid,'cohort':roles['T1']['cohort'],'display_only':True,
            'registered_scores_unchanged':True,'models':{r:{k:roles[r][k] for k in ('model_id','revision','registered_checkpoint_sha256')} for r in roles},
            'verification_absolute_tolerance':TOLERANCE,'sections':rows})
    progress('probability_export_complete',songs=325)

def word_tokens(s):return re.findall(r'[^\W_]+',unicodedata.normalize('NFKC',s).casefold(),re.UNICODE)
def cjk_chars(s):
    def script_character(c):
        in_range=('\u3040'<=c<='\u30ff' or '\u3400'<=c<='\u9fff' or '\uac00'<=c<='\ud7af'
                  or '\u1100'<=c<='\u11ff' or '\uf900'<=c<='\ufaff' or 0x20000<=ord(c)<=0x2FA1F)
        return in_range and unicodedata.category(c)[0] in ('L','M')
    return ''.join(c for c in unicodedata.normalize('NFKC',s).casefold() if script_character(c))
def overlapping(reading,source):
    a,b=word_tokens(reading),word_tokens(source)
    runs={tuple(b[i:i+4]) for i in range(len(b)-3)}
    words=any(tuple(a[i:i+4]) in runs for i in range(len(a)-3))
    a,b=cjk_chars(reading),cjk_chars(source)
    chars={b[i:i+4] for i in range(len(b)-3)}
    return {'four_word_overlap':words,'four_cjk_character_overlap':any(a[i:i+4] in chars for i in range(len(a)-3))}
def readings():
    if not all(read(DISPLAY/f'{r}_verification.json')['all_scored_sections_pass'] for r in ('T1','T3')):raise RuntimeError('Probability reproduction must pass before L1')
    reg=verify_registration();item,folder=identity(reg,'L1')
    import torch
    from transformers import AutoTokenizer,AutoModelForCausalLM
    if not torch.cuda.is_available():raise RuntimeError('CUDA unavailable; no silent fallback')
    torch.manual_seed(20261115)
    tokenizer=AutoTokenizer.from_pretrained(folder,local_files_only=True)
    model=AutoModelForCausalLM.from_pretrained(folder,local_files_only=True,torch_dtype=torch.float16,device_map='cuda').eval()
    prompt=(DISPLAY/'l1_reading_prompt.txt').read_text(encoding='utf8');prompt_sha=sha(prompt.encode())
    status_counts=Counter();generated=0;reused=0;started=time.monotonic()
    for n,tid in enumerate(sorted(sample()),1):
        public,texts,pb,qb=inputs(tid);old=registered(tid,'L1',reg,pb,qb)
        oldmap={s['section_id']:s for s in old['sections']};rows=[]
        target=DISPLAY/'l1_readings'/f'{tid}.json'
        if target.is_file():
            data=read(target)
            if data['prompt_sha256']!=prompt_sha or data['registered_checkpoint_sha256']!=file_sha(R2/'scores/L1'/f'{tid}.json'):raise RuntimeError('Reading checkpoint provenance changed')
            rows=data['sections']
        else:
            for row in public['sections']:
                sid=row['section_id'];content=texts[sid]
                result={k:row.get(k) for k in ('section_id','set','language','text_sha256')}
                result.update(registered_status=oldmap[sid]['status'],status='missing_text',reading=None,reading_label=READING_LABEL,
                    interpretation_notice=READING_NOTICE,overlap_flags=None,generation_source=None)
                if content:
                    key=sha(prompt.encode()+content.encode()+item['revision'].encode())
                    cache_path=DISPLAY/'checkpoints/L1_reading'/f'{key}.json'
                    if cache_path.is_file():
                        generated_result=read(cache_path);result['generation_source']='identical_input_cache';reused+=1
                    else:
                        ids=tokenizer.apply_chat_template([{'role':'user','content':prompt+'\n\nPASSAGE:\n'+content}],tokenize=True,add_generation_prompt=True,return_tensors='pt').to('cuda')
                        with torch.inference_mode():
                            output=model.generate(ids,attention_mask=torch.ones_like(ids),max_new_tokens=128,do_sample=False,num_beams=1,pad_token_id=tokenizer.eos_token_id)
                        tokens=output[0,ids.shape[1]:];reply=tokenizer.decode(tokens,skip_special_tokens=True).strip()
                        flags=overlapping(reply,content)
                        # Unscanned text is never written, even to logs or checkpoints.
                        status='overlap_blocked' if any(flags.values()) else 'empty_response' if not reply else 'ok'
                        if status=='ok' and len(tokens)==128 and int(tokens[-1]) not in (tokenizer.eos_token_id,model.config.eos_token_id):status='generation_limit'
                        generated_result={'reading':reply if status=='ok' else None,'status':status,'overlap_flags':flags,'raw_output_sha256':sha(reply.encode()),
                            'prompt_sha256':prompt_sha,'input_key':key,'generated_tokens':len(tokens)}
                        save(cache_path,generated_result);result['generation_source']='local_greedy';generated+=1
                    result.update({k:generated_result[k] for k in ('reading','status','overlap_flags','raw_output_sha256')})
                    if result['reading'] and any(overlapping(result['reading'],content).values()):raise RuntimeError('Cached reading overlap')
                rows.append(result)
            save(target,{'schema_version':1,'track_id':tid,'cohort':public['cohort'],'display_only':True,'registered_scores_unchanged':True,
                'model_id':item['id'],'revision':item['revision'],'prompt_sha256':prompt_sha,'registered_checkpoint_sha256':file_sha(R2/'scores/L1'/f'{tid}.json'),
                'reading_label':READING_LABEL,'interpretation_notice':READING_NOTICE,'reading_type':'model_interpretation',
                'decoding':{'do_sample':False,'num_beams':1,'max_new_tokens':128,'torch_dtype':'float16','seed':20261115},
                'overlap_rule':'NFKC/casefold 4-word ngrams; additionally 4 consecutive ja/ko/zh script characters with spacing/punctuation removed; block if either overlaps.',
                'sections':rows})
        for row in rows:status_counts[row['status']]+=1
        if n%10==0 or n==325:progress('l1_readings',songs_done=n,songs_total=325,status_counts=dict(status_counts),unique_generations_this_run=generated,identical_input_reuses_this_run=reused)
    save(DISPLAY/'L1_reading_summary.json',{'songs':325,'status_counts':dict(status_counts),'prompt_sha256':prompt_sha,'model_id':item['id'],'revision':item['revision'],
        'unique_generations_this_run':generated,'identical_input_reuses_this_run':reused,'seconds_this_run':time.monotonic()-started,
        'overlap_blocked_sections':status_counts['overlap_blocked'],'all_published_readings_overlap_free':True,'display_only':True,
        'reading_label':READING_LABEL,'interpretation_notice':READING_NOTICE,
        'limitation':'Overlap scan detects verbatim reuse, not semantic translation or theme accuracy. No human validation of readings is claimed.'})
    progress('readings_complete',songs=325,status_counts=dict(status_counts))

def integrity():
    baseline=read(DISPLAY/'registered_files_start.json');current=hash_paths(protected_files())
    changed=[name for name,rec in baseline['files'].items() if current.get(name)!=rec]
    extras=sorted(set(current)-set(baseline['files']))
    private_changed=[]
    for tid,expected in baseline['private_input_sha256'].items():
        path=R2/'private'/f'{tid}.json'
        if sha(path.read_bytes() if path.is_file() else b'')!=expected:private_changed.append(tid)
    result={'utc':now(),'registered_files':{'unchanged':len(baseline['files'])-len(changed),'total':len(baseline['files']),'changed_or_missing':changed,'extra':extras},'private_input_changes':private_changed}
    def verify(snapshot,base,round1=False,extra_check=False):
        records=read(snapshot)['files']
        def check(pair):
            name,rec=pair;p=base/name
            if not p.is_file():return 'missing',name
            if p.stat().st_size!=rec['size'] or file_sha(p)!=rec['sha256']:return 'changed',name
            return 'ok',name
        with ThreadPoolExecutor(max_workers=16) as pool:outcomes=list(pool.map(check,records.items()))
        changed=[n for status,n in outcomes if status=='changed'];missing=[n for status,n in outcomes if status=='missing'];extra=[]
        if round1 or extra_check:
            for p in base.rglob('*'):
                if not p.is_file() or p==snapshot:continue
                rel=p.relative_to(base)
                if round1 and rel.parts[0] in {'private','model_cache','stems'}:continue
                if rel.as_posix() not in records:extra.append(rel.as_posix())
        return {'total':len(records),'unchanged':len(records)-len(changed)-len(missing),'changed':changed,'missing':missing,'extra':sorted(extra),'passed':not(changed or missing or extra)}
    for name,snapshot,base,r1,extra in [('round1',R2/'r1_snapshot.json',ROOT/'emotion',True,False),('v1',ROOT/'analysis_v2/v1_snapshot.json',ROOT,False,False),('mvp_325',ROOT/'mvp_325/snapshot.json',ROOT/'mvp_325',False,True)]:
        result[name]=verify(snapshot,base,r1,extra);save(DISPLAY/'integrity_end.json',result)
        progress('snapshot_verification',snapshot=name,unchanged=result[name]['unchanged'],total=result[name]['total'],passed=result[name]['passed'])
    result['passed']=not(changed or extras or private_changed) and all(result[n]['passed'] for n in ('round1','v1','mvp_325'))
    save(DISPLAY/'integrity_end.json',result)
    progress('complete' if result['passed'] else 'integrity_failed',passed=result['passed'])
    if not result['passed']:raise RuntimeError('Integrity check failed; inspect display/integrity_end.json')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['inventory','T1','T3','combine','L1','integrity']);args=parser.parse_args()
    if args.action=='inventory':inventory()
    elif args.action in ('T1','T3'):probabilities(args.action)
    elif args.action=='combine':combine()
    elif args.action=='L1':readings()
    else:integrity()
if __name__=='__main__':main()
