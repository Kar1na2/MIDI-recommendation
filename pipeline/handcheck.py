"""Blind manual section-boundary template and one-to-one boundary scoring."""
from pathlib import Path
import csv
from .common import read,save,atomic_write_bytes
from .beats import bar_position

ROOT=Path(__file__).resolve().parents[1]
FIELDS=['track_id','title','section_no','my_start_mmss','my_label','my_texture_note','confidence_1to3']


def prepare():
    from .batch import atomic_csv
    old=ROOT/'pilot/HAND_CHECK.csv'; archive=ROOT/'pilot/archive/HAND_CHECK_before_backend_mvp.csv'
    if old.exists():
        archive.parent.mkdir(parents=True,exist_ok=True)
        if archive.exists(): raise FileExistsError(archive)
        old.rename(archive)
    target=ROOT/'pilot/HAND_CHECK_SECTIONS.csv'
    if target.exists(): return
    titles=['meaningful love','ATTITUDE','505','HUMBLE.','you should see me in a crown',
            'Chopin Nocturne Op 9 No 1','Bling-Bang-Bang-Born','Love Virus']
    songs=read(ROOT/'pilot/selection.json')['songs']; rows=[]
    for title in titles:
        song=next(s for s in songs if title.casefold() in s['title'].casefold())
        for number in (1,2):
            rows.append({'track_id':song['track_id'],'title':song['title'],'section_no':number,
                         **{k:'' for k in FIELDS[3:]}})
    atomic_csv(target,rows,FIELDS)


def seconds(value):
    fields=value.strip().split(':')
    if len(fields)!=2: raise ValueError('Use mm:ss or mm:ss.sss for hand-check times')
    return 60*int(fields[0])+float(fields[1])


def compare(truth,predicted,tolerance):
    # Maximum-cardinality bipartite matching: no duplicate credit for one boundary.
    matched={}
    def augment(i,seen):
        for j in sorted(range(len(predicted)),key=lambda j:abs(truth[i]-predicted[j])):
            if j in seen or abs(truth[i]-predicted[j])>tolerance+1e-9: continue
            seen.add(j)
            if j not in matched or augment(matched[j],seen): matched[j]=i; return True
        return False
    hits=sum(augment(i,set()) for i in range(len(truth)))
    return {'matched':hits,'manual_boundaries':len(truth),'predicted_boundaries':len(predicted),
        'precision':hits/len(predicted) if predicted else None,
        'recall':hits/len(truth) if truth else None}


def score(path=None):
    from .core import data
    path=Path(path or ROOT/'pilot/HAND_CHECK_SECTIONS.csv')
    with path.open(encoding='utf-8-sig') as stream:
        rows=list(csv.DictReader(stream))
    grouped={}
    for row in rows:
        if row['my_start_mmss'].strip(): grouped.setdefault(row['track_id'],[]).append(seconds(row['my_start_mmss']))
    songs={s['track_id']:s for s in read(ROOT/'pilot/selection.json')['songs']}; results=[]
    for tid,truth in grouped.items():
        out=ROOT/'outputs'/tid; song=songs[tid]
        if not (out/'beats.json').exists() and song.get('cached_reference'):
            out=ROOT/'outputs'/song['cached_reference']
        grid=data(out/'beats.json'); report=read(out/'report.json')
        basis=report.get('rhythm_grid_basis','bars'); tolerance=1 if basis=='bars' else 2
        convert=(lambda t:bar_position(grid,t)) if basis=='bars' else (lambda t:t)
        result={'track_id':tid,'title':song['title'],'basis':basis,'tolerance':tolerance}
        for name,key in [('lyrics','lyric_sections'),('rhythm','rhythmic_sections')]:
            boundaries=sorted({s.get('raw_start_s') if name=='lyrics' else s.get('start_s') for s in report.get(key,[])}-{None})
            result[name]=compare([convert(t) for t in truth],[convert(t) for t in boundaries],tolerance)
        results.append(result)
    output={'songs_scored':len(results),'empty_rows_skipped':sum(not r['my_start_mmss'].strip() for r in rows),'songs':results}
    save(ROOT/'pilot/HAND_CHECK_SCORES.json',output); return output
