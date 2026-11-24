"""Download public candidate weights and manifests only; no lyrics or audio are sent."""
import concurrent.futures
import json
import os
import sys
from pathlib import Path

if sys.executable != '/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
HERE=Path(__file__).resolve().parent
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
os.environ['HF_HOME']=str(HERE/'model_cache/hf')
from huggingface_hub import snapshot_download

CANDIDATES={
    'T3':('Horizon-Labs/multilingual-sentiment-base','fede3aec59df484af8f39535558e582c72039b63',
          ['README.md','LICENSE','NOTICE','config.json','model.safetensors','tokenizer.json','tokenizer_config.json','special_tokens_map.json']),
    'L1':('Qwen/Qwen3-4B-Instruct-2507','cdbee75f17c01a7cc42f958dc650907174af0554',
          ['README.md','LICENSE','config.json','generation_config.json','model*.safetensors','model.safetensors.index.json','tokenizer.json','tokenizer_config.json','vocab.json','merges.txt']),
    'M3':('huyleit/mert-v1-95m-music-emotion-int8','080701a30fa9021ccacd2a7bddb9d61f58c48ead',
          ['README.md','config.json','mert_emotion_int8.onnx']),
    'V3':('MERaLiON/MERaLiON-SER-v1','7e3ee6fa4534dea8316e8ca43e377e2fbb58496b',
          ['README.md','MERaLiON-Public-Licence-SER-V1.pdf','*.py','config.json','preprocessor_config.json',
           'tokenizer_config.json','special_tokens_map.json','added_tokens.json','vocab.json','merges.txt','normalizer.json','model.safetensors']),
}

def download(item):
    role,(repo,revision,patterns)=item
    dest=HERE/'model_cache'/role
    try:
        snapshot_download(repo_id=repo,revision=revision,local_dir=dest,allow_patterns=patterns,max_workers=3)
        return role,{'repo':repo,'revision':revision,'status':'downloaded','path':str(dest.relative_to(HERE))}
    except Exception as error:
        return role,{'repo':repo,'revision':revision,'status':'failed','error':type(error).__name__+': '+str(error)}

def main():
    if not json.loads((HERE/'integrity_start.json').read_text())['passed']: raise RuntimeError('Initial audit failed')
    results={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for future in concurrent.futures.as_completed([pool.submit(download,item) for item in CANDIDATES.items()]):
            role,entry=future.result(); results[role]=entry
            print(json.dumps({'role':role,'status':entry['status']}),flush=True)
    (HERE/'model_download_status.json').write_text(json.dumps(results,indent=2)+'\n',encoding='utf-8')
    if any(x['status']!='downloaded' for x in results.values()): raise SystemExit(1)

if __name__=='__main__': main()
