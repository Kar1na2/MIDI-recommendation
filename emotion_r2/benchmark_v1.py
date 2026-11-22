"""Measure V1 model initialization and synthetic inference only."""
import json,os,sys,time
from pathlib import Path
if sys.executable!='/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent
os.environ.update(HF_HOME=str(HERE/'model_cache/hf'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',XDG_CACHE_HOME=str(HERE/'scratch/xdg'))
sys.path.insert(0,str(ROOT/'emotion'));sys.path.insert(0,str(ROOT/'emotion/vendor'))
start=time.monotonic()
import torch
from transformers import Wav2Vec2FeatureExtractor
from voice_model import EmotionModel
if not torch.cuda.is_available(): raise RuntimeError('CUDA unavailable')
path=ROOT/'emotion/model_cache/audeering--wav2vec2-large-robust-12-ft-emotion-msp-dim'
extractor=Wav2Vec2FeatureExtractor.from_pretrained(path,local_files_only=True)
model,info=EmotionModel.from_pretrained(path,local_files_only=True,output_loading_info=True)
if info['missing_keys'] or info['unexpected_keys'] or info['mismatched_keys']: raise RuntimeError('Model load incomplete')
model.to('cuda').eval()
begin=time.monotonic()
with torch.inference_mode(): result=model(torch.zeros((1,64000),device='cuda'))
torch.cuda.synchronize();inference_seconds=time.monotonic()-begin
if result.shape!=(1,3) or not torch.isfinite(result).all(): raise ValueError('Invalid V1 output')
payload={'role':'V1','wall_seconds_including_model_load':time.monotonic()-start,'inference_seconds':inference_seconds,'synthetic_only':True,'audio_seconds':4,'result_shape':list(result.shape)}
(HERE/'scratch/benchmark_V1.json').write_text(json.dumps(payload,indent=2)+'\n',encoding='utf8')
print(json.dumps(payload),flush=True)
