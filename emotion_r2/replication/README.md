# Task K operations

All Python invocations use `/home/usr/venvs/midi/bin/python` from WSL in the
repository directory. `-B` prevents writes into protected Python cache trees.

- Live state: `progress.json`; backend state: `../../library/progress.json`.
- Human-readable log: `../REPLICATION_PROGRESS.md` (three-hour counts/ETA;
  no interim effects); worker logs: `logs/` and `supervisor.log`.
- Detached start/resume: `/home/usr/venvs/midi/bin/python -B -m emotion_r2.replication.supervisor start`.
  Existing checkpoints are verified and reused. Deliberate backend stop markers
  and failure stop conditions require review before resuming the backend via
  its existing commands. The supervisor never overrides those markers.
- Backend status/stop/resume: `/home/usr/venvs/midi/bin/python -B -m pipeline.batch status --set library`
  (replace `status` with `stop` or `resume` as needed).
- After a Windows restart, run `launch.ps1` from PowerShell to re-establish the
  user-level keep-awake helper and resume the checkpoint supervisor. The old
  Task B Windows scheduled task remains disabled; no replacement schedule or
  system power setting is installed.
- The freeze and its local commit are mandatory before the statistics entry
  point permits any replication H1 calculations. H2/H3 are never called.
- New lyric text stays under ignored `private/`. New large scores, stems,
  curves and display checkpoints stay ignored. Only explicit Task K paths are
  staged, and foreign staged paths/wrong branches abort every commit.

The fixed plan, operational decisions and protections are in
`../REPLICATION_NOTES.md`; source/model/prompt/normalization changes are errors,
not reasons to substitute another pipeline.
