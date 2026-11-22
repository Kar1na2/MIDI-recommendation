"""Explicit post-completion stem retention, with measured byte savings."""
from pathlib import Path
import numpy as np
import soundfile as sf
from .common import atomic_path, digest, read, save, StageError
from .config import SEPARATION_KEEP_STEMS


def apply(folder, keep=SEPARATION_KEEP_STEMS):
    folder=Path(folder).resolve(); stems=folder/'stems'
    total=lambda: sum(p.stat().st_size for p in folder.rglob('*') if p.is_file())
    before=total(); target=stems/'no_drums.wav'
    if not target.exists():
        sources=[stems/f'{name}.wav' for name in ('bass','other','vocals')]
        if not all(p.exists() for p in sources):
            raise StageError('Cannot retain no_drums: source stems missing; no silent substitute')
        handles=[sf.SoundFile(p) for p in sources]
        temporary=atomic_path(target)
        try:
            first=handles[0]
            if any((h.samplerate,h.channels,len(h)) != (first.samplerate,first.channels,len(first)) for h in handles):
                raise StageError('Stem formats differ; cannot sum no_drums')
            with sf.SoundFile(temporary,'w',samplerate=first.samplerate,channels=first.channels,
                              subtype='FLOAT',format='WAV') as output:
                for _ in range(0,len(first),65536):
                    output.write(sum(h.read(65536,dtype='float32',always_2d=True) for h in handles))
            temporary.replace(target)
        finally:
            for h in handles: h.close()
            temporary.unlink(missing_ok=True)
    deleted=[]
    for name in ('bass','drums','other'):
        if name in keep: continue
        path=stems/f'{name}.wav'
        if path.exists():
            deleted.append({'path':str(path),'bytes':path.stat().st_size}); path.unlink()
    # Demucs CLI leaves a second working copy after its canonical no_drums
    # artifact is published. It is no longer a required stage artifact.
    work=folder/'routed_sources/demucs_two_stems'
    if work.exists():
        for path in work.rglob('*.wav'):
            if not path.resolve().is_relative_to(folder):
                raise StageError('Stem cleanup path escaped the song output folder')
            deleted.append({'path':str(path),'bytes':path.stat().st_size}); path.unlink()
    after=total()
    record={'keep_stems':list(keep),'bytes_before':before,'bytes_after':after,
            'bytes_saved':sum(p['bytes'] for p in deleted), 'net_bytes_saved':before-after,
            'deleted':deleted,'no_drums_source':'sum of measured bass + other + vocals stems'}
    save(folder/'retention.json',record)
    stage=folder/'separation.json'
    if stage.exists():
        value=read(stage); value['data']['retention']=record
        value['data']['stems']={name:str(stems/f'{name}.wav') for name in keep}
        value['artifact_sha256']={f'stems/{name}.wav':digest(stems/f'{name}.wav') for name in keep}
        save(stage,value)
    return record
