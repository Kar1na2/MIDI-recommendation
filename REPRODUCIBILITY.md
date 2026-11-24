# Reproduction and methodology

## What is in this repository

- `pipeline/` builds beat grids, transcriptions, patterns, rhythmic sections, lyric sections and joined reports. The local `songs.yaml` supplies song-specific inputs and options.
- `emotion/` and `emotion_r2/` contain the registered emotion-analysis code, plans and model manifests. In the working copy, `emotion/REPORT.md`, `emotion_r2/REPORT.md`, and `emotion_r2/digest/DIGEST.md` describe the completed development analyses; these local reports are not included in this documentation commit.
- `analysis_v2/` contains an isolated analysis of cached pipeline results; see its [registered plan](analysis_v2/ANALYSIS_PLAN.md). The completed report and subsequent local plan amendment are not included in this documentation commit.
- `frontend/` contains a local, read-only review interface. Its [instructions](frontend/README.md) describe the required local data.
- [REPOSITORY_GUIDE.md](REPOSITORY_GUIDE.md) maps the local working tree, including historical work and data that may not be distributed with the repository.

The completed development analysis used a random 300-song sample plus 25 pilot songs. Results for the pilot or the combined 325 are not a random sample. The separately registered library replication is in progress; it is not a completed result.

## Inputs and environment

The recorded runs use Linux under WSL, Python 3.11 and a CUDA-capable GPU. The project dependencies and source revisions are specified in [pyproject.toml](pyproject.toml) and [uv.lock](uv.lock). On a compatible machine with `uv` installed, `uv sync --locked` is the starting point for installing the locked project environment. On the original machine, commands use the already-provisioned `/home/usr/venvs/midi/bin/python`; on another machine, substitute the interpreter in the environment you created. The dependency lock specifies Linux x86-64 and CUDA 13 Torch builds, so installation and GPU support depend on the host. The lock alone does not guarantee that a fresh machine can reproduce the local environment or model downloads; the emotion-analysis stack and its model access have additional requirements in the plans and manifests.

Reproduction also requires the source audio, song manifest, local model weights and applicable model access permissions, cached source data, and analysis inputs. Audio, credentials, private lyric text, model weights, and large generated caches are not generally included. Supply audio you are entitled to use, place it at the paths in a local manifest, and keep credentials outside Git. Genius uses `GENIUS_ACCESS_TOKEN` from the local environment; requests use the `Bearer` authorization scheme. LRCLIB, Genius, and optional manual lyric sections have different availability and provenance. WhisperX fallback uses a separate environment because its pinned Torch version conflicts with the main environment; its pins are in the local `requirements-whisperx.txt`.

The round-two model manifests and the [analysis plan](emotion_r2/ANALYSIS_PLAN.md) specify revisions, prompts, gates, thresholds and the registered tests. Some model licenses and training-data overlap remain unresolved; check the model cards and the local `emotion_r2/REPORT.md` before reuse. The development normalization parameters and the protected local sample inputs are also required to reproduce the registered scores; a fresh clone by itself is insufficient.

## Run and inspect the pipeline

From the repository root in the original WSL environment:

```sh
/home/usr/venvs/midi/bin/python -B -m pipeline --help
/home/usr/venvs/midi/bin/python -B -m pipeline run --help
/home/usr/venvs/midi/bin/python -B -m pipeline run --song aespa-attitude --device cuda
make test
```

Check the manifest and local audio paths before running a song. The command writes generated outputs and may download or load models; GPU, disk space, source availability, and network permissions affect whether it succeeds. `make test` uses the configured project interpreter and Windows Node path on the original machine; inspect [Makefile](Makefile) and override its variables as needed elsewhere. The local preview requires the sample manifests, per-song outputs, and audio in addition to the tracked frontend code; see [frontend/README.md](frontend/README.md).

## How the analysis works

The main transcription uses the full mix. A beat grid and transcribed notes are quantized into instrument layers; patterns and repetitions inform rhythmic sections. Timed lyric sections are obtained from available sources and joined with the rhythm timeline. Missing sources and failed stages are recorded rather than replaced with invented sections. Instrumental tracks do not require lyric sections. Exact configuration and status fields are defined in the pipeline and its reports.

The emotion study scores available sections with separate text, vocal and background models. Its round-two plan fixes the model versions, voice gate, normalization and tests. Model agreement is not evidence of human emotional accuracy. In the development sample, voice verdicts remain **provisional**; the chorus-versus-verse result is a **development robustness check**, not independent confirmation. H2 and H3 were not run because their precondition failed, which is not a negative finding. Training-data overlap, model licensing, missing text, language uncertainty and the voice gate limit interpretation; the detailed report documents these caveats.

The separate local `analysis_v2/REPORT.md` evaluates lyric/rhythm boundary alignment against its registered null models. Its observational findings and their limitations should not be treated as causal proof. See the individual plans and reports for the exact cohorts, exclusions, metrics, seeds and provenance; do not infer completed replication results from an active run.
