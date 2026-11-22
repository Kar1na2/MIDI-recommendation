# Task K — library replication progress

No interim H1 or chorus/verse comparisons are computed. The scored set must be
frozen and committed before analysis. Development trust verdicts remain fixed;
H2/H3 will not run.

2026-11-22 preflight: 803 non-MVP IDs: 728 local audio, 75 missing audio.
60 completed backend caches; 668 backend tracks pending. Seven metadata
near-duplicate flags (two with local audio) are retained for sensitivity only.
Exact lists: `replication/inventory.json`, `replication/near_duplicates.csv`,
and the existing `library/missing_audio.csv`.

Initial snapshots all passed: round 1 unchanged **9066/9066**; v1 unchanged
**7849/7849**; MVP-325 unchanged **1097/1097**; completed round 2 unchanged
**3668/3668**. The fourth manifest is
`replication_development_snapshot.json`.

Measured 325-run mean footprint: backend 172.56 MB/song, emotion instrumental
stem 18.92 MB/song, round-2 scores 0.18 MB/song. Additional projection 129.18 GB,
free 863.42 GB; 1.3× margin passes. Temporary/model/display overhead is additional;
disk is checked again by the existing backend preflight and monitored during run.
Actual logged transcription mean 334.91 s/song projects to 62.14 h for the 668
pending backend tracks. Other stages, CPU lyric acquisition and emotion scoring
are additional. Complete measured stage table: `replication/preflight_inventory.json`.

The old Windows scheduled task is Disabled. The local branch is
`codex/library-replication`. No push, pages work or protected outputs are part
of this task. New orchestration tests: 6 passed; backend suite: 144 passed;
emotion-round-2 suite including the 6 new tests: 28 passed; display: 6 passed.

The supervisor appends counts, failure categories and ETA every three hours and
at stage transitions. Its exact current phase is in `replication/progress.json`;
worker errors are in `replication/supervisor.log` and `replication/logs/`.

- 2026-11-22T15:02:27.821966+00:00 — STOPPED: Command '['git', 'show', 'HEAD:emotion_r2/REPLICATION_NOTES.md']' returned non-zero exit status 128.; backend {'done': 60, 'pending': 668}; failures {}; emotion checkpoints {'T1': 0, 'T3': 0, 'L1': 0, 'M1': 0, 'M2': 0, 'M3': 0, 'voice_gate': 0, 'V1': 0, 'V3': 0}; backend ETA hours not yet measured. No interim H1/chorus–verse statistics.

- 2026-11-22T15:27:23.326855+00:00 — Pass 1 actively transcribing; registration-start guard resolved; final orchestration tests passed; backend {'done': 60, 'running': 1, 'pending': 667}; failures {}; emotion checkpoints {'T1': 0, 'T3': 0, 'L1': 0, 'M1': 0, 'M2': 0, 'M3': 0, 'voice_gate': 0, 'V1': 0, 'V3': 0}; backend ETA hours not yet measured; free disk bytes 863411257344. No interim H1/chorus–verse statistics.
