# Repository state after Task G

## Before you push

The cleanup was performed locally. The branch at the start was `codex/rhythm-vocal-preview` at `e8127190`; it had no upstream, so upstream ahead/behind is undefined. Against the **last fetched** refs, it was 28 ahead and 0 behind `origin/main`, and 6 ahead and 0 behind `origin/pages`. These are local comparisons, not proof of current remote or deployed state. The index was empty initially. The working tree contained many pre-existing modifications, deletions and untracked files; those changes were preserved. `.gitignore`, `README.md` and `Makefile` already had local edits and their current content was used as the basis for this refresh.

The following six commits are not reachable from any locally known remote-tracking ref (`git rev-list HEAD --not --remotes`). Largest additions show byte sizes; modified files are not counted as additions.

| Commit | Subject | Files | Insertions / deletions | Largest added files |
|---|---|---:|---:|---|
| `e8127190` | Preregister emotion round-2 analysis | 1,943 | 1,487 / 339,930 | `emotion_r2/models.json` 41,237 B; `emotion_r2/ANALYSIS_PLAN.md` 8,522 B; `emotion_r2/build_models_manifest.py` 7,686 B; `emotion_r2/l1_prompt.txt` 1,001 B; `emotion_r2/smoke_V3.json` 194 B; `emotion_r2/models_sha256.txt` 65 B |
| `234f010f` | Bind preregistration to pinned models | 2 | 41 / 0 | no added files |
| `b5cdbc8d` | Bind preregistration to pinned models | 2 | 304 / 0 | `emotion/models.json` 10,371 B |
| `1a086f40` | Preregister emotion congruence analysis | 1 | 74 / 0 | `emotion/ANALYSIS_PLAN.md` 15,034 B |
| `9630443f` | Preregister isolated MVP v2 analysis | 1 | 120 / 0 | `analysis_v2/ANALYSIS_PLAN.md` 7,342 B |
| `eb8d8914` | Document legacy pipelines and preserve their previously untracked source | 55 | 12,586 / 0 | `docs/LEGACY_INVENTORY.json` 292,888 B; `scripts/midi_pipeline/phase3c.py` 62,721 B; `scripts/midi_pipeline/review_workbook.py` 49,283 B; `scripts/rhythm_layers/diagnose_other.py` 37,670 B; `scripts/midi_pipeline/phase3.py` 33,360 B; `scripts/midi_pipeline/phase3c.js` 31,045 B; `scripts/midi_pipeline/test_pipeline.py` 25,538 B; `scripts/midi_pipeline/phase3b.py` 25,498 B; `scripts/rhythm_layers/patterns.py` 20,676 B; `scripts/rhythm_layers/build_preview.py` 19,728 B |

### Decision on the 1,937 deletions in `e8127190`

The commit deleted 1,933 `docs/data/` paths and four `output/analysis/` paths from Git. All 1,937 exist locally on disk. Both directories were ignored in the pre-existing working-tree `.gitignore` at the start of cleanup; however, the committed `.gitignore` in `e8127190`'s parent did not ignore `docs/data/` and explicitly re-included `output/analysis/*.md`. The last fetched `origin/pages` uses `docs/index.html` and `docs/js/app.js`, which load `docs/data/manifest.json`, `docs/data/sequences.json` and per-song files. The local replacement `docs/index.html`/`docs/app.mjs` uses `docs/app-data/`; `docs/RETIRED_PAGES.md` describes the older pages as retired. No live Pages configuration or live deployment was verified. Pushing this history to a branch alone changes the live site only if that branch is the configured Pages source or later becomes its source; deploying a tree with old frontend code but without its data can break the old site.

**Recommendation:** verify the actual Pages source and deployment first and preserve the old data until the replacement is deployed and verified. Options below are examples, not commands run during cleanup. The restore stages the historical Git blobs without replacing the current local data; review the staged diff and local differences before committing. The second command explicitly handles ignored paths through `git restore`, not `git add`.

Option 1 — make a new restoring commit, preserving history:

```sh
git restore --source=e8127190^ --staged -- docs/data output/analysis
git diff --cached --name-status
git commit -m "Restore historical site data and analysis reports"
```

Option 2 — accept the deletions after confirming the published site uses the replacement and that the historical material is safely retained: make no restoring commit and push only the intended reviewed branch. Do not remove the local files. The commands below illustrate publishing this named branch; they do not select or modify the Pages source.

### Secret and size checks

A masked, local scan of tracked working-tree text and all six commit trees checked Hugging Face token shapes, credential-assignment shapes, and literal values read from local `.env`, `.cache` and `.spotify-cache*` files. No matches were reported. No `.env`, `.cache` or `.spotify-cache*` filename was tracked in HEAD or the six audited trees. This is not proof that every possible secret pattern was detected. Values were never printed. No tracked HEAD file and no file newly added by those six commits exceeded 25 MB; therefore none exceeded 100 MB under that test. Historical blobs outside those commits were not a full-repository history scan.

## Scheduled task

`MIDI-Recommendation-Library-Pass1-20261111` was Ready and enabled, with a placeholder 1999 last-run time, before cleanup. It was disabled using `schtasks.exe /Change /TN MIDI-Recommendation-Library-Pass1-20261111 /DISABLE`. The subsequent query showed Disabled. It was not run or deleted; start the library work manually later when ready.

## What moved

The following root paths now live in `docs/history/`: `LIKED_SONGS_IMPORT_NOTES.md`, `AMT_MIGRATION_AUDIT.md`, `AMT_RESEARCH.md`, `MIDI_FIRST_MIGRATION.md`, `PHASE3_TRANSCRIPTION_VALIDATION.md`, `PHASE3C_TRANSCRIPTION_INSPECTION.md`, and `Screenshot 2026-11-05 021850.png`. The untracked originals were explicitly added and moved, and the tracked notes used `git mv`; relative links in the moved transcription report were updated. The previous working-tree README and guide were preserved as `docs/history/README_legacy.md` and `docs/history/REPOSITORY_GUIDE_legacy.md` before refreshing their root versions.

`ONION_PILOT_REPORT.md` stays at the root because scripts write it. `LIKED_SONGS_ANALYSIS_REPORT_2026-10-30.md`, `PIPELINE_MAP.md`, `PROJECT_OVERVIEW.md`, `ROADMAP.md`, and `RUN_REPORT_2026-09-12.md` stay because code, documentation or the separate onion worktree references them. `BATCH_REPORT.md` is snapshot-protected and stays. No protected directory was moved.

## .gitignore changes

Existing rules were retained; grouped additions cover tool caches, local Claude settings, nested private/model-cache/scratch/vendor directories, generated media and weights, batch runtime files and `frontend/site-export/`. Exact exceptions retain the 75 intentionally tracked `docs/audio/*.mp3` files. Nested emotion ignore files were left intact. `frontend/pages-release/` was not newly ignored: it contains a separate Git repository with modified files and an unavailable Windows-style object alternate under WSL, so regenerability was not established. `.claude/` was not ignored wholesale because it also has a lock file.

`git check-ignore -v --no-index` confirmed the added patterns with representative cache, private, media, weights, batch and export paths; `docs/audio/bad-boy.mp3` matched its explicit exception. The complete tracked-file ignore audit returned one path: `.claude/settings.local.json`. It remains tracked pending your decision. No authored file, media or runtime file was untracked or deleted.

## Candidates for deletion (not deleted)

| Path | Approximate size | Reason to consider / caveat |
|---|---:|---|
| `.pytest_cache/` | 3.2 MiB | Regenerable test cache. |
| `frontend/site-export/` | 135 MiB | Generated by `frontend/export_site.mjs`; confirm the export is no longer needed before removal. |
| `frontend/pages-release/` | 152 MiB | Separate release worktree with its own Git metadata and modifications; do not delete without inspecting and preserving any needed work. |

No candidate was deleted during this cleanup.

## Test and snapshot results

Baseline and final: backend 144, emotion 17, emotion round 2 12, frontend Python 2, frontend Node 7: **182 tests passed**. The first `make test` attempt could not find Node in WSL after its Python suites passed; the target now uses this machine's Windows Node path and the complete rerun passed. The local Windows Node server served the index and a cached song report (HTTP 200 each) and was stopped.

All three snapshots were verified by size and SHA-256 at the start and again after every cleanup change. Round one also re-checked for unexpected extra files. Start: round one unchanged 9,066/9,066, v1 unchanged 7,849/7,849, MVP-325 unchanged 1,097/1,097. End: round one unchanged 9,066/9,066, v1 unchanged 7,849/7,849, MVP-325 unchanged 1,097/1,097. No changed, missing or extra files were found in either check.

## Commands to push when ready

These commands were **not run**. First resolve the deletion decision above, inspect the staged and committed state, verify the remote and Pages configuration, and choose the intended target. This exact command pushes the current named branch to a branch of the same name; it does not by itself select that branch as the Pages source:

```sh
git status --short
git log --oneline --decorate --graph --all -30
git push -u origin codex/rhythm-vocal-preview
```
