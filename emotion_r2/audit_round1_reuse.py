"""Verify round-one M1/M2 input hashes for the registered sample only."""
import collections,csv,hashlib,json,sys
from pathlib import Path
if sys.executable!='/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
HERE=Path(__file__).resolve().parent; ROOT=HERE.parent
def read(p):return json.loads(p.read_text(encoding='utf-8-sig')) if p.is_file() else {}
def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
with (ROOT/'mvp_325/summary.csv').open(encoding='utf-8-sig',newline='') as f:songs=list(csv.DictReader(f))
sources={r['track_id']:r for r in read(ROOT/'mvp/sample.json')['songs']+read(ROOT/'pilot/selection.json')['songs']}
if len(songs)!=325 or set(sources)!={s['track_id'] for s in songs}:raise RuntimeError('Sample identity mismatch')
records=[]; counts=collections.Counter()
for i,song in enumerate(songs,1):
    tid=song['track_id'];folder=ROOT/'emotion/stems'/tid;stem=folder/'instrumental.flac';meta=read(folder/'separation.json')
    section=ROOT/'emotion/validation/input_sections'/f'{tid}.json';record={'track_id':tid,'cohort':song['cohort']}
    try:
        if not stem.is_file() or not section.is_file():raise FileNotFoundError('instrumental stem or original section input')
        stem_hash=sha(stem)
        if meta.get('status')!='complete' or meta.get('instrumental_sha256')!=stem_hash:raise ValueError('instrumental stem hash mismatch')
        b=section.read_bytes(); cached=read(ROOT/'emotion/scratch/m2'/f'{tid}.json')
        expected=hashlib.sha256(b+stem_hash.encode()).hexdigest()
        record['m2']='verified' if cached.get('status')=='complete' and cached.get('input_sha256')==expected else 'cache_missing_or_hash_mismatch'
        try:
            audio=Path(sources[tid]['audio_path']).resolve()
            if not audio.is_relative_to((ROOT/'liked-audio').resolve()) or not audio.is_file():raise FileNotFoundError('registered source audio')
            audio_hash=sha(audio);cached=read(ROOT/'emotion/scratch/m1'/f'{tid}.json')
            expected=hashlib.sha256(b+stem_hash.encode()+audio_hash.encode()).hexdigest()
            record['m1']='verified' if cached.get('status')=='complete' and cached.get('input_sha256')==expected and cached.get('audio_sha256')==audio_hash else 'cache_missing_or_hash_mismatch'
        except (OSError,ValueError,KeyError) as error:
            record['m1']='input_unavailable_or_invalid';record['m1_reason']=type(error).__name__+': '+str(error)
    except (OSError,ValueError,KeyError) as error:
        record['m1']=record['m2']='input_unavailable_or_invalid';record['reason']=type(error).__name__+': '+str(error)
    for role in ('m1','m2'):counts[role+'_'+record[role]]+=1
    records.append(record)
    if i%25==0:print('Round-one input checks',i,'/',len(songs),flush=True)
out={'counts':dict(counts),'songs':records,'selection':'325-song sample only','policy':'unverified cache is missing and is never reused'}
(HERE/'round1_reuse_audit.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf8');print(json.dumps(out['counts'],indent=2),flush=True)
