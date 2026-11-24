"""Prepare, transcribe and inspect Phase 3 Meaningful Love AMT comparison inputs."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
from pathlib import Path
import shutil
import tempfile

import numpy as np

from .backend import MODEL_ROOT, select
from .policy import enforce
from .run import execute
from .store import digest, load_run, save

TRACK_ID = "6rQCPgrLJMQavladOGpwZt"
BASE = Path("output/midi_pipeline/phase3/meaningful-love-v1")
MIX = Path("liked-audio") / f"Silo - meaningful love - instrumental [{TRACK_ID}].mp3"
OTHER = Path("instrumentals/stems") / TRACK_ID / "other.wav"
LEGACY = Path("stems") / f"Silo - meaningful love - instrumental [{TRACK_ID}]"
FOCUS = [6., 18.]
CONTEXT = [0., 30.]


def _audio_info(path):
    import soundfile as sf
    item = sf.info(path)
    return {"sample_rate": item.samplerate, "channels": item.channels,
            "frames": item.frames, "duration_seconds": item.duration, "format": item.format,
            "subtype": item.subtype, "sha256": digest(path)}


def prepare(root=BASE):
    """Create a shared exact 0–30s demo WAV and a corresponding Other-stem crop."""
    import soundfile as sf
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if not MIX.is_file() or not OTHER.is_file():
        raise FileNotFoundError("Required local mix or Other stem is missing")
    sources = {"original_instrumental_mix": {"path": str(MIX), **_audio_info(MIX)},
               "existing_instrumental_other": {"path": str(OTHER), **_audio_info(OTHER)}}
    crops = {}
    for key, path, filename in (("reference", MIX, "reference.wav"),
                                 ("other_stem", OTHER, "reference_other.wav")):
        info = sf.info(path)
        if info.duration < CONTEXT[1]:
            raise ValueError(f"Source is shorter than the required 30-second context: {path}")
        frames = int(round(CONTEXT[1] * info.samplerate))
        samples, rate = sf.read(path, start=0, stop=frames, always_2d=True, dtype="float32")
        if len(samples) != frames or rate != info.samplerate:
            raise ValueError(f"Unexpected crop length/rate for {path}")
        target = root / filename
        if target.exists():
            raise FileExistsError(f"Preserving existing excerpt; choose another version directory: {target}")
        sf.write(target, samples, rate, subtype="PCM_16")
        crops[key] = {"path": filename, **_audio_info(target), "source_path": str(path),
                      "source_sha256": digest(path), "source_time_window": CONTEXT,
                      "resampling": "none", "channels": info.channels}
    manifest = {"schema": "phase3-reference/1", "track_id": TRACK_ID,
                "created_for": "Songscription free-demo and matching local YourMT3+ input",
                "common_mix_reference": "reference.wav",
                "external_demo_audio_sha256": crops["reference"]["sha256"],
                "focus_window_seconds": FOCUS, "context_window_seconds": CONTEXT,
                "sources": sources, "excerpts": crops,
                "source_note": "The repo has no combined original instrumental WAV. reference.wav is a lossless PCM16 excerpt of the locally decoded original instrumental MP3; the separate original is unchanged. Both the free demo and mix transcription must use these exact WAV bytes."}
    save(root / "reference-manifest.json", manifest)
    external = root / "external"
    (external / "screenshots").mkdir(parents=True, exist_ok=True)
    (external / "README.txt").write_text(
        "Manually place the unmodified Songscription export here:\n"
        "  songscription.mid (preferred)\n  songscription.musicxml (optional)\n"
        "  screenshots/ (optional PNG/JPG review evidence)\n\n"
        "Use reference.wav from this directory in the free 30-second demo. No login, payment, or demo-limit automation is used.\n"
        "Then run: python -m scripts.midi_pipeline.phase3 compare --songscription-midi "
        f"{external.as_posix()}/songscription.mid\n", encoding="utf-8")
    annotations = {"schema": "human-note-review/1", "track_id": TRACK_ID,
                   "reference_audio_sha256": crops["reference"]["sha256"],
                   "focus_window_seconds": FOCUS, "complete_notes": False,
                   "coverage_note": "Partial review is allowed. Accuracy metrics remain unavailable unless all pitched notes in 6–18s are independently reviewed.",
                   "model_runs": {}, "verified_notes": [], "reviews": [],
                   "review_issue_enum": ["verified", "missing_note", "false_positive", "incorrect_pitch",
                      "incorrect_onset", "incorrect_offset", "uncertain", "instrument_assignment_error"],
                   "review_record_fields": {"model_key": "required model key from model_runs",
                      "note_id": "note id in that model's canonical notes.json; null for an independently noticed missing note",
                      "issue": "one review_issue_enum value", "reviewed_onset": "optional seconds on original recording clock",
                      "reviewed_offset": "optional seconds on original recording clock",
                      "reviewed_pitch": "optional MIDI pitch", "reviewed_instrument": "optional plain-language observation",
                      "comment": "listening note; never promoted to ground truth by itself"}}
    if (root / "human-annotations.json").exists():
        raise FileExistsError("Human review file already exists; preserving it")
    save(root / "human-annotations.json", annotations)
    generate_report(root, manifest, annotations)
    print(json.dumps({"status": "prepared", "root": str(root), "sources": sources,
                      "reference": crops["reference"], "other_stem_excerpt": crops["other_stem"],
                      "songscription": "pending_manual_download"}, indent=2))
    return root


def run_local(root=BASE, model_root=MODEL_ROOT, device="cuda", batch_size=1):
    """Transcribe the common original mix and the independent Other-stem excerpt."""
    # Resolve the requested version directory before execute() returns WSL absolute
    # paths. This keeps relative_to() stable when Python is launched from WSL while
    # the caller supplied a relative project path.
    root = Path(root).resolve()
    manifest = json.loads((root / "reference-manifest.json").read_text(encoding="utf-8"))
    for key, filename in (("reference", "reference.wav"), ("other_stem", "reference_other.wav")):
        excerpt = manifest["excerpts"][key]
        if digest(root / filename) != excerpt["sha256"]:
            raise ValueError(f"Reference excerpt changed after preparation: {filename}")
    backend = select("yourmt3-plus")(model_root, device, batch_size, seed=0)
    output = root / "runs"
    runs = {}
    for filename, stem, separation in (
            ("reference.wav", "original-mix-common-reference", "none-original-mix"),
            ("reference_other.wav", "existing-other-stem", "existing-demucs-htdemucs_ft-version-unverified")):
        source = root / filename
        # execute() uses a content-addressed run directory. If an earlier attempt
        # completed inference but failed while writing this Phase 3 index, reuse
        # only that exact, hash-validated current-pipeline bundle.
        path = None
        source_hash = digest(source)
        for candidate in output.iterdir() if output.exists() else ():
            if not candidate.is_dir():
                continue
            try:
                candidate_run, _ = load_run(candidate)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
            identity = candidate_run["identity"]
            if (identity.get("source_sha256") == source_hash
                    and identity.get("source_audio_id") == TRACK_ID
                    and identity.get("stem") == stem
                    and identity.get("separation_model_version") == separation):
                path = candidate
                break
        if path is None:
            path = execute(backend, source, TRACK_ID, stem, separation,
                           output_root=output)
        run, notes = load_run(path)
        run_key = "yourmt3_mix" if filename == "reference.wav" else "yourmt3_other"
        runs[run_key] = {"run_id": run["run_id"], "path": path.relative_to(root).as_posix(),
                         "status": run["status"], "note_count": len(notes["notes"]),
                         "source_excerpt_sha256": manifest["excerpts"]["reference" if filename == "reference.wav" else "other_stem"]["sha256"],
                         "source_time_window": CONTEXT,
                         "clock_offset_to_original_seconds": 0.,
                         "clock_offset_provenance": "Both excerpts start at source recording zero; raw MIDI is crop-relative to the exact 0–30s WAV; canonical event times use the same origin."}
    annotations = json.loads((root / "human-annotations.json").read_text(encoding="utf-8"))
    annotations["model_runs"].update(runs)
    save(root / "human-annotations.json", annotations)
    generate_report(root, manifest, annotations)
    save(root / "local-runs.json", {"schema": "phase3-local-runs/1", "track_id": TRACK_ID,
          "context_seconds": CONTEXT, "focus_seconds": FOCUS, "runs": runs,
          "basic_pitch_used": False, "fallbacks": [], "legacy_cache_inputs": []})
    print(json.dumps(runs, indent=2))
    return runs


def _midi_notes(path, model_key, offset=0.):
    import pretty_midi
    midi = pretty_midi.PrettyMIDI(str(path))
    notes, tracks = [], []
    for ti, inst in enumerate(midi.instruments):
        tracks.append({"track": ti, "name": inst.name, "program": int(inst.program), "is_drum": bool(inst.is_drum)})
        for n in inst.notes:
            notes.append({"id": len(notes), "track": ti, "instrument": inst.name, "program": int(inst.program),
                          "is_drum": bool(inst.is_drum), "pitch": int(n.pitch), "onset": float(n.start + offset),
                          "offset": float(n.end + offset), "duration": float(n.end - n.start),
                          "velocity": int(n.velocity), "confidence": None})
    return midi, notes, tracks


def _audio_onset_envelope(path, rate=22050, hop=.01):
    """Diagnostic alignment reference only; never generates note predictions."""
    import soundfile as sf
    from scipy.signal import resample_poly
    audio, sr = sf.read(path, always_2d=True, dtype="float32")
    mono = audio.mean(axis=1)
    if sr != rate:
        divisor = math.gcd(sr, rate)
        mono = resample_poly(mono, rate // divisor, sr // divisor)
    hop_n = round(rate * hop)
    padded = np.pad(mono, (0, (-len(mono)) % hop_n))
    blocks = padded.reshape(-1, hop_n)
    envelope = np.sqrt(np.mean(blocks.astype(np.float64) ** 2, axis=1))
    # Smoothed positive energy change is a timing guide, not a note detector.
    novelty = np.maximum(np.diff(envelope, prepend=envelope[0]), 0.)
    novelty -= novelty.mean()
    return novelty, hop_n / rate


def suggest_offset(audio_path, midi_notes, search_seconds=2., hop=.01):
    audio, step = _audio_onset_envelope(audio_path, hop=hop)
    impulses = np.zeros_like(audio)
    for n in midi_notes:
        at = round(n["onset"] / step)
        if 0 <= at < len(impulses):
            impulses[at] += 1
    impulses -= impulses.mean()
    best = (float("-inf"), 0.)
    for shift in range(-round(search_seconds / step), round(search_seconds / step) + 1):
        if shift >= 0:
            a, b = audio[shift:], impulses[:len(audio) - shift]
        else:
            a, b = audio[:len(audio) + shift], impulses[-shift:]
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        correlation = float(np.dot(a, b) / denom) if denom else 0.
        if correlation > best[0]:
            best = (correlation, shift * step)
    return {"suggested_global_offset_seconds": best[1], "score": best[0],
            "method": "coarse global cross-correlation between MIDI onset impulses and positive 10ms RMS changes in the reference audio",
            "role": "diagnostic suggestion only; this must not be treated as truth or applied silently",
            "search_range_seconds": [-search_seconds, search_seconds], "search_step_seconds": step}


def compare(root=BASE, songscription_midi=None, musicxml=None, alignment_offset=None):
    root = Path(root)
    manifest = json.loads((root / "reference-manifest.json").read_text(encoding="utf-8"))
    runs = json.loads((root / "local-runs.json").read_text(encoding="utf-8"))["runs"]
    models, run_notes = {}, {}
    for key, item in runs.items():
        run, document = load_run(root / item["path"])
        run_notes[key] = document
        models[key] = {"tracks": document["tracks"], "notes": [dict(n, model_key=key) for n in document["notes"]]}
    external_dir = root / "external"
    external_info = {"status": "pending_manual_download", "midi": None, "musicxml": None,
                     "screenshots": [p.relative_to(root).as_posix() for p in (external_dir / "screenshots").glob("*") if p.is_file()]}
    alignments = {}
    if songscription_midi:
        midi_path = Path(songscription_midi)
        if not midi_path.is_file():
            raise FileNotFoundError(midi_path)
        external_key = "songscription_midi"
        midi_obj, notes, tracks = _midi_notes(midi_path, external_key)
        external_info["midi"] = {"path": str(midi_path.resolve()), "sha256": digest(midi_path),
                                  "tracks": tracks, "note_count": len(notes),
                                  "tempo_header_bpm": float(midi_obj.estimate_tempi()[0]) if midi_obj.estimate_tempi() else None,
                                  "tempo_use": "metadata only; times below use MIDI absolute seconds"}
        alignment = suggest_offset(root / "reference.wav", notes)
        if alignment_offset is not None:
            if not math.isfinite(alignment_offset) or abs(alignment_offset) > 10:
                raise ValueError("Manual single global offset must be within +/-10 seconds")
            alignment.update({"applied_global_offset_seconds": float(alignment_offset),
                              "applied_provenance": "explicit --alignment-offset supplied by reviewer; no time-warp"})
            notes = [dict(n, onset=n["onset"] + alignment_offset, offset=n["offset"] + alignment_offset)
                     for n in notes]
        else:
            alignment.update({"applied_global_offset_seconds": 0.,
                              "applied_provenance": "no correction applied; original MIDI timestamps retained pending review"})
        alignments[external_key] = alignment
        models[external_key] = {"tracks": tracks, "notes": [dict(n, model_key=external_key) for n in notes]}
        external_info["status"] = "midi_supplied_alignment_review_required"
        external_info["midi"]["applied_alignment_seconds"] = alignment.get("applied_global_offset_seconds")
    if musicxml:
        xml_path = Path(musicxml)
        if not xml_path.is_file():
            raise FileNotFoundError(xml_path)
        import music21
        score_obj = music21.converter.parse(str(xml_path))
        with tempfile.TemporaryDirectory() as tmp:
            derived = Path(tmp) / "songscription-converted.mid"
            score_obj.write("midi", fp=str(derived))
            _, notes, tracks = _midi_notes(derived, "songscription_musicxml")
        models["songscription_musicxml"] = {"tracks": tracks,
                                            "notes": [dict(n, model_key="songscription_musicxml") for n in notes]}
        external_info["musicxml"] = {"path": str(xml_path.resolve()), "sha256": digest(xml_path),
                                      "tracks": tracks, "note_count": len(notes),
                                      "conversion": "music21 MusicXML-to-MIDI interpretation, temporary derivative; source MusicXML unchanged"}
        external_info["status"] = "external_export_supplied_alignment_review_required"
        alignments["songscription_musicxml"] = {"applied_global_offset_seconds": 0.,
              "applied_provenance": "no correction applied; conversion clock requires human review",
              **suggest_offset(root / "reference.wav", notes)}
    agreements = {}
    keys = list(models)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            agreements[f"{a}__vs__{b}"] = pairwise_agreement(models[a]["notes"], models[b]["notes"])
    annotations = json.loads((root / "human-annotations.json").read_text(encoding="utf-8"))
    human_summary = annotation_summary(annotations)
    data = {"schema": "phase3-comparison/1", "context_seconds": CONTEXT,
            "evaluation_window_seconds": FOCUS, "reference_audio_sha256": manifest["excerpts"]["reference"]["sha256"],
            "models": models, "external_inputs": external_info, "alignments": alignments,
            "pairwise_agreement_only_not_accuracy": agreements,
            "human_annotations": human_summary,
            "metrics": reviewed_metrics(annotations, models),
            "diagnostic_boundaries": {"waveform_role": "navigation/alignment diagnostics only; it creates no note predictions",
                                      "time_warp": "none", "local_input_timing": "YourMT3+ mix and Other share exact [0,30] WAV origin; canonical source time starts at 0"}}
    save(root / "comparison.json", data)
    generate_report(root, manifest, annotations, data)
    print(json.dumps({"status": external_info["status"], "models": {k: len(v["notes"]) for k, v in models.items()},
                      "pairwise_agreement_only": agreements, "human_annotation_metrics": data["metrics"],
                      "alignment": alignments}, indent=2))
    return data


def pairwise_agreement(a, b, tolerance=.05):
    """One-to-one model agreement; do not call it accuracy."""
    from scipy.optimize import linear_sum_assignment
    if not a or not b:
        hits = []
    else:
        distance = np.array([[abs(x["onset"] - y["onset"]) for y in b] for x in a])
        allowed = (distance <= tolerance) & np.array([[x["pitch"] == y["pitch"] and x["is_drum"] == y["is_drum"]
                        for y in b] for x in a])
        ii, jj = linear_sum_assignment(np.where(allowed, distance, 1e6))
        hits = [(i, j) for i, j in zip(ii, jj) if allowed[i, j]]
    return {"matching_pitch_and_onset_tolerance_seconds": tolerance, "matched_events": len(hits),
            "notes_A": len(a), "notes_B": len(b),
            "agreement_precision_A": len(hits) / len(a) if a else None,
            "agreement_recall_A": len(hits) / len(b) if b else None,
            "mean_absolute_onset_difference_seconds": float(np.mean([abs(a[i]["onset"] - b[j]["onset"])
                                                                      for i, j in hits]) if hits else 0.) if hits else None,
            "mean_absolute_offset_difference_seconds": float(np.mean([abs(a[i]["offset"] - b[j]["offset"])
                                                                       for i, j in hits]) if hits else 0.) if hits else None,
            "interpretation": "model-to-model agreement only; neither transcription is reference truth"}


def annotation_summary(document):
    entries = document.get("reviews", [])
    return {"complete_notes": bool(document.get("complete_notes")), "review_count": len(entries),
            "issue_counts": {issue: sum(item.get("issue") == issue for item in entries)
                             for issue in document.get("review_issue_enum", [])},
            "verified_reference_note_count": len(document.get("verified_notes", [])),
            "partial_review_metrics": "suppressed until complete_notes=true and verified note list covers 6–18 seconds"}


def reviewed_metrics(annotations, models):
    """Only complete independently reviewed note references qualify for accuracy metrics."""
    if not annotations.get("complete_notes"):
        return {"status": "unavailable_partial_or_no_human_labels", "metrics": None}
    reference = annotations.get("verified_notes", [])
    if not reference:
        return {"status": "unavailable_empty_human_reference", "metrics": None}
    from scipy.optimize import linear_sum_assignment
    result = {}
    for key, model in models.items():
        predicted = [n for n in model["notes"] if FOCUS[0] <= n["onset"] < FOCUS[1] and not n["is_drum"]]
        ref = [n for n in reference if FOCUS[0] <= n["onset"] < FOCUS[1] and not n.get("is_drum", False)]
        if ref and predicted:
            distance = np.array([[abs(x["onset"] - y["onset"]) for y in predicted] for x in ref])
            onset_allowed = distance <= .05
            ii, jj = linear_sum_assignment(np.where(onset_allowed, distance, 1e6))
            onset_pairs = [(i, j) for i, j in zip(ii, jj) if onset_allowed[i, j]]
            allowed = onset_allowed & np.array([[x["pitch"] == y["pitch"] for y in predicted] for x in ref])
            pi, pj = linear_sum_assignment(np.where(allowed, distance, 1e6))
            pairs = [(i, j) for i, j in zip(pi, pj) if allowed[i, j]]
        else:
            onset_pairs, pairs = [], []
        tp, fp, missing = len(pairs), len(predicted) - len(pairs), len(ref) - len(pairs)
        precision = tp / len(predicted) if predicted else (None if not ref else 0.)
        recall = tp / len(ref) if ref else (None if not predicted else 0.)
        f1 = 2*precision*recall/(precision+recall) if precision is not None and recall is not None and precision+recall else (0. if precision == 0 or recall == 0 else None)
        result[key] = {"note_onset_pitch_precision_recall_f1": {"precision": precision, "recall": recall, "f1": f1,
                          "true_positive": tp, "false_positive": fp, "missing": missing},
                       "mean_onset_error_seconds": float(np.mean([abs(ref[i]["onset"]-predicted[j]["onset"]) for i,j in pairs])) if pairs else None,
                       "mean_offset_error_seconds": float(np.mean([abs(ref[i]["offset"]-predicted[j]["offset"]) for i,j in pairs])) if pairs else None,
                       "mean_duration_error_seconds": float(np.mean([abs((ref[i]["offset"]-ref[i]["onset"])-predicted[j]["duration"]) for i,j in pairs])) if pairs else None,
                       "pitch_accuracy_given_matched_onset": tp / len(onset_pairs) if onset_pairs else None,
                       "instrument_attribution": ({"matched_pitch_and_onset": len(pairs),
                            "labeled_instrument_notes": sum("program" in ref[i] or "instrument" in ref[i] for i,j in pairs),
                            "instrument_errors": sum(("program" in ref[i] and ref[i]["program"] != predicted[j].get("program")) or
                                                      ("instrument" in ref[i] and ref[i]["instrument"] != predicted[j].get("instrument"))
                                                      for i,j in pairs if "program" in ref[i] or "instrument" in ref[i]),
                            "status": "only counted when human label supplies instrument or program"}),
                       "matching_tolerance_seconds": .05, "reference_source": "complete manually reviewed verified_notes"}
    return {"status": "complete_human_reference_metrics", "metrics": result}


def generate_report(root, manifest=None, annotations=None, comparison=None):
    import soundfile as sf
    root = Path(root)
    manifest = manifest or json.loads((root / "reference-manifest.json").read_text(encoding="utf-8"))
    annotations = annotations or json.loads((root / "human-annotations.json").read_text(encoding="utf-8"))
    if comparison is None:
        comparison_path = root / "comparison.json"
        comparison = json.loads(comparison_path.read_text(encoding="utf-8")) if comparison_path.exists() else None
    sources = {}
    if comparison:
        for key, model in comparison["models"].items():
            sources[key] = {"tracks": model["tracks"], "notes": model["notes"]}
    runs = annotations.get("model_runs", {})
    audio_manifest = {"source": "reference.wav", "duration": 30,
                      "sample_rate": manifest["excerpts"]["reference"]["sample_rate"],
                      "sha256": manifest["excerpts"]["reference"]["sha256"]}
    audio_data, audio_rate = sf.read(root / "reference.wav", always_2d=True, dtype="float32")
    mono = audio_data.mean(axis=1)
    edges = np.linspace(0, len(mono), 1501, dtype=int)
    waveform = [float(np.sqrt(np.mean(mono[a:b].astype(np.float64) ** 2))) if b > a else 0.
                for a, b in zip(edges, edges[1:])]
    app = json.dumps({"audio": audio_manifest, "wave": waveform, "focus": FOCUS, "models": sources,
                      "annotations": annotations.get("reviews", []), "external_status":
                      comparison["external_inputs"] if comparison else {"status": "pending_manual_download"},
                      "metrics": comparison["metrics"] if comparison else {"status": "pending_no_reviewed_reference"}}).replace("<", "\\u003c")
    keys = sorted(sources)
    checks = "".join(f'<label><input type="checkbox" class="show-model" value="{html.escape(key)}" checked>{html.escape(key)}</label>' for key in keys)
    pages = []
    for key in keys:
        options = "".join(f'<option value="{track["track"]}">{track["track"]}: {html.escape(track["name"])} (program {track["program"]})</option>'
                           for track in sources[key]["tracks"])
        if not options:
            options = '<option value="-1">No tracks</option>'
        pages.append(f'<section class="model-pane" data-model="{html.escape(key)}"><h2>{html.escape(key)}</h2><label>Instrument track <select>{options}</select></label><canvas class="piano" width="1300" height="330"></canvas><p class="detail">Click a note for pitch and timing.</p></section>')
    image_files = sorted(p.relative_to(root).as_posix() for p in (root / "external" / "screenshots").glob("*")
                         if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
    images = "".join(f'<img width="500" src="{html.escape(p)}" alt="Optional manually provided comparison screenshot">' for p in image_files)
    page = """<!doctype html><meta charset="utf-8"><title>Meaningful Love AMT validation</title>
<style>body{font:16px system-ui;max-width:1350px;margin:22px auto;color:#222}audio{width:100%}canvas{width:100%;height:auto;background:#fff;border:1px solid #ccc}section{border-top:1px solid #aaa;padding:12px 0}.model-pane[hidden]{display:none}label{margin-right:18px}.status{background:#f4f0df;padding:12px}pre{white-space:pre-wrap}</style>
<h1>Silo — Meaningful Love: AMT comparison</h1>
<p class="status">This report compares predictions against audio. Agreement between models is not accuracy. Human note-level accuracy stays unavailable until complete independent labels are added.</p>
<h2>Shared 0–30 second reference WAV</h2><audio id="audio" controls src="reference.wav"></audio>
<p id="clock">0.000 s (original recording clock)</p><canvas id="wave" width="1300" height="110" title="Waveform for navigation/alignment only"></canvas>
<p>Click waveform to seek. Focus interval: 6.000–18.000 s; all note times use original recording seconds.</p>
<div id="toggles">MODEL_TOGGLES</div><p><label><input id="overlay" type="checkbox"> Overlay visible predictions (same pitch/time scale)</label></p>
MODEL_PANES
<h2>Inputs and evaluation state</h2><pre id="state"></pre><pre id="metrics"></pre>
<p>External import location: <a href="external/README.txt">external/README.txt</a>. Manual note review is separate: <a href="human-annotations.json">human-annotations.json</a>.</p>
<p>Reproduce: <code>python -m scripts.midi_pipeline.phase3 prepare</code>, then <code>python -m scripts.midi_pipeline.phase3 run</code>; after manually placing the Songscription file, run <code>python -m scripts.midi_pipeline.phase3 compare --songscription-midi output/midi_pipeline/phase3/meaningful-love-v1/external/songscription.mid</code>.</p>
SCREENSHOTS
<script>const app=APP;const audio=document.getElementById('audio'),wave=document.getElementById('wave'),wc=wave.getContext('2d');
const palette=['#1769aa','#e67700','#32965a','#944bbb','#c34254'];
function drawWave(){const W=wave.width,H=wave.height;wc.clearRect(0,0,W,H);wc.fillStyle='#e8edf1';wc.fillRect(0,0,W,H);wc.fillStyle='rgba(255,170,30,.28)';wc.fillRect(6/30*W,0,12/30*W,H);const x=app.wave||[];wc.strokeStyle='#506070';wc.beginPath();for(let i=0;i<x.length;i++){let px=i/(x.length-1)*W,y=H/2-x[i]*H*.42;wc.moveTo(px,H/2);wc.lineTo(px,y)}wc.stroke();wc.fillStyle='#d22';wc.fillRect(audio.currentTime/30*W,0,2,H);document.getElementById('clock').textContent=(audio.currentTime).toFixed(3)+' s (original recording clock)';requestAnimationFrame(drawWave)}
function paint(pane){if(pane.hidden)return;const model=pane.dataset.model,entry=app.models[model],canvas=pane.querySelector('canvas'),ctx=canvas.getContext('2d'),track=Number(pane.querySelector('select').value);ctx.clearRect(0,0,canvas.width,canvas.height);ctx.fillStyle='#fafafa';ctx.fillRect(0,0,canvas.width,canvas.height);for(let t=0;t<=30;t+=3){let x=t/30*canvas.width;ctx.strokeStyle=t>=6&&t<=18?'#ecd7af':'#e5e5e5';ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,300);ctx.stroke();ctx.fillStyle='#555';ctx.fillText(t+'s',x+2,320)}let ns=(entry?.notes||[]).filter(n=>n.track===track&&!n.is_drum);let pitches=ns.map(n=>n.pitch);let low=Math.max(0,Math.min(48,...pitches)-3),high=Math.min(108,Math.max(84,...pitches)+3);for(let n of ns){let x=n.onset/30*canvas.width,w=Math.max(2,(n.offset-n.onset)/30*canvas.width),y=(high-n.pitch)/(high-low)*290;ctx.fillStyle=palette[n.track%palette.length];ctx.fillRect(x,y,w,5)}if(document.getElementById('overlay').checked){for(const [mi,m] of Object.entries(app.models)){if(mi===model)continue;for(let n of m.notes.filter(n=>n.track===track&&!n.is_drum)){let x=n.onset/30*canvas.width,w=Math.max(2,(n.offset-n.onset)/30*canvas.width),y=(high-n.pitch)/(high-low)*290;ctx.strokeStyle=palette[(n.track+2)%palette.length];ctx.strokeRect(x,y,w,6)}}}if(app.annotations){for(const r of app.annotations.filter(r=>r.model_key===model&&r.note_id!==null)){let n=entry.notes.find(n=>n.id===r.note_id);if(n){ctx.strokeStyle='#d22';ctx.lineWidth=2;let x=n.onset/30*canvas.width,y=(high-n.pitch)/(high-low)*290;ctx.strokeRect(x-2,y-3,9,12)}}}canvas.onclick=e=>{let rect=canvas.getBoundingClientRect(),x=(e.clientX-rect.left)/rect.width*30;let nearby=ns.filter(n=>Math.abs(n.onset-x)<.25).sort((a,b)=>Math.abs(a.onset-x)-Math.abs(b.onset-x));if(nearby.length){let n=nearby[0];pane.querySelector('.detail').textContent=`${model} · note ${n.id} · ${n.instrument} program ${n.program} · MIDI ${n.pitch} · ${n.onset.toFixed(3)}–${n.offset.toFixed(3)} s · duration ${n.duration.toFixed(3)} s · velocity ${n.velocity} · confidence ${n.confidence??'not provided'}`;audio.currentTime=n.onset}else audio.currentTime=x}}
function refresh(){for(const p of document.querySelectorAll('.model-pane')){const shown=[...document.querySelectorAll('.show-model')].find(x=>x.value===p.dataset.model)?.checked;p.hidden=!shown;paint(p)}}
wave.onclick=e=>{const r=wave.getBoundingClientRect();audio.currentTime=(e.clientX-r.left)/r.width*30};audio.addEventListener('timeupdate',()=>{});for(const x of document.querySelectorAll('.show-model,#overlay,.model-pane select'))x.addEventListener('change',refresh);document.getElementById('state').textContent=JSON.stringify(app.external_status,null,2);document.getElementById('metrics').textContent=JSON.stringify(app.metrics,null,2);drawWave();refresh();
</script>"""
    page = page.replace("APP", app).replace("MODEL_TOGGLES", checks).replace("MODEL_PANES", "".join(pages)).replace("SCREENSHOTS", images)
    (root / "report.html").write_text(page, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--root", type=Path, default=BASE)
    p = sub.add_parser("run"); p.add_argument("--root", type=Path, default=BASE); p.add_argument("--model-root", type=Path, default=MODEL_ROOT); p.add_argument("--device", choices=("cuda", "cpu"), default="cuda"); p.add_argument("--batch-size", type=int, default=1)
    p = sub.add_parser("compare"); p.add_argument("--root", type=Path, default=BASE); p.add_argument("--songscription-midi", type=Path); p.add_argument("--songscription-musicxml", type=Path); p.add_argument("--alignment-offset", type=float, help="One explicit global seconds correction; applied only to display/evaluation notes; original MIDI remains unchanged")
    p = sub.add_parser("annotation-summary"); p.add_argument("--root", type=Path, default=BASE)
    args = parser.parse_args()
    enforce()
    if args.command == "prepare": prepare(args.root)
    elif args.command == "run": run_local(args.root, args.model_root, args.device, args.batch_size)
    elif args.command == "compare": compare(args.root, args.songscription_midi, args.songscription_musicxml, args.alignment_offset)
    elif args.command == "annotation-summary": print(json.dumps(annotation_summary(json.loads((args.root / "human-annotations.json").read_text())), indent=2))


if __name__ == "__main__":
    main()
