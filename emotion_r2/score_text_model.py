"""Score one registered local text model at a time, with per-song checkpoints."""
import argparse,csv,hashlib,json,os,sys,time
from pathlib import Path
if sys.executable!='/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent
os.environ.update(HF_HOME=str(HERE/'model_cache/hf'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',TOKENIZERS_PARALLELISM='false')
os.environ.setdefault('XDG_CACHE_HOME',str(HERE/'scratch/xdg'));os.environ.setdefault('NUMBA_CACHE_DIR',str(HERE/'scratch/numba'))
sys.path.insert(0,str(ROOT/'emotion/vendor'));sys.path.append(str(HERE/'vendor'))
from registration import verify_registration,verify_weights
def read(p): return json.loads(p.read_text(encoding='utf-8-sig')) if p.is_file() else {}
def sha(data): return hashlib.sha256(data).hexdigest()
def strict_reply(reply):
    try: data=json.loads(reply.strip())
    except ValueError: return None
    if not isinstance(data,dict) or not isinstance(data.get('uncertain'),bool): return None
    a,b=data.get('valence'),data.get('arousal')
    if isinstance(a,bool) or isinstance(b,bool) or not isinstance(a,(int,float)) or not isinstance(b,(int,float)) or not(-1<=a<=1 and -1<=b<=1): return None
    return {'valence':float(a),'arousal':float(b),'uncertain':data['uncertain']}
def main():
    parser=argparse.ArgumentParser();parser.add_argument('role',choices=['T1','T3','L1']);parser.add_argument('--song',action='append');args=parser.parse_args()
    reg=verify_registration(); manifest=reg['manifest']; role=args.role
    with (ROOT/'mvp_325/summary.csv').open(encoding='utf-8-sig',newline='') as f: sample={x['track_id']:x for x in csv.DictReader(f)}
    paths=sorted((HERE/'sections_input').glob('*.json'))
    if {p.stem for p in paths}!=set(sample): raise RuntimeError('Section inputs do not match exactly the 325-song sample')
    if args.song:
        wanted=set(args.song);paths=[p for p in paths if p.stem in wanted]
        if {p.stem for p in paths}!=wanted: raise RuntimeError('Song outside sample')
    item=next(m for m in manifest['models'] if m.get('role')==role)
    modelpath=(ROOT/'emotion/model_cache'/item['id'].replace('/','--')) if role=='T1' else HERE/'model_cache'/role
    verify_weights(manifest,role,modelpath)
    import torch
    from transformers import AutoTokenizer,AutoModelForSequenceClassification,AutoModelForCausalLM
    if not torch.cuda.is_available(): raise RuntimeError('CUDA unavailable')
    torch.manual_seed(20261115);tokenizer=AutoTokenizer.from_pretrained(modelpath,local_files_only=True)
    if role=='L1': model=AutoModelForCausalLM.from_pretrained(modelpath,local_files_only=True,torch_dtype=torch.float16,device_map='cuda').eval()
    else: model=AutoModelForSequenceClassification.from_pretrained(modelpath,local_files_only=True).to('cuda').eval()
    labels={v.lower():k for k,v in model.config.id2label.items()} if role!='L1' else {}
    prompt=(HERE/'l1_prompt.txt').read_text(encoding='utf8');prompt_sha=sha(prompt.encode())
    if prompt_sha!='46f33c7d62256022323d6de55f3f821a8a955af72ea94ef0c6b711bd7fff4571': raise RuntimeError('L1 prompt changed')
    dest=HERE/'scores'/role;dest.mkdir(parents=True,exist_ok=True)
    for path in paths:
        started=time.monotonic();tid=path.stem;public=read(path);private_path=HERE/'private'/path.name
        private_bytes=private_path.read_bytes() if private_path.is_file() else b''
        fingerprint=sha(path.read_bytes()+private_bytes+reg['amendment_commit'].encode()+role.encode()+prompt_sha.encode())
        target=dest/path.name;existing=read(target)
        if existing.get('input_sha256')==fingerprint: print(json.dumps({'role':role,'track_id':tid,'status':'cached'}),flush=True);continue
        private={x['section_id']:x for x in json.loads(private_bytes).get('sections',[])} if private_bytes else {}
        oldpath=ROOT/'emotion/scratch/text'/path.name;old=read(oldpath);oldmap={x['section_id']:x for x in old.get('sections',[])}
        if role=='T1' and old:
            old_public=ROOT/'emotion/validation/input_sections'/path.name;old_private=ROOT/'emotion/private'/path.name
            if not old_public.is_file() or not old_private.is_file() or sha(old_public.read_bytes()+old_private.read_bytes())!=old.get('input_sha256'):
                raise RuntimeError('Round-one T1 input hash failed for '+tid)
        result=[]
        for row in public.get('sections',[]):
            content=(private.get(row['section_id']) or {}).get('text') or ''
            rec={'section_id':row['section_id'],'set':row.get('set'),'language':row.get('language'),'text_sha256':row.get('text_sha256'),'status':'missing_text'}
            if not content: result.append(rec);continue
            if sha(content.encode('utf8'))!=row.get('text_sha256'): raise RuntimeError('Private text hash mismatch '+tid+'/'+row['section_id'])
            previous=oldmap.get(row['section_id'],{})
            if role=='T1' and previous.get('text_sha256')==row.get('text_sha256') and previous.get('language')==row.get('language'):
                rec.update(status='reused_round1',valence=previous.get('T1'),truncated=previous.get('T1_truncated'));result.append(rec);continue
            if role=='T1' and row.get('language') not in item['languages']:
                rec.update(status='unsupported_or_uncertain_language',valence=None);result.append(rec);continue
            with torch.inference_mode():
                if role=='L1':
                    ids=tokenizer.apply_chat_template([{'role':'user','content':prompt+'\n'+content}],tokenize=True,add_generation_prompt=True,return_tensors='pt').to('cuda')
                    gen=model.generate(ids,attention_mask=torch.ones_like(ids),max_new_tokens=64,do_sample=False,pad_token_id=tokenizer.eos_token_id)
                    parsed=strict_reply(tokenizer.decode(gen[0,ids.shape[1]:],skip_special_tokens=True))
                    rec.update(parsed or {},status='ok' if parsed else 'unparseable_response')
                else:
                    encoded=tokenizer(content,truncation=True,max_length=512,return_tensors='pt').to('cuda')
                    probabilities=model(**encoded).logits.softmax(dim=-1)[0]
                    rec.update(valence=float(probabilities[labels['positive']]-probabilities[labels['negative']]),status='ok',truncated=len(tokenizer.encode(content,add_special_tokens=True,truncation=False))>512)
            result.append(rec)
        payload={'track_id':tid,'cohort':public['cohort'],'role':role,'amendment_commit':reg['amendment_commit'],'manifest_sha256':reg['amended_manifest_sha256'],'prompt_sha256':prompt_sha if role=='L1' else None,'input_sha256':fingerprint,'seconds':time.monotonic()-started,'sections':result}
        tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(payload,allow_nan=False)+'\n',encoding='utf8');tmp.replace(target)
        print(json.dumps({'role':role,'track_id':tid,'cohort':public['cohort'],'sections':len(result),'seconds':payload['seconds']}),flush=True)
if __name__=='__main__': main()
