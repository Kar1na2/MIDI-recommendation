"""Time a single model's synthetic smoke run without scoring any song."""
import json, sys, time
from pathlib import Path
if sys.executable != '/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
from smoke_candidates import run
if len(sys.argv)!=2 or sys.argv[1] not in {'T3','L1','M3','V3'}: raise ValueError('Expected one model role')
role=sys.argv[1]; started=time.monotonic(); result=run(role)
payload={'role':role,'wall_seconds_including_model_load':time.monotonic()-started,'synthetic_only':True,'result':result}
path=Path(__file__).resolve().parent/'scratch'/('benchmark_'+role+'.json')
path.write_text(json.dumps(payload,indent=2)+'\n',encoding='utf8')
print(json.dumps(payload),flush=True)
