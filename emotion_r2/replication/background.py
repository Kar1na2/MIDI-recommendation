"""New-song M1/M2 frame scoring with the registered kernels and prompts."""
import hashlib
import itertools
import statistics
import time
from .audit import ROOT, DEST, read, save, sha
from .worker import selected, inputs, cached, verified_stem, emit

def score(role):
    from guards import verify
    from audio_frames import read_mono,windows
    commit=verify([role])
    if role=='M1':
        from background_m1 import model_score
        from essentia.standard import TensorflowPredict2D,TensorflowPredictMusiCNN
        folder=ROOT/'emotion/model_cache/essentia'
        embedder=TensorflowPredictMusiCNN(graphFilename=str(folder/'msd-musicnn-1.pb'),output='model/dense/BiasAdd')
        predictor=TensorflowPredict2D(graphFilename=str(folder/'emomusic-msd-musicnn-2.pb'),output='model/Identity')
    else:
        import torch
        from transformers import ClapModel,ClapProcessor
        from background_m2 import PROMPTS
        if not torch.cuda.is_available():raise RuntimeError('CUDA unavailable')
        torch.set_num_threads(2)
        folder=ROOT/'emotion/model_cache/laion--clap-htsat-unfused'
        processor=ClapProcessor.from_pretrained(folder,local_files_only=True)
        model=ClapModel.from_pretrained(folder,local_files_only=True).to('cuda').eval()
        text=processor(text=PROMPTS,return_tensors='pt',padding=True).to('cuda')
        with torch.inference_mode():prompts=model.get_text_features(**text)
    for song in selected():
        tid=song['track_id'];public,_,pb,_=inputs(tid);stem=verified_stem(tid,'instrumental')
        stem_hash=sha(stem) if stem else '';original=ROOT/song['audio_path']
        audio_hash=sha(original)
        fingerprint=hashlib.sha256(pb+stem_hash.encode()+(audio_hash.encode() if role=='M1' else b'')).hexdigest()
        target=DEST/'scores'/role/f'{tid}.json'
        if cached(target,fingerprint):continue
        start=time.monotonic();payload={'track_id':tid,'cohort':song['cohort'],'role':role,'plan_commit':commit,
            'input_sha256':fingerprint,'audio_sha256':audio_hash,'stem_sha256':stem_hash,'sections':[]}
        if stem is None:
            payload['status']='instrumental_stem_unavailable';save(target,payload);continue
        rate=16000 if role=='M1' else 48000
        audio=read_mono(stem,rate);mix=read_mono(original,rate) if role=='M1' else None
        sections=public['sections']
        if song['ring1_source']=='instrumental' and not sections:
            sections=[{'section_id':'instrumental-whole','set':'instrumental','start_s':0.,
                       'end_s':min(len(audio),len(mix))/rate if mix is not None else len(audio)/rate}]
        for section in sections:
            rec={'section_id':section['section_id'],'set':section.get('set'),'frames':[],'status':'ok'}
            a,b=section.get('start_s'),section.get('end_s')
            if a is None or b is None:
                rec['status']='missing_raw_timing';payload['sections'].append(rec);continue
            try:
                if role=='M1':
                    for frame,full in zip(windows(a,b,audio,rate),windows(a,b,mix,rate)):
                        inst_score=model_score(frame['samples'],embedder,predictor) if frame['actual_seconds'] else [None,None]
                        full_score=model_score(full['samples'],embedder,predictor) if full['actual_seconds'] else [None,None]
                        rec['frames'].append({'start_s':frame['start_s'],'end_s':frame['end_s'],
                            'instrumental_coverage':frame['coverage'],'mix_coverage':full['coverage'],
                            'M1_valence':inst_score[0],'M1_arousal':inst_score[1],
                            'M1_full_mix_valence':full_score[0],'M1_full_mix_arousal':full_score[1]})
                else:
                    frames=iter(windows(a,b,audio,rate))
                    while True:
                        group=list(itertools.islice(frames,4))
                        if not group:break
                        valid=[f for f in group if f['actual_seconds']];scores=[]
                        if valid:
                            encoded=processor(audio=[f['samples'] for f in valid],sampling_rate=48000,return_tensors='pt',padding=True).to('cuda')
                            with torch.inference_mode():similarity=model.get_audio_features(**encoded)@prompts.T
                            if not torch.isfinite(similarity).all():raise ValueError('Nonfinite CLAP similarities')
                            scores=similarity.cpu().numpy().tolist()
                        i=0
                        for frame in group:
                            row={'start_s':frame['start_s'],'end_s':frame['end_s'],'instrumental_coverage':frame['coverage'],
                                 'M2_valence':None,'M2_arousal':None}
                            if frame['actual_seconds']:
                                values=scores[i];i+=1;row.update(M2_valence=values[0]-values[1],M2_arousal=values[2]-values[3])
                            rec['frames'].append(row)
                for dimension in ('valence','arousal'):
                    values=[f[role+'_'+dimension] for f in rec['frames'] if f.get(role+'_'+dimension) is not None]
                    rec[dimension+'_mean']=statistics.fmean(values) if values else None
            except (ValueError,RuntimeError) as error:
                rec.update(status='error',error=type(error).__name__+': '+str(error),frames=[])
            payload['sections'].append(rec)
        payload.update(status='complete',wall_seconds=time.monotonic()-start)
        save(target,payload);emit(role,tid,sections=len(payload['sections']),seconds=payload['wall_seconds'])
