# Repository guide

This map describes the local working tree at cleanup time. “Tracked” means Git tracks at least some contents; “untracked” does not mean disposable. Nested ignore rules and snapshots protect individual files. Never assume ignored data can be deleted.

| Directory | Contents | Git status | Readers / protection |
|---|---|---|---|
| `.claude/` | Local Claude settings and lock | Settings tracked but ignored; lock local | Claude tooling; preserve settings |
| `.codex-attachments/` | Prompt attachments | Ignored | Codex; local source documents |
| `.git/` | Repository metadata | Internal | Git; preserve |
| `.pytest_cache/` | Test cache | Ignored | pytest; regenerable |
| `.uv-cache-codex/` | Package cache | Ignored | uv; local cache |
| `.venv/` | Local environment | Ignored | local Python tools |
| `analysis_v2/` | v2 analyses and v1 inventory | Partly tracked; data and scripts untracked, caches ignored | analysis_v2; snapshot-protected v1 inputs elsewhere |
| `annotations/` | Local annotations | Untracked | review/analysis; preserve |
| `archive/` | Legacy scripts and historical inputs | Partly tracked, some ignored | archived workflows; preserve |
| `audio/` | Original local audio | Ignored | legacy pipeline; preserve |
| `batch/` | Batch runtime and logs | Untracked, runtime files ignored | pipeline batch; preserve completed work |
| `chords/` | Generated chords | Ignored | legacy analysis; preserve |
| `corrected/` | Corrected legacy outputs | Ignored | legacy pipeline; preserve |
| `data/` | Local cached sources and lookup data | Mostly untracked; Genius HTML ignored | pipeline and lyric alignment; preserve |
| `docs/` | Project history and Pages source | Partly tracked; some generated data ignored | Pages/frontend; some files snapshot-protected |
| `emotion/` | Emotion round 1 | Partly tracked; local data and code untracked, private caches ignored | emotion analysis; round-one snapshot protected |
| `emotion_r2/` | Emotion round 2 | Partly tracked; local data and code untracked, private caches ignored | round-two analysis; contains round-one snapshot |
| `frontend/` | Local review app and exports | Untracked; site-export ignored | Node server and exporter; release worktree preserved |
| `ground_truth/` | Authored reference annotations | Tracked | evaluation code; preserve |
| `ground_truth_anchor/` | Reference anchors | Partly tracked; media ignored | evaluation code; preserve |
| `instrumental-audio-host/` | Media hosting manifests | Partly tracked | historical site export; preserve |
| `instrumentals/` | Historical instrumental work | Partly untracked, media ignored | historical scripts; preserve |
| `k-indie/` | Local collection | Ignored | historical workflows; preserve |
| `lib/` | Shared legacy analysis code | Tracked | legacy code; preserve |
| `library/` | Library run state | Untracked, runtime files ignored | batch pipeline; preserve |
| `liked-audio/` | Local source music | Ignored | pipeline and frontend; preserve |
| `merged/` | Merged outputs | Ignored | legacy pipeline; preserve |
| `midi/` | Local MIDI outputs | Ignored | legacy pipeline; preserve |
| `mvp/` | Random 300 sample and results | Untracked | frontend, analysis, reports; snapshots protect files |
| `mvp_325/` | Random sample plus pilot | Untracked | frontend and analysis; snapshot protected |
| `onion-site/` | Separate site/worktree | Untracked | onion site; do not modify during cleanup |
| `output/` | Historical outputs and reports | Ignored | legacy code and historical pages; preserve |
| `outputs/` | Per-song pipeline outputs | Ignored | pipeline, frontend and analyses; snapshot-protected files |
| `pilot/` | Pilot sample and review materials | Untracked | pipeline and analysis; preserve |
| `pipeline/` | Current rhythm and lyric pipeline | Untracked | pipeline CLI and tests; preserve |
| `preview/` | Historical preview output | Partly ignored | legacy preview; preserve |
| `run-logs/` | Local run logs | Ignored | run diagnostics; preserve unless user decides otherwise |
| `scripts/` | Legacy scripts | Partly tracked, many pre-existing deletions | legacy workflows; preserve current state |
| `stems/` | Local separated audio | Ignored | pipeline and legacy code; preserve |
| `tests/` | Current backend tests | Untracked | unittest; preserve |
| `tools/` | Pipeline utilities and legacy tool builds | Partly tracked, some ignored | maintenance scripts; preserve |

Snapshots are `analysis_v2/v1_snapshot.json`, `mvp_325/snapshot.json`, and `emotion_r2/r1_snapshot.json`. Verify them before touching their listed files. See [repository state](docs/REPO_STATE.md) for the cleanup audit and unresolved Git decisions. The previous guide is preserved at [docs/history/REPOSITORY_GUIDE_legacy.md](docs/history/REPOSITORY_GUIDE_legacy.md).
