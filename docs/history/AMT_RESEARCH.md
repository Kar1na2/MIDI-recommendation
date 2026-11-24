# Learned music transcription for MIDI pattern analysis

Research and implementation date: November 4, 2026.

## What Songscription actually discloses

Its [launch post](https://www.songscription.ai/blog/launch-announcement) describes a proprietary Transformer architecture and professional-artist training data. Its [funding post](https://www.songscription.ai/blog/seed-funding-announcement) identifies public-domain material and artist/music-business partnerships. Neither supplies layer definitions, weights, a dataset manifest, alignment procedures, losses, augmentation settings, or a training schedule. An exact reproduction is therefore unsupported by the public posts reviewed here.

Its [October 2026 comparison](https://www.songscription.ai/blog/state-of-the-art-music-transcription) describes direct full-recording, multi-instrument transcription. The company reports MIDI scores of 89.4 for piano and 52.2 for multi-instrument audio, versus Basic Pitch's 48.9 and 32.7. These are averages of pitch-aware onset, onset-offset, and frame F1, **not percentages of perfectly transcribed songs**. They are vendor-reported results on their benchmark, not measurements on this project's recordings. Arrangement and engraving scores measure different tasks from faithful audio-to-MIDI transcription.

**Conclusion:** pursue a reproducible learned multi-instrument transcriber and measure its downstream utility. The public evidence does not establish whether Songscription uses MT3, synthetic pretraining, a particular tokenizer, source separation internally, or reinforcement learning.

## Reproducible research alternatives

[YourMT3+ paper](https://arxiv.org/html/2407.04822v2), [official repository](https://github.com/mimbres/YourMT3), and [author-maintained source/checkpoints](https://huggingface.co/spaces/mimbres/YourMT3) provide a concrete starting point. Its published system combines a Perceiver time-frequency encoder, mixture-of-experts layers, and a multi-channel T5 decoder of MIDI-like tokens. It trains with intra-/cross-dataset stem mixing and supports masking incomplete instrument annotations through its multi-channel design. Its roughly 2.048-second chunks are joined using note ties. This implementation uses the published **YPTF.MoE+Multi noPS** checkpoint rather than training another model from scratch. Repository licensing and file-level notices differ; retain upstream notices and review the checkpoint's applicable terms before choosing a production dependency.

[MuScriptor paper](https://arxiv.org/html/2607.08168v2) and [official repository](https://github.com/muscriptor/muscriptor) describe a decoder-only Transformer trained with synthetic pretraining, aligned real recordings, and subsequent reinforcement learning. The paper reports 1.45 million synthetic MIDI sources, 170,000 real recordings, and 300 curated tracks for post-training. Its private real-data collection is a major obstacle to reproducing its training. The current release requires Hugging Face access; code is MIT and weights are CC BY-NC 4.0. It remains an additional research baseline, not an installed dependency or a claim about Songscription's methods.

The hardware detected locally is an NVIDIA RTX 4060 Ti. Inference and a small frozen-backbone adaptation are practical experiments; the cited large-scale training recipes should not be presumed feasible on this card.

## What changed in this repository

The existing rhythm preview extracts spectral-flux peaks and chroma. Those are useful acoustic features but do not establish individual notes, repeated same-pitch attacks, or instrument-specific polyphony. The Silo audit in `output/analysis/MEANINGFUL_LOVE_OTHER_DIAGNOSTIC.md` documents the resulting missed pattern. Existing Basic Pitch MIDI is another model hypothesis, not reference truth.

The new experimental route is:

```text
recording or stem -> learned multitrack AMT -> raw MIDI + note table
                                         -> reviewed note evaluation
                                         -> MIDI rhythm/pitch phrase candidates
aligned audio/MIDI references -> validated composition splits -> supervised adapter
```

AMT models may still consume a spectrogram and output events. The substantive change is learning pitch, instrument, onset and offset jointly from audio and note supervision, followed by symbolic analysis, instead of equating a detected spectral peak with a musical attack.

| Module | Behavior |
| --- | --- |
| `scripts.amt.bootstrap_yourmt3` | Fetches a pinned source revision and one ~562 MB checkpoint, records SHA-256 provenance, excludes the upstream demo/authentication examples |
| `scripts.amt.transcribe` | Transcribes an arbitrary WAV/recording window; writes `transcription.mid`, `notes.csv`, and `run.json` into a new directory |
| `scripts.amt.midi_patterns` | Measures note durations/IOIs, pitch register, polyphonic attack groups and repeated contiguous figures per track |
| `scripts.amt.evaluate` | Compares multiple MIDI candidates with one reviewed CSV window at 50 ms onset tolerance; distinguishes attack-only, pitch-aware onset/offset, and 10 ms pitch-frame scoring |
| `scripts.amt.dataset` | Checks aligned-pair metadata, complete annotations, composition groups, exact duplicate leakage, file hashes and audio/label duration |
| `scripts.amt.finetune` | Trains only the vocabulary projection on fully annotated pairs, retaining the learned acoustic model and decoder; writes a small adapter and validation token-loss report |
| `scripts.amt.smoke_fixture` | Creates known synthetic tones/MIDI to verify data loading and gradient updates; offers no evidence of real-music accuracy |

The MIDI export retains input-recording seconds, including a nonzero crop start. It clips padding predictions, records decoding errors, and does not quantize notes. Inference defaults to float32; reduced precision is explicit with `--precision float16` and is recorded in the run metadata. Its 120 BPM MIDI header is a placeholder, **not a tempo estimate**. `midi_patterns` defaults to seconds; supply a reviewed `--bpm` and optional `--beat-origin` for beat coordinates. Input windows from an instrumental edit and an original vocal recording still have different clocks.

The upstream duration/pitch decoder may correct malformed note events during merging. The exported offsets are therefore model/postprocessor estimates, not guaranteed acoustic release times. All inference weights are checked with strict loading. The tested optional dependency versions are recorded locally in `output/amt/requirements-tested.txt`.

Pattern comparison groups notes within 30 ms of the first onset, preserving repeated attacks and all simultaneous pitches. It searches contiguous groups of 4/6/8/12/16 attacks across arbitrary positions, comparing tempo-normalized IOIs; pitched comparisons additionally use register and note durations. Transposition invariance is opt-in. Drum keys remain drum classes. These are candidate matches with exact timing buckets; they can miss expressive variants and do not prove arpeggios, chord identities, meter, downbeats, or section boundaries. MIDI offsets describe key-off, not pedal-expanded sounding duration. Velocity is not used as pattern evidence.

## Run locally in WSL

Use an isolated environment because the upstream code needs `transformers==4.45.1`. The main pipeline's dependencies remain unchanged. The setup below reuses the existing project libraries through a path file so PyTorch/CUDA need not be downloaded again:

```sh
.venv/bin/python -m venv --system-site-packages output/amt/env
output/amt/env/bin/python -c 'from pathlib import Path; import sysconfig; (Path(sysconfig.get_paths()["purelib"]) / "project-libraries.pth").write_text(str(Path(".venv/lib/python3.11/site-packages").resolve()) + "\n")'
output/amt/env/bin/python -m pip install -r scripts/amt/requirements-yourmt3.txt
output/amt/env/bin/python -m scripts.amt.bootstrap_yourmt3

output/amt/env/bin/python -m scripts.amt.transcribe \
  --audio instrumentals/stems/6rQCPgrLJMQavladOGpwZt/other.wav \
  --start 6 --duration 18 --output-dir output/amt/runs/silo-other-6-24

output/amt/env/bin/python -m scripts.amt.midi_patterns \
  --midi output/amt/runs/silo-other-6-24/transcription.mid \
  --start 6 --end 24 --output output/amt/runs/silo-other-6-24/patterns.json

output/amt/env/bin/python -m unittest scripts.amt.test_amt -v
```

Choose new output paths when rerunning experiments. Audio, model files, adapters, and generated JSON/CSV remain under ignored `output/amt/`. Recordings are processed locally. No existing transcription, listening annotation, player data, catalog database, or published site is replaced by this route.

## Training recipe for this project

1. **Establish references first.** Hand-review note attacks/pitches/offsets for the Silo instrumental's 6–18 s repeat and subsequent overlays, the separate 1nonly Meaningless Love case, and contrasting bass/drum/polyphonic passages. Keep source hashes and exact windows. Use multiple reviewed passages for each instrument type; one listening example cannot establish general accuracy.
2. **Collect genuine paired supervision.** Use licensed/public dataset audio aligned to MIDI performance timing, own multitrack recordings with MIDI capture, or reviewed annotations. A score MIDI with nominal tempo is not automatically aligned to a recording. Demucs stems do not acquire ground truth merely because we separated them. Predicted Basic Pitch or new-model MIDI is not accepted as reference by this manifest format.
3. **Separate by composition before cropping or synthesis.** Put arrangements, performances and augmentations of a composition in one split. Supply stable `group_id` values; the validator can check declared IDs and duplicate hashes but cannot discover every arrangement alias or prove alignment from metadata. Do not use the held-out diagnostic songs for training if they will be the final test.
4. **Test a small supervised adaptation.** The implemented tuner freezes the backbone/decoder and adjusts its output projection using cross-entropy on upstream note tokens, including chunk-boundary ties. It needs complete labels: unlike the full upstream training pipeline, it does not implement partial-annotation masking, cross-dataset mixing, or reinforcement learning. A token-loss improvement alone does not establish a note-accuracy improvement.
5. **Scale only after validation.** Broader decoder/backbone adaptation, aligned synthetic soundfont mixtures, stem randomization, pitch/tempo augmentation and longer-context modeling are follow-up experiments. Transpose/retime audio and labels together. Compare genuine real-audio held-out performance before investing in large synthetic pretraining. Nothing here identifies those choices as Songscription's recipe.

Example `pairs.jsonl`, with paths relative to the manifest file:

Labels must use the upstream program vocabulary; programs 100/101 are reserved for singing rather than their ordinary General MIDI meanings. Unsupported programs are rejected during token preparation. Dataset license/provenance text is a declaration to review, not an automated rights check.

```json
{"audio":"train.wav","midi":"train.mid","group_id":"composition-A","split":"train","label_source":"aligned_performance","license":"recorded by project; MIDI capture","complete_annotation":true}
{"audio":"validation.wav","midi":"validation.mid","group_id":"composition-B","split":"validation","label_source":"manually_verified","license":"project-authorized reference","complete_annotation":true}
```

```sh
output/amt/env/bin/python -m scripts.amt.dataset path/to/pairs.jsonl
output/amt/env/bin/python -m scripts.amt.finetune \
  --manifest path/to/pairs.jsonl --steps 100 --learning-rate 1e-5 \
  --output-dir output/amt/adapters/experiment-01
# Add --adapter output/amt/adapters/experiment-01/adapter.pt to transcribe.
```

For an engineering smoke check only, generate pairs with `python -m scripts.amt.smoke_fixture --output-dir output/amt/smoke-pairs`, then tune for two steps. Do not interpret that synthetic exercise as training a useful new AMT model.

## Evaluation and promotion to the rhythm player

Use the same original/stem audio window, absolute clock and reviewed note reference for Basic Pitch, the existing piano specialist, YourMT3+ and any adapter. MIDI tracks must match the reference's instrument scope. The evaluator defaults to instrument-agnostic pitched-note pooling; it does not pretend to score instrument identity. Drum offsets are not a quality metric. Header-only references return `no_reviewed_reference`; partial onset labels cannot produce a pitch-accuracy claim.

```sh
output/amt/env/bin/python -m scripts.amt.evaluate \
  --reference path/to/reviewed-other.csv --start 6 --end 18 \
  --estimate output/amt/runs/silo-other-6-24/transcription.mid \
  --estimate path/to/basic-pitch-on-same-recording.mid \
  --output output/amt/evaluation/silo-other.json
```

Evaluate pitch-aware onset/offset precision and recall, instrument assignment, repeated-note recall, and duration errors. Then evaluate downstream repeated-phrase detection and boundaries against independently reviewed references. A denser MIDI file or a plausible sounding synthesis is not an accuracy score. Establish the acceptance thresholds before selecting a model. Integrate the MIDI representation into the player only after this evidence, and preserve an unresolved state where transcription is unreliable.

## Local experiment results

The downloaded source revision is `5e66c1ea173a8186e0d20432b841d3180cc015b5`. The checkpoint SHA-256 is `ae38e415c79efd5592dcb9b658cdb99ddb11d4c4e1eaa364cab04a052473fc25`. Tests ran with PyTorch 2.11.0+cu130, Transformers 4.45.1, NumPy 1.26.4 and Lightning 2.6.6.

The runs used **Silo — meaningful love (instrumental), seconds 6–24**, without supplied pattern boundaries or instrument restrictions:

| Input | Predicted notes by track | Runtime, including model loading | Decode/merge errors |
| --- | --- | ---: | --- |
| Separated `other.wav`, float32 | Piano 36; clean guitar 16; bass 1 — **53 total** | 31.8 s | 1 onset-not-found; 5 segment-tie merges |
| Separated `other.wav`, float16 | Piano 44; clean guitar 16; bass 1 — **61 total** | 35.2 s | 1 onset-not-found; 5 segment-tie merges |
| Dedicated instrumental mix, float16 | Piano 40; clean guitar 3; bass 1; strings 10; drums 93 — **147 total** | 34.1 s | 8 onset-not-found; 8 segment-tie merges |

These instrument names are predictions. The Other WAV hash matches the earlier audit exactly: `e4b05c7ce723a844ffb53f6289ada92d3841370f1c97aa4e96deac05612dc93b`. The recommended float32 baseline is in `output/amt/runs/silo-other-fp32-6-24/`. The float16 stem artifacts are in `output/amt/runs/silo-other-6-24-v2/`, and mix artifacts in `output/amt/runs/silo-mix-6-24/`. The first two float16 runs predate the explicit precision metadata field; their precision is recorded here. An earlier incomplete export is retained separately under `silo-other-6-24/`.

Both stem analyses found **six rhythm-only repeat candidates and zero pitch-plus-duration repeats**. All piano-labelled notes in both runs last roughly 29–40 ms; guitar-labelled notes last roughly 298–580 ms. The short piano durations persist in full precision, so reduced precision alone does not explain them. Float32 does change the predicted note count; do not mix precisions in an accuracy comparison. That suspicious duration distribution and the join errors are reasons to inspect the decoding, not evidence that the audible piano is that short. The mix analysis has 15 piano rhythm-only candidates, 11 drum rhythm-only candidates, two drum-class candidates, and no pitched repeats. **These runs do not verify the user's four ascending arpeggios, establish the section boundaries, or show an accuracy gain over Basic Pitch.** The next model comparison needs reviewed notes on this exact recording and window.

The initial engineering tuning check performed two actual gradient updates of 305,152 output-head parameters, wrote a ~1.2 MB adapter, and completed a separate validation pass. Validation token loss changed from 0.7894046 to 0.7893964, an immaterial change on one synthetic example. The saved adapter was successfully loaded for a separate transcription pass. This establishes the executable training/inference path only; no useful custom AMT training or accuracy parity with Songscription is implied.

The focused suite passes **13 tests**, covering reattacks under sustain, non-chaining attack groups, register-sensitive versus transposed matching, drum semantics, crop clocks, real MIDI serialization, onset versus pitch scoring, frame-duration errors, invalid references, empty/partial references, composition leakage and exact duplicate leakage. The final strict-loader training check is recorded in `output/amt/adapters/engineering-smoke-verified/`.
