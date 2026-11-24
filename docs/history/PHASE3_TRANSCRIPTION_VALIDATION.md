# Phase 3: YourMT3+ transcription validation

**Track:** Silo — “Meaningful Love” (instrumental)  
**Version:** `output/midi_pipeline/phase3/meaningful-love-v1/`  
**Evaluation interval:** 6.000–18.000 seconds on the original recording clock  
**Validation status:** local runs completed; Songscription comparison pending; not ready for Phase 4

## Result

YourMT3+ successfully generated native MIDI and canonical note-event JSON for both the 0–30-second original mix excerpt and the corresponding existing Other stem. The pipeline read and hash-validated both result bundles. This establishes that the AMT execution path works in the configured WSL/CUDA environment; it does not establish note accuracy.

The 6–18-second predictions are sparse and disagree by input. The mix yielded 38 non-drum events whose onsets fall in the interval; the Other-stem run yielded 49. In the mix result, 31 of those 38 events have durations below 50 ms; in the Other result, 29 of 49 do. These very short decoded events, the decoder’s reported onset/segment-tie errors, and the difference between the two predictions are concrete AMT quality concerns. Without independent reviewed labels, they cannot be classified as particular missed notes, false positives, or wrong pitches. The available evidence does not support a dependable arpeggio, chord, repeated-phrase, or layer count.

This is separate from the earlier `rhythm-harmony-v1` diagnostic: the repository audit traced that Meaningful Love diagnostic to an audio-derived attack/chroma path, not Basic Pitch or MIDI events. Its failure to recover a convincing rhythm can therefore arise downstream independently of this new AMT output. Phase 3 now exposes additional transcription-side uncertainty; it does not retroactively make the old rhythm detector a note transcriber or establish that all earlier failures came from AMT.

**Recommendation:** do not begin Phase 4 pattern recognition from these predictions yet. First obtain the Songscription export or independently review enough note-level events—ideally complete labels for 6–18 seconds—to determine which events are real and to measure note recovery. Agreement between the mix and Other-stem runs is only model/input agreement, never accuracy.

## Sources and timebase

The repository contains no combined original instrumental WAV. The preserved source mix is the 48 kHz, two-channel MP3 at `liked-audio/Silo - meaningful love - instrumental [6rQCPgrLJMQavladOGpwZt].mp3` (291.168979 s; SHA-256 `0255ab6ac45248f7c5e3022c5ec913a8794886b332132ea865f001b0760bab97`). The existing `instrumentals/stems/6rQCPgrLJMQavladOGpwZt/other.wav` is mono PCM16 at 22.05 kHz (291.216009 s; SHA-256 `e4b05c7ce723a844ffb53f6289ada92d3841370f1c97aa4e96deac05612dc93b`). Its exact separator model/version was not available; the run records this as an existing Demucs HTDemucs-Fine-Tuned stem with version unverified.

The shared mix excerpt, [reference.wav](../../output/midi_pipeline/phase3/meaningful-love-v1/reference.wav), is exactly 0–30 s, stereo PCM16 at 48 kHz, 1,440,000 frames, SHA-256 `c496918598787218577b11fbb852f6c5886d27dae0818a5a447df2ed729f085d`. The corresponding Other excerpt, [reference_other.wav](../../output/midi_pipeline/phase3/meaningful-love-v1/reference_other.wav), is exactly 0–30 s, mono PCM16 at 22.05 kHz, 661,500 frames, SHA-256 `bc5a2d607720acbfc992195fee2bc770f051a996181acaf4f558cf6ea034c0cb`. The original MP3 and stem WAV were not changed. Full source and crop hashes, formats, and clock windows are recorded in `reference-manifest.json`.

Both local runs used the full 0–30 s context; neither was reduced to 6–18 s. Both crops begin at source time zero, so the local canonical event clocks have a verified 0.000-second crop offset. The backend downmixes channels by mean and resamples model input to 16 kHz; source excerpts and native MIDI remain preserved. No beat-grid quantization was applied. The reference report uses the mix excerpt. Songscription was not supplied, so its clock offset and any external correction remain unknown. The tool can suggest one diagnostic global offset when an export arrives; it never silently shifts or time-warps a transcription.

## Local YourMT3+ runs

| Input | Run ID | Notes, all context | Non-drum notes starting 6–18 s | Under 50 ms in focus | Runtime |
|---|---|---:|---:|---:|---:|
| Original mix | `d9f84370acc85c188ef15ff7ad67c5274ca86e9b6d3ea0b330002b258e19fd0c` | 206 | 38 | 31 | 19.06 s |
| Existing Other stem | `6c17c2d754a5e15c42bd7b39299dfc9229a1dc141c02cb64925d501a7c44f283` | 54 | 49 | 29 | 10.06 s |

Each run has `raw.mid`, `notes.json`, `run.json`, plus aligned input, analysis, diagnostics, and native token artifacts. The raw MIDI is retained without quantization. Run metadata includes processing configuration, dependency versions, source hash, model/checkpoint identity, artifact hashes, runtime, and decoder diagnostics. Both artifacts report `prediction_available_unvalidated`.

| Runtime metadata | Value |
|---|---|
| Backend/checkpoint | YourMT3+ `mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops@last.ckpt` |
| Checkpoint SHA-256 | `ae38e415c79efd5592dcb9b658cdb99ddb11d4c4e1eaa364cab04a052473fc25` |
| Device | CUDA, NVIDIA GeForce RTX 4060 Ti |
| Peak CUDA allocation | 399,680,000 bytes |
| Model input | 16 kHz, 32,767-frame segments; 15 segments for each 30 s input |
| PyTorch / torchaudio | `2.11.0+cu130` / `2.11.0+cu130` |
| Transformers / Lightning | `4.45.1` / `2.6.6` |
| NumPy / pretty-midi | `1.26.4` / `0.2.11.post0` |
| Basic Pitch / fallback / legacy cache | not used / none / none |

The mix decode logged 27 “onset not found” and 25 “merging segment tie” diagnostics. The Other-stem decode logged 15 of each. These are model decoder diagnostics, not counts of independently confirmed note errors. In the focus interval the mix assigned events to Acoustic Piano (31), Guitar (5), and Bass (2); the Other prediction assigned Acoustic Piano (30), Guitar (18), and Bass (1). Instrument names are model labels, not verified source-part identities. Many of the short events are approximately 30 ms; the available evidence flags them for listening/review but cannot establish whether each is spurious.

The comparison tool reports 22 matching pitch/onset events at 50 ms tolerance between the two runs over the 0–30-second context. Its agreement precision/recall fields use one run as a comparison set, not ground truth. Mean absolute onset difference is about 22.8 ms and offset difference about 96.0 ms for matched events. The mix and Other stem are different audio inputs, so this is diagnostic agreement only.

## Independent and human references

Songscription MIDI/MusicXML/screenshots have not been provided. The report marks the external comparison `pending_manual_download`; no automated account or demo access was attempted. Import instructions are in `external/README.txt`. The external file, when supplied, is kept separate and unmodified; MusicXML conversion is only a temporary derivative for comparison.

`human-annotations.json` is an empty, partial-review template. No note has been independently verified, and no listening description has been promoted to note truth. Accordingly onset precision/recall/F1, pitch accuracy, onset/offset/duration error, and false/missed-note counts are unavailable. Instrument attribution error is also unavailable without verified instrument labels. The current report explicitly suppresses these accuracy metrics.

## Diagnostic report and reproduction

Open [report.html](../../output/midi_pipeline/phase3/meaningful-love-v1/report.html) locally. It provides reference playback, a navigation/alignment-only waveform, separate piano rolls for each available local model run, toggles, an optional overlay, per-model track selection, note-level timing/pitch display, and clear pending/missing external status. It covers the whole 30-second context and highlights 6–18 seconds. It does not use the old event-based rhythm detector to generate note predictions.

From the repository root inside WSL, with the existing `output/amt/env` environment and model checkpoint available:

```sh
output/amt/env/bin/python -B -m unittest scripts.midi_pipeline.test_pipeline -v
CUBLAS_WORKSPACE_CONFIG=:4096:8 output/amt/env/bin/python -B -m scripts.midi_pipeline.phase3 run
CUBLAS_WORKSPACE_CONFIG=:4096:8 output/amt/env/bin/python -B -m scripts.midi_pipeline.phase3 compare
```

To prepare a fresh version, choose a new empty `--root` because `prepare` deliberately refuses to overwrite an existing excerpt or human annotation file. After manually saving an export under `external/`, pass its path to `compare --songscription-midi ...`; optionally supply MusicXML or a reviewer-approved single `--alignment-offset`. The original export stays unchanged. The generated summary is `comparison.json`; local inference logs are `output/midi_pipeline/logs/phase3-local-runs.log`.

## Verification completed

- All 32 tests in `scripts.midi_pipeline.test_pipeline` passed in 10.779 seconds. This includes Phase 3 excerpt/hash preservation, pending external state, partial-label metric suppression, complete-review metrics, one-to-one note matching, diagnostic global-offset suggestion, and Basic Pitch/fallback/cache enforcement.
- Both current run bundles passed the strict reader’s schema, identity, and artifact-hash validation during comparison generation.
- The Phase 3 CLI generated `local-runs.json`, `comparison.json`, and the refreshed interactive `report.html` with external status pending and human accuracy metrics unavailable.
- Original source hashes remain recorded and the WAV/MP3 sources were not modified by preparation or inference.

## What remains uncertain

There is no independent transcription or reviewed note set yet. Therefore exact missed/hallucinated notes, pitch correctness, duration correctness, track attribution, and phrase/rhythm accuracy remain unknown. The distinct mix/stem event totals and decoder warnings show AMT-side uncertainty before downstream interpretation; they do not prove that the earlier rhythm-analysis algorithm caused those note-level problems. Phase 4 should wait for independent review and supported note-level metrics.
