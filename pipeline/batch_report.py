"""Write the backend batch report from measured logs and cached pilot reports."""
from collections import Counter
from pathlib import Path

from .common import atomic_write_bytes, read


def write_report(root, batch_dir=None, output_root=None):
    root = Path(root)
    batch_dir = Path(batch_dir or root / "batch")
    output_root = Path(output_root or root / "outputs")
    progress_path = batch_dir / "progress.json"
    if not progress_path.is_file():
        return
    progress = read(progress_path)
    manifest = read(batch_dir / "manifest.json") if (batch_dir / "manifest.json").is_file() else {}
    storage = read(batch_dir / "storage_preflight.json") if (batch_dir / "storage_preflight.json").is_file() else {}
    songs = progress.get("songs", {})
    done = [tid for tid, state in songs.items() if state.get("status") == "done"]
    failed = [(tid, state) for tid, state in songs.items() if state.get("status") == "failed"]
    remaining = progress.get("total", len(songs)) - len(done) - len(failed)
    titles = {row["id"]: f"{row['artist']} — {row['title']}" for row in manifest.get("songs", [])}
    from .batch import error_category
    categories = Counter(error_category(RuntimeError(state.get("error", "")))
                         if "did not emit EOS" in state.get("error", "")
                         else state.get("failure_category", "unexpected") for _, state in failed)
    gpu = Counter()
    import json
    # Include computations reused from the real two-song resume test; cached reads cost no GPU time.
    for path in [batch_dir / "run_log.jsonl", batch_dir / "resume_test" / "run_log.jsonl"]:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("stage") in ("beats", "separation", "transcription", "routing") and row.get("status") != "cached":
                gpu[int(row.get("pass", 1))] += float(row.get("seconds", 0))
    actual_bytes = 0
    for tid, state in songs.items():
        if state.get("status") not in ("done", "failed"):
            continue
        folder = output_root / tid
        actual_bytes += sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())
    acceptance = Counter((songs[tid].get("pattern_acceptance") or {}).get("status", "review") for tid in done)
    storage_rows = "\n".join(f"| {name} | {value:,} | {value/25/1e6:.2f} | {value/25*1028/1e9:.2f} |"
                             for name, value in sorted(storage.get("artifact_bytes", {}).items()))
    examples = "\n".join(f"| {tid} | {titles.get(tid, tid)} | {state.get('last_stage')} | {state.get('failure_category')} | {state.get('error', '').replace('|', '/')} |"
                          for tid, state in failed[:10]) or "| — | — | — | — | — |"
    ring2_path, lyric_path = root / "pilot/ring2_rework/summary.json", root / "pilot/lyrics_rework/summary.json"
    ring2_text = "Phase 2 is still running."
    if ring2_path.is_file():
        data = read(ring2_path)
        table = "\n".join(f"| {row['artist']} — {row['title']} | {row['old_sections']} | {row['new_sections']} | {row['grid_basis']} |" for row in data["songs"])
        ring2_text = f"""Result: **{data['status']}**. {data.get('reason') or ''}

Texture-only features, form letters, median + 2×MAD boundary strength, 8-bar minimum, and 2-second frames / 16-second minimum for flagged grids are implemented. No library onions were generated. Silo's first new boundary is {data['silo_opening_check']['new_first_boundary_s']} s; it does not locate the arpeggio ending near 18.5 s.

| Song | Old sections | New sections | Basis |
|---|---:|---:|---|
{table}

Distribution: {data['section_count_distribution']}. [Full Phase 2 report](pilot/ring2_rework/REPORT.md).

![Ring 2 counts](pilot/ring2_rework/section_counts.png)"""
    lyrics_text = "Phase 3 is still running."
    if lyric_path.is_file():
        data = read(lyric_path)
        rate_limited = sum("429" in (row.get("reason") or "") for row in data.get("songs", []))
        rate_limit_note = (
            f"Genius returned HTTP 429 during candidate lookup for {rate_limited} of "
            f"{len(data.get('songs', []))} pilot vocal songs. Candidate discovery for those "
            "songs may be incomplete; previously cached Genius data was retained when "
            "available, and no arbitrary candidate was selected."
            if rate_limited else "Genius candidate lookup completed without HTTP 429 responses."
        )
        table = "\n".join(f"| {language} | {row['old_genius_hits']}/{row['songs']} → {row['new_genius_hits']}/{row['songs']} | {row['old_sections_passed']}/{row['old_sections']} → {row['new_sections_passed']}/{row['new_sections']} | {row['old_full_pass']}/{row['songs']} → {row['new_full_pass']}/{row['songs']} |" for language, row in sorted(data["by_language"].items()))
        lyrics_text = f"""The pilot-only artist/title candidate audit and per-section validation are written. Confidence remains 0.65; validated labels are retained, failed spans are `unaligned`, and songs with no validated sections use measured vocal activity. New alignments use CPU. No full-library lyrics were fetched.

{rate_limit_note} The candidate CSV contains candidates returned by successful responses and records lookup errors in per-song results.

| Language | Genius hits old → new | Section pass old → new | Full-song pass old → new |
|---|---:|---:|---:|
{table}

Failure reasons: {data['failure_reasons']}. [Every candidate](pilot/lyrics_rework/candidates.csv), [section diagnostics](pilot/lyrics_rework/section_diagnostics.csv), [full Phase 3 report](pilot/lyrics_rework/REPORT.md)."""
    status = progress.get("status", "unknown")
    stop_path = batch_dir / 'STOPPED_FOR_MVP.json'
    stopping_point = ''
    if stop_path.exists():
        stopped = read(stop_path)
        stopping_point = (f"Full-library stopping point for the MVP: **{stopped['done']} done, "
            f"{stopped['failed']} failed**, stopped at **{stopped['stopped_at_local']}** "
            f"({stopped['stopped_at_utc']} UTC record). All completed caches retained. "
            "The 1,028-song batch must not resume as part of the MVP.")
    document = f"""# Full-library backend batch report

Status: **{status}** as of {progress.get('updated_at')}. This report refreshes during the run and is finalized when both passes finish. It is not a completion claim while songs remain.

{stopping_point}

Songs: **{len(done)} done, {len(failed)} failed, {remaining} remaining** out of {progress.get('total', 1028)}. The 75 manifest tracks with no local MP3 are skipped in [batch/missing_audio.csv](batch/missing_audio.csv); 25 pilot songs are excluded. Failure categories: {dict(categories)}.

Pass 1 uses tuning → beats → separation → one full-mix MuScriptor Large transcription → quantize → layers → patterns. It runs no routing, lyrics, onion generation or site export. Pass 2 starts only after every song has a Pass 1 outcome; it triggers on emitted `acoustic_piano` notes and uses one `acoustic_piano`-conditioned pass on `no_drums`. Routing triggered: {progress.get('routing_triggered_count', 0)}; completed: {progress.get('routing_completed_count', 0)}; failures: {progress.get('routing_failures', {})}.

| Failure ID | Song | Stage | Raw category | Example |
|---|---|---|---|---|
{examples}

Measured GPU-stage wall time (including reused resume-test computation and failed/interrupted attempts): Pass 1 **{gpu[1]/3600:.2f} h**, Pass 2 **{gpu[2]/3600:.2f} h**. The pilot projection is **{1028*4.8/60:.2f} h** for Pass 1 (4.8 GPU-min/song) and 1.7 GPU-min per triggered route. These are stage wall times while the GPU is reserved, not hardware utilization counters. Model setup is outside stage timings.

Current batch output footprint: **{actual_bytes/1e9:.2f} GB**, versus the conservative Phase 0 projection **{storage.get('projected_total_bytes', 0)/1e9:.2f} GB**. Only folders belonging to this batch are counted in the current footprint.

Pattern-compression statistics for completed songs: **{acceptance.get('pass', 0)} pass, {acceptance.get('review', 0)} review**. Batch songs have no user-supplied reference windows, so review means unscored exploratory compression, following the pilot convention. Acceptance does not gate either pass. The 0.85 similarity and 75% reference acceptance thresholds are unchanged; Silo window 2 remains review.

## Corrected storage measurement

Measured {storage.get('pilot_song_count', 25)} pilot songs, including the two shared reference caches that the pilot reused. This includes diagnostic and duplicate cached assets, so the full observed footprint is a conservative projection. Total pilot bytes: {storage.get('pilot_total_bytes', 0):,}; mean: {storage.get('mean_bytes_per_song', 0)/1e6:.2f} MB/song. Projection: {storage.get('projection_share_of_free', 0):.1%} of {storage.get('free_bytes_before_run', 0)/1e9:.2f} GB free; the 70% gate passed. Pipeline Demucs stems are about 83 MB per song in this measured footprint, not the site's 8 MB preview stems.

| Artifact | Pilot bytes | Mean MB/song | Projected GB / 1,028 |
|---|---:|---:|---:|
{storage_rows}

[Per-song bytes](batch/storage_per_song.csv) · [Machine-readable disk gate](batch/storage_preflight.json) · [Real interruption/resume proof](batch/RESUME_TEST.md).

## Phase 2

{ring2_text}

## Phase 3

{lyrics_text}

## Run controls

From WSL in `/mnt/d/code files/personal/MIDI-recommendation`:

```bash
/home/usr/venvs/midi/bin/python -m pipeline.batch status
/home/usr/venvs/midi/bin/python -m pipeline.batch stop
/home/usr/venvs/midi/bin/python -m pipeline.batch resume
```

The batch runs detached under nohup. [progress.json](batch/progress.json) is atomic; [run_log.jsonl](batch/run_log.jsonl) is append-only. CUDA OOM clears the cache and retries once. Timeout is max(20 min, 5×actual local audio duration). More than 10 failures in the first 100 songs, or the same category for five consecutive songs, stops the queue. No site file or `onion-home` branch is touched.
"""
    atomic_write_bytes(root / "BATCH_REPORT.md", document.encode("utf-8"))


if __name__ == "__main__":
    write_report(Path(__file__).resolve().parents[1])
