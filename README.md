# MIDI recommendation research

This repository contains a rhythm and lyric-section pipeline and analyses of a music-library sample. The current pipeline uses beat grids and transcription to build rhythmic patterns and sections, aligns available lyric sections, and joins those results for review. The analyses are research outputs; model scores and inferred labels are not ground truth.

## Current work

- `pipeline/`: full-mix transcription, rhythm, lyric alignment, and reports. `songs.yaml` configures songs.
- `mvp/` and `mvp_325/`: the random 300-song MVP and its 25-song pilot supplement.
- `analysis_v2/`: separate v2 analyses of cached v1 results.
- `emotion/` and `emotion_r2/`: round-one and round-two emotion research and their plans and reports.
- `frontend/`: read-only local review application; see [its README](frontend/README.md).
- `docs/`: published-site source and project records. The deployed Pages source must be checked before changing it; see [repository state](docs/REPO_STATE.md).

See [REPOSITORY_GUIDE.md](REPOSITORY_GUIDE.md) for the directory map and [the legacy README](docs/history/README_legacy.md) for the previous documentation.

## WSL commands

From `/mnt/d/code files/personal/MIDI-recommendation`, the following help and dry-run commands were verified with the project environment:

```sh
/home/usr/venvs/midi/bin/python -B -m pipeline --help
/home/usr/venvs/midi/bin/python -B -m pipeline run --help
/home/usr/venvs/midi/bin/python -B -m pipeline diagnose --help
make -n help
make -n test
```

Run `make test` in WSL to execute the backend, emotion and frontend test suites. It defaults to `/home/usr/venvs/midi/bin/python`; the `PYTHON` make variable can be overridden explicitly.

For the local read-only review application, use Windows Node from the repository root:

```sh
node frontend/server.mjs
```

Open `http://127.0.0.1:8771/`. It reads local audio and cached data; it does not publish a site. See the frontend README for requirements and controls.
