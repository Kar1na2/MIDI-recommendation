"""Offline runtime checks on synthetic inputs; no corpus scoring or statistics."""
import argparse
import gc
import json
import os
import subprocess
import sys
import time
from pathlib import Path

if sys.executable != '/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
os.environ['HF_HOME']=str(HERE/'model_cache/hf')
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
os.environ['TOKENIZERS_PARALLELISM']='false'
os.environ['XDG_CACHE_HOME']=str(HERE/'scratch/xdg')
os.environ['TORCHINDUCTOR_CACHE_DIR']=str(HERE/'scratch/torchinductor')
os.environ['TRITON_CACHE_DIR']=str(HERE/'scratch/triton')
os.environ['NUMBA_CACHE_DIR']=str(HERE/'scratch/numba')
sys.path.insert(0,str(ROOT/'emotion/vendor'))
sys.path.append(str(HERE/'vendor'))

def gpu_idle():
    result=subprocess.run(['nvidia-smi','--query-compute-apps=pid','--format=csv,noheader'],capture_output=True,text=True,check=True)
    return not result.stdout.strip()

def run(role):
    import numpy as np
    if role=='M3':
        import onnxruntime as ort
        session=ort.InferenceSession(str(HERE/'model_cache/M3/mert_emotion_int8.onnx'),providers=['CPUExecutionProvider'])
        begin=time.monotonic()
        result=session.run(None,{'audio_waveform':np.zeros((1,96000),dtype=np.float32)})[0]
        inference_seconds=time.monotonic()-begin
        if result.shape!=(1,2) or not np.isfinite(result).all(): raise ValueError('Unexpected M3 output')
        return {'device':'cpu','synthetic_seconds':4,'result_shape':list(result.shape),'inference_seconds':inference_seconds}
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, AutoModelForCausalLM, AutoProcessor, AutoModelForAudioClassification
    if not gpu_idle(): raise RuntimeError('Another GPU process is active; queue support is required')
    if not torch.cuda.is_available(): raise RuntimeError('CUDA unavailable')
    path=HERE/'model_cache'/role
    with torch.inference_mode():
        if role=='T3':
            tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True)
            model=AutoModelForSequenceClassification.from_pretrained(path,local_files_only=True).to('cuda').eval()
            begin=time.monotonic()
            output=model(**{k:v.to('cuda') for k,v in tokenizer('This is a test.',return_tensors='pt').items()}).logits
            torch.cuda.synchronize(); inference_seconds=time.monotonic()-begin
            if output.shape[-1]!=3 or not torch.isfinite(output).all(): raise ValueError('Unexpected T3 output')
            result={'device':'cuda','result_shape':list(output.shape),'inference_seconds':inference_seconds}
        elif role=='L1':
            tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True)
            model=AutoModelForCausalLM.from_pretrained(path,local_files_only=True,torch_dtype=torch.float16).to('cuda').eval()
            messages=[{'role':'user','content':'Return a JSON object with valence, arousal, and uncertain for the text: This is a test.'}]
            batch=tokenizer.apply_chat_template(messages,tokenize=True,add_generation_prompt=True,return_tensors='pt').to('cuda')
            begin=time.monotonic()
            output=model.generate(batch,max_new_tokens=80,do_sample=False,pad_token_id=tokenizer.eos_token_id)
            torch.cuda.synchronize(); inference_seconds=time.monotonic()-begin
            reply=tokenizer.decode(output[0,batch.shape[1]:],skip_special_tokens=True).strip()
            try: parsed=json.loads(reply); valid=isinstance(parsed,dict) and all(x in parsed for x in ('valence','arousal','uncertain'))
            except (ValueError,TypeError): valid=False
            result={'device':'cuda','generated_tokens':int(output.shape[1]-batch.shape[1]),'json_shape_valid':valid,'inference_seconds':inference_seconds}
        elif role=='V3':
            processor=AutoProcessor.from_pretrained(path,local_files_only=True)
            model=AutoModelForAudioClassification.from_pretrained(path,trust_remote_code=True,local_files_only=True).to('cuda').eval()
            inputs=processor(np.zeros(64000,dtype=np.float32),sampling_rate=16000,return_tensors='pt',return_attention_mask=True)
            begin=time.monotonic()
            output=model(**{k:v.to('cuda') for k,v in inputs.items() if k in ('input_features','attention_mask')})
            torch.cuda.synchronize(); inference_seconds=time.monotonic()-begin
            dims=output['dims']
            if dims.shape[-1]!=3 or not torch.isfinite(dims).all(): raise ValueError('Unexpected V3 output')
            result={'device':'cuda','synthetic_seconds':4,'result_shape':list(dims.shape),'inference_seconds':inference_seconds}
        else: raise ValueError(role)
    del model
    gc.collect(); torch.cuda.empty_cache()
    return result

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('role',choices=['T3','L1','M3','V3']); args=parser.parse_args()
    try:
        result={'role':args.role,'status':'passed','details':run(args.role),'input':'synthetic, no song data'}
    except Exception as error:
        result={'role':args.role,'status':'failed','error':type(error).__name__+': '+str(error),'input':'synthetic, no song data'}
    (HERE/f'smoke_{args.role}.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2),flush=True)
    if result['status']!='passed': raise SystemExit(1)

if __name__=='__main__': main()
