"""Resumable GPU library pass and conditional piano-routing pass.

Run with the project WSL interpreter: ``python -m pipeline.batch start``.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from collections import Counter

from . import layers, patterns, quantize, routing, separation, tuning
from .common import (ModelUnavailable, Note, StageError, atomic_write_bytes,
                     digest, fingerprint, read, save)
from .config import PATTERN_SIMILARITY_THRESHOLD, PERIOD_ACCEPTANCE_RATE
from .runner import Runner

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "output" / "liked_songs_analysis.sqlite"
PILOT_SELECTION = ROOT / "pilot" / "selection.json"
DEFAULT_BATCH_DIR = ROOT / "batch"
DEFAULT_OUTPUT_ROOT = ROOT / "outputs"
PILOT_GPU_MIN_PER_SONG = 4.8
ROUTING_GPU_MIN_PER_SONG = 1.7
GPU_STAGES = {"beats", "separation", "transcription", "routing"}
STOP_SIGNAL = signal.SIGTERM


class BatchStopped(BaseException):
    """Raised by the stop signal handler so the active stage stays uncommitted."""


class SongTimeout(TimeoutError):
    pass


def now_utc():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def atomic_csv(path: Path, rows, fieldnames):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    atomic_write_bytes(path, output.getvalue().encode("utf-8"))


def storage_preflight(batch_dir: Path, song_count: int, output_root: Path):
    """Measure the 25 pilots, including shared reference caches they reused."""
    if Path(batch_dir).resolve() == (ROOT / 'library').resolve():
        from .library import storage_preflight as library_storage_preflight
        return library_storage_preflight(song_count, output_root)
    pilot = read(PILOT_SELECTION)["songs"]
    totals = Counter()
    per_song = []
    categories = ["stem_bass", "stem_drums", "stem_other", "stem_vocals", "no_drums",
                  "A440_corrected_audio", "MIDI", "JSON", "click.wav", "click_dbn.wav",
                  "other_WAV", "other_files"]
    for song in pilot:
        folders = {ROOT / "outputs" / song["track_id"]}
        if song.get("cached_reference"):
            folders.add(ROOT / "outputs" / song["cached_reference"])
        sizes = Counter()
        for folder in folders:
            for path in folder.rglob("*"):
                if not path.is_file():
                    continue
                name, suffix = path.name.lower(), path.suffix.lower()
                if suffix == ".json":
                    category = "JSON"
                elif suffix in (".mid", ".midi"):
                    category = "MIDI"
                elif suffix == ".wav" and path.parent.name == "stems":
                    category = "stem_" + path.stem
                elif name == "no_drums.wav":
                    category = "no_drums"
                elif name == "tuning_corrected.wav":
                    category = "A440_corrected_audio"
                elif name in ("click.wav", "click_dbn.wav"):
                    category = name
                elif suffix == ".wav":
                    category = "other_WAV"
                else:
                    category = "other_files"
                sizes[category] += path.stat().st_size
        totals.update(sizes)
        per_song.append({"track_id": song["track_id"], "artist": song["artist"],
            "title": song["title"], **{name: sizes[name] for name in categories},
            "total_bytes": sum(sizes.values())})
    measured = sum(totals.values())
    projected = measured / len(pilot) * song_count
    free = shutil.disk_usage(output_root if output_root.exists() else ROOT).free
    result = {"schema_version": 1, "measured_at": now_utc(), "pilot_song_count": len(pilot),
        "measurement_scope": "25 pilot track-ID folders plus the two shared reference caches; includes existing diagnostic and duplicate cached assets",
        "artifact_bytes": dict(totals), "pilot_total_bytes": measured,
        "mean_bytes_per_song": measured / len(pilot), "projected_song_count": song_count,
        "projected_total_bytes": projected, "free_bytes_before_run": free,
        "projection_share_of_free": projected / free, "stop_limit_share": .70,
        "passed": projected <= .70 * free}
    save(batch_dir / "storage_preflight.json", result)
    atomic_csv(batch_dir / "storage_per_song.csv", per_song,
               ["track_id", "artist", "title", *categories, "total_bytes"])
    if not result["passed"]:
        raise StageError(f"Disk gate failed: projection {projected / 1e9:.1f} GB exceeds "
                         f"70% of {free / 1e9:.1f} GB free; no stages started")
    print(f"Disk gate: {projected / 1e9:.1f} GB projected for {song_count} songs; "
          f"{projected/free:.1%} of {free / 1e9:.1f} GB free (limit 70%)", flush=True)
    return result


def build_dataset(batch_dir: Path):
    """Match all manifest IDs to local files and exclude the 25 cached pilots."""
    if Path(batch_dir).resolve() == (ROOT / 'library').resolve():
        from .library import dataset
        return dataset()
    if Path(batch_dir).resolve() == (ROOT / 'mvp').resolve():
        from .mvp import dataset
        return dataset()
    if not DATABASE.is_file():
        raise StageError(f"Spotify library manifest database is missing: {DATABASE}")
    if not PILOT_SELECTION.is_file():
        raise StageError(f"Pilot selection is missing: {PILOT_SELECTION}")
    batch_dir.mkdir(parents=True, exist_ok=True)
    pilot_rows = json.loads(PILOT_SELECTION.read_text(encoding="utf-8"))["songs"]
    pilot_ids = {row["track_id"] for row in pilot_rows}
    audio_dir = ROOT / "liked-audio"
    local = {}
    for path in audio_dir.glob("*.mp3"):
        match = re.search(r"\[([A-Za-z0-9]{20,30})\](?:\.mp3)?$", path.name, re.I)
        if match:
            local[match.group(1)] = path.resolve()
    if len(local) != len(list(audio_dir.glob("*.mp3"))):
        raise StageError("At least one local MP3 has no bracketed Spotify track ID")
    with sqlite3.connect(DATABASE) as connection:
        records = connection.execute(
            "SELECT track_id,title,artist,duration_ms FROM spotify_tracks ORDER BY track_id"
        ).fetchall()
    manifest_ids = {row[0] for row in records}
    if len(records) != len(manifest_ids):
        raise StageError("The Spotify manifest contains duplicate track IDs")
    missing_ids = manifest_ids - local.keys()
    unexpected = local.keys() - manifest_ids
    if unexpected:
        raise StageError(f"Local audio IDs absent from the manifest: {sorted(unexpected)[:5]}")
    missing_rows = [
        {"track_id": row[0], "artist": row[2] or "", "title": row[1] or "",
         "duration_s": row[3] / 1000 if row[3] else "", "reason": "no_local_mp3"}
        for row in records if row[0] in missing_ids
    ]
    atomic_csv(batch_dir / "missing_audio.csv", missing_rows,
               ["track_id", "artist", "title", "duration_s", "reason"])
    if len(pilot_ids) != 25 or not pilot_ids <= manifest_ids:
        raise StageError("Pilot exclusion does not match the expected 25 manifest songs")
    songs = []
    for track_id, title, artist, duration_ms in records:
        if track_id not in local or track_id in pilot_ids:
            continue
        if not duration_ms or duration_ms <= 0:
            raise StageError(f"Manifest duration is missing for {track_id}")
        try:
            import soundfile as sf
            audio_duration_s = float(sf.info(local[track_id]).duration)
        except Exception as error:
            raise StageError(f"Cannot read audio duration for {track_id}: {type(error).__name__}") from error
        songs.append({"id": track_id, "track_id": track_id, "artist": artist or "Unknown artist",
                      "title": title or track_id, "audio_path": str(local[track_id]),
                      "duration_s": audio_duration_s, "manifest_duration_s": duration_ms / 1000.0,
                      "instrument_hints": None,
                      "genius_url": None})
    songs.sort(key=lambda song: (song["duration_s"], song["id"]))
    if len(songs) != len(local) - 25:
        raise StageError(f"Expected {len(local)-25} batch songs, found {len(songs)}")
    if len(missing_rows) != 75:
        raise StageError(f"Expected 75 missing-audio manifest rows, found {len(missing_rows)}")
    manifest = {"schema_version": 1, "created_at": now_utc(), "source_database": str(DATABASE),
                "local_audio_count": len(local), "manifest_count": len(records),
                "pilot_skipped_count": len(pilot_ids), "missing_audio_count": len(missing_rows),
                "batch_count": len(songs), "order": "duration_s ascending, then track_id",
                "songs": songs}
    save(batch_dir / "manifest.json", manifest)
    return songs, manifest


def error_category(error):
    text = str(error).casefold()
    kind = type(error).__name__.casefold()
    if isinstance(error, SongTimeout) or "timeout" in text or "timed out" in text:
        return "timeout"
    if "outofmemory" in kind or "out of memory" in text or "cuda error: out of memory" in text:
        return "cuda_oom"
    if "did not emit eos" in text or "strict-eos" in text:
        return "transcription_eos"
    if isinstance(error, ModelUnavailable) or "gatedrepo" in kind or "huggingface" in text:
        return "model_unavailable"
    if any(token in text for token in ("connectionerror", "connection reset", "name or service", "http error")):
        return "network"
    if isinstance(error, (FileNotFoundError, PermissionError)):
        return "input_or_filesystem"
    if isinstance(error, StageError):
        return "stage_error"
    return "unexpected"


def timeout_diagnostic_budget(error, stage, batch_dir, retry_count=0):
    """Only a failed library transcription gets the separate 45-minute retry."""
    if (Path(batch_dir).resolve() == (ROOT / 'library').resolve()
            and stage == 'transcription' and isinstance(error, SongTimeout)
            and retry_count == 0):
        return 45 * 60.0
    return 0.0


def append_log(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def new_progress(manifest, songs, device, output_root):
    return {"schema_version": 1, "status": "starting", "created_at": now_utc(),
            "updated_at": now_utc(), "manifest_count": manifest["manifest_count"],
            "local_audio_count": manifest["local_audio_count"],
            "pilot_skipped_count": manifest["pilot_skipped_count"],
            "missing_audio_skipped_count": manifest["missing_audio_count"],
            "total": len(songs), "device": device, "output_root": str(output_root),
            "song_ids": [row["id"] for row in songs],
            "songs": {row["id"]: {"status": "pending", "duration_s": row["duration_s"]}
                      for row in songs},
            "active_song_id": None, "active_stage": None, "failure_categories": {},
            "gpu_seconds_pass1": 0.0, "gpu_seconds_pass2": 0.0,
            "processed_audio_seconds": 0.0, "processed_compute_seconds": 0.0,
            "measured_seconds_per_audio_second": None,
            "routing_triggered_count": 0, "routing_completed_count": 0,
            "routing_failures": {}, "stop_reason": None}


def write_progress(progress_path, progress):
    states = list(progress.get("songs", {}).values())
    for state in states:
        if state.get("status") == "failed" and state.get("failure_category") == "unexpected":
            state["failure_category"] = error_category(RuntimeError(state.get("error", "")))
    progress["failure_categories"] = dict(Counter(
        state.get("failure_category", "unexpected") for state in states
        if state.get("status") == "failed"))
    progress["routing_failures"] = dict(Counter(
        state.get("pass2_error_category", "unexpected") for state in states
        if state.get("pass2_status") == "failed"))
    progress["routing_triggered_count"] = sum(bool(state.get("pass2_triggered")) for state in states)
    progress["routing_completed_count"] = sum(state.get("pass2_status") == "complete" for state in states)
    progress["updated_at"] = now_utc()
    save(progress_path, progress)


def refresh_report(batch_dir, output_root):
    if Path(batch_dir).resolve() == (ROOT / 'mvp').resolve():
        from .mvp import write_report
        write_report()
        return
    if Path(batch_dir).resolve() == (ROOT / 'library').resolve():
        from .library import write_report
        write_report()
        return
    if Path(batch_dir).resolve() != DEFAULT_BATCH_DIR.resolve():
        return
    try:
        from .batch_report import write_report
        write_report(ROOT, batch_dir, output_root)
    except Exception as error:
        print(f"Batch report refresh failed: {type(error).__name__}: {error}", flush=True)


def report_status(batch_dir: Path):
    path = batch_dir / "progress.json"
    if not path.is_file():
        print(f"No batch progress found at {path}")
        return 1
    data = read(path)
    states = [row.get("status", "pending") for row in data.get("songs", {}).values()]
    done, failed, skipped = states.count("done"), states.count("failed"), states.count("skipped")
    remaining = max(0, data.get("total", len(states)) - done - failed - skipped)
    print(f"Status: {data.get('status', 'unknown')}")
    print(f"Songs: {done} done / {failed} failed / {remaining} remaining / {skipped} skipped")
    print(f"Missing local audio skipped: {data.get('missing_audio_skipped_count', 0)}")
    print(f"Failures by category: {json.dumps(data.get('failure_categories', {}), sort_keys=True)}")
    route_done = data.get("routing_completed_count", 0)
    route_triggered = data.get("routing_triggered_count", 0)
    if route_done or route_triggered:
        print(f"Pass 2 routing: {route_done} completed / {route_triggered} triggered")
    ratio = data.get("measured_seconds_per_audio_second")
    manifest_path = batch_dir / "manifest.json"
    durations = ({row["id"]: row.get("duration_s", 0)
                  for row in read(manifest_path).get("songs", [])}
                 if manifest_path.is_file() else {})
    remaining_audio = sum(
        float(row.get("duration_s", durations.get(song_id, 0)))
        for song_id, row in data.get("songs", {}).items()
        if row.get("status") not in ("done", "failed", "skipped")
    )
    eta = remaining_audio * ratio if ratio is not None else None
    if eta is not None:
        print(f"Measured pace: {ratio:.3f} compute seconds/audio-second; ETA about {eta/3600:.1f} hours")
    elif remaining:
        print("ETA: collecting measured seconds per audio-second")
    print(f"Active: {data.get('active_song_id') or 'none'} / {data.get('active_stage') or 'idle'}")
    print(f"GPU time: Pass 1 {data.get('gpu_seconds_pass1', 0)/3600:.2f} h; "
          f"Pass 2 {data.get('gpu_seconds_pass2', 0)/3600:.2f} h")
    return 0


def _is_oom(error):
    return error_category(error) == "cuda_oom"


def _clear_cuda_cache():
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _model_preflight(device):
    from .__main__ import preflight
    preflight("run")
    import torch
    from .backends.muscriptor import MuScriptorBackend
    from huggingface_hub import get_token
    if not get_token():
        raise StageError("Batch preflight failed: no Hugging Face token is available")
    if device == "cuda" and not torch.cuda.is_available():
        raise StageError("Batch preflight failed: CUDA was requested but is unavailable")
    backend = MuScriptorBackend(device=device, model="large")
    backend.load()  # Verify account access, checkpoint availability and GPU loading before stages.
    return backend


def _run_stage(runner, name, call, progress, progress_path, log_path,
               audio_duration_s, pass_number=1):
    stop_path = Path(progress_path).parent / 'stop.requested'
    if stop_path.exists():
        raise BatchStopped(stop_path.read_text())
    prior_path = runner.output / f"{name}.json"
    if prior_path.is_file():
        try:
            if read(prior_path).get("status") == "failed":
                runner.force.add(name)
        except (ValueError, OSError):
            runner.force.add(name)
    started = time.monotonic()
    retries = 0
    progress["active_stage"] = name
    write_progress(progress_path, progress)
    while True:
        try:
            data = call()
            record = runner.stage_records.get(name, {})
            elapsed = time.monotonic() - started
            cached = bool(record.get("cache_hit")) or bool(name=='beats' and data.get('predictions_cached'))
            status = "cached" if cached else "complete"
            entry = {"timestamp": now_utc(), "song_id": runner.song["id"], "stage": name,
                     "status": status, "seconds": round(elapsed, 3),
                     "error_category": None, "retry_count": retries,
                     "pass": pass_number}
            append_log(log_path, entry)
            if not cached and name in GPU_STAGES:
                key = "gpu_seconds_pass1" if pass_number == 1 else "gpu_seconds_pass2"
                progress[key] = float(progress.get(key, 0)) + elapsed
            if (Path(progress_path).parent.resolve() == (ROOT / 'library').resolve()
                    and name == 'transcription' and not cached):
                from .library import check_transcription_pace, run_events
                library_songs = read(Path(progress_path).parent / 'manifest.json')['songs']
                warning = check_transcription_pace(run_events(), library_songs)
                if warning:
                    from .library import write_report
                    write_report()
            if stop_path.exists():
                raise BatchStopped(stop_path.read_text())
            return data
        except BatchStopped:
            elapsed = time.monotonic() - started
            append_log(log_path, {"timestamp": now_utc(), "song_id": runner.song["id"],
                "stage": name, "status": "interrupted", "seconds": round(elapsed, 3),
                "error_category": "stopped", "retry_count": retries, "pass": pass_number})
            raise
        except Exception as error:
            if _is_oom(error) and retries == 0:
                retries += 1
                _clear_cuda_cache()
                runner.force.add(name)
                print(f"{runner.song['id']}: {name} CUDA OOM; cleared cache and retrying once", flush=True)
                continue
            elapsed = time.monotonic() - started
            category = error_category(error)
            append_log(log_path, {"timestamp": now_utc(), "song_id": runner.song["id"],
                "stage": name, "status": "failed", "seconds": round(elapsed, 3),
                "error_category": category, "error": str(error)[:1200],
                "retry_count": retries, "pass": pass_number})
            if name in GPU_STAGES:
                key = "gpu_seconds_pass1" if pass_number == 1 else "gpu_seconds_pass2"
                progress[key] = float(progress.get(key, 0)) + elapsed
            raise


def _run_song(song, progress, progress_path, log_path, output_root, device, shared_backend):
    song_started = time.monotonic()
    runner = Runner(song, output_root=output_root, device=device, backend="muscriptor", model="large")
    if shared_backend is not None:
        runner.model_instance = shared_backend
    audio_duration = runner.audio_duration()
    timeout = max(20 * 60.0, 5.0 * audio_duration)
    progress["songs"][song["id"]] = {"status": "running", "duration_s": audio_duration,
        "manifest_duration_s": song.get("manifest_duration_s"),
        "started_at": now_utc(), "timeout_s": timeout}
    progress["active_song_id"] = song["id"]
    write_progress(progress_path, progress)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    library_run = Path(progress_path).parent.resolve() == (ROOT / 'library').resolve()
    diagnostic_timeout_retry = None
    try:
        corrected = runner.output / "tuning_corrected.wav"
        tuning_data = _run_stage(runner, "tuning", lambda: runner.stage(
            "tuning", lambda: tuning.run(song["audio_path"], corrected),
            {"source_sha256": runner.source_hash, "target_cents": tuning.TARGET_TUNING_CENTS,
             "analysis_sample_rate": tuning.ANALYSIS_SAMPLE_RATE},
            lambda data: [corrected] if data and data.get("corrected") else []),
            progress, progress_path, log_path, audio_duration)
        runner.transcription_audio_path = Path(tuning_data["transcription_audio_path"])
        grid = _run_stage(runner, "beats", runner.grid, progress, progress_path, log_path,
                          audio_duration)
        _run_stage(runner, "separation", runner.stems, progress, progress_path, log_path,
                   audio_duration)
        original_song = runner.song
        if tuning_data.get("corrected"):
            runner.song = {**song, "audio_path": str(runner.transcription_audio_path)}
        transcribe_inputs = {"backend": runner.backend, "model": runner.model,
            "hints": song.get("instrument_hints"), "device": runner.device}
        if tuning_data.get("corrected"):
            transcribe_inputs.update(input_audio_sha256=digest(runner.transcription_audio_path),
                                     tuning=fingerprint(tuning_data))
        midi = runner.output / "transcription.mid"
        try:
            try:
                transcribed = _run_stage(runner, "transcription", lambda: runner.stage(
                    "transcription", runner.transcribe, transcribe_inputs, [midi]),
                    progress, progress_path, log_path, audio_duration)
            except Exception as error:
                retry_budget = timeout_diagnostic_budget(
                    error, "transcription", Path(progress_path).parent)
                if not retry_budget:
                    raise
                diagnostic_timeout_retry = {
                    "status": "running", "reason": "original_transcription_timeout",
                    "budget_s": retry_budget, "started_at": now_utc(),
                    "timeout_s": timeout, "prior_stage": "transcription"}
                progress["songs"][song["id"]]["timeout_diagnostic_retry"] = diagnostic_timeout_retry
                write_progress(progress_path, progress)
                _clear_cuda_cache()
                runner.model_instance = None
                runner.force.add("transcription")
                signal.setitimer(signal.ITIMER_REAL, retry_budget)
                retry_started = time.monotonic()
                try:
                    transcribed = _run_stage(runner, "transcription", lambda: runner.stage(
                        "transcription", runner.transcribe, transcribe_inputs, [midi]),
                        progress, progress_path, log_path, audio_duration)
                    diagnostic_timeout_retry.update(
                        status="complete", elapsed_s=round(time.monotonic()-retry_started, 3),
                        finished_at=now_utc())
                    progress["songs"][song["id"]]["timeout_diagnostic_retry"] = diagnostic_timeout_retry
                    write_progress(progress_path, progress)
                except Exception as retry_error:
                    diagnostic_timeout_retry.update(
                        status="failed", elapsed_s=round(time.monotonic()-retry_started, 3),
                        finished_at=now_utc(), failure_category=error_category(retry_error),
                        error=str(retry_error)[:1000])
                    progress["songs"][song["id"]]["timeout_diagnostic_retry"] = diagnostic_timeout_retry
                    write_progress(progress_path, progress)
                    raise
                finally:
                    signal.setitimer(signal.ITIMER_REAL, 0)
        finally:
            runner.song = original_song
        quantized = _run_stage(runner, "quantize", lambda: runner.stage(
            "quantize", lambda: quantize.quantize(transcribed["notes"], grid, runner.steps,
                runner.step_candidates, runner.grid_fit_tolerance_ms),
            {"notes": fingerprint(transcribed), "grid": fingerprint(grid), "steps": runner.steps,
             "step_candidates": runner.step_candidates,
             "grid_fit_tolerance_ms": runner.grid_fit_tolerance_ms}),
            progress, progress_path, log_path, audio_duration)
        layer_data = _run_stage(runner, "layers", lambda: runner.stage(
            "layers", lambda: layers.split_notes(quantized["notes"], grid,
                quantized.get("instrument_steps_per_bar", {})),
            {"quantized": fingerprint(quantized), "grid": fingerprint(grid)}),
            progress, progress_path, log_path, audio_duration)
        compressed = _run_stage(runner, "patterns", lambda: runner.stage(
            "patterns", lambda: patterns.run(quantized, grid, runner.threshold,
                runner.onset_distance, runner.pitch_distance, runner.section_window,
                runner.min_section_bars, runner.contour_distance, layer_data["pattern_notes"]),
            {"quantized": fingerprint(quantized), "grid": fingerprint(grid),
             "layers": fingerprint(layer_data), "novelty_threshold": runner.threshold,
             "onset_distance": runner.onset_distance, "pitch_distance": runner.pitch_distance,
             "contour_distance": runner.contour_distance, "section_window": runner.section_window,
             "min_section_bars": runner.min_section_bars,
             "boundary_shuffles": runner.boundary_shuffles}),
            progress, progress_path, log_path, audio_duration)
        if Path(progress_path).parent.name == 'mvp':
            from .core import recompute
            _run_stage(runner, 'rhythm', lambda: recompute(song, output_root,
                refresh_beats=False, evaluate_grids=False), progress, progress_path, log_path, audio_duration)
        elif library_run:
            from .core import recompute
            from .retention import apply as retain_stems
            _run_stage(runner, 'rhythm', lambda: recompute(song, output_root,
                refresh_beats=False, evaluate_grids=False, use_routing=False,
                baseline_shuffles=0), progress, progress_path, log_path, audio_duration)
            retain_stems(runner.output, song.get('separation', {}).get(
                'keep_stems', ['vocals', 'no_drums']))
            if diagnostic_timeout_retry and diagnostic_timeout_retry.get("status") == "complete":
                report_path = runner.output / "report.json"
                record = read(report_path)
                data = record.get("data", record)
                data["timeout_diagnostic_retry"] = diagnostic_timeout_retry
                data["status"] = "ok_with_warning"
                if "data" in record:
                    record["data"] = data
                else:
                    record = data
                save(report_path, record)
        if runner.model_instance is not None:
            shared_backend = runner.model_instance
        song_state = progress["songs"][song["id"]]
        song_state.update(status="done", finished_at=now_utc(), last_stage="patterns",
                          result_status='ok_with_warning' if (transcribed.get('eos_warning') or
                              (diagnostic_timeout_retry and diagnostic_timeout_retry.get('status') == 'complete')) else 'ok',
                          source_sha256=runner.source_hash,
                          grid_suspect=bool(quantized.get("grid_suspect")),
                          pattern_acceptance={"status": "review", "pass": False,
                              "scored": False,
                              "reason": "No user reference window was provided; exploratory pattern statistics only, consistent with the pilot convention",
                              "similarity_threshold": PATTERN_SIMILARITY_THRESHOLD,
                              "acceptance_rate": PERIOD_ACCEPTANCE_RATE,
                              "pattern_count": len(compressed.get("patterns", [])),
                              "repeating_phrase_count": len(compressed.get("repeating_phrases", [])),
                              "rhythmic_section_count": len(compressed.get("rhythmic_sections", []))})
        progress["processed_audio_seconds"] += audio_duration
        elapsed = max(.001, time.monotonic() - song_started)
        progress["processed_compute_seconds"] += elapsed
        if progress["processed_audio_seconds"] > 0:
            progress["measured_seconds_per_audio_second"] = (
                progress["processed_compute_seconds"] / progress["processed_audio_seconds"])
        return shared_backend
    except BatchStopped:
        progress["songs"][song["id"]].update(status="pending", interrupted_at=now_utc(),
                                               last_stage=progress.get("active_stage"))
        raise
    except Exception as error:
        category = error_category(error)
        progress["songs"][song["id"]].update(status="failed", failed_at=now_utc(),
            failure_category=category, error=str(error)[:1200], last_stage=progress.get("active_stage"))
        progress["failure_categories"][category] = progress["failure_categories"].get(category, 0) + 1
        if category == "timeout":
            _clear_cuda_cache()
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        progress["active_song_id"] = None
        progress["active_stage"] = None
        write_progress(progress_path, progress)


def _run_pass2(songs, progress, progress_path, log_path, output_root, device, shared_backend):
    progress["pass2_status"] = "running"
    write_progress(progress_path, progress)
    for song in songs:
        state = progress["songs"].get(song["id"], {})
        if state.get("status") != "done":
            continue
        if state.get("pass2_status") in ("complete", "not_triggered"):
            continue
        out = output_root / song["id"]
        transcription_path = out / "transcription.json"
        if not transcription_path.is_file():
            continue
        transcription = read(transcription_path).get("data", {})
        notes = transcription.get("notes", [])
        if not any(row.get("instrument") == "acoustic_piano" for row in notes):
            state["pass2_status"] = "not_triggered"
            state["pass2_triggered"] = False
            continue
        state["pass2_triggered"] = True
        state["pass2_status"] = "running"
        progress["routing_triggered_count"] = sum(
            bool(item.get("pass2_triggered")) for item in progress["songs"].values())
        progress["active_song_id"] = song["id"]
        progress["active_stage"] = "routing"
        runner = Runner(song, output_root=output_root, device=device, backend="muscriptor", model="large")
        runner.model_instance = shared_backend
        tuning_record = read(out / "tuning.json") if (out / "tuning.json").is_file() else {}
        tuning_data = tuning_record.get("data", {})
        source_path = Path(tuning_data.get("transcription_audio_path", song["audio_path"]))
        no_drums = out / "routed_sources" / "no_drums.wav"
        routed_midi = out / "transcription_routed.mid"

        def do_route():
            separated = separation.run_no_drums(source_path, no_drums, device)
            result = routing.apply_piano_route(runner.transcriber(), notes,
                separated["no_drums_path"], ["acoustic_piano"], target_instrument="acoustic_piano",
                tuning_corrected=False)
            routed_notes = [Note(**row) for row in result["merged_notes"]]
            runner.transcriber()._write_midi(routed_notes, routed_midi)
            return {**result, "separation": separated, "routed_midi_path": str(routed_midi),
                    "trigger": "full_mix MuScriptor emitted acoustic_piano notes",
                    "route_input": "no_drums", "model": "MuScriptor Large",
                    "hints": ["acoustic_piano"], "tuning_corrected_source": bool(tuning_data.get("corrected"))}

        try:
            signal.setitimer(signal.ITIMER_REAL, max(20 * 60.0, 5.0 * float(song["duration_s"])))
            _run_stage(runner, "routing", lambda: runner.stage("routing", do_route,
                {"full_mix_notes": fingerprint(notes), "source_audio_sha256": digest(source_path),
                 "route": {"source": "no_drums", "instrument": "acoustic_piano",
                           "hints": ["acoustic_piano"], "model": "large"}},
                [no_drums, routed_midi]), progress, progress_path, log_path,
                float(song["duration_s"]), pass_number=2)
            state["pass2_status"] = "complete"
            if Path(progress_path).parent.name == 'mvp':
                from .core import recompute
                recompute(song, output_root, refresh_beats=False, evaluate_grids=False)
            state.pop("pass2_error", None)
            state.pop("pass2_error_category", None)
            progress["routing_completed_count"] = sum(
                item.get("pass2_status") == "complete" for item in progress["songs"].values())
        except Exception as error:
            category = error_category(error)
            state["pass2_status"] = "failed"
            state["pass2_error_category"] = category
            state["pass2_error"] = str(error)[:1000]
            progress["routing_failures"][category] = progress["routing_failures"].get(category, 0) + 1
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            progress["active_song_id"] = None
            progress["active_stage"] = None
        write_progress(progress_path, progress)
    progress["pass2_status"] = "complete"
    write_progress(progress_path, progress)


def run_batch(batch_dir=DEFAULT_BATCH_DIR, output_root=DEFAULT_OUTPUT_ROOT,
              device="cuda", limit=None, resume=False, run_pass2=True):
    batch_dir, output_root = Path(batch_dir).resolve(), Path(output_root).resolve()
    is_library = batch_dir == (ROOT / 'library').resolve()
    if is_library:
        run_pass2 = False
    batch_dir.mkdir(parents=True, exist_ok=True)
    pid_path, log_path = batch_dir / "batch.pid", batch_dir / "run_log.jsonl"
    if pid_path.is_file():
        try:
            old_pid = int(pid_path.read_text().strip())
            if old_pid != os.getpid():
                os.kill(old_pid, 0)
        except (ValueError, ProcessLookupError, PermissionError):
            pid_path.unlink(missing_ok=True)
        else:
            if old_pid == os.getpid():
                old_pid = None
            if old_pid is not None:
                raise StageError(f"A batch process is already running with PID {old_pid}")
    # These metadata writes do not run stages. All gates precede stage execution.
    songs, manifest = build_dataset(batch_dir)
    if is_library:
        from .library import preflight
        preflight(songs, output_root)
    elif resume and (batch_dir / "storage_preflight.json").is_file() and (batch_dir / "progress.json").is_file():
        prior = read(batch_dir / "progress.json")
        finished = sum(state.get("status") in ("done", "failed", "skipped")
                       for state in prior.get("songs", {}).values())
        pending = max(0, len(songs) - finished)
        baseline = read(batch_dir / "storage_preflight.json")
        free = shutil.disk_usage(output_root).free
        projected_remaining = baseline["mean_bytes_per_song"] * pending
        save(batch_dir / "storage_resume_check.json", {"checked_at": now_utc(),
            "remaining_songs": pending, "remaining_projected_bytes": projected_remaining,
            "free_bytes": free, "passed": projected_remaining <= .70 * free})
        if projected_remaining > .70 * free:
            raise StageError("Resume disk gate failed: remaining projection exceeds 70% of current free space")
    else:
        storage_preflight(batch_dir, len(songs), output_root)
    shared_backend = _model_preflight(device)
    if limit is not None:
        songs = songs[:limit]
    progress_path = batch_dir / "progress.json"
    expected_ids = [song["id"] for song in songs]
    if is_library and progress_path.is_file():
        progress = read(progress_path)
        if progress.get("song_ids") != expected_ids:
            raise StageError("Prepared library manifest/order differs from progress.json")
        for song_id in expected_ids:
            progress["songs"].setdefault(song_id, {"status": "pending"})
        progress["status"] = "running"
        progress["stop_reason"] = None
    elif resume and progress_path.is_file():
        progress = read(progress_path)
        if progress.get("song_ids") != expected_ids:
            raise StageError("Cannot resume: the batch manifest/order differs from progress.json")
        for song_id in expected_ids:
            progress["songs"].setdefault(song_id, {"status": "pending"})
        progress["status"] = "running"
        progress["stop_reason"] = None
    else:
        progress = new_progress(manifest, songs, device, output_root)
        write_progress(progress_path, progress)
    atomic_write_bytes(pid_path, str(os.getpid()).encode("ascii"))
    (batch_dir / 'stop.requested').unlink(missing_ok=True)
    old_signal_handler = signal.getsignal(STOP_SIGNAL)
    signal.signal(STOP_SIGNAL, lambda *_: (_ for _ in ()).throw(BatchStopped("Stop requested")))
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(SongTimeout("Song exceeded its timeout")))
    progress["status"] = "running"
    progress["started_at"] = progress.get("started_at") or now_utc()
    progress["resumed_at"] = now_utc() if resume else None
    write_progress(progress_path, progress)
    refresh_report(batch_dir, output_root)
    is_mvp = batch_dir == (ROOT / 'mvp').resolve()
    if is_mvp:
        from .mvp import start_lyrics
        start_lyrics()
    elif is_library:
        from .library import start_lyrics
        start_lyrics()
    try:
        attempted_this_run = 0
        for index, song in enumerate(songs):
            state = progress["songs"].get(song["id"], {})
            if state.get("status") in ("done", "failed", "skipped"):
                continue
            try:
                shared_backend = _run_song(song, progress, progress_path, log_path,
                                           output_root, device, shared_backend)
            except BatchStopped:
                progress["status"] = "stopped"
                progress["stop_reason"] = "user_requested"
                write_progress(progress_path, progress)
                print("Batch stopped. Use `python -m pipeline.batch resume` to continue.", flush=True)
                return 2
            except Exception as error:
                print(f"{song['id']}: failed ({error_category(error)}): {str(error)[:300]}", flush=True)
                if error_category(error) == "model_unavailable":
                    progress["status"] = "stopped_preflight_or_model_access"
                    progress["stop_reason"] = "model_unavailable"
                    write_progress(progress_path, progress)
                    return 1
            attempted_this_run += 1
            write_progress(progress_path, progress)
            report_interval = 50 if is_library else 10
            if attempted_this_run % report_interval == 0:
                refresh_report(batch_dir, output_root)
            if is_mvp and not (batch_dir/'eta_after20.json').exists() and sum(
                    s.get('status') in ('done','failed') for s in progress['songs'].values()) >= 20:
                print('Measured MVP ETA after 20 songs:', flush=True)
                report_status(batch_dir)
                remaining_audio=sum(s['duration_s'] for s in songs if
                    progress['songs'][s['id']].get('status') not in ('done','failed','skipped'))
                save(batch_dir/'eta_after20.json',{'timestamp':now_utc(),
                    'done':sum(s.get('status')=='done' for s in progress['songs'].values()),
                    'failed':sum(s.get('status')=='failed' for s in progress['songs'].values()),
                    'seconds_per_audio_second':progress['measured_seconds_per_audio_second'],
                    'remaining_pass1_eta_hours':remaining_audio*progress['measured_seconds_per_audio_second']/3600,
                    'scope':'Pass 1; Pass 2 routing and any remaining CPU lyrics are additional'})
                refresh_report(batch_dir,output_root)
            attempted_prefix = [progress["songs"].get(item["id"], {}).get("status")
                                for item in songs[:min(100, index + 1)]]
            first_hundred_failures = attempted_prefix.count("failed")
            if index < 100 and first_hundred_failures > 10:
                progress["status"] = "stopped_failure_rate"
                progress["stop_reason"] = "more_than_10_percent_failures_in_first_100"
                write_progress(progress_path, progress)
                return 1
            last_five = [progress["songs"].get(item["id"], {}).get("failure_category")
                         for item in songs[max(0, index - 4):index + 1]]
            if len(last_five) == 5 and last_five[0] and len(set(last_five)) == 1:
                progress["status"] = "stopped_repeated_error"
                progress["stop_reason"] = f"five_consecutive_{last_five[0]}_failures"
                write_progress(progress_path, progress)
                return 1
        statuses = [progress["songs"].get(song["id"], {}).get("status") for song in songs]
        if any(status not in ("done", "failed", "skipped") for status in statuses):
            progress["status"] = "incomplete"
            write_progress(progress_path, progress)
            return 1
        if run_pass2:
            try:
                _run_pass2(songs, progress, progress_path, log_path, output_root,
                           device, shared_backend)
            except BatchStopped:
                progress["status"] = "stopped"
                progress["stop_reason"] = "user_requested_during_pass2"
                write_progress(progress_path, progress)
                return 2
        if is_mvp:
            from .mvp import finish
            progress['status'] = 'finalizing_cpu_lyrics_and_join'
            write_progress(progress_path, progress)
            try:
                finish()
            except BatchStopped:
                progress=read(progress_path)
                progress.update(status='stopped',stop_reason='user_requested_during_cpu_finalization')
                write_progress(progress_path,progress)
                return 2
            except Exception as error:
                progress=read(progress_path)
                progress.update(status='stopped_finalization_error',stop_reason=str(error))
                write_progress(progress_path,progress)
                raise
            progress = read(progress_path)
        elif is_library:
            from .library import finish
            progress['status'] = 'finalizing_cpu_lyrics_and_join'
            write_progress(progress_path, progress)
            finish()
            progress = read(progress_path)
        progress["status"] = "complete"
        progress["finished_at"] = now_utc()
        progress["pass1_projected_gpu_hours"] = 64.0 if is_library else len(songs) * PILOT_GPU_MIN_PER_SONG / 60
        progress["pass1_actual_gpu_hours"] = progress.get("gpu_seconds_pass1", 0) / 3600
        progress["pass2_projected_gpu_hours"] = (
            progress.get("routing_triggered_count", 0) * ROUTING_GPU_MIN_PER_SONG / 60)
        progress["pass2_actual_gpu_hours"] = progress.get("gpu_seconds_pass2", 0) / 3600
        write_progress(progress_path, progress)
        refresh_report(batch_dir, output_root)
        print(f"Batch complete: {len(songs)} songs; {progress['pass1_actual_gpu_hours']:.2f} measured GPU-stage hours.", flush=True)
        return 0
    finally:
        if is_mvp:
            from .mvp import stop_lyrics
            stop_lyrics()
        elif is_library:
            from .library import stop_lyrics
            stop_lyrics()
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(STOP_SIGNAL, old_signal_handler)
        pid_path.unlink(missing_ok=True)
        refresh_report(batch_dir, output_root)


def launch_detached(batch_dir=DEFAULT_BATCH_DIR, output_root=DEFAULT_OUTPUT_ROOT,
                    device="cuda", resume=False, limit=None):
    batch_dir, output_root = Path(batch_dir).resolve(), Path(output_root).resolve()
    batch_dir.mkdir(parents=True, exist_ok=True)
    pid_path = batch_dir / "batch.pid"
    if pid_path.is_file():
        try:
            pid = int(pid_path.read_text().strip())
            os.kill(pid, 0)
        except (ValueError, ProcessLookupError, PermissionError):
            pid_path.unlink(missing_ok=True)
        else:
            print(f"Batch already running as PID {pid}")
            return 0
    command = [sys.executable, "-m", "pipeline.batch", "run",
               "--batch-dir", str(batch_dir), "--output-root", str(output_root),
               "--device", device]
    if resume:
        command.append("--resume")
    if limit is not None:
        command.extend(["--limit", str(limit)])
    log_path = (batch_dir / "daemon.log").open("ab", buffering=0)
    nohup = shutil.which("nohup")
    if nohup:
        command = [nohup, *command]
    process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
        stdout=log_path, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
    log_path.close()
    atomic_write_bytes(pid_path, str(process.pid).encode("ascii"))
    print(f"Started detached batch PID {process.pid}; log: {batch_dir / 'daemon.log'}")
    return 0


def stop_batch(batch_dir=DEFAULT_BATCH_DIR):
    batch_dir = Path(batch_dir).resolve()
    pid_path = batch_dir / "batch.pid"
    if not pid_path.is_file():
        print("No active batch PID was found")
        return 1
    pid = int(pid_path.read_text().strip())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pid_path.unlink(missing_ok=True)
        print("The recorded batch process is no longer running")
        return 1
    atomic_write_bytes(batch_dir / 'stop.requested', b'user_requested')
    print(f"Stop requested for PID {pid}; the active stage will finish and its cache will be kept")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Resumable full-library MIDI backend batch")
    subs = parser.add_subparsers(dest="command", required=True)
    for cmd in ("run", "start", "resume"):
        sub = subs.add_parser(cmd)
        sub.add_argument("--batch-dir", type=Path, default=DEFAULT_BATCH_DIR)
        sub.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
        sub.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
        sub.add_argument("--limit", type=int)
        sub.add_argument('--set', choices=('library','mvp'), default='library', dest='dataset')
        if cmd == "run":
            sub.add_argument("--resume", action="store_true")
    for cmd in ("status", "stop"):
        sub = subs.add_parser(cmd)
        sub.add_argument("--batch-dir", type=Path, default=DEFAULT_BATCH_DIR)
        sub.add_argument('--set', choices=('library','mvp'), default='library', dest='dataset')
    args = parser.parse_args(argv)
    if args.dataset == 'mvp':
        args.batch_dir = ROOT / 'mvp'
    elif args.dataset == 'library':
        args.batch_dir = ROOT / 'library'
    try:
        if args.command == "status":
            return report_status(args.batch_dir)
        if args.command == "stop":
            return stop_batch(args.batch_dir)
        if args.command == "start":
            return launch_detached(args.batch_dir, args.output_root, args.device,
                                   resume=False, limit=args.limit)
        if args.command == "resume":
            return launch_detached(args.batch_dir, args.output_root, args.device,
                                   resume=True, limit=args.limit)
        return run_batch(args.batch_dir, args.output_root, args.device,
                         limit=args.limit, resume=bool(getattr(args, "resume", False)))
    except (StageError, OSError, ValueError) as error:
        print(f"Batch failed before or between songs: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
