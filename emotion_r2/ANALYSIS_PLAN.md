# Task F round-2 analysis plan — registered before any round-2 scoring

This plan is committed before any correlation, agreement or effect statistic is computed on round-2
scores. The only round-2 model work done before this commit was the permitted runtime smoke tests on
synthetic inputs (`smoke_T3.json`, `smoke_L1.json`, `smoke_M3.json`, `smoke_V3.json`), none of which
touched song data.

The pinned model manifest is `emotion_r2/models.json`, SHA-256
`bab8f1a06d8232a62a0e01bd61ea0991725a713d402498b2da53043615d52fae`. Every report quotes both that hash
and this plan's commit hash.

Round 1 under `emotion/` is frozen and read-only. Phase 1 data fixes described below were completed
before this commit and are inputs to the plan, not results of it. They changed no threshold.

## Scope and inputs

Sections come from the existing pipeline `report.json` joins. A primary section is a lyric-timed section
of a vocal song. Windows, coverage rules and missingness handling are unchanged from round 1: 4 s windows
at 1 s hop starting at the section start, the final window clipped to the section and zero-padded, with the
fraction of real audio recorded. A voice window is active only when at least 20% of its 100 ms RMS frames
exceed -40 dBFS on the vocal stem. Model errors and nonfinite values are explicit missing statuses and are
never imputed.

Lyric text lives only under `emotion_r2/private/`, which is git-ignored. No lyric text is sent to any
external service; every model runs locally.

## Samples

**Development sample:** the 325 MVP songs, with the Phase 1 fixes applied. Channel trust is decided here
and nowhere else.

**Replication sample:** up to 300 songs drawn uniformly without replacement, seed 20261115, from processed
library songs that are not among the 325 and that have both Pass 1 outputs and lyric sections. Selection is
`select_replication.py`, which sorts eligible tracks by track_id before sampling so the draw is reproducible.
If fewer than 300 qualify, all qualifying songs are used and the count is reported. The replication sample is
used only for the confirmatory hypotheses H1-H3 and for nothing else.

Replication songs are prepared exactly like the 325: instrumental stem via the pinned Demucs revision with
`--two-stems vocals`, the same section definitions, and the same text handling.

## Models and channels

| Channel | Dimension | Models counted for trust | Excluded |
|---|---|---|---|
| Words | valence | T1, T3, L1 | T2 (reference only) |
| Words | arousal | L1, plus T2 e5 anchors reported as low-confidence | — |
| Background | valence, arousal | M1, M2, M3 | — |
| Voice | valence, arousal | V1, V3 | V2 (explanation-only) |

T3 is `Horizon-Labs/multilingual-sentiment-base`, a supervised multilingual sentiment model with training
data distinct from T1. It replaces the zero-shot e5 anchors in the words-valence trust decision.

M3 is `huyleit/mert-v1-95m-music-emotion-int8`, a DEAM-trained regressor on a MERT backbone. Its dataset and
backbone both differ from M1 (emoMusic on MusiCNN) and M2 (CLAP HTSAT), so it is not flagged
`shared_backbone`.

V3 is `MERaLiON/MERaLiON-SER-v1`, trained on corpora distinct from V1's MSP-Podcast. It emits native
valence/arousal/dominance, so no categorical-to-dimensional mapping is needed; dominance is recorded and
unused.

### L1 setup (fixed now)

- **Input:** the section's lyric text only. No title, artist, album or any song metadata, so the model rates
  the text rather than recalling a song it may know.
- **Prompt:** one fixed English prompt covering all languages, stored verbatim in `emotion_r2/l1_prompt.txt`.
  Its SHA-256 is recorded in the scoring output and quoted in the report.
- **Output:** strict JSON with `valence` and `arousal` in -1…1 and a boolean `uncertain`. Unparseable output
  after one re-read of the same deterministic generation is recorded as missing, never as a neutral score.
- **Decoding:** temperature 0, greedy, fixed seed 20261115, no few-shot examples drawn from this dataset.

## Normalization and consensus

For each channel, model and dimension separately, the mean and population standard deviation are computed over
all available primary development-sample sections, without duration weighting. Those fixed parameters are then
applied to the replication sample and to every secondary table. A zero SD or no observations makes that score
and everything depending on it unavailable.

Consensus is the arithmetic mean of the available models' z-scores for that channel and dimension. A missing
model is never imputed as the consensus.

## Trust (unchanged thresholds)

- Spearman rho >= 0.40
- repeat consistency p < 0.01
- relation |z| >= 0.5
- profile rules exactly as in round 1

For a channel/dimension with two models, trust requires their pairwise Spearman rho >= 0.40 on primary
development sections where both are present. For a channel/dimension with three models, trust requires the
**median pairwise rho >= 0.40**. Every pairwise rho is reported regardless.

Both forms additionally require the round-1 repeat test: within-song mean absolute differences in consensus z
for same-label versus different-label section pairs, averaged with equal weight per song, with labels permuted
within each song 10,000 times at seed 20261112 and p = (1 + null count >= observed) / 10001. Unlabeled sections,
`inferred_group` sections and songs lacking both pair types are omitted. Not evaluable is not trusted.

Trust verdicts are reported overall and by language.

## Hypotheses

All three confirmatory tests run on the replication sample only.

**H1, the chorus lift.** Background-arousal consensus is higher in Chorus than in Verse within the same song.
Only Genius labels count; Pre-Chorus, Post-Chorus and `inferred_group` are excluded. A song qualifies when it
has at least one scored Chorus section and at least one scored Verse section. Test: one-sided Wilcoxon
signed-rank on per-song (chorus mean - verse mean), alternative greater, alpha = 0.01. Report the median
difference, the share of songs above 0, and a 95% bootstrap CI from 10,000 resamples at seed 20261116.
This hypothesis came from round-1 exploratory data (151 songs, 72% above 0), so it is confirmatory only here.

**H2, words versus background.** Runs only if both words valence and background valence are trusted on the
development sample. Within-song Spearman between section text valence consensus and background valence
consensus, with the same-language shuffle control at 10,000 shuffles, seed 20261113, alpha = 0.01. Report the
relation shares (supports / neutral / contradicts) under the round-1 |z| >= 0.5 rule.

**H3, the text-misses-it ablation.** Runs only if words, voice and background valence are all trusted. The
round-1 definition applies unchanged: among sections where voice and background agree in sign with each |z| >=
0.5, the share where words take the opposite sign with |z| >= 0.5. This measures disagreement with a
model-based audio consensus, not error against human truth.

If a hypothesis's precondition fails, it is reported as not run. An unrun test is never reported as a negative
finding.

**Secondary, all labelled exploratory:** chorus - verse difference in text valence; H1 repeated on the
development sample; H1 using `inferred_group` labels; every hypothesis broken down by language.

## Explanation model

Features are the Phase 1 rebuild: instrument onset densities collapsed into the eight families (drums, bass,
keys, guitar, strings, brass/wind, synth, voice), any feature below 5% non-null coverage dropped,
training-fold median imputation with missing-value indicator columns, and standardization inside each
training fold. Ridge alpha = 1, five-fold cross-validation grouped by song. Out-of-fold R-squared is reported
with fold-level values, permutation-importance stability across folds, and single-feature baselines for
loudness, tempo, total onset density and brightness. The model is fit only for trusted channels. If R-squared
is at or below 0, that is stated plainly and no drivers are presented.

## Integrity and outputs

`emotion_r2/r1_snapshot.json`, `analysis_v2/v1_snapshot.json` and `mvp_325/snapshot.json` are verified before
the work and again at the end, each reported as "unchanged: N/N". All new artifacts are written under
`emotion_r2/`. Round 1, `outputs/`, `mvp/`, `mvp_325/`, `analysis_v2/`, `pilot/` and `library/` are read-only
to this task.

No threshold, model, prompt or test in this plan may change after round-2 results are seen.
