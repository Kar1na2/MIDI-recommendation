# Task K operational decisions — 2026-11-22

These decisions implement the original registration `e8127190` and Amendment 1
`7484df2`. Task K authorizes the previously deferred library work. No registered
model, revision, prompt, gate, window, threshold, normalization parameter or test is
changed. This note is committed before new scoring. No replication outcomes have
been inspected.

## Cohorts and eligibility

Process every library ID outside the exact 325 IDs in `mvp_325/summary.csv`.
The batch manifest contains only local-audio tracks; the separate missing-audio
inventory is included in counts and failures. Missing audio is not substituted.
Existing completed non-MVP caches are reused only with the existing provenance
checks. The library backend retains the existing v1/strict-meter profile and
Task B launcher/state, shortest songs first, with explicit existing failure and
stop conditions. Task K's emotion Pass 2 is distinct from piano-source routing;
the existing Task B backend's routing hold remains in place.

The original replication is a uniformly sampled **up to 300** eligible tracks,
seed **20261115**, sorted by track ID before sampling without replacement. The
full scored library and this confirmatory draw are separate populations. Draw
after backend/lyric eligibility is known, before emotion scoring. Eligibility
uses the exact original `select_replication.py` stage/status and lyric-section
criteria; it never uses emotion outcomes, label balance, or model success.
There is no replacement/resampling after a scoring failure. Report missing
scores and the final H1 qualifying count explicitly. The inherited empty
`emotion_r2/replication_sample.json` remains untouched; the Task K draw goes under
`emotion_r2/replication/`.

The plan does not exclude alternative versions of development tracks. Retain
them in the draw, flag same normalized artist and base title (removing bracketed
version text and spaced-dash suffixes) in `replication/near_duplicates.csv`, and
run the requested labelled sensitivity on the same draw without flagged IDs,
without replacement or redraw. These flags use metadata only. The registered
draw is used for confirmatory H1 only; descriptive library summaries use a
separately labelled all-new-song cohort, not the random draw as a new development
sample.

## Isolation, inputs and scoring

The fourth snapshot is `replication_development_snapshot.json`, covering all
existing files in completed round-2 `scores/`, `songs/`, `validation/`, `digest/`
and `REPORT.md`. Verify it and the three existing snapshots before scoring and
after completion. Never update a baseline to hide an integrity mismatch.
Keep development inputs, outputs, normalization and trust verdicts read-only.
New inputs, private lyric text, stems, model checkpoints and validation artifacts
go under `emotion_r2/replication/`. Per-ID display exports alone go into the
existing `emotion_r2/display/text_model_probs/` and `l1_readings/` directories;
the 325 IDs and their display manifests remain untouched.

Reuse computational functions from the registered implementations; adapt only
cohort selection, paths and orchestration. Bind source, audio, stem, private text,
section, prompt and model hashes in resumable checkpoints. A mismatched cache is
an explicit error, not a reason to silently recompute with different inputs.
Use the pinned Demucs vocals/no-vocals procedure on original audio for emotion
background stems, and the verified backend vocal stem for the unchanged pYIN
gate and V1/V3. Run GPU workers sequentially. Missing instrumental lyric
sections do not create synthetic emotion sections.

Apply the final Amendment 1 confidence-weighted primary text-section language
vote, supported-code list and script-conflict rule. Retain Genius labels with
their provenance separately from paragraph-derived inferred groups. Recover
text only through the same cached timed LRCLIB/WhisperX/Genius provenance rules;
unrecoverable text stays missing. The backend may use its existing acquisition
queue; no extra round-2 Genius crawl or manual-save campaign is added.
T1/T3 use the same 512-token truncation; L1 uses the registered prompt hash and
greedy decoding. No round-one text or frame score exists for new songs, so all
eligible new sections are scored with the same registered models.

Apply saved development `validation/normalization.json` unchanged, checking its
hash. Never fit normalization on new songs. Missing values remain missing.
Display-only probabilities must reproduce registered valence within 1e-5, and
L1 display readings retain their separate prompt, overlap filter, and explicit
"L1's interpretation of the lyrics" notice. They do not affect registered scores.

## Freeze and analysis barrier

No H1, chorus/verse contrast, aggregate effect, or interim significance is
computed or printed until every eligible song has terminal checkpoints,
coverage/errors and input hashes have been verified, and the scored manifest
has been frozen **and committed**. The analysis entry point must check the
committed freeze and all its hashes. Failures are retained in the freeze.

H1 uses only Genius-labelled Chorus and Verse primary sections, excluding
Pre-/Post-Chorus and inferred groups: per-song mean Chorus minus mean Verse
background-arousal consensus; one-sided Wilcoxon greater, alpha .01; median,
positive share and 95% bootstrap CI, 10,000 resamples, seed **20261116**.
No development trust re-decision; H2/H3 remain not run (precondition failed),
which is not a negative result. Voice verdicts stay PROVISIONAL. Shared-data,
license and related-family caveats remain in force.

Report new-song correlations, gate agreement and fixed-method explanation fits
as descriptive comparisons alongside development values, with no new trust
verdict. Explanation cross-validation follows the existing registered method,
including fold-level R², single-feature baselines and importance stability.
`analysis_v2/ANALYSIS_PLAN.md` has no replication section; new lyric/rhythm
alignment measurements are therefore exploratory, not a registered replication.

## Supervision and compute accounting

Use existing Task B `library/progress.json`, launcher, checkpoints, timeouts,
OOM/EOS retry and failure stop conditions. Keep its Windows scheduled task
disabled and launch manually. A non-admin SetThreadExecutionState helper keeps
the current session awake without changing persistent system settings. A power
loss/shutdown cannot be prevented; resumable checkpoints preserve progress.
Unexpected worker exit is recorded; deliberate stop or failure-rate stop is
never automatically overridden. Progress lines every few hours contain only
stage counts, failure categories and compute-based ETA, never interim effects.

Measure storage and stage durations from the actual 325 artifacts and logs;
report cache hits, unlogged stages and incomplete timing coverage explicitly.
Do not present synthetic timing estimates as measured utilization. Commit code,
notes, freeze and reports with explicit task paths and a staged-path allowlist;
never push, rewrite history or touch docs/pages/front-end work.
