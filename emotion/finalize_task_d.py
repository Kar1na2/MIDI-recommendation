"""Add registered per-frame consensus curves and finish Task D documentation.

This reads public Task D outputs only, never the private lyric directory. It
does not refit models or change trust thresholds. It is safe to rerun.
"""
import csv
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from stems import library_active

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent

def load(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def save(path,obj):
 path=Path(path); temp=path.with_suffix(path.suffix+'.tmp')
 temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8');temp.replace(path)
def series(df,name):
 return pd.to_numeric(df[name],errors='coerce') if name in df.columns else pd.Series(np.nan,index=df.index,dtype=float)
def standardize(values,param):
 if not param or not param.get('std'): return pd.Series(np.nan,index=values.index,dtype=float)
 return (values-param['mean'])/param['std']

def curves(df,params):
 feature_params=params['v2_features']; model_params=params['section_model']
 f={name:standardize(series(df,'voice_'+name),feature_params.get(name)) for name in
    ('brightness_hz','f0_median_hz','rms_variance','rms_mean','f0_range_semitones','voice_note_onset_rate_hz')}
 v2v=(f['brightness_hz']+f['f0_median_hz']-f['rms_variance'])/3
 v2a=(f['rms_mean']+f['rms_variance']+f['f0_range_semitones']+f['brightness_hz']+f['voice_note_onset_rate_hz'])/5
 df['V2_valence_z']=standardize(v2v,model_params.get('V2_valence'))
 df['V2_arousal_z']=standardize(v2a,model_params.get('V2_arousal'))
 for dim in ('valence','arousal'):
  df['background_'+dim+'_consensus_z']=(series(df,'M1_'+dim+'_z')+series(df,'M2_'+dim+'_z'))/2
  df['voice_'+dim+'_consensus_z']=(series(df,'V1_'+dim+'_z')+series(df,'V2_'+dim+'_z'))/2
 df['text_valence_consensus_z']=(series(df,'T1_z')+series(df,'T2_valence_z'))/2
 return df

SCHEMA_DETAIL='''

## Field reference

The unit and range of a nullable field describe non-null values. No value is substituted for unavailable input.

| Field or field family | Unit / range | Computation or provenance |
|---|---|---|
| `track_id`, `section_id` | identifiers | Cohort manifest ID and deterministic lyric/span index; `instrumental-whole` is a whole-track background span. |
| `title`, `artist`, `label`, `display_label` | text metadata | Title/artist from cohort manifests; section names from cached lyrics stage. No lyric lines are exposed. |
| `cohort` | `random` or `pilot` | Membership in the 300-song random sample or 25-song nonrandom pilot. |
| `language` | language code or null | `analysis_v2/language.csv`; absence stays null. |
| `ring1_source`, `timing_source`, `text_source` | source names | Recorded backend lyric/timing provenance; missing text is not replaced. |
| `start_s`, `end_s`, `duration_s`, `*_end_s` | seconds, nonnegative | Raw unsnapped lyric boundaries, frame bounds, or cached audio duration. |
| `set`, `validated` | primary/secondary/instrumental; boolean | Primary requires validated, increasing raw times. Secondary covers unaligned/vocal-activity spans. |
| `line_count`, `line_no`, `text_sha256` | count/index/hash | Number/index of attributed lyric lines and SHA-256 of normalized text; content remains private. |
| `text_scores.T1`, line `T1` | [-1,1] | Sentiment model P(positive) − P(negative). |
| `T2_valence`, `T2_arousal` | cosine difference [-2,2] | E5 cosine to the registered positive/high anchor minus negative/low anchor. Text arousal is low-confidence. |
| `*_truncated`, `status`, `feature_source_status` | boolean or status | Explicit model truncation, completion, or missing-source status. |
| `M1_*`, `M2_*`, `V1_*` | model-native real scores | Pinned Essentia, CLAP prompt difference, and dimensional speech model outputs; no hard bound is assumed for regression outputs. |
| `mean`, `median`, `std` | model-native units; std ≥ 0 | Summary of finite 4 s windows in that section; standard deviation is population standard deviation. |
| `available_frames`, `possible_frames`, `frame_share` | counts and fraction [0,1] | Finite windows divided by valid possible windows. `excluded_zero_length_frames` counts zero-coverage rounding artifacts omitted from the denominator. |
| `instrumental_coverage`, `mix_coverage`, `voice_coverage`, `m1_coverage`, `m2_coverage` | fraction [0,1] | Actual samples in a four-second padded window divided by the window length. |
| `voice_active` | boolean | At least 20% of 100 ms vocal-stem RMS blocks exceed −40 dBFS. |
| `*_z`, `model_z`, `*_consensus` | unitless real values | Each model is standardized using primary random-section mean and population SD; consensus is the mean of two available model z-scores. Frame curves use those same section-based parameters. |
| `*_agreement_abs` | unitless ≥ 0 | Absolute difference of the two within-channel model z-scores. |
| `V2_valence_z`, `V2_arousal_z` | unitless real values | Fixed registered prosody composites, standardized using random-primary section parameters; missing components give null. |
| `voice_f0_median_hz`, `voice_f0_range_semitones` | Hz > 0; semitones ≥ 0 | pYIN voiced-pitch median and range in each active window. |
| `voice_voiced_ratio` | fraction [0,1] | Share of usable f0 frames voiced in an active window. |
| `voice_vibrato_rate_hz`, `voice_vibrato_extent_semitones` | 3–8 Hz; semitones ≥ 0 | Band-limited voiced-f0 oscillation rate and half the 5th–95th percentile extent; null when insufficient. |
| `voice_rms_mean`, `voice_rms_variance` | linear digital amplitude ≥ 0 and amplitude² ≥ 0 | Mean/variance of active vocal-stem RMS blocks. |
| `voice_brightness_hz` | Hz ≥ 0 | Mean vocal spectral centroid. |
| `voice_voice_note_onset_rate_hz` | note onsets/second ≥ 0 | MuScriptor voice-track onsets in the window divided by available window duration; null if no voice track was emitted. |
| `relations.*.relation` | supports / contradicts / neutral/ambiguous / null | Registered ±0.5 sign rule on the pair's section consensus; primary relation withheld when either channel is untrusted. |
| `left_z`, `right_z`, `delta_z` | unitless real values | Channel consensuses and left-minus-right delta. |
| `confidence` | [0,1] or null | Minimum channel frame coverage × exp(−mean absolute within-channel disagreement); descriptive, not a probability. |
| `profile`, `duration_weighted_shares`, `scored_sections` | category; fractions [0,1]; count | Duration-weighted primary section relations: ≥60% supports congruent, ≥40% contradicts contrasting, otherwise mixed; fewer than 3 scored sections flat; no trusted relation unavailable. |
| `strongest_contrast` | section object or null | Available contradictory section with greatest absolute pairwise z difference. |
| `tempo_bpm`, `onset_density_total`, `onset_density__<instrument>` | beats/min; note onsets/second ≥ 0 | Cached beat BPM and local quantized onsets divided by raw section duration. |
| `off_strong_beat_share` | fraction [0,1] | Share of section's quantized onsets off quarter-note strong grid positions. |
| `median_register_midi`, `key_strength`, `major_mode_score` | MIDI note 0–127; correlation [−1,1]; 0 or 1 | Median transcribed pitch and best Krumhansl-style pitch-class profile correlation; 1 denotes estimated major mode, 0 minor. |
| `chord_layer_onset_rate` | onsets/second ≥ 0 | Distinct instrument/bar/step chord-layer events inside the raw section divided by duration. |
| `pattern_compression_ratio`, `pattern_repetition_rate` | ratio ≥ 0; fraction [0,1] | Aggregates of cached rhythmic sections overlapping the lyric span; repetition uses regular bars minus runs divided by regular bars. |
| `instrumental_rms`, `instrumental_brightness_hz` | linear amplitude ≥ 0; Hz ≥ 0 | Raw section instrumental-stem RMS and mean STFT spectral centroid. |
| `features.voice_*` | model-specific units | Section means of the corresponding active-frame prosody features listed above. |
| `top_reasons`, `contribution` | signed model-score z units | Three largest absolute standardized feature contributions from the registered full-sample Ridge model. They explain model ratings, not ground truth; interpret only with grouped CV fit quality. |
| `trusted`, `rho`, `repeat_consistency.p` | boolean; correlation [−1,1]; probability [0,1] | Trusted only when model Spearman ρ ≥ 0.40 and repeated-label permutation p < 0.01. |
| `grouped_cv_r2`, `mean_r2_drop` | unitless, potentially negative | Grouped-by-song out-of-fold R² and held-out permutation drop; negative R² means worse than a mean predictor. |
| `plan_commit` | Git commit hash | Immutable preregistration and model manifest binding. |
'''

def main():
 if sys.executable!='/home/usr/venvs/midi/bin/python': raise RuntimeError('Wrong interpreter')
 if library_active(): raise RuntimeError('Library batch active; defer CPU/IO-heavy finalization')
 if not (HERE/'REPORT.md').is_file() or not load(HERE/'validation'/'integrity_end.json').get('passed'):
  raise RuntimeError('Task D report and end audit must be complete first')
 params=load(HERE/'validation'/'normalization.json'); trust=load(HERE/'validation'/'trust.json')
 with (ROOT/'mvp_325'/'summary.csv').open(encoding='utf-8-sig',newline='') as f: songs=list(csv.DictReader(f))
 if len(songs)!=325: raise ValueError('Expected 325 cohort songs')
 frame_count=0; separation_seconds=[]
 for i,song in enumerate(songs,1):
  if library_active(): raise RuntimeError('Library batch started; finalization stopped safely')
  track=song['track_id']; directory=HERE/'songs'/track; path=directory/'curves.parquet'
  df=pd.read_parquet(path,engine='pyarrow'); df=curves(df,params); frame_count+=len(df)
  tmp=path.with_suffix('.parquet.tmp'); df.to_parquet(tmp,index=False,engine='pyarrow'); tmp.replace(path)
  data=load(directory/'song.json'); data['dimension_trust']={name:bool(entry['trusted']) for name,entry in trust.items()}
  save(directory/'song.json',data)
  sec=load(HERE/'stems'/track/'separation.json').get('wall_seconds')
  if isinstance(sec,(int,float)) and math.isfinite(sec): separation_seconds.append(sec)
  if i%50==0: print(f'consensus curves {i}/325',flush=True)
 stats={'songs':len(songs),'frame_rows':frame_count,'separation_timed_songs':len(separation_seconds),
        'separation_total_wall_hours':sum(separation_seconds)/3600,
        'separation_mean_wall_seconds':sum(separation_seconds)/len(separation_seconds) if separation_seconds else None,
        'pandas_version':pd.__version__}
 save(HERE/'validation'/'final_qa.json',stats)
 schema=HERE/'SCHEMA.md'; text=schema.read_text(encoding='utf-8')
 marker='## Field reference'
 if marker not in text: schema.write_text(text.rstrip()+SCHEMA_DETAIL+'\n',encoding='utf-8')
 report=HERE/'REPORT.md'; content=report.read_text(encoding='utf-8')
 marker='## Further validation details'
 if marker not in content:
  detail='''

## Further validation details

The background-arousal explanatory fit has strongly negative grouped cross-validation R² (−7.94): it predicted substantially worse than a mean predictor. Its reported feature importances and section contributions are unstable model descriptions and should not be presented as reliable drivers of emotion. The word/background shuffle result is exploratory because both valence channels failed the preregistered model-agreement criterion; it is not evidence of validated lyric/music congruence.

Initial and final snapshot audits both passed: v1 7,849/7,849 unchanged and MVP-325 1,097/1,097 unchanged. Model-agreement correlations by language and the permutation-control results are in `validation/trust.json` and `validation/shuffle_control.json`. Per-song artist and duration provenance is recorded in `validation/metadata_enrichment.json`.

| Preregistered probe | Expected direction | Observed |
|---|---|---|
| YOASOBI — Yoru ni Kakeru | contrasting | Local audio not found; not scored |
| Foster the People — Pumped Up Kicks | contrasting | Local audio not found; not scored |
| OutKast — Hey Ya! | contrasting | Local audio not found; not scored |
| Mark Ronson ft. Bruno Mars — Uptown Funk | congruent | Local audio not found; not scored |
| Adele — Someone Like You | congruent | Local audio not found; not scored |

Published sources supporting the expected directions are linked in `ANALYSIS_PLAN.md`. The alternate 24K Magic candidate was also absent. See `validation/final_qa.json` for aggregate separation runtime and frame counts. Scores and all agreement tests are model-based; no human labels have been supplied.
'''
  report.write_text(content.rstrip()+detail+'\n',encoding='utf-8')
 print(json.dumps(stats,indent=2))

if __name__=='__main__': main()
