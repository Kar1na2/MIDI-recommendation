# Task I — per-model lyric display data

These files are display data only. No registered score, checkpoint, threshold, gate, model revision or verdict was changed. No frontend files were edited and nothing was pushed.

## Part A: class probabilities

`text_model_probs/<track_id>.json` contains all 325 songs and all 3,726 registered sections (primary and secondary). Join to existing frontend records by track ID and section ID. No lyric text is included.

| Model | Rescored sections | Largest absolute valence error | Required tolerance |
|---|---:|---:|---:|

| T1 | 175 | 2.98023223877e-08 | 1e-5 |

| T3 | 2207 | 2.98023223877e-08 | 1e-5 |

Every rescored section passed P(positive) − P(negative) versus the registered valence. Class probabilities are softmax outputs, not calibrated emotion confidence. Both models used their pinned local weights, the same inputs, FP32 classifier loading, 512-token truncation, evaluation mode and seed as the registered scoring code.

T1 retains 1,326 `reused_round1`, 706 `unsupported_or_uncertain_language`, and 1,519 `missing_text` statuses. Their probability and valence fields are null; they were not rescored. T3 retains 1,519 `missing_text` records with null values. Reused round-one values can still be read from the registered results, but no class probabilities are inferred for them.

## Part B: optional L1 readings

Status: **complete**. New prompt: `l1_reading_prompt.txt`; SHA-256 `ffdf21672c16d12394441664976928ac03a51b35d721d3925cca9be8adc58e3d`.

There are 325 song files in `l1_readings/`. Status counts: `{"missing_text": 1519, "ok": 2207}`. The overlap scan blanked 0 section readings.

**Display label: L1's interpretation of the lyrics.** Keep this attribution beside every reading in the front end, and render readings as plain text. These outputs showcase the model's interpretation; they are not verified songwriter intent, human emotion, or an explanation of how registered scores were computed.

The pinned L1 model used local greedy decoding with the new display prompt; its registered score prompt and checkpoints were not changed. Identical prompt/text/revision inputs may reuse an explicitly recorded display-only generation checkpoint.

Overlap rule: normalize NFKC and case; block any four consecutive matching words, and any four matching ja/ko/zh script characters after removing spacing/punctuation. The latter rule is deliberately conservative. Rejected text is never saved, including in logs or intermediate checkpoints. All published readings passed a second scan.

These descriptions are unvalidated model-generated interpretations. The overlap scan detects verbatim reuse, not semantic translation or theme accuracy; it cannot guarantee that a description preserves no source meaning. Readings never replace or affect registered L1 scores.

## Schema

Each probability file has `track_id`, `cohort`, model IDs/revisions and source-checkpoint SHA-256 values. Each `sections` item has `section_id`, `set`, `language`, `text_sha256`, and separate `T1`/`T3` objects. Model objects retain the registered `status` and contain `probabilities` (`positive`, `neutral`, `negative`), `valence`, `registered_valence`, `absolute_valence_error`, `reproduction_check`, and `truncated`. Skipped records use null numerical values. Scores are not changed or synthesized.

Optional reading files additionally store the new prompt SHA-256, decoding settings, registered status, display reading status, overlap flags and a nullable `reading`. They contain no passage text.

## Verification

Six display-only overlap tests and the 22 existing Task F tests passed. The registered statistical tests were not edited. `export_verification.json` checks every exported section against its source status and score; `registered_files_start.json` and `integrity_end.json` audit unchanged registered artifacts and private input hashes.

- round1: unchanged: 9,066/9,066; pass: True; extras: 0.

- v1: unchanged: 7,849/7,849; pass: True; extras: 0.

- mvp_325: unchanged: 1,097/1,097; pass: True; extras: 0.

- Registered Task F files: unchanged: 2,618/2,618; changed private inputs: 0.

Overall integrity pass: **True**. See `integrity_end.json` for the full changed/missing/extra lists. These counts describe the actual verification and are not inferred from earlier Task F results.
