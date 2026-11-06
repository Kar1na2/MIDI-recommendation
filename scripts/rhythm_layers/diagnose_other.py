"""Reproduce and visualize the Other-stem detector, without changing its output.

Run in the project analysis environment:
  .venv/bin/python -m scripts.rhythm_layers.diagnose_other

Only local audio and sanitized sample metadata are read. No live catalog,
lyrics, remote service, or hand-written note transcription is used.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
from datetime import datetime, timezone
from pathlib import Path

import librosa
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import soundfile as sf
from scipy.signal import find_peaks

from .common import ROOT, OUT, PREVIEW, PARTS, read_json, write_json
from .patterns import SR, HOP, FFT, VERSION, load_audio, extract, pulse, grid, signatures, signature_distance, make_layers, cycle_summary, event_distance

TRACK = "6rQCPgrLJMQavladOGpwZt"
ARTIFACT = ROOT / "output/analysis/meaningful_love_other"
WEB = PREVIEW / "diagnostics/meaningful-love-other"
GOLD, GREEN, RED, INK = "#efbd79", "#9bdfc0", "#ff928b", "#09160f"
ANCHORS = [6, 18, 30, 42, 55, 68]
plt.rcParams.update({"figure.facecolor": INK, "axes.facecolor": INK, "savefig.facecolor": INK,
                     "text.color": "#edf3e7", "axes.labelcolor": "#dbe9de", "xtick.color": "#b9cec0",
                     "ytick.color": "#b9cec0", "axes.edgecolor": "#45594b", "font.size": 10,
                     "axes.titleweight": "bold", "axes.titlesize": 12, "grid.alpha": .15})


def rounded(value):
    return round(float(value), 5) if value is not None else None


def candidate_scores(times, data, start, end):
    """Mirror cycle_summary's five tested lags, and assert parity below."""
    first, last = np.searchsorted(times, [start, end])
    rows = []
    for beats in (1, 2, 4, 8, 16):
        lag = beats * 4
        if last - first < 2 * lag:
            continue
        rhythm, harmony = [], []
        for part, values in data.items():
            a, b = values["rhythm"][first:last - lag], values["rhythm"][first + lag:last]
            if np.count_nonzero(a) + np.count_nonzero(b) >= 4:
                rhythm.append(1 - event_distance(a, b))
            if part != "drums":
                a, b = values["harmonic"][first:last - lag], values["harmonic"][first + lag:last]
                harmony.append(float(np.mean(np.sum(a * b, axis=1))))
        if not rhythm:
            continue
        r, h = float(np.mean(rhythm)), float(np.mean(harmony)) if harmony else None
        score = .75 * r + .25 * h if h is not None else r
        rows.append({"beats": beats, "lag_seconds_median": rounded(np.median(times[first + lag:last] - times[first:last - lag])),
                     "rhythm": rounded(r), "harmony": rounded(h), "score": rounded(score),
                     "passes_threshold": bool(score >= .60 and r >= .55)})
    return rows


def boundary_diagnostics(times, data, fine=False):
    """Reproduce the exact comparison, threshold, distance, and prominence tests."""
    context = 16 if fine else 32
    weights = np.array([.58, .27, .15] if fine else [.72, .23, .05])
    scores = np.zeros(len(times))
    for index in range(context, len(times) - context, 4):
        left = signatures(data, index - context, index)
        right = signatures(data, index, index + context)
        scores[index] = float(np.dot(weights, signature_distance(left, right)))
    positive = scores[scores > 0]
    if not len(positive):
        return {"fine": fine, "threshold": .20 if fine else .24, "tested": 0, "max_score": 0,
                "median_positive": 0, "mad_positive": 0, "pre_prominence_peaks": [], "accepted": []}, scores
    median = float(np.median(positive))
    mad = float(np.median(np.abs(positive - median)))
    threshold = max(.20 if fine else .24, median + 1.4 * mad)
    distance = 24 if fine else 32
    pre, before = find_peaks(scores, distance=distance, height=threshold)
    peaks, after = find_peaks(scores, distance=distance, height=threshold, prominence=.07)
    pack = lambda indices, props, key: [{"time": rounded(times[i]), "score": rounded(scores[i]),
                                         "prominence": rounded(props[key][n])} for n, i in enumerate(indices)]
    return {"fine": fine, "threshold": rounded(threshold), "tested": int(len(positive)),
            "max_score": rounded(np.max(positive)), "median_positive": rounded(median), "mad_positive": rounded(mad),
            "minimum_peak_distance_grid_slots": distance, "minimum_peak_prominence": .07,
            "pre_prominence_peaks": pack(pre, before, "peak_heights"),
            "accepted": pack(peaks, after, "prominences")}, scores


def save(fig, name):
    fig.savefig(ARTIFACT / name, dpi=165, bbox_inches="tight")
    plt.close(fig)
    return name


def marks(ax, times=ANCHORS):
    for at in times:
        ax.axvline(at, color=GOLD, lw=.75, alpha=.65, ls="--")


def heading(ax, title, ylabel=None):
    ax.set_title(title, loc="left", pad=10)
    if ylabel:
        ax.set_ylabel(ylabel)
    ax.grid(axis="x")


def provenance_figure(y, feature, auto, duration):
    fig, axes = plt.subplots(4, 1, figsize=(14, 9), sharex=True,
                             gridspec_kw={"height_ratios": [1, 2.6, 1, 1.3]}, layout="constrained")
    stride = max(1, len(y) // 15000)
    n = len(y) // stride * stride
    chunks = y[:n].reshape(-1, stride)
    x = np.arange(len(chunks)) * stride / SR
    axes[0].fill_between(x, chunks.min(axis=1), chunks.max(axis=1), color=GREEN, lw=0)
    heading(axes[0], "The actual Other WAV · full recording", "Amplitude")
    axes[0].text(.99, .88, "Waveform is shown for inspection only; amplitude does not set the rings.",
                 transform=axes[0].transAxes, ha="right", va="top", fontsize=9)
    image = axes[1].imshow(feature["chroma"].T, origin="lower", aspect="auto", cmap="magma", vmin=0, vmax=1,
                           extent=[0, feature["times"][-1], -.5, 11.5], interpolation="nearest")
    axes[1].set_yticks(range(12), ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"])
    heading(axes[1], "What the current detector sees: normalized pitch classes (octaves folded together)", "Pitch class")
    fig.colorbar(image, ax=axes[1], label="Unit-normalized strength", pad=.01, fraction=.02)
    axes[2].vlines(feature["onsets"], 0, 1, color=GREEN, lw=.6)
    axes[2].set_yticks([]); heading(axes[2], "What becomes rhythm data: detected attacks, with all velocities discarded")
    rows = [(auto[0], 3, "Automatic broad"), (auto[1], 2, "Automatic fine")]
    for layer, row, label in rows:
        axes[3].hlines(row, 0, duration, color="#50675a", lw=2)
        at = [c["time"] for c in layer["cuts"]]
        axes[3].scatter(at, np.full(len(at), row), marker="|", s=200, color=GREEN)
    for row, at in [(1, [6, 18, 68]), (0, [6, 18, 30, 42, 55, 68])]:
        axes[3].hlines(row, 0, duration, color="#50675a", lw=2)
        axes[3].scatter(at, np.full(len(at), row), marker="|", s=200, color=GOLD)
    axes[3].set_yticks([3, 2, 1, 0], ["Automatic broad", "Automatic fine", "Your broad anchors", "Your finer anchors"])
    axes[3].set_ylim(-.6, 3.6)
    heading(axes[3], "Boundary provenance · green = unassisted output; gold = supplied listening notes")
    axes[-1].set_xlabel("Time in this Other WAV (seconds)"); axes[-1].set_xlim(0, duration)
    return save(fig, "01-source-and-boundaries.png")


def attack_figure(feature, times, values, cq_db, cq_times):
    fig, axes = plt.subplots(4, 1, figsize=(14, 11), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1.3, .9, 1]}, layout="constrained")
    image = axes[0].imshow(cq_db, origin="lower", aspect="auto", cmap="magma", vmin=-65, vmax=0,
                           extent=[0, cq_times[-1], 35.5, 107.5], interpolation="nearest")
    axes[0].set_yticks(range(36, 108, 12), ["C2", "C3", "C4", "C5", "C6", "C7"])
    heading(axes[0], "Additional diagnostic: pitch register over the first 68 s (constant-Q spectrogram)", "Pitch / octave")
    fig.colorbar(image, ax=axes[0], label="dB relative to excerpt maximum", pad=.01, fraction=.02)
    axes[0].text(.99, .98, "This diagnostic was NOT used to generate the current rings.", ha="right", va="top",
                 transform=axes[0].transAxes, bbox={"facecolor": INK, "alpha": .8, "edgecolor": "none"}, fontsize=9)
    mask = feature["times"] <= 68
    onsets = feature["onsets"][feature["onsets"] <= 68]
    axes[1].plot(feature["times"][mask], feature["flux"][mask], color=GREEN, lw=.8)
    axes[1].scatter(onsets, np.interp(onsets, feature["times"], feature["flux"]), color=RED, s=15, zorder=3)
    heading(axes[1], "Current attack detector: spectral-flux envelope and accepted peaks (red)", "Normalized flux")
    axes[2].vlines(onsets, .65, 1.2, color=RED, lw=1)
    placed = times[(values["rhythm"] > 0) & (times <= 68)]
    axes[2].vlines(placed, -.15, .4, color=GREEN, lw=1)
    axes[2].set_yticks([.9, .1], ["Raw detected times", "Occupied beat slots"])
    heading(axes[2], "The rhythm representation is just this sparse event raster")
    axes[3].set_ylim(0, 1); axes[3].set_yticks([])
    for start, end, label in [(0, 6, "Intro"), (6, 18, "Ascending arps"), (18, 30, "Chord figure"),
                              (30, 42, "+ synth/noise"), (42, 55, "+ melody"), (55, 68, "Shift")]:
        axes[3].axvspan(start, end, alpha=.12 if start % 2 else .2, color=GOLD)
        axes[3].text((start + end) / 2, .5, label, ha="center", va="center", fontsize=9)
    heading(axes[3], "Your listening notes, plotted for reference — these are not detector predictions")
    for ax in axes: marks(ax)
    axes[-1].set_xlabel("Other WAV time (seconds)"); axes[-1].set_xlim(0, 68)
    return save(fig, "02-pitch-and-detected-attacks.png")


def repeat_figure(cq_db, cq_times, feature, candidates):
    fig = plt.figure(figsize=(14, 11), layout="constrained")
    spec = fig.add_gridspec(3, 2, height_ratios=[1.3, 1.3, 1.2])
    for col, (start, end) in enumerate([(6, 12), (12, 18)]):
        ax = fig.add_subplot(spec[0, col])
        mask = (cq_times >= start) & (cq_times < end)
        ax.imshow(cq_db[:, mask], origin="lower", aspect="auto", cmap="magma", vmin=-65, vmax=0,
                  extent=[0, end - start, 35.5, 107.5], interpolation="nearest")
        ax.set_yticks(range(36, 108, 12), ["C2", "C3", "C4", "C5", "C6", "C7"])
        heading(ax, f"User-supplied repeat window {start}–{end} s", "Pitch / octave")
        ax.set_xlabel("Seconds from start of this window")
    sample_times = np.arange(0, 68, .12)
    chroma = np.stack([np.interp(sample_times, feature["times"], feature["chroma"][:, k]) for k in range(12)], axis=1)
    # Convert dB back to linear magnitude. Adding the display floor instead
    # makes all bins equally positive and falsely makes unrelated times match.
    cq = np.power(10., cq_db / 20.)
    q = np.stack([np.interp(sample_times, cq_times, cq[k]) for k in range(cq.shape[0])], axis=1)
    repeat_stats = {}
    for col, (matrix, name) in enumerate([(chroma, "Current harmonic representation · 12 pitch classes"),
                                         (q, "Additional diagnostic · 72 pitch + octave bins")]):
        matrix /= np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-9)
        first, second = (sample_times >= 6) & (sample_times < 12), (sample_times >= 12) & (sample_times < 18)
        aligned = np.sum(matrix[first] * matrix[second], axis=1)
        repeat_stats["chroma_6s" if col == 0 else "pitch_register_6s"] = {
            "mean": rounded(np.mean(aligned)), "median": rounded(np.median(aligned))}
        similarity = np.clip(matrix @ matrix.T, 0, 1)
        ax = fig.add_subplot(spec[1, col]); image = ax.imshow(similarity, origin="lower", aspect="equal", cmap="magma", vmin=0, vmax=1, extent=[0, 68, 0, 68])
        heading(ax, name); ax.set_xlabel("Time (s)"); ax.set_ylabel("Time (s)")
        for at in [6, 18, 30, 42, 55]:
            ax.axvline(at, color=GOLD, lw=.5, alpha=.4); ax.axhline(at, color=GOLD, lw=.5, alpha=.4)
        fig.colorbar(image, ax=ax, label="Frame cosine similarity", shrink=.7)
    ax = fig.add_subplot(spec[2, :])
    labels = [f"{row['beats']} beats\n≈{row['lag_seconds_median']:.2f} s" for row in candidates]
    x = np.arange(len(candidates))
    ax.bar(x - .25, [r["rhythm"] for r in candidates], .25, color=RED, label="Attack-position similarity")
    ax.bar(x, [r["harmony"] for r in candidates], .25, color=GREEN, label="Pitch-class similarity")
    ax.bar(x + .25, [r["score"] for r in candidates], .25, color=GOLD, label="Weighted score")
    ax.axhline(.55, color=RED, ls="--", lw=1, label="Rhythm requirement 0.55")
    ax.axhline(.60, color=GOLD, ls=":", lw=1, label="Score requirement 0.60")
    ax.set_xticks(x, labels); ax.set_ylim(0, 1.08)
    heading(ax, "Exact recurrence test for 6–18 s · both the rhythm and weighted thresholds must pass", "Similarity (0–1)")
    ax.legend(loc="upper right", ncol=3, fontsize=8)
    return save(fig, "03-repeat-evidence-and-thresholds.png"), repeat_stats


def boundary_figure(times, diagnostics, scores):
    fig, axes = plt.subplots(2, 1, figsize=(14, 7), sharex=True, layout="constrained")
    for ax, result, values in zip(axes, diagnostics, scores):
        fine = result["fine"]
        mask = (times >= 0) & (times <= 100)
        x, y = times[mask], values[mask]
        positive = y > 0
        ax.scatter(x[positive], y[positive], color=GREEN, s=11, alpha=.75, label="Measured comparison score")
        ax.axhline(result["threshold"], color=GOLD, ls="--", label=f"Required score ≥ {result['threshold']:.3f}")
        for peak in result["pre_prominence_peaks"]:
            ax.scatter([peak["time"]], [peak["score"]], color=RED, marker="x", s=70,
                       label="Height candidate; prominence gate failed" if peak["prominence"] < .07 else "Height candidate")
        for cut in result["accepted"]:
            ax.axvline(cut["time"], color=GOLD, lw=1.2, alpha=.9)
        ax.set_xlim(0, 100); ax.set_ylim(bottom=0)
        context_slots = 16 if fine else 32
        context_seconds = context_slots * np.median(np.diff(times))
        label = "Fine · four-beat context" if fine else "Broad · eight-beat context"
        heading(ax, f"{label} · tested {result['tested']} positions · max {result['max_score']:.3f} · threshold {result['threshold']:.3f}", "Pattern-change score")
        ax.legend(loc="upper right", fontsize=8, ncol=2)
        ax.text(.01, .96, f"Comparison context ≈{context_seconds:.1f} s on average", transform=ax.transAxes, va="top", fontsize=9)
    axes[-1].set_xlabel("Other WAV time (seconds; first 100 s shown)")
    return save(fig, "04-why-no-automatic-cut.png")


def main():
    ARTIFACT.mkdir(parents=True, exist_ok=True); WEB.mkdir(parents=True, exist_ok=True)
    media = read_json(OUT / "media-index.json")
    catalog = read_json(PREVIEW / "data.json")
    record = next(t for t in catalog["tracks"] if t["track_id"] == TRACK)
    paths = {p: ROOT / media[TRACK][p] for p in PARTS}
    feature = {p: extract(path) for p, path in paths.items()}
    duration = min(float(f["duration"]) for f in feature.values())
    beats, bpm, confidence = pulse(feature, duration)
    times, data = grid(feature, beats, duration)
    other = {"other": data["other"]}
    automatic = make_layers(times, other, duration)
    cuts = [{"name": layer["name"], "cuts": [s["boundary"] for s in layer["segments"] if s["boundary"]]}
            for layer in automatic]
    boundary_results, boundary_scores = zip(*(boundary_diagnostics(times, other, fine=fine) for fine in (False, True)))
    for reported, actual in zip(boundary_results, cuts):
        assert [round(c["time"], 3) for c in reported["accepted"]] == [c["time"] for c in actual["cuts"]], "Diagnostic drift from changes()"
    windows = []
    for start, end in zip([0, *ANCHORS], [*ANCHORS, duration]):
        scores = candidate_scores(times, other, start, end)
        summary = cycle_summary(times, other, start, end)
        if scores:
            best = max(scores, key=lambda row: row["score"])
            chosen = next(row for row in scores if row["score"] >= best["score"] - .025 - 1e-5)
            assert abs(chosen["score"] - summary["similarity"]) <= .0011, "Diagnostic drift from cycle_summary"
        windows.append({"start": start, "end": rounded(end), "raw_attack_count": int(np.sum((feature["other"]["onsets"] >= start) & (feature["other"]["onsets"] < end))),
                        "summary": summary, "candidates": scores})
    info = sf.info(paths["other"])
    with paths["other"].open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    coverage = []
    for track in catalog["tracks"]:
        unique = {(s["start"], s["end"]): s for layer in track["views"]["other"]["layers"] for s in layer["segments"]}
        coverage.append({"artist": track["artist"], "title": track["title"], "unique_intervals": len(unique),
                         "without_short_cycle": sum(not s["cycle"].get("beats") for s in unique.values()),
                         "annotated_intervals": sum(bool(s.get("annotation")) for s in unique.values())})
    repeat_figure_name, repeat_stats = None, None
    audit = {"generated_at": datetime.now(timezone.utc).isoformat(), "track_id": TRACK, "method": VERSION,
             "file": {"relative_path": paths["other"].relative_to(ROOT).as_posix(), "bytes": paths["other"].stat().st_size,
                      "sha256": sha, "sample_rate": info.samplerate, "channels": info.channels, "frames": info.frames,
                      "format": info.format, "subtype": info.subtype, "duration": rounded(info.duration)},
             "separation": {"model": "Demucs htdemucs_ft", "stem": "other", "channels_averaged_to_mono": True,
                            "joint_peak_scale_across_bass_drums_other": True, "output_sample_rate": 22050,
                            "encoding": "mono PCM-16 WAV"},
             "analysis": {"sample_rate": SR, "hop_samples": HOP, "fft_samples": FFT, "mel_bands": 24,
                          "onset_delta": .09, "onset_wait_frames": 2, "grid_subdivisions_per_beat": 4,
                          "grid_tolerance_seconds": .18, "pulse_bpm": rounded(bpm), "pulse_confidence": rounded(confidence),
                          "median_beat_seconds": rounded(np.median(np.diff(beats))), "candidate_beats": [1, 2, 4, 8, 16],
                          "minimum_weighted_score": .60, "minimum_rhythm_similarity": .55},
             "automatic_boundaries_without_annotations": cuts, "boundary_diagnostics": boundary_results,
             "repeat_visual_diagnostics": {}, "windows": windows, "sample_other_coverage": coverage,
             "source": record.get("source"), "notes_are_user_supplied": record["listening_notes"]}
    y = load_audio(paths["other"])
    cq_hop = 128
    cq = np.abs(librosa.cqt(y[:int(68 * SR)], sr=SR, hop_length=cq_hop, fmin=librosa.midi_to_hz(36), n_bins=72, bins_per_octave=12))
    cq_db = librosa.amplitude_to_db(cq, ref=np.max, top_db=65)
    cq_times = librosa.frames_to_time(np.arange(cq.shape[1]), sr=SR, hop_length=cq_hop)
    repeat_figure_name, repeat_stats = repeat_figure(cq_db, cq_times, feature["other"], windows[1]["candidates"])
    audit["repeat_visual_diagnostics"] = repeat_stats
    figures = [provenance_figure(y, feature["other"], cuts, duration),
               attack_figure(feature["other"], times, data["other"], cq_db, cq_times),
               repeat_figure_name, boundary_figure(times, boundary_results, boundary_scores)]
    write_json(ARTIFACT / "metrics.json", audit)
    make_report(audit, figures)
    print(json.dumps({"file": audit["file"], "pulse": audit["analysis"], "automatic_boundaries": cuts,
                      "unassisted_boundary_tests": boundary_results,
                      "repeat_visual_diagnostics": repeat_stats,
                      "windows": [{k: w[k] for k in ("start", "end", "raw_attack_count", "summary")} for w in windows],
                      "coverage": coverage, "report": str(WEB / "index.html")}, ensure_ascii=False, indent=2))


def make_report(audit, figures):
    f, a, windows = audit["file"], audit["analysis"], audit["windows"]
    first = windows[1]; cycle = first["summary"]
    source = audit.get("source") or {}
    source_url = source.get("url", "https://www.youtube.com/watch?v=A_IlqfBhhKU")
    e = html.escape
    automatic = audit["automatic_boundaries_without_annotations"]
    auto_text = "; ".join(f"{row['name']}: " + (", ".join(f"{c['time']:.2f} s" for c in row['cuts']) or "no cuts") for row in automatic)
    total_windows = sum(r["unique_intervals"] for r in audit["sample_other_coverage"])
    failures = sum(r["without_short_cycle"] for r in audit["sample_other_coverage"])
    repeat = audit["repeat_visual_diagnostics"]
    broad_gate, fine_gate = audit["boundary_diagnostics"]
    overview = (f"The current method did not independently identify the named arpeggios, chord sequence, or synth entries in this recording. "
                f"In 6–18 s, it kept {first['raw_attack_count']} attack peaks ({cycle['attack_count']} occupied beat slots). "
                f"The selected repeat candidate has attack similarity {cycle['rhythm_similarity']:.3f} and pitch-class similarity {cycle['harmony_similarity']:.3f}; "
                f"the attack requirement is 0.550. Separately, an octave-aware spectrogram check finds mean frame similarity {repeat['pitch_register_6s']['mean']:.3f} "
                f"between your 6–12 s and 12–18 s windows. That is tonal evidence worth following up; it does not name notes or verify rhythm. "
                f"Unassisted broad/fine cut thresholds ({broad_gate['threshold']:.3f}/{fine_gate['threshold']:.3f}) were above their maximum scores "
                f"({broad_gate['max_score']:.3f}/{fine_gate['max_score']:.3f}), so neither ring produced any cuts. The arpeggio label and 6/18 s boundaries came from your listening notes.")
    why = [
        "It is an event-recurrence heuristic, not a note transcriber. Spectral-flux peaks are treated as attacks; a sustained or overlapping pitched figure can change notes without producing a detected peak. A missing peak is a detector limitation, not evidence that no musical pattern exists.",
        f"The attack envelope is computed from just 24 mel bands at {SR:,} Hz, with {FFT / SR * 1000:.1f} ms FFT windows and {HOP / SR * 1000:.1f} ms steps. The detector normalizes flux, uses delta 0.09 and a two-frame wait, then reduces accepted peaks to binary quarter-beat slots. It loses articulation strength and much of the pitch motion before recurrence is tested.",
        f"The common pulse is estimated from Bass + Drums + Other together: {a['pulse_bpm']:.2f} BPM, with an internal support score of {a['pulse_confidence']:.3f}. That score is not calibrated timing accuracy. Other must fit this shared grid; independent or expressive timing can lower the attack score.",
        f"It tests only 1, 2, 4, 8, and 16 estimated-beat delays. It has no free-duration phrase search or local time-warping at this stage. The supplied 6 s phrase spacing is not one of the tested lags. A separate octave-aware frame comparison at 6 s averages {repeat['pitch_register_6s']['mean']:.3f}; this supports investigating repeated tonal content, but does not establish a repeat of the same notes or attacks.",
        "Chroma combines C in every octave into one bin and mixes simultaneous instruments. It cannot directly preserve an ascending/descending arpeggio's register, separate a pad from the arpeggio, or verify four chords. A high harmonic cosine can arise from shared sustained tones.",
        "Section changes and cycle naming are separate decisions. Broad changes compare the preceding/following eight beats, fine changes compare four; rhythm/harmony/texture weights are 72/23/5% and 58/27/15%. Strongest cuts are capped at four and six. A failed cycle name does not mean that the whole interval is musically uniform.",
    ]
    provenance = ("For this Silo preview the override additionally locks automatic broad and fine cuts inside 6–68 s, "
                  "then inserts your broad anchors (6, 18, 68 s) and finer anchors (30, 42, 55 s). "
                  "Matching intervals receive your supplied descriptions. That makes this an illustrated listening annotation, "
                  "not an independently validated automatic result. The green boundary rows below rerun the same features with those overrides removed.")
    next_steps = [
        "Retain this example as a labelled evaluation case. Remove the listening overrides from the evaluation run; compare predicted boundaries and repeated phrases against reviewed note/phrase timings.",
        "Replace the sparse Other representation with register-aware pitch/onset evidence and multi-pitch note candidates. Validate note events on 6–18 s before claiming the four ascending arpeggios; Other is polyphonic, so a single dominant-pitch tracker is insufficient.",
        "Find recurring phrases over a wider range of durations, allowing phase offsets and modest local timing differences. Use recurrence blocks to propose boundaries instead of requiring fixed beat lags alone.",
        "Track persistent figures and new overlays separately (for example the arpeggio/chord bed versus an entering synth). Check stem leakage against the source recording if pitched events are missing from Other.",
        "Keep unsupported windows labelled as unresolved by the detector. Do not lower thresholds merely to make every window receive a musical name. Review this representation on the three samples before a full-library run.",
    ]
    rows = "".join(f"<tr><td>{e(r['artist'])} — {e(r['title'])}</td><td>{r['unique_intervals']}</td><td>{r['without_short_cycle']}</td><td>{r['annotated_intervals']}</td></tr>" for r in audit["sample_other_coverage"])
    scores = "".join(f"<tr><td>{r['beats']}</td><td>{r['lag_seconds_median']:.3f}</td><td>{r['rhythm']:.3f}</td><td>{r['harmony']:.3f}</td><td>{r['score']:.3f}</td><td>{'Pass' if r['passes_threshold'] else 'Below threshold'}</td></tr>" for r in first["candidates"])
    window_rows = "".join(f"<tr><td>{w['start']:g}–{w['end']:g} s</td><td>{w['raw_attack_count']}</td><td>{w['summary']['attack_count']}</td><td>{w['summary']['beats'] or 'Unresolved'}</td><td>{w['summary']['rhythm_similarity']}</td><td>{w['summary']['harmony_similarity']}</td></tr>" for w in windows)
    embeds = []
    for name in figures:
        b64 = base64.b64encode((ARTIFACT / name).read_bytes()).decode()
        embeds.append(f'<img src="data:image/png;base64,{b64}" alt="{e(name)}">')
    notes = ["Read the waveform, pitch classes, and raw event raster. A waveform shows loudness; the color map below it shows harmonic content. Neither is a verified score.",
             "Horizontal pitch traces are sustained tones; changing traces show changing register content. The red markers are the peaks actually accepted by the old detector. Gold lines are your reference times. The added constant-Q view preserves octave, but is only a spectrogram and is not used in the current segmentation.",
             "The two top panels align your approximate 6–12 and 12–18 s windows for inspection. In the similarity matrices, a bright cell means similar spectral/pitch content at two times. Repeated diagonal bands can suggest recurrence; pads can also make large bright blocks. These images alone do not verify identical notes or rhythm. The bottom plot is the exact old 6–18 s recurrence test, not a new automatic transcription.",
             "This plots the exact broad/fine comparison scores and robust thresholds. Each point compares rhythm, harmony, and texture on either side of that time; the dashed line is the required score and accepted automatic cuts would be vertical markers. This shows whether a missed boundary reflects weak feature changes, the adaptive threshold, or the peak-spacing/prominence gate."]
    html_out = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Why Other missed the pattern · Silo audit</title>
<style>body{{margin:0;background:{INK};color:#eaf0e4;font:16px/1.7 system-ui,sans-serif}}main{{max-width:1150px;margin:auto;padding:46px 24px 80px}}a{{color:{GREEN}}}h1,h2{{font-family:Georgia,serif;line-height:1.15}}h1{{font-size:clamp(34px,5vw,60px);max-width:900px}}h2{{font-size:30px;margin-top:54px}}.eyebrow{{font:12px ui-monospace,monospace;letter-spacing:.14em;color:{GREEN}}}.lead{{font-size:21px;max-width:940px}}.callout{{border-left:3px solid {GOLD};padding:18px 24px;background:#13251a}}.muted,figcaption{{color:#b5c9bb}}img{{width:100%;height:auto;display:block;border:1px solid #304938;border-radius:8px}}figure{{margin:28px 0 42px}}figcaption{{font-size:14px;margin-top:12px}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:12px 10px;border-bottom:1px solid #304938;text-align:left}}.scroll{{overflow-x:auto}}code{{overflow-wrap:anywhere;color:#e8c58e}}.facts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}}.fact{{padding:18px;background:#13251a;border:1px solid #304938}}.fact strong{{display:block;color:{GOLD}}}li{{margin:14px 0}}button{{background:#b2e6c9;color:#102619;padding:12px 16px;border:0;border-radius:30px;font:inherit;cursor:pointer;margin:6px}}.listen{{padding:16px;background:#13251a}}#audioStatus{{display:block;color:#c5d9ca;font-size:14px}}</style>
<main><a href="/">← Back to the record player</a><p class="eyebrow">LOCAL METHOD AUDIT · THREE-SONG SAMPLE · NO PUBLICATION</p><h1>Why the “Other” detector missed the musical figure.</h1><p class="lead">Silo — meaningful love (instrumental)</p><div class="callout">{e(overview)}</div>
<h2>1. The exact audio used</h2><p>The pattern input and the Other-disc audio preview are the same local WAV. It is separated from the dedicated instrumental recording, not a MIDI file, vocal track, or the original-MP3 player. Instrumental stems were produced with {e(audit['separation']['model'])}; the three outputs were averaged to mono, jointly peak-scaled, then saved as mono 22.05 kHz PCM-16 WAVs.</p>
<p><code>{e(f['relative_path'])}</code></p><div class="facts"><div class="fact"><strong>Recording</strong>{f['duration']:.3f} seconds · {f['sample_rate']:,} Hz · {f['channels']} channel</div><div class="fact"><strong>File format</strong>{f['format']} · {f['subtype']} · {f['bytes']:,} bytes</div><div class="fact"><strong>Analysis copy</strong>Mono · {SR:,} Hz · peak normalized</div></div><p class="muted">SHA-256: <code>{f['sha256']}</code><br>Source recording: <a href="{e(source_url)}">{e(source.get('title') or 'Supplied instrumental source')}</a>. The hash identifies these exact WAV bytes; no independent source-authenticity claim is made.</p>
<div class="listen"><strong>Audition this exact Other stem</strong><br><button data-start="0" data-end="68">0–68 s overview</button><button data-start="6" data-end="18">6–18 s arpeggio reference</button><button data-start="18" data-end="30">18–30 s reference</button><button data-start="30" data-end="42">30–42 s synth reference</button><button id="pauseAudio">Pause</button><span id="audioStatus">Audio is available when this report is opened through the local review server. These labels are your listening references.</span><audio id="auditAudio" preload="none" src="/audio/{TRACK}/other"></audio></div>
<h2>2. What was measured, and what was supplied</h2><p>{e(provenance)}</p><p><strong>Unassisted cut times:</strong> {e(auto_text)}. The user-supplied interval lock hides auto-proposed cuts inside 6–68 s; independently, rerunning without overrides still yields no cuts.</p><figure>{embeds[0]}<figcaption>{e(notes[0])}</figcaption></figure><figure>{embeds[3]}<figcaption>{e(notes[3])}</figcaption></figure>
<h2>3. Look inside the first 68 seconds</h2><figure>{embeds[1]}<figcaption>{e(notes[1])}</figcaption></figure>
<h2>4. The repeat test that failed</h2><figure>{embeds[2]}<figcaption>{e(notes[2])}</figcaption></figure>
<p>Acceptance needs <strong>attack similarity ≥ 0.55 AND weighted score ≥ 0.60</strong>. The score is 75% attack similarity + 25% harmonic similarity. The shortest lag within 0.025 of the best weighted score is selected before applying those gates.</p>
<div class="scroll"><table><thead><tr><th>Lag (beats)</th><th>Median lag (s)</th><th>Attack fit</th><th>Pitch-class fit</th><th>Weighted score</th><th>Both gates</th></tr></thead><tbody>{scores}</tbody></table></div>
<h2>5. What the current representation loses</h2><ol>{''.join(f'<li>{e(text)}</li>' for text in why)}</ol>
<h2>6. Is this limited to Silo?</h2><p>In the saved three-song sample, <strong>{failures} of {total_windows} distinct Other intervals</strong> have no accepted short attack cycle. Exact duplicate intervals across the two rings are counted once. Partially overlapping intervals still exist, so these are coverage counts, not independent trials or musical comparisons.</p><div class="scroll"><table><thead><tr><th>Sample file</th><th>Distinct intervals</th><th>No accepted cycle</th><th>User-annotated</th></tr></thead><tbody>{rows}</tbody></table></div>
<h2>7. Numbers for the reference windows</h2><p>These window edges are your review references. Scores are calculated on the untouched detector data inside them; this does not make the boundaries automatic.</p><div class="scroll"><table><thead><tr><th>Other time</th><th>Raw peaks</th><th>Occupied grid slots</th><th>Accepted beats</th><th>Attack fit</th><th>Pitch-class fit</th></tr></thead><tbody>{window_rows}</tbody></table></div>
<h2>8. Recommended next analysis</h2><ol>{''.join(f'<li>{e(text)}</li>' for text in next_steps)}</ol><p class="muted">Generated {e(audit['generated_at'])}. Detector version: <code>{VERSION}</code>. The current segmentation, thresholds, and supplied annotations were not replaced by this diagnostic. No classifier or cross-song recommendation was made.</p></main>
<script>const audio=document.getElementById('auditAudio'),status=document.getElementById('audioStatus');let stop=0;document.querySelectorAll('button[data-start]').forEach(b=>b.addEventListener('click',async()=>{{audio.pause();stop=Number(b.dataset.end);audio.currentTime=Number(b.dataset.start);try{{await audio.play();status.textContent=`Playing the actual Other WAV: ${{b.dataset.start}}–${{b.dataset.end}} s`;}}catch{{status.textContent='Open this report at http://127.0.0.1:8767/diagnostics/meaningful-love-other/ to audition local audio.';}}}}));document.getElementById('pauseAudio').addEventListener('click',()=>{{audio.pause();status.textContent='Paused.';}});audio.addEventListener('timeupdate',()=>{{if(audio.currentTime>=stop){{audio.pause();status.textContent='Reference excerpt finished.';}}}});</script></html>'''
    (WEB / "index.html").write_text(html_out, encoding="utf-8")
    (ARTIFACT / "MEANINGFUL_LOVE_OTHER_VISUAL_REPORT.html").write_text(html_out, encoding="utf-8")
    md = ["# Other-stem diagnostic: Silo — meaningful love (instrumental)", "", overview, "", "## Exact input", "",
          f"- WAV: `{f['relative_path']}`", f"- {f['duration']:.3f} seconds; {f['sample_rate']:,} Hz; {f['channels']} channel; {f['subtype']}; {f['bytes']:,} bytes.",
          f"- SHA-256: `{f['sha256']}`", f"- Source: {source_url}", f"- Analysis version: `{VERSION}`; mono {SR:,} Hz.", "", "## Boundary provenance", "", provenance,
          "", f"Unassisted cuts: {auto_text}.", ""]
    for i, name in enumerate(figures): md += [f"![Diagnostic figure {i + 1}](meaningful_love_other/{name})", "", notes[i], ""]
    md += ["## Method and failure modes", ""] + [f"{i}. {text}" for i, text in enumerate(why, 1)]
    md += ["", "## Exact 6–18 s candidate scores", "", "| Beats | Median lag s | Attack | Pitch class | Weighted | Passes both |", "| --- | ---: | ---: | ---: | ---: | --- |"]
    md += [f"| {r['beats']} | {r['lag_seconds_median']:.3f} | {r['rhythm']:.3f} | {r['harmony']:.3f} | {r['score']:.3f} | {r['passes_threshold']} |" for r in first["candidates"]]
    md += ["", "## Other sample coverage", "", f"{failures}/{total_windows} distinct intervals have no accepted short attack cycle. Exact interval duplicates across rings are counted once; overlapping windows are not independent trials.", "", "| File | Distinct intervals | No accepted cycle | Annotated |", "| --- | ---: | ---: | ---: |"]
    md += [f"| {r['artist']} — {r['title']} | {r['unique_intervals']} | {r['without_short_cycle']} | {r['annotated_intervals']} |" for r in audit["sample_other_coverage"]]
    md += ["", "## Recommended next analysis", ""] + [f"{i}. {text}" for i, text in enumerate(next_steps, 1)]
    md += ["", "## Reproduce / inspect", "", "Run `.venv/bin/python -m scripts.rhythm_layers.diagnose_other` in the existing WSL project environment.",
           "The self-contained HTML (embedded PNG figures) and `metrics.json` are generated under `output/analysis/meaningful_love_other/`.",
           "The local served copy is at http://127.0.0.1:8767/diagnostics/meaningful-love-other/; its audio buttons use the exact registered WAV. Offline HTML retains the figures but needs the local server for audio.",
           "The additional constant-Q spectrogram is diagnostic only. No note transcription, new boundaries, classifier, publication, or cross-song recommendation is claimed.", ""]
    (ROOT / "output/analysis/MEANINGFUL_LOVE_OTHER_DIAGNOSTIC.md").write_text("\n".join(md), encoding="utf-8")


if __name__ == "__main__":
    main()
