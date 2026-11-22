"""Verify the preregistered three-song V3 smoke check did not degenerate."""
import json,math,sys
from pathlib import Path
if sys.executable!='/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
here=Path(__file__).resolve().parent
ids=['1xOqGUkyxGQRdCvGpvWKmL','0LVdV9NG0y0ROxbYLpjbKc','3BPoSr2pO34Aan6alFfVto']
results=[]
for tid in ids:
    data=json.loads((here/'scores/V3'/f'{tid}.json').read_text(encoding='utf8'))
    frames=[f for s in data['sections'] for f in s.get('frames',[]) if f.get('active')]
    for dimension in ('valence','arousal'):
        vals=[f.get(dimension) for f in frames]
        if len(vals)<2 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in vals) or max(vals)==min(vals):
            raise ValueError('Degenerate V3 '+dimension+' output for '+tid)
    errors=[s for s in data['sections'] if s.get('status')=='error']
    if errors: raise ValueError('V3 section errors for '+tid+': '+str(errors))
    results.append({'track_id':tid,'cohort':data['cohort'],'active_frames':len(frames),'nonfinite':0,
                    'valence_min':min(f['valence'] for f in frames),'valence_max':max(f['valence'] for f in frames),
                    'arousal_min':min(f['arousal'] for f in frames),'arousal_max':max(f['arousal'] for f in frames)})
out={'status':'passed','songs':results,'criteria':'at least two active finite values and nonconstant range in both dimensions per song'}
(here/'v3_real_smoke.json').write_text(json.dumps(out,indent=2,allow_nan=False)+'\n',encoding='utf8');print(json.dumps(out,indent=2))
