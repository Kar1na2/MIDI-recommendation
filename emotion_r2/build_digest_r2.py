"""Source-linked Task E-style digest of completed Task F artifacts; no inference."""
import base64,csv,hashlib,html,json,math,os,sys
from pathlib import Path
if sys.executable!='/home/usr/venvs/midi/bin/python':raise RuntimeError('Wrong interpreter')
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent;DEST=HERE/'digest'
os.environ.setdefault('MPLCONFIGDIR',str(HERE/'scratch/matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd
from markdown_it import MarkdownIt

def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
def write(p,text):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text+'\n',encoding='utf8')
def num(x):return 'missing' if x is None else f'{x:.3f}'
def arrow(x):return 'missing' if x is None else ('↑' if x>=.5 else '↓' if x<=-.5 else '→')+' '+num(x)
def mmss(x):return 'untimed' if x is None else f'{int(x)//60}:{int(x)%60:02d}'
def chart(name,data,source,draw):
    save(DEST/'charts'/f'{name}.json',{'source':source,'data':data})
    fig=draw();fig.tight_layout();fig.savefig(DEST/'charts'/f'{name}.png',dpi=200,bbox_inches='tight');plt.close(fig)
def main():
    for folder in ('charts','cards','frontend_data'): (DEST/folder).mkdir(parents=True,exist_ok=True)
    h=read(HERE/'validation/h1.json');trust=read(HERE/'validation/trust.json');old=read(ROOT/'emotion/validation/trust.json')
    ex=read(HERE/'validation/explanation_checks.json');gate=read(HERE/'validation/voice_gate_audit.json');ac=read(HERE/'validation/voice_acoustic_correlations.json')
    with (ROOT/'mvp_325/summary.csv').open(encoding='utf-8-sig',newline='') as f:songs=list(csv.DictReader(f))
    byid={s['track_id']:s for s in songs};lifts=h['random']['round2']['songs'];ordered=sorted(lifts,key=lambda s:(s['lift'],s['track_id']))
    with (ROOT/'pilot/HAND_CHECK_SECTIONS.csv').open(encoding='utf-8-sig',newline='') as f:hand=list(dict.fromkeys(r['track_id'] for r in csv.DictReader(f)))
    chosen=list(dict.fromkeys([s['track_id'] for s in list(reversed(ordered[-10:]))+ordered[:5]]+hand))
    if len(chosen)>23 or set(chosen)-set(byid):raise ValueError('Card selection scope error')
    selection={'rule':'Random-300 Genius-qualified songs: 10 largest chorus lifts, then 5 smallest; ties by track ID. Add all eight hand-check IDs and deduplicate. Pilot cards are not a random sample. Congruence rankings are unavailable because H2 was not run.',
               'source':['validation/h1.json','pilot/HAND_CHECK_SECTIONS.csv'],'track_ids':chosen}
    save(HERE/'validation/card_selection.json',selection)
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'figure.facecolor':'white','axes.grid':True,'grid.alpha':.18})
    font_paths=[Path('/mnt/c/Windows/Fonts/malgun.ttf'),Path('/mnt/c/Windows/Fonts/meiryo.ttc')]
    available=[]
    for font in font_paths:
        if font.is_file():
            font_manager.fontManager.addfont(str(font));available.append(font_manager.FontProperties(fname=str(font)).get_name())
    if available:plt.rcParams['font.family']=available+['DejaVu Sans']
    def liftplot():
        fig,ax=plt.subplots(figsize=(8,4));ax.hist([s['lift'] for s in lifts],bins=22,color='#0072b2',edgecolor='white');ax.axvline(0,color='black',ls='--');ax.set(xlabel='Within-song Chorus − Verse (background arousal z)',ylabel='Songs',title='Development robustness check — random sample, not confirmation');return fig
    chart('chorus_lift_distribution',h['random']['round2'],'emotion_r2/validation/h1.json#/random/round2',liftplot)
    channels=list(trust);oldkey={'words_valence':'text_valence'}
    panel=[{'channel':ch,'round1_rho':old[oldkey.get(ch,ch)]['rho']['rho'],'round2_median_rho':trust[ch]['overall']['median_rho'],
            'repeat_p':trust[ch]['overall']['repeat']['p'],'status':'PROVISIONAL / EXPERIMENTAL' if ch.startswith('voice_') else 'experimental_untrusted' if not trust[ch]['overall']['trusted'] else 'trusted'} for ch in channels]
    def trustplot():
        fig,ax=plt.subplots(figsize=(10,4.8));x=np.arange(len(panel));ax.bar(x-.18,[p['round1_rho'] for p in panel],.36,label='Round 1: original pair',color='#0072b2');ax.bar(x+.18,[p['round2_median_rho'] for p in panel],.36,label='Round 2: median pairwise',color='#e69f00');ax.axhline(.4,color='black',ls='--',label='Registered correlation criterion');ax.set_xticks(x,[p['channel'].replace('_','\n')+ ('\nPROVISIONAL' if p['channel'].startswith('voice_') else '\nEXPERIMENTAL' if not trust[p['channel']]['overall']['trusted'] else '') for p in panel]);ax.set(ylabel='Spearman rho',title='Model agreement; correlation alone is not the complete trust test',ylim=(0,1));ax.legend(loc='upper left',fontsize=8);return fig
    chart('trust_round1_vs_round2',panel,['emotion/validation/trust.json','emotion_r2/validation/trust.json'],trustplot)
    valid={ch:d for ch,d in ex.items() if d.get('status')=='completed' and d['full']['oof_r2']>0}
    def featureplot():
        fig,axes=plt.subplots(1,len(valid),figsize=(5*len(valid),4.5),squeeze=False)
        for ax,(ch,d) in zip(axes[0],valid.items()):
            top=d['full']['permutation_importance'][:6];ax.barh([p['feature'].replace('instrumental_','').replace('voice_','voice ') for p in top][::-1],[p['mean_r2_drop'] for p in top][::-1],color='#009e73');ax.set_title(ch.replace('_',' ')+('\nPROVISIONAL / EXPERIMENTAL' if ch.startswith('voice_') else ''));ax.set_xlabel('Held-out R² drop after permutation')
        fig.suptitle('Explains model ratings, not human emotion or causal effects');return fig
    chart('feature_importance',{ch:d['full']['permutation_importance'][:6] for ch,d in valid.items()},'emotion_r2/validation/explanation_checks.json',featureplot)
    frontend=[];cards=[]
    for tid in chosen:
        meta=read(HERE/'songs'/tid/'song.json');secs=read(HERE/'songs'/tid/'sections.json')['sections'];df=pd.read_parquet(HERE/'songs'/tid/'curves.parquet')
        points=[]
        for r in df.to_dict('records'):
            if r['set']!='primary':continue
            value=r.get('background_arousal_consensus_z');points.append({'section_id':r['section_id'],'start_s':r['start_s'],'end_s':r['end_s'],'background_arousal_z':float(value) if isinstance(value,(int,float)) and math.isfinite(value) else None,'models':int(r.get('background_arousal_models',0))})
        def timeline(points=points,secs=secs,meta=meta):
            fig,ax=plt.subplots(figsize=(10,3.6));first=True
            for s in secs:
                if s['set']!='primary' or s['start_s'] is None or s['end_s'] is None:continue
                ax.axvspan(s['start_s'],s['end_s'],color='#e69f00',alpha=.06);part=[p for p in points if p['section_id']==s['section_id']]
                ax.plot([p['start_s'] for p in part],[p['background_arousal_z'] for p in part],color='#0072b2',label='Background arousal consensus' if first else None);first=False
                ax.axvline(s['start_s'],color='#999999',lw=.4)
            ax.axhline(0,color='black',lw=.7);ax.set(title=meta['title']+' — models’ background-arousal ratings',xlabel='Time (seconds)',ylabel='z relative to random primary sections');
            if first:ax.text(.5,.5,'No registered primary lyric sections; no timeline values',ha='center',transform=ax.transAxes)
            else:ax.legend(fontsize=8)
            return fig
        chart('timeline_'+tid,{'track_id':tid,'cohort':meta['cohort'],'points':points,'section_bands':[{'section_id':s['section_id'],'label':s['display_label'],'start_s':s['start_s'],'end_s':s['end_s']} for s in secs if s['set']=='primary']},f'emotion_r2/songs/{tid}/curves.parquet and sections.json',timeline)
        rows=[];experimental=[]
        for s in secs:
            if s['set']!='primary':continue
            label=str(s.get('display_label') or s.get('label') or 'Unlabeled').replace('|','/')
            rows.append(f"| {label} | {mmss(s['start_s'])}–{mmss(s['end_s'])} | {arrow(s['consensus'].get('words_valence'))} | {arrow(s['consensus'].get('background_arousal'))} | not available | not measured | not measured |")
            experimental.append(f"| {label} | {arrow(s['consensus'].get('voice_valence'))} | {arrow(s['consensus'].get('voice_arousal'))} |")
        lift=next((s['lift'] for s in lifts if s['track_id']==tid),None)
        verdict=f"The models rate this song’s Chorus background arousal {num(lift)} z above Verse; this is a development result." if lift is not None else 'This hand-check song has no random-sample Genius-qualified chorus-lift estimate; no value is invented.'
        card=(f"# {meta['title']}\n\n{verdict}\n\nCohort: {meta['cohort']}; "+meta['sampling_note']+f". Language: {meta['language']}. Source: `emotion_r2/songs/{tid}/sections.json`; selection source: `validation/h1.json`.\n\n"+
             '| Section | Time | Words valence z | Background arousal z | Relation | Confidence | Local reasons |\n|---|---|---|---|---|---|---|\n'+'\n'.join(rows)+f"\n\n![Background-arousal timeline](../charts/timeline_{tid}.png)\n\n"+
             '> **EXPERIMENTAL — PROVISIONAL voice scores.** The gate and singing-domain validity remain unresolved. These ratings must not be presented as established emotion.\n>\n> | Section | Voice valence z | Voice arousal z |\n> |---|---|---|\n'+'\n'.join('> '+row for row in experimental)+
             '\n\nConfidence is not calibrated; no section-local reasons were measured. Congruence and relation are unavailable because background valence was not trusted. Missing values stay missing. Arrows reuse the registered ±0.5 display bands; z is a model rating relative to this sample, not human truth.')
        write(DEST/'cards'/f'{tid}.md',card);cards.append(card.replace('../charts/','charts/'))
    # Export compact, lyric-free data for every registered song, not only cards.
    for song in songs:
        tid=song['track_id'];meta=read(HERE/'songs'/tid/'song.json');secs=read(HERE/'songs'/tid/'sections.json')['sections'];df=pd.read_parquet(HERE/'songs'/tid/'curves.parquet')
        compact=[];seen=set()
        for r in df.to_dict('records'):
            if r['set']!='primary':continue
            key=(r['section_id'],int(r['start_s']))
            if key in seen:continue
            seen.add(key);values={ch:(float(r[ch+'_consensus_z']) if isinstance(r.get(ch+'_consensus_z'),(int,float)) and math.isfinite(r[ch+'_consensus_z']) else None) for ch in ('words_valence','background_arousal','voice_valence','voice_arousal','background_valence')}
            compact.append({'section_id':r['section_id'],'start_s':r['start_s'],'background_arousal_z':values['background_arousal'],
                            'words_valence_z':values['words_valence'],'experimental':{k:v for k,v in values.items() if k.startswith('voice_') or k=='background_valence'}})
        data={'kind':'song','track_id':tid,'title':meta['title'],'cohort':meta['cohort'],'language':meta['language'],'sampling_note':meta['sampling_note'],
              'source':f'emotion_r2/songs/{tid}/sections.json and curves.parquet','voice_status':'PROVISIONAL / EXPERIMENTAL',
              'sections':[{'section_id':s['section_id'],'label':s['display_label'],'start_s':s['start_s'],'end_s':s['end_s'],'set':s['set'],
                           'words_valence_z':s['consensus'].get('words_valence'),'background_arousal_z':s['consensus'].get('background_arousal'),
                           'experimental':{k:s['consensus'].get(k) for k in ('voice_valence','voice_arousal','background_valence')},
                           'available_models':s['available_models'],'confidence':None,'reasons':[],'relation':None} for s in secs],
              'curves_1hz':compact}
        save(DEST/'frontend_data'/f'{tid}.json',data);frontend.append(data)
    corpus={'kind':'corpus','primary_sample':'random 300','secondary_sample_note':'Pilot and combined are not random samples','sources':['validation/h1.json','validation/trust.json','validation/explanation_checks.json','validation/voice_gate_audit.json'],
            'h1':h['random']['round2'],'trust':panel,'explanations':{k:{'r2':d['full']['oof_r2'],'baselines':{b:v.get('oof_r2') for b,v in d['single_feature_baselines'].items()}} for k,d in ex.items() if d.get('status')=='completed'},
            'gate':gate['overall'],'card_selection':selection,'h2':'not run (precondition failed)','h3':'not run (precondition failed)'}
    save(DEST/'frontend_data/corpus.json',corpus)
    schema={'$schema':'https://json-schema.org/draft/2020-12/schema','oneOf':[
        {'type':'object','required':['kind','track_id','title','cohort','language','sections','curves_1hz','source','voice_status'],'properties':{'kind':{'const':'song'},'track_id':{'type':'string'},'cohort':{'enum':['random','pilot']},'sections':{'type':'array','items':{'type':'object','required':['section_id','label','start_s','end_s','set','experimental','confidence','reasons']}},'curves_1hz':{'type':'array'}}},
        {'type':'object','required':['kind','primary_sample','sources','h1','trust','explanations','gate'],'properties':{'kind':{'const':'corpus'}}}]}
    nullable={'type':['number','null']}
    experimental={'type':'object','required':['voice_valence','voice_arousal','background_valence'],
                  'properties':{k:nullable for k in ('voice_valence','voice_arousal','background_valence')},'additionalProperties':False}
    section_schema={'type':'object','required':['section_id','label','start_s','end_s','set','words_valence_z','background_arousal_z','experimental','available_models','confidence','reasons','relation'],
                    'properties':{'section_id':{'type':'string'},'label':{'type':'string'},'start_s':nullable,'end_s':nullable,'set':{'type':'string'},
                                  'words_valence_z':nullable,'background_arousal_z':nullable,'experimental':experimental,'available_models':{'type':'object'},
                                  'confidence':{'type':'null'},'reasons':{'type':'array','maxItems':0},'relation':{'type':'null'}},'additionalProperties':False}
    point_schema={'type':'object','required':['section_id','start_s','words_valence_z','background_arousal_z','experimental'],
                  'properties':{'section_id':{'type':'string'},'start_s':{'type':'number'},'words_valence_z':nullable,'background_arousal_z':nullable,'experimental':experimental},'additionalProperties':False}
    schema['oneOf'][0]['properties'].update({k:{'type':['string','null']} for k in ('title','language','sampling_note','source','voice_status')})
    schema['oneOf'][0]['properties'].update(sections={'type':'array','items':section_schema},curves_1hz={'type':'array','items':point_schema})
    schema['oneOf'][0]['additionalProperties']=False
    save(DEST/'frontend_data/schema.json',schema)
    r=h['random']['round2'];best=ex['background_arousal']['single_feature_baselines']['instrumental_rms']['oof_r2'];ten=gate['seeded_ten']
    digest=f'''# Emotion round 2: what this sample supports

**Voice results are PROVISIONAL. The chorus lift is a development robustness check, not confirmation. Replication is deferred.**

We compared local model ratings of three channels: words (what is said), voice (how it sounds) and instrumental background (context). This maps conceptually to conversation analysis, but singing is not speech and this project has not demonstrated transfer to a voice-AI product. Scores describe model outputs, not a listener’s actual feelings. Headline results use the random 300; pilot and combined examples are not random samples. [1]

## How far to trust it

- Words valence passes the registered model-agreement and repeat checks (median rho {trust['words_valence']['overall']['median_rho']:.3f}), including a T1 pair. T3 and L1 share a Qwen family connection, so their agreement is not independent validation. [2]
- Background arousal passes (median rho {trust['background_arousal']['overall']['median_rho']:.3f}). M1 and M3 share MediaEval dataset provenance; exact training-split overlap remains unresolved. [2,3]
- Voice valence and arousal pass the numerical rules, but **remain PROVISIONAL** because gate quality, singing transfer and acoustic shortcuts are unresolved. See the experimental panel below. [2,4]

> **EXPERIMENTAL:** background valence fails (median rho {trust['background_valence']['overall']['median_rho']:.3f}). H2/H3 were not run: their failed precondition is not a negative finding. No trusted congruent/contrasting ranking, relation map or same-language H2 shuffle is available. No new probes were selected; round-one probe availability is recorded in `emotion/validation/missing_probes.csv`. [2,5]

## Five findings

1. The chorus-lift robustness check includes **{r['n']}** random-sample songs with explicitly sourced Genius Verse and Chorus sections. Their median lift is **{r['median']:.3f} z**; **{100*r['share_above_zero']:.1f}%** are above zero. This repeats a pattern already seen on these songs, not an independent confirmation. [1]
2. The bootstrap interval for the median is **{r['bootstrap_95_ci'][0]:.3f} to {r['bootstrap_95_ci'][1]:.3f} z**; the registered one-sided Wilcoxon p is **{r['p']:.3g}**. Labels and matching are still pipeline-derived. [1]
3. Background-arousal agreement remains above criterion without the M1–M3 pair. The full median is **{trust['background_arousal']['overall']['median_rho']:.3f}**; agreement is not accuracy against human listening. [2]
4. The background-arousal explanation fit has held-out **R² {ex['background_arousal']['full']['oof_r2']:.3f}**, versus **{best:.3f}** for loudness alone. It explains part of the models’ ratings, not causes of human emotion. [6]
5. In the seeded gate check, detector and transcription agreed in only **{ten['confusion']['both_active']+ten['confusion']['neither']}/{ten['windows']} windows ({100*ten['window_agreement']:.1f}%)**. Neither detector is human truth; voice findings need listening review. [4]

## What is associated with the ratings

The feature-importance chart uses only positive held-out fits. Its data JSON records each feature’s held-out R² drop and fold evidence. The full report names the leading features and shows every fold and all four baselines. Words-valence R² is below zero, so no words drivers are offered. Local section reasons and calibrated confidence were not measured; the cards say so rather than invent them. [6]

> **EXPERIMENTAL — PROVISIONAL voice panel:** V1–V3 arousal rho is {trust['voice_arousal']['overall']['median_rho']:.3f}. Both models’ arousal scores correlate with voice brightness, pitch and RMS. A shared acoustic shortcut is plausible, but these correlations cannot establish its cause. Voice scores are isolated in labelled experimental tables; missing scores remain missing. [2,7]

## What the cards show

Because congruence is unavailable, selection uses the 10 largest and 5 smallest Genius-qualified chorus lifts in the random sample, then adds the eight hand-check songs and deduplicates. Each card shows sourced section ratings and a background-arousal timeline. Instrumentals without registered lyric sections have no invented timeline. Selection is descriptive, not a new hypothesis test. [8]

## Three presentation limits

First, these are model ratings without a completed human emotion anchor. Second, voice selection and stem artifacts can hide or distort singing; model agreement does not repair missing coverage. Third, missing text, uncertain language and a deliberately chosen pilot limit coverage and generalization. M3 licensing remains unresolved; this is research material, not a deployment clearance. [3,4,9]

## Glossary

**Valence:** model-rated positive versus negative tone. **Arousal:** model-rated activation or energy. **z:** distance from the random-primary section mean in standard-deviation units. **rho:** agreement in rank order. **R²:** held-out variance explained; zero or negative gives no useful explanatory fit. **Consensus:** mean of available normalized model scores, never a replacement for missing scores. **Provisional:** a numerical pass with unresolved validity. **Development robustness:** checking a known pattern on the same development songs rather than confirming it on a new sample.

Sources: [1] `emotion_r2/validation/h1.json`; [2] `validation/trust.json`; [3] `validation/provenance_review.json`; [4] `validation/voice_gate_audit.json`; [5] `validation/conditional_hypotheses.json`; [6] `validation/explanation_checks.json`; [7] `validation/voice_acoustic_correlations.json`; [8] `validation/card_selection.json`; [9] `language_weighted_summary.json`, `genius_word_recovery.json`. Paths without a prefix are under `emotion_r2/`.
'''
    top=ex['background_arousal']['full']['permutation_importance'][:3]
    plain={'voice_brightness_hz':'high-frequency emphasis in the voice stem','instrumental_brightness_hz':'high-frequency emphasis in the instrumental stem','instrumental_rms':'instrumental loudness proxy','onset_density_total':'total note-onset density','median_register_midi':'typical note register','voice_f0_median_hz':'typical voice pitch','voice_rms_mean':'voice loudness proxy','voice_f0_range_semitones':'voice pitch range'}
    feature_text=' Leading associations were '+', '.join(plain.get(p['feature'],p['feature'].replace('_',' '))+f" (mean held-out R² drop {p['mean_r2_drop']:.3f}; positive in {p['positive_folds']}/5 folds)" for p in top)+'. [6]'
    digest=digest.replace('The feature-importance chart uses only positive held-out fits.','The feature-importance chart uses only positive held-out fits.'+feature_text)
    write(DEST/'DIGEST.md',digest);write(DEST/'SONG_CARDS.md',selection['rule']+'\n\n'+'\n\n---\n\n'.join(cards))
    caveats='''# Caveats

Voice verdicts are PROVISIONAL. Speech-trained models on singing, uncertain stem separation, detector misses and shared acoustic response can undermine validity despite numerical agreement. Background valence is experimental; H2/H3 did not run and are not negative findings. Model ratings are not human feelings. Multilingual sentiment on poetic lyrics is not independently calibrated. Primary/secondary section timing and missing data are explicit; unreliable secondary sections do not enter headline tests. The pilot and combined sample are not random. The chorus result uses the same development songs as round one; replication is deferred. M1/M3 share dataset lineage and their exact split overlap is unresolved; T3/L1 share a family connection. M3 license claims conflict. No human emotion anchor is claimed. Any voice-gate listening file is summarized separately in REPORT.md as post hoc, never used to alter scores or verdicts.

## Skeptical engineer questions

**Does rho around 0.93 prove the voice models recognize singing emotion?** No. It can reflect shared acoustics and selection by the same gate. The saved brightness, pitch and RMS diagnostics are associations, not a causal explanation or human validation.

**Is the chorus result a fresh confirmation?** No. It is a development robustness check on songs where the effect was already observed. New held-out songs would be needed for confirmation.

**Can this be presented as lyric/music irony or deployed commercially?** No trusted words/background-valence relation was established, so H2/H3 and congruence rankings are unavailable. Model and dataset permissions also need clarification; no commercial-use clearance is provided.
'''
    write(DEST/'caveats.md',caveats)
    options='''# Frontend options for future work

This document proposes layouts only. No production site was touched. Every option must omit lyric text and audio, keep missing values missing, and isolate provisional voice and untrusted background-valence data in clearly labelled experimental boxes.

## A. Song explorer — recommended

Layout: scope/trust banner → song picker → background-arousal timeline → source-linked section table → optional experimental voice panel. Flow: choose a song, inspect a section, read coverage and caveats. Use `frontend_data/<id>.json`: metadata, cohort, language, `sections`, `curves_1hz`, model availability and null confidence; corpus claims come from `corpus.json`. Show model counts and missing coverage instead of invented confidence. The voice-AI pitch is the separation of content, delivery and context as a research interface, not demonstrated conversation performance.

## B. Contrast map first — unavailable with current evidence

Proposed layout: future trusted relation map → selectable point → song details. Current background valence is untrusted and H2 did not run, so the necessary trusted map and profile distribution do not exist. Do not manufacture quadrants or rankings from the experimental data. Use the trust panel and a clear unavailable state instead. Future data requirements: validated relation values and calibrated confidence in a revised schema; these are not present today.

## C. Case-study story

Layout: known-sample caveat → largest and smallest chorus-lift examples → their background-arousal timelines → corpus lift distribution → content/delivery/context analogy. The examples are selected descriptively from `validation/card_selection.json`; exact points and sections come from song JSON files. Do not call the examples congruent, contrasting or ironic. Voice panels are optional experimental appendices. This supports a careful research story for a voice-AI audience without claiming product transfer.

Recommend A because it makes missingness, provenance and uncertain voice coverage visible while letting reviewers inspect examples. B is blocked by the failed registered precondition. The local `index.html` is review-only and makes no network requests. See `../SCHEMA.md` and `frontend_data/schema.json`.
'''
    write(DEST/'frontend_options.md',options)
    write(DEST/'charts/NOT_RUN.md','Contrast map, congruence-profile distribution and relation-by-section charts were not produced: background valence is untrusted and H2/H3 did not run. No replacement numbers were invented. The requested round-two charts are the chorus-lift distribution, card-song background-arousal timelines, round-one/round-two trust panel and valid explanation importance.')
    # Self-contained preview: all charts and text embedded; no CDN, fetch or audio.
    images=[]
    for p in sorted((DEST/'charts').glob('*.png')):
        if p.stem.startswith('timeline_'):continue
        images.append('<figure><img alt="'+html.escape(p.stem)+'" src="data:image/png;base64,'+base64.b64encode(p.read_bytes()).decode()+'"><figcaption>'+html.escape(p.stem)+'</figcaption></figure>')
    md=MarkdownIt('commonmark',{'html':False}).enable('table')
    rendered=md.render(selection['rule']+'\n\n'+'\n\n'.join(cards))
    for tid in chosen:
        p=DEST/'charts'/('timeline_'+tid+'.png')
        rendered=rendered.replace('src="charts/'+p.name+'"','src="data:image/png;base64,'+base64.b64encode(p.read_bytes()).decode()+'"')
    page='<!doctype html><html lang="en"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'"><title>Task F research digest</title><style>body{max-width:1100px;margin:40px auto;padding:0 22px;font:16px/1.6 system-ui;color:#172b3a;background:#fafafa}img{max-width:100%;height:auto}figure{margin:30px 0;padding:12px;background:white;border:1px solid #ddd}h1,h2{color:#005b8f}table{border-collapse:collapse;font-size:14px;width:100%;display:block;overflow-x:auto}th,td{padding:7px;border:1px solid #ddd}blockquote{border-left:5px solid #e69f00;padding:10px 20px;background:#fff4df}code{overflow-wrap:anywhere}</style><h1>Task F — local research preview</h1>'+md.render(digest)+''.join(images)+'<h2>Song cards</h2>'+rendered+'</html>'
    write(DEST/'index.html',page)
    # Automated field/long-text scan: notes and lyrics are not included in frontend data.
    forbidden={'text','lyrics','lyric','plainlyrics','syncedlyrics','audio','audio_path','notes'};violations=[]
    def scan(v,path=''):
        if isinstance(v,dict):
            for k,x in v.items():
                if k.casefold() in forbidden:violations.append(path+'/'+k)
                scan(x,path+'/'+k)
        elif isinstance(v,list):
            for i,x in enumerate(v):scan(x,path+'/'+str(i))
        elif isinstance(v,str) and len(v)>600:violations.append(path+': long free-text field')
    for p in (DEST/'frontend_data').glob('*.json'):
        if p.name!='schema.json':scan(read(p),p.name)
    if violations:raise ValueError('Frontend privacy scan failed: '+repr(violations[:10]))
    save(DEST/'build_audit.json',{'source_files':['validation/h1.json','validation/trust.json','validation/explanation_checks.json','validation/voice_gate_audit.json'],
          'card_ids':chosen,'frontend_song_files':len(frontend),'frontend_bytes':sum(p.stat().st_size for p in (DEST/'frontend_data').glob('*.json')),
          'privacy_scan':'passed: forbidden keys and long free-text fields absent','network_requests':'none: inline text and data-URI PNG images; CSP default-src none',
          'no_new_inference':True,'no_new_hypothesis_statistics_in_digest':True})
    print('Digest written with',len(chosen),'cards and',len(frontend),'song JSON files',flush=True)
if __name__=='__main__':main()
