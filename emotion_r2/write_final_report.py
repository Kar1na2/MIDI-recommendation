"""Render Task F evidence and caveats from saved analyses; no new model scoring."""
import csv,datetime,hashlib,json,re,sys
from pathlib import Path
if sys.executable!='/home/usr/venvs/midi/bin/python':raise RuntimeError('Wrong interpreter')
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def fmt(x,d=3):return 'not evaluable' if x is None else f'{x:.{d}g}'
def table(headers,rows):return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join('---' for _ in headers)+' |']+['| '+' | '.join(str(v).replace('|','/') for v in r)+' |' for r in rows])+'\n'
def main():
    trust=read(HERE/'validation/trust.json');h=read(HERE/'validation/h1.json');ex=read(HERE/'validation/explanation_checks.json')
    gate=read(HERE/'validation/voice_gate_audit.json');ac=read(HERE/'validation/voice_acoustic_correlations.json')
    language=read(HERE/'language_weighted_summary.json');prov=read(HERE/'validation/provenance_review.json')
    old=read(ROOT/'emotion/validation/trust.json');sections=read(HERE/'validation/sections_analysis.json')
    out=['# Task F — emotion round 2 on the 325-song sample',
         '**Voice verdicts are PROVISIONAL.** V1/V3 pass the unchanged numerical agreement/repetition rules, but the pYIN gate misses activity in many lyric sections and agreement may reflect shared acoustic shortcuts. This is not validated singing-emotion recognition.',
         '**The chorus lift is a development robustness check, not confirmation. H2 and H3 were not run because background valence did not pass; that is not a negative result. Replication is deferred and outside this task.**',
         'Only the 300 random and 25 pilot tracks in `mvp_325/summary.csv` were analysed. Headline statistics use the random 300; pilot and combined findings below are not random samples. No thresholds, models, gates or registered tests were changed, no Genius manual saves/crawls were run in this completion, and no library run or routing was started.']
    out+=['## Integrity, registration and disclosures']
    for name in ('integrity_finish_start.json','integrity_finish_end.json'):
        p=HERE/name
        if p.is_file():
            d=read(p)
            counts=[key+' unchanged: '+format(d[key]['unchanged'],',')+'/'+format(d[key]['total'],',') for key in ('round1','v1','mvp_325')]
            out.append(f'`{name}`: '+ '; '.join(counts)+f'. Overall pass: {d["passed"]}.')
        else:out.append(f'`{name}`: pending; this report is not final until the end audit passes.')
    out+=['Original plan commit: `e8127190bd82e2b631bb1e749874e73308e45a2c`. Amendment 1 commit: `7484df243f1e43b82249c4f9a3b26ad7e02c0aa3`. The original text is preserved, with the amendment appended.',
          'Original manifest SHA-256: `'+hashlib.sha256((HERE/'models.json').read_bytes()).hexdigest()+'`. Amended manifest SHA-256: `'+hashlib.sha256((HERE/'models_amended.json').read_bytes()).hexdigest()+'`.',
          'L1 fixed-prompt SHA-256: `46f33c7d62256022323d6de55f3f821a8a955af72ea94ef0c6b711bd7fff4571`; deterministic greedy decoding. The amendment was committed before new sample scoring. No later changes to scoring definitions were made.',
          'The original preregistration commit swept in 1,937 unrelated deletions; see `docs/REPO_STATE.md`. History was not rewritten. Development checks had already scored T3/L1 on `01A9Xt0XyeYIzC9K7MSvua`, `01prGWjCTxKBxJc400zwvQ`, `03EdQABuvf0suFxxbqdYBl`, and M3 on the first two. These are five model-song checks on three distinct songs, not five distinct songs. All were included in the full rescore, without treating them as held out.',
          'Round-one background-arousal trust and a chorus lift in these same songs were already known. The original separate-replication section is deferred, not executed. Round-one M1/M2 reuse passed input-hash audits on 325/325 songs each. Public section timing matched round one on all 3,726 records, including 40 missing-timing records.']
    out+=['## Model provenance and licensing — verified versus unresolved',prov['conclusion']]
    for s in prov['sources']:out.append(f"- [{s['id']}]({s['url']}): {s['verified']} Unresolved: {s['unresolved']}")
    out+=['T3/L1 are a related family: T3 uses mmBERT, but its sentiment labels came from a Qwen teacher and L1 is Qwen. The extra T1-pair requirement remains in force. The rejected MuSe/VGGish replacement was not introduced because its metadata also conflicted. M1, V1 and V3 already have noncommercial terms; M3 permissions remain unresolved. These results are retained as research outputs, not a commercial-use license determination.']
    out+=['## Trust: random primary sections', 'A registered pass requires correlation at least 0.40 and the unchanged 10,000-shuffle repeat p below 0.01. Words additionally require a T1 pair at least 0.40. Trust here means model agreement and label consistency, not human emotion accuracy. Voice passes remain PROVISIONAL.']
    mapping={'words_valence':'text_valence'};trs=[];pairs=[]
    for ch,d in trust.items():
        v=d['overall'];before=old[mapping.get(ch,ch)];verdict='PROVISIONAL (registered pass)' if ch.startswith('voice_') else 'trusted' if v['trusted'] else 'not trusted'
        trs.append([ch,'trusted' if before['trusted'] else 'not trusted',verdict,fmt(v['median_rho']),fmt(v['repeat']['p']),v['repeat']['songs']])
        for pair,p in v['pairs'].items():pairs.append([ch,pair,fmt(p['rho']),p['n']])
    out+=[table(['Channel','Round 1','Round 2','Median rho','Repeat p','Repeat songs'],trs),table(['Channel','Model pair','Spearman rho','Sections'],pairs)]
    for ch in ('background_valence','background_arousal'):
        out.append(f"{ch}: correlation condition without M1–M3 = {trust[ch]['without_m1_m3_correlation_pass']}; repeat p remains {fmt(trust[ch]['overall']['repeat']['p'])}. Thus the registered trust verdict would {'still pass' if trust[ch]['without_m1_m3_correlation_pass'] and trust[ch]['overall']['repeat']['p']<.01 else 'not pass'} without that pair. This does not establish independence of the remaining models.")
    out.append('Words: the T1-pair condition passes. Words arousal (L1 and unchanged-text/language T2 references) remains low-confidence reference data, without a registered multi-model trust verdict.')
    out+=['### By language','All pairwise correlations, sample sizes, median/repeat tests and verdicts by song language are in `validation/trust.json` and `validation/trust_by_language.csv`. Small language groups and uncertain assignments limit interpretation; there is no new sample-size threshold.']
    langrows=[]
    for ch,d in trust.items():
        for lang,v in d['by_language'].items():
            verdict = ('PROVISIONAL numerical pass' if v['trusted'] else 'PROVISIONAL; not trusted / not evaluable') if ch.startswith('voice_') else ('pass' if v['trusted'] else 'not trusted / not evaluable')
            langrows.append([ch,lang,fmt(v['median_rho']),fmt(v['repeat']['p']),v['repeat']['songs'],verdict])
    out.append(table(['Channel','Language','Median rho','Repeat p','Songs','Verdict'],langrows))
    out+=['## Voice gate: disagreement and missed lyric sections',gate['definition'],gate['majority_definition']]
    grows=[]
    for cohort,d in gate['overall'].items():grows.append([cohort,d['windows'],d.get('both_active',0),d.get('detector_only',0),d.get('transcription_only',0),d.get('neither',0),fmt(d['agreement'])])
    out.append(table(['Cohort','Windows','Both active','Detector only','Transcription only','Neither','Agreement'],grows))
    ten=gate['seeded_ten'];c=ten['confusion']
    out.append(f"The registered seeded-ten comparison agreed in {c['both_active']+c['neither']}/{ten['windows']} windows ({100*ten['window_agreement']:.2f}%): detector-only {c['gate_only']}, transcription-only {c['note_only']}, both-active {c['both_active']}, neither {c['neither']}. MuScriptor is an imperfect comparator, not truth. The full per-song split, denominators and section counts are in `validation/voice_gate_audit.csv`/`.json`.")
    missed=[s for s in gate['songs'] if s['majority_sections_without_active_windows']]
    out.append(f"The detector finds zero active windows in most timed primary lyric sections for {len(missed)} songs (combined, not a random sample). This describes detector output, not absence of actual singing. All such songs follow; APT. and Breakin' Dishes are explicitly retained when they meet this rule.")
    out.append(table(['Track','Title','Cohort','Zero-active / timed sections','Agreement','Detector only','Transcription only'],[[s['track_id'],s['title'],s['cohort'],f"{s['sections_without_active_windows']}/{s['timed_primary_sections']}",fmt(s['agreement']),s['detector_only'],s['transcription_only']] for s in missed]))
    examples=[s for s in gate['songs'] if s['title'] in ('APT.',"Breakin' Dishes")]
    out.append('Requested examples are shown explicitly below. Zero-active sections and sections with mostly inactive windows are distinct descriptions; neither is a new scoring rule. In particular, Breakin\' Dishes does not meet the strict zero-active-primary-section majority condition, but its window coverage is poor. Complete lyric-section fractions, including secondary sections, are in `validation/voice_gate_sections.csv`.')
    out.append(table(['Title','Primary zero-active / timed','All timed lyric sections','All lyric zero-active','Lyric sections with most windows inactive'],[[s['title'],str(s['sections_without_active_windows'])+'/'+str(s['timed_primary_sections']),s.get('timed_lyric_sections','pending'),s.get('zero_active_lyric_sections','pending'),s.get('lyric_sections_with_majority_inactive_windows','pending')] for s in examples]))
    mostly_inactive=[s for s in gate['songs'] if s.get('timed_lyric_sections',0)>0 and s.get('lyric_sections_with_majority_inactive_windows',0)>s['timed_lyric_sections']/2]
    out.append(f"The following {len(mostly_inactive)} songs (combined sample) have a majority of all timed lyric sections in which a majority of windows were inactive according to pYIN. This broader coverage description includes partially active sections and does not establish whether singing was present; it does not change the gate or verdict.")
    out.append(table(['Track','Title','Cohort','Sections mostly inactive / all timed lyric sections','Zero-active / all timed lyric sections'],[[s['track_id'],s['title'],s['cohort'],str(s['lyric_sections_with_majority_inactive_windows'])+'/'+str(s['timed_lyric_sections']),str(s['zero_active_lyric_sections'])+'/'+str(s['timed_lyric_sections'])] for s in mostly_inactive]))
    out+=['### Voice arousal agreement may be an acoustic shortcut',f"V1–V3 arousal Spearman rho is {trust['voice_arousal']['overall']['median_rho']:.6f}. The following requested post hoc diagnostics use each model's raw section score and the existing voice-brightness, f0-median and RMS features on random primary sections. No new gate or verdict is derived.",ac['feature_source']]
    ar=[]
    for model,d in ac['correlations'].items():
        for dim,features in d.items():
            for feature,p in features.items():ar.append([model,dim,feature,fmt(p['rho']),p['n']])
    out+=[table(['Model','Dimension','Voice feature','Spearman rho','Pairwise-complete sections'],ar),ac['interpretation']]
    out+=['## Human listening check (post hoc)']
    listen=HERE/'review/voice_gate_listening.csv'
    if listen.is_file():
        with listen.open(encoding='utf-8-sig',newline='') as f:reviews=list(csv.DictReader(f))
        filled=[r for r in reviews if any(v and k not in ('track_id','title','cohort') for k,v in r.items())]
        out.append(f"The human listening file was present when this report was written: {len(reviews)} rows, {len(filled)} with a nonempty review field. SHA-256 `{hashlib.sha256(listen.read_bytes()).hexdigest()}`. This is post hoc, optional and not a random validation sample unless its own protocol establishes that. It changes no gate, threshold, registered test or verdict. Free-text notes are not copied into this report.")
        out.append(table(['Column','Nonempty responses'],[[k,sum(bool(r.get(k)) for r in filled)] for k in (reviews[0].keys() if reviews else []) if k not in ('title','track_id','notes','audio_path','path')]))
        categories=[]
        for k in (reviews[0].keys() if reviews else []):
            if not re.search(r'(sing|vocal|agreement|agree|detect|gate|verdict|rating|response)',k,re.I) or re.search(r'(note|comment|free|text)',k,re.I):continue
            vals=[r[k].strip() for r in filled if r.get(k) and r[k].strip()]
            if vals and len(set(vals))<=10 and all(len(v)<=40 for v in vals):
                for val in sorted(set(vals)):categories.append([k,val,vals.count(val)])
        if categories:out.append(table(['Review field','Response','Rows'],categories))
    else:out.append('Placeholder: `review/voice_gate_listening.csv` was not present when this report was written. No human listening result is claimed. A later summary must be labelled post hoc and must not change any gate, threshold or verdict.')
    out+=['## H1 — development robustness check, not confirmation','Only explicitly sourced Genius Verse and Chorus sections qualify. Pre/Post-Chorus are not counted as Chorus. The one-sided Wilcoxon test, 10,000-bootstrap interval and seeds are unchanged.']
    hr=[]
    for cohort,d in h.items():
        for kind in ('round2','round1_models','inferred_group_exploratory'):
            a=d[kind];hr.append([cohort+(' (not random)' if cohort!='random' else ''),kind,a['n'],fmt(a['median']),fmt(a['share_above_zero']),fmt(a['p']),str(a['bootstrap_95_ci'])])
    out.append(table(['Cohort','Variant','Songs','Median lift','Share >0','Wilcoxon p','95% bootstrap CI'],hr))
    out.append('The inferred comparison is likely-chorus paragraph groups versus other inferred groups, not Genius Chorus versus verified Verse. It is exploratory. Language-specific H1 outputs and the per-song differences are in `validation/h1.json`; the source cohort is carried through every per-song export. H2 and H3: **not run (precondition failed)** because background valence was not trusted. This is not a negative finding. No same-language H2 shuffle or song congruence profile is claimed when H2 is unrun.')
    out.append('### Development and exploratory comparisons by language')
    textlift=read(HERE/'validation/exploratory_text_chorus_lift.json')
    langlift=[]
    for label, groups in [('background arousal',h['random']['by_language']),('words valence (exploratory)',textlift['random']['by_language'])]:
        for lang,a in groups.items():
            langlift.append([label,lang,a['n'],fmt(a['median']),fmt(a['share_above_zero']),fmt(a['p']),str(a['bootstrap_95_ci'])])
    out.append('These are random-cohort subsets and are development/exploratory analyses, not independent replications. Language groups can be small, and uncertainty in assignment and multiple comparisons limit interpretation.')
    out.append(table(['Channel','Language','Songs','Median lift','Share >0','Wilcoxon p','95% bootstrap CI'],langlift))
    out+=['## Explanation checks for every completed channel','The estimator, five grouped folds, training-fold median imputation, missing indicators, scaling, alpha, features, coverage rule and permutation seeds are unchanged. `validation/explanation_checks.json` retains fold-level R², all four baseline fits and importance stability. `explanation_folds.csv` and `explanation_importance_stability.csv` expose every row. Negative-R² importance is retained only for audit, with no drivers presented.']
    for ch,d in ex.items():
        out.append('### '+ch)
        if d.get('status')!='completed':out.append(d['status']);continue
        full=d['full'];out.append(f"{d['n_sections']} random primary sections from {d['n_songs']} songs. Full out-of-fold R² = {full['oof_r2']:.6f}.")
        fr=[]
        for name,model in [('full',full)]+list(d['single_feature_baselines'].items()):fr.append([name,fmt(model.get('oof_r2'))]+[fmt(f['r2']) for f in model.get('folds',[])])
        out.append(table(['Fit','OOF R²','Fold 1','Fold 2','Fold 3','Fold 4','Fold 5'],fr))
        best=max((v.get('oof_r2',float('-inf')),k) for k,v in d['single_feature_baselines'].items())
        out.append(f"The best single-feature baseline is {best[1]} at R² {best[0]:.6f}; the full-minus-best gap is {full['oof_r2']-best[0]:.6f}. No equivalence test was registered, so the exact gap is reported rather than inventing a 'same performance' threshold.")
        if ch=='voice_valence':out.append('This provisional full fit is only modestly better in absolute R² than its best single feature; both explain little held-out variation. The baseline is roughly comparable in scale, not formally equivalent.')
        if ch=='words_valence':out.append('The best single-feature baseline is numerically better than the full model, but both have negative held-out R².')
        if full['oof_r2']<=0:out.append('The model does not explain held-out ratings better than the pooled mean baseline. **No drivers are presented.** Full diagnostic importance values remain in the audit JSON/CSV only.')
        else:out.append(table(['Leading feature','Mean held-out R² drop','Positive folds','Fold SD','Drops across all folds'],[[p['feature'],fmt(p['mean_r2_drop']),str(p['positive_folds'])+'/5',fmt(p['fold_std_r2_drop']),str(p['fold_r2_drops'])] for p in full['permutation_importance'][:8]])+'These are feature associations with model ratings, not causal effects or human-validated emotion drivers.')
    oldex=read(HERE/'explanation_phase1.json')
    out.append(f"The earlier round-one explanation repair reproduced R² {oldex['old']['oof_r2']:.4f} → {oldex['fix']['evaluation']['oof_r2']:.4f}; it targeted round-one consensus, so it is not the same target as round two. Round-two background-arousal R² is {ex['background_arousal']['full']['oof_r2']:.4f}.")
    out+=['## Data recovery, languages and inferred groups',f"Forty-one of 286 originally missing-text sections were recovered from validated LRCLIB; the remaining 245 had no usable additional cached-Genius-plus-WhisperX route (168 lacked HTML and 77 lacked retained word timings). No further Genius crawl/manual save was run. The 1,263 existing Genius labels remain separate from inferred paragraph groups.",
          f"Song languages use confidence-weighted section votes at the unchanged {language['threshold']} rule and supported-code list. Pilot validation before metadata correction: 13/16 correct, two uncertain, one incorrect; the mismatch was then marked uncertain and disclosed. The earlier re-detection changed 923 sections; `language_revision_audit.json` summarizes them. The later weighted assignment changed 1,889 sections relative to round one. This is not validation of every changed label.",
          'Language assignments, all uncertain songs and changes are in `language.csv`, `language_weighted_summary.json` and `language_revision_audit.json`. By-language trust uses revised song language; uncertain is retained as a category. Full inferred-group H1 and Genius-only sensitivity outputs are in `validation/h1.json`, never relabelled as confirmed sections. Additional exploratory text-valence chorus results and language counts by cohort are in `validation/exploratory_text_chorus_lift.json` and `validation/language_by_cohort.json`.']
    langcounts=read(HERE/'validation/language_by_cohort.json')
    out.append(table(['Cohort','Song language','Songs'],[[cohort+(' (not random)' if cohort!='random' else ''),lang,count] for cohort,d in langcounts.items() for lang,count in d['song_languages'].items()]))
    out+=['## Round 1 → round 2: what changed and why',table(['Item','Round 2 change','Interpretation'],[
          ['Words','T3 and L1 added; T1 rescored only where input changed','Stronger registered agreement, with T3/L1 related-family caveat'],
          ['Background','M1/M2 reused after hashes; M3 added','Arousal remains trusted; valence fails; shared data lineage unresolved'],
          ['Voice','V1 and V3 share the fixed pYIN gate','Numerical passes are PROVISIONAL; gate disagreement and acoustic shortcut remain'],
          ['H1','Same sample with amended model set','Development robustness, not independent confirmation'],
          ['H2/H3','Global preconditions evaluated','Not run; not negative findings'],
          ['Replication','Outside 325 scope','Deferred']])]
    out+=['## Computation and artifacts','All inference was local. The supervisor records M3 CPU wall time from 2026-11-17 21:44:58 to 2026-11-18 01:28:22 Pacific; V1 GPU-worker wall time 01:28:24–01:39:24; V3 GPU-worker wall time 01:39:24–02:39:27. These include overhead and are not utilization-measured GPU hours. T1/T3/L1 checkpoint inference seconds are in `validation/model_inventory.json`; they exclude some loading/verification and interrupted overhead. No exact total GPU-usage meter exists, so a full utilization total is not invented. The earlier single-input runtime estimates in `compute_estimate.json` are planning estimates only.',
          '`songs/<id>/song.json`, `sections.json`, and `curves.parquet` exist for every sample song; `songs.csv` and `sections.csv` are flat indexes. Missing values stay missing. Instrumentals without registered lyric-section inputs have empty section/curve exports rather than fabricated boundaries. Local per-section explanations and calibrated confidence were not measured; they remain unavailable. `SCHEMA.md` documents the output fields. Digest numbers come only from saved analysis artifacts and are source-linked. The digest is a local preview, not a site export.']
    out+=['## Open issues and anything needed from you',
          '- Optional: finish the separate human listening review of the voice gate. It will remain post hoc and will not alter the gate, tests or verdicts.',
          '- M3 commercial-use permissions and exact M1/M3 training-split overlap remain unresolved; author clarification or actual training manifests would be needed to settle them.',
          '- Voice singing-domain validity and possible shared acoustic shortcuts need independent human validation; these results do not establish human emotion accuracy.',
          '- The 245 unavailable lyric sections and uncertain languages remain missing/uncertain. Optional manual page saves can be done by you later; none were crawled here.',
          '- Replication, full-library analysis, production frontend work, and commercial deployment are outside this task. No full-library run was started and nothing was pushed.']
    (HERE/'REPORT.md').write_text('\n\n'.join(out)+'\n',encoding='utf8')
    print('Wrote REPORT.md',flush=True)
if __name__=='__main__':main()
