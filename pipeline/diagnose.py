"""Controlled full-mix versus stem comparison on a common original-time window."""
from dataclasses import asdict, replace
import csv
import math
from pathlib import Path

from .common import StageError, ModelUnavailable, fingerprint, read, save, warning
from .plots import piano_rolls
from .quantize import quantize


def onset_groups(notes):
    times = sorted(n["onset_s"] for n in notes)
    result = []
    for t in times:
        if not result or t - result[-1] > .03:
            result.append(t)
    return result


def run(runner, start, end):
    if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end:
        raise StageError("Diagnostic needs a finite 0 <= start < end window")
    folder = runner.output / f"diagnose_{start:g}_{end:g}_{runner.backend}"
    folder.mkdir(parents=True, exist_ok=True)
    summary_path = runner.output / "diagnose.json"
    try:
        duration = runner.audio_duration()
        if end > duration:
            raise StageError(f"Diagnostic ends after the {duration:.3f}s recording")
        prior_transcription = runner.output / "transcription.json"
        if prior_transcription.exists() and "diagnose" not in runner.force:
            prior = read(prior_transcription)
            if prior.get("model_unavailable") and runner.backend == "muscriptor":
                raise ModelUnavailable("MuScriptor previously unavailable: " + prior["reason"] +
                                       " Resolve access/installation first, then use --force diagnose. No retry or model fallback.")
        grid, separated = runner.grid(), runner.stems(require_all=True)
        for flag in grid["flags"]:
            warning(f"Suspect grid: {flag}", runner.warnings)
        sources = {"full_mix": runner.song["audio_path"], **separated["stems"]}
        key = fingerprint({"audio": runner.source_hash, "stems": separated["stem_sha256"],
                           "backend": runner.backend, "model": runner.model,
                           "grid": grid, "steps": runner.steps, "window": [start, end],
                           "hints": runner.song.get("instrument_hints"), "code": runner.code_hash})
        cached = read(summary_path) if summary_path.exists() else None
        if (cached and cached.get("cache_key") == key and cached.get("status") == "complete"
                and "diagnose" not in runner.force
                and Path(cached["piano_roll_path"]).exists() and Path(cached["table_path"]).exists()):
            print("diagnose cached", flush=True)
            return cached
        import soundfile as sf
        backend = runner.transcriber()
        predictions, table = {}, []
        for name, path in sources.items():
            info = sf.info(path)
            a, b = round(start * info.samplerate), round(end * info.samplerate)
            y, rate = sf.read(path, start=a, stop=b, always_2d=True, dtype="float32")
            crop = folder / f"{name}.wav"
            sf.write(crop, y, rate, subtype="FLOAT")
            cache = folder / f"{name}.json"
            if cache.exists() and "diagnose" not in runner.force and read(cache).get("cache_key") == key:
                notes = read(cache)["notes"]
            else:
                print(f"diagnose: {name} {start:g}–{end:g}s ({runner.backend})", flush=True)
                raw = backend.transcribe(crop, runner.song.get("instrument_hints"))
                notes = [asdict(replace(n, onset_s=n.onset_s + a / rate,
                                        offset_s=n.offset_s + a / rate)) for n in raw
                         if 0 <= n.onset_s < len(y) / rate]
                save(cache, {"cache_key": key, "notes": notes, "source_start_s": a / rate,
                             "warnings": backend.warnings})
            predictions[name] = notes
            onsets = onset_groups(notes)
            # Each attack group contributes once to the timing statistic,
            # independent of the number of simultaneous pitches.
            proxy = [{"onset_s": t, "offset_s": t + .01, "pitch": 60,
                      "velocity": None, "instrument": "attack_groups"} for t in onsets]
            quantized = quantize(proxy, grid, runner.steps)
            bar_counts = {bar["bar"]: sum(bar["start_s"] <= t < bar["end_s"] for t in onsets)
                          for bar in grid["bars"] if bar["start_s"] < end and bar["end_s"] > start}
            mapped = quantized["notes"]
            table.append({"source": name, "note_count": len(notes), "onset_count": len(onsets),
                          "onset_count_per_bar": bar_counts,
                          "onsets_outside_grid": len(quantized["excluded_notes"]),
                          "within_30ms_share": sum(abs(n["quantization_error_ms"]) <= 30 for n in mapped) / len(mapped) if mapped else None})
        plot = folder / "piano_rolls.png"
        piano_rolls(predictions, start, end, grid, plot)
        csv_path = folder / "comparison.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(table[0]))
            writer.writeheader()
            writer.writerows(table)
        summary = {"status": "complete", "cache_key": key, "window_s": [start, end],
                   "backend": runner.backend, "model": runner.model, "table": table,
                   "piano_roll_path": str(plot), "table_path": str(csv_path),
                   "warnings": runner.warnings + backend.warnings,
                   "interpretation": "Controlled transcription diagnostic, not an accuracy score. More notes or closer grid adherence alone do not prove better transcription; inspect the rolls and audio, and use manual note labels to establish accuracy."}
    except Exception as error:
        summary = {"status": "failed", "window_s": [start, end], "reason": str(error),
                   "backend": runner.backend, "warnings": runner.warnings}
        print(f"diagnose failed: {error}", flush=True)
    save(summary_path, summary)
    report_path = runner.output / "report.json"
    if report_path.exists():
        report = read(report_path)
        report["diagnostic"] = summary
        report.setdefault("acceptance", {})["diagnose_20s"] = (
            summary["status"] == "complete" and math.isclose(end - start, 20))
        save(report_path, report)
    return summary
