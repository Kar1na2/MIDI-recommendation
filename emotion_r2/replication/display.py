"""Task I display extras for new IDs only; registered scores remain read-only."""
import argparse
from collections import Counter
import hashlib
import math
import time
from .audit import ROOT,R2,DEST,read,save,sha
from .controls import pre_scoring
from .worker import selected,inputs,model_path,cached,emit

DISPLAY=R2/'display'

def safe_target(folder,tid):
    from .audit import rows
    if tid in {s['track_id'] for s in rows(ROOT/'mvp_325/summary.csv')}:raise RuntimeError('Protected development display ID')
    return DISPLAY/folder/f'{tid}.json'

def probabilities(role):
    from registration import verify_registration
    reg=verify_registration();item,path=model_path(reg,role)
    import torch
    from transformers import AutoTokenizer,AutoModelForSequenceClassification
    if not torch.cuda.is_available():raise RuntimeError('CUDA unavailable')
    torch.manual_seed(20261115)
    tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True)
    model=AutoModelForSequenceClassification.from_pretrained(path,local_files_only=True).to('cuda').eval()
    labels={v.lower():k for k,v in model.config.id2label.items()}
    if set(labels)!={'positive','neutral','negative'}:raise RuntimeError('Unexpected classifier labels')
    maximum=0.;counts=Counter()
    for song in selected():
        tid=song['track_id'];public,texts,_,_=inputs(tid);source=DEST/'scores'/role/f'{tid}.json'
        checkpoint=read(source);target=DEST/'display_checkpoints'/role/f'{tid}.json'
        if target.exists():
            data=read(target)
            if data['registered_checkpoint_sha256']!=sha(source):raise RuntimeError('Registered display source changed')
        else:
            result=[]
            for original in checkpoint['sections']:
                rec={k:original.get(k) for k in ('section_id','set','language','text_sha256','status')}
                rec.update(probabilities={'positive':None,'neutral':None,'negative':None},valence=None,registered_valence=None,
                    absolute_valence_error=None,reproduction_check='not_scored_registered_status',truncated=original.get('truncated'))
                if original['status']=='ok':
                    content=texts[original['section_id']]
                    with torch.inference_mode():
                        encoded=tokenizer(content,truncation=True,max_length=512,return_tensors='pt').to('cuda')
                        probs=model(**encoded).logits.softmax(dim=-1)[0]
                    values={label:float(probs[i]) for label,i in labels.items()}
                    if not all(math.isfinite(p) and 0<=p<=1 for p in values.values()) or abs(sum(values.values())-1)>1e-5:raise RuntimeError('Invalid display probabilities')
                    value=values['positive']-values['negative'];error=abs(value-original['valence'])
                    truncated=len(tokenizer.encode(content,add_special_tokens=True,truncation=False))>512
                    if truncated!=original['truncated'] or error>1e-5:
                        save(DEST/'display_FAILURE.json',{'role':role,'track_id':tid,'section_id':original['section_id'],'error':error,'tolerance':1e-5})
                        raise RuntimeError('Display reproduction mismatch; no further scoring')
                    rec.update(probabilities=values,valence=value,registered_valence=original['valence'],absolute_valence_error=error,reproduction_check='passed')
                elif original['status'] not in ('reused_round1','missing_text','unsupported_or_uncertain_language'):raise RuntimeError('Unknown classifier status')
                result.append(rec)
            data={'track_id':tid,'cohort':public['cohort'],'role':role,'model_id':item['id'],'revision':item['revision'],
                  'registered_checkpoint_sha256':sha(source),'max_length':512,'truncation':True,'display_only':True,'sections':result}
            save(target,data)
        for sec in data['sections']:
            counts[sec['status']]+=1;maximum=max(maximum,sec['absolute_valence_error'] or 0.)
        emit('display_'+role,tid,sections=len(data['sections']))
    save(DEST/('display_'+role+'_verification.json'),{'all_scored_sections_pass':True,'maximum_absolute_valence_error':maximum,'absolute_tolerance':1e-5,'status_counts':dict(counts)})

def combine():
    for song in selected():
        tid=song['track_id'];roles={r:read(DEST/'display_checkpoints'/r/f'{tid}.json') for r in ('T1','T3')}
        if [s['section_id'] for s in roles['T1']['sections']]!=[s['section_id'] for s in roles['T3']['sections']]:raise RuntimeError('Display order mismatch')
        result=[]
        for a,b in zip(roles['T1']['sections'],roles['T3']['sections']):
            common=('section_id','set','language','text_sha256')
            result.append({**{k:a[k] for k in common},'T1':{k:v for k,v in a.items() if k not in common},'T3':{k:v for k,v in b.items() if k not in common}})
        payload={'schema_version':1,'track_id':tid,'cohort':song['cohort'],'display_only':True,'registered_scores_unchanged':True,
                 'models':{r:{k:roles[r][k] for k in ('model_id','revision','registered_checkpoint_sha256')} for r in roles},
                 'verification_absolute_tolerance':1e-5,'sections':result}
        target=safe_target('text_model_probs',tid)
        if target.exists():
            if read(target)!=payload:raise RuntimeError('Existing new-song display mismatch')
        else:save(target,payload)

def readings():
    from registration import verify_registration
    from emotion_r2.display.export_readings import overlapping,READING_LABEL,READING_NOTICE
    if not all(read(DEST/('display_'+r+'_verification.json'))['all_scored_sections_pass'] for r in ('T1','T3')):raise RuntimeError('Probability reproduction incomplete')
    reg=verify_registration();item,path=model_path(reg,'L1')
    import torch
    from transformers import AutoTokenizer,AutoModelForCausalLM
    torch.manual_seed(20261115)
    if not torch.cuda.is_available():raise RuntimeError('CUDA unavailable')
    tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True)
    model=AutoModelForCausalLM.from_pretrained(path,local_files_only=True,torch_dtype=torch.float16,device_map='cuda').eval()
    prompt=(DISPLAY/'l1_reading_prompt.txt').read_text(encoding='utf8');ph=hashlib.sha256(prompt.encode()).hexdigest()
    if ph!='ffdf21672c16d12394441664976928ac03a51b35d721d3925cca9be8adc58e3d':raise RuntimeError('Display prompt changed')
    counts=Counter();start=time.monotonic()
    for song in selected():
        tid=song['track_id'];public,texts,_,_=inputs(tid);source=DEST/'scores/L1'/f'{tid}.json';old={s['section_id']:s for s in read(source)['sections']}
        target=safe_target('l1_readings',tid)
        if target.exists():
            data=read(target)
            if data['prompt_sha256']!=ph or data['registered_checkpoint_sha256']!=sha(source):raise RuntimeError('Display reading provenance mismatch')
        else:
            results=[]
            for sec in public['sections']:
                sid=sec['section_id'];content=texts.get(sid,'')
                result={k:sec.get(k) for k in ('section_id','set','language','text_sha256')}
                result.update(registered_status=old[sid]['status'],status='missing_text',reading=None,reading_label=READING_LABEL,
                              interpretation_notice=READING_NOTICE,overlap_flags=None,generation_source=None)
                if content:
                    key=hashlib.sha256(prompt.encode()+content.encode()+item['revision'].encode()).hexdigest()
                    cp=DEST/'display_checkpoints/L1_reading'/f'{key}.json'
                    if cp.exists():generated=read(cp);result['generation_source']='identical_input_cache'
                    else:
                        ids=tokenizer.apply_chat_template([{'role':'user','content':prompt+'\n\nPASSAGE:\n'+content}],tokenize=True,add_generation_prompt=True,return_tensors='pt').to('cuda')
                        with torch.inference_mode():output=model.generate(ids,attention_mask=torch.ones_like(ids),max_new_tokens=128,do_sample=False,num_beams=1,pad_token_id=tokenizer.eos_token_id)
                        tokens=output[0,ids.shape[1]:];reply=tokenizer.decode(tokens,skip_special_tokens=True).strip();flags=overlapping(reply,content)
                        status='overlap_blocked' if any(flags.values()) else 'empty_response' if not reply else 'ok'
                        if status=='ok' and len(tokens)==128 and int(tokens[-1]) not in (tokenizer.eos_token_id,model.config.eos_token_id):status='generation_limit'
                        generated={'reading':reply if status=='ok' else None,'status':status,'overlap_flags':flags,
                                   'raw_output_sha256':hashlib.sha256(reply.encode()).hexdigest(),'prompt_sha256':ph,'input_key':key,'generated_tokens':len(tokens)}
                        save(cp,generated);result['generation_source']='local_greedy'
                    result.update({k:generated[k] for k in ('reading','status','overlap_flags','raw_output_sha256')})
                    if result['reading'] and any(overlapping(result['reading'],content).values()):raise RuntimeError('Cached reading overlap')
                results.append(result)
            data={'schema_version':1,'track_id':tid,'cohort':public['cohort'],'display_only':True,'registered_scores_unchanged':True,
                  'model_id':item['id'],'revision':item['revision'],'prompt_sha256':ph,'registered_checkpoint_sha256':sha(source),
                  'reading_label':READING_LABEL,'interpretation_notice':READING_NOTICE,'reading_type':'model_interpretation',
                  'decoding':{'do_sample':False,'num_beams':1,'max_new_tokens':128,'torch_dtype':'float16','seed':20261115},
                  'overlap_rule':'NFKC/casefold 4-word ngrams; additionally 4 consecutive ja/ko/zh script characters with spacing/punctuation removed; block if either overlaps.',
                  'sections':results}
            save(target,data)
        counts.update(s['status'] for s in data['sections']);emit('display_L1',tid,sections=len(data['sections']))
    save(DEST/'display_L1_summary.json',{'status_counts':dict(counts),'seconds':time.monotonic()-start,'prompt_sha256':ph})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['T1','T3','combine','L1']);a=p.parse_args();pre_scoring()
    if a.action in ('T1','T3'):probabilities(a.action)
    elif a.action=='combine':combine()
    else:readings()
