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

## Amendment 1 — 2026-11-17 (325-song scope)

This amendment supersedes conflicting provisions above without changing the original text. Original plan commit: `e8127190bd82e2b631bb1e749874e73308e45a2c`. The corrected manifest is `emotion_r2/models_amended.json`, SHA-256 `835ec85443b5fcf0448c5dee494188338f2374e2ee088fbcfa3129a0cdcf2a12`; the original manifest remains unchanged. No threshold below has been lowered.

### Disclosures and scope

The original preregistration commit accidentally included 1,937 unrelated deletions; see `docs/REPO_STATE.md`. They have not been rewritten. Before this amendment, actual-song development checks scored three tracks with T3 and L1 (`01A9Xt0XyeYIzC9K7MSvua`, `01prGWjCTxKBxJc400zwvQ`, `03EdQABuvf0suFxxbqdYBl`) and two of those same tracks with M3 (the first two IDs). Thus there were five model-song checks but three distinct songs. They are rescored under this amendment like the rest; they are not held out. Round 1 has already established its background-arousal trust result and observed a chorus lift on these same songs.

The only sample in this amendment is the 325 IDs in `mvp_325/summary.csv`, carrying `cohort` throughout. The random 300 is the primary sample; pilot and combined results are labelled not a random sample. The original replication section is deferred and is not part of this task. H1 on these songs is a **development robustness check, not confirmation**; H2 and H3 are development results. No new songs, library jobs or other cohorts are selected, started, scheduled or scored.

Phase 1 findings: 41 of 286 originally missing-text sections were recovered from validated LRCLIB timing; of the 245 still missing, 168 lacked cached Genius HTML and 77 had cached HTML but no retained WhisperX word timings. No text was inferred from section timing alone. Existing 1,263 Genius labels remain separate from paragraph-based inferred groups; no further Genius crawl is used. Language votes are per primary text section, weighted by the local classifier confidence; the inherited 0.8 confidence requirement and supported-code list are applied. Script conflicts or unsupported codes are uncertain. Against 16 known-language pilot tracks, the pre-correction comparison had 13 correct, two uncertain, and one disagreement; the disagreement was subsequently marked uncertain based on the known metadata, with the original error retained in the validation record. The earlier re-detection changed 923 sections; the new method changes 1,889 sections relative to round one. These are not independent human validations of every section.

### Model provenance and trust

Essentia's proposed MuSe VGGish replacement has downloadable weights, a VGGish embedding and a published CC BY-NC-SA model license, but its own metadata describes DEAM and MusiCNN while other fields say MuSe and VGGish. That internal conflict prevents verification of both the actual dataset and backbone required for replacement. Keep M3 with `possible_shared_training_data: true`: its MERT backbone differs, but overlap between its DEAM/MediaEval training tracks and M1's training data is unresolved. The original M3 manifest license says CC-BY-NC-4.0 while its model card says Apache-2.0; underlying permissions remain unresolved, and use is conservatively noncommercial pending clarification. See https://essentia.upf.edu/models/classification-heads/muse/muse-audioset-vggish-2.json, https://essentia.upf.edu/models.html and https://huggingface.co/huyleit/mert-v1-95m-music-emotion-int8.

T3's backbone is mmBERT, not XLM-R. T3 and L1 are `related_family`: T3 labels came from a Qwen teacher, and L1 is a Qwen model. Words-valence trust requires both the median of T1/T3/L1 pairwise Spearman correlations at least 0.40 and at least one T1 pair at least 0.40, plus the original repeat p < 0.01. Report all pairs. Background trust uses the median pairwise Spearman correlation across M1, M2 and M3, plus the repeat test; report all pairs and whether the correlation condition would hold when the M1–M3 pair is removed. The repeat test, relation |z| >= 0.5, round-one profile rules and all other registered thresholds remain unchanged. H2 and H3 run only when their specified channel preconditions hold; otherwise report not run.

### Voicing, computation and normalization

Replace the RMS activity proxy with pYIN voiced probability >= 0.5; a four-second window is active when at least 20% of frames meet that probability, counting zero padding as unvoiced. Both V1 and V3 are rescored using the same saved pYIN gate and the same registered windows. A seeded draw (`20261117`) of ten vocal sample songs with usable timed primary sections was compared with MuScriptor voice-note activity: 745 of 1,421 windows agreed (52.43%; `voice_gate_validation.json`). MuScriptor is not ground truth. Gate comparison is not a trust or hypothesis test and does not change a threshold. The corrected V3 vocal path must be tested on three real sample songs after this amendment is committed; stop and repair if values are constant or nonfinite.

Reuse M1 and M2 round-one frame scores only after verifying each source stem, source input and original section hash. Rescore T1 only for sections whose text hash or language differs; otherwise verify and reuse its round-one input and score. Run T3, L1, M3, V1 and V3 sequentially by model, with resumable per-song checkpoints; L1 uses the original fixed prompt hash, greedy decoding and seed. Missing scores remain missing. Compute each model/dimension normalization mean and population SD on primary random-300 sections only; record parameters before applying them to pilot and combined rows.

For planning, 2,207 section texts and at most 70,971 section windows are present; 881 text sections changed for T1. Synthetic single-input measurements, linearly extrapolated and highly uncertain, give T1 ~0.65 GPU h (T3 rate as proxy), T3 ~1.63 GPU h, L1 ~0.83 GPU h, M3 ~4.84 CPU h, V1 ~2.71 GPU h and V3 ~4.65 GPU h; pYIN ~1.53 CPU h from ten-song timing, and M1/M2 inference is reused (hash reading excluded). The voice estimates assume the ten-song active-window share of 27.59%; longer inputs, cold-start costs, hardware contention, missing stems, secondary sections and errors can change actual times considerably. See `compute_estimate.json`; these are estimates, not measured full-run throughput.
