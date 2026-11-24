"""Describe supported, nested changes in downloaded instrumental stems.

The analysis uses one-second summaries of three Demucs stems. It deliberately
allows a layer to inherit the same span when a finer stable split is absent.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

import librosa
import numpy as np
from scipy.signal import find_peaks

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "instrumentals"
DB = LIBRARY / "catalog.sqlite"
STEMS = ("bass", "drums", "other")
VERSION = "instrumental-stem-patterns-v4"
SR = 22050
HOP = 512


def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    return db


def summarize(path: Path):
    y, sr = librosa.load(path, sr=SR, mono=True)
    stft = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    mel = librosa.feature.melspectrogram(S=stft**2, sr=sr, n_mels=32)
    log_mel = librosa.power_to_db(mel, ref=np.max)
    mfcc = librosa.feature.mfcc(S=log_mel, n_mfcc=13)
    chroma = librosa.feature.chroma_stft(S=stft, sr=sr)
    rms = librosa.feature.rms(S=stft)[0]
    centroid = librosa.feature.spectral_centroid(S=stft, sr=sr)[0]
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    frame_count = min(mfcc.shape[1], chroma.shape[1], len(rms), len(centroid), len(onset))
    frame_times = librosa.frames_to_time(np.arange(frame_count), sr=sr, hop_length=HOP)
    seconds = np.floor(frame_times).astype(int)
    count = max(1, int(np.ceil(len(y) / sr)))
    raw = np.vstack([mfcc[:, :frame_count], chroma[:, :frame_count], rms[:frame_count],
                     centroid[:frame_count], onset[:frame_count]]).T
    x = np.zeros((count, raw.shape[1]), dtype=np.float32)
    energy = np.zeros(count, dtype=np.float32)
    for second in range(count):
        mask = seconds == second
        if np.any(mask):
            x[second] = raw[mask].mean(axis=0)
            energy[second] = float(rms[:frame_count][mask].mean())
    median = np.median(x, axis=0)
    scale = np.median(np.abs(x - median), axis=0) * 1.4826
    scale[scale < 1e-5] = 1.0
    normalized = np.clip((x - median) / scale, -6, 6)
    normalized[:, np.std(x, axis=0) < 1e-6] = 0
    return normalized.astype(np.float32), energy, float(len(y) / sr)


def detect_cuts(features, start, end, context, minimum_gap, sensitivity=3.0):
    lo = int(np.ceil(start))
    hi = int(np.floor(end))
    radius = max(2, int(round(context)))
    side_floor = max(2, min(radius, int(minimum_gap // 3)))
    if hi - lo < 2 * side_floor + minimum_gap:
        return []
    novelty = np.zeros(hi - lo, dtype=np.float32)
    valid_positions = []
    for t in range(lo + side_floor, hi - side_floor):
        left = features[max(lo, t - radius):t]
        right = features[t:min(hi, t + radius)]
        if len(left) < side_floor or len(right) < side_floor:
            continue
        left = left.mean(axis=0)
        right = right.mean(axis=0)
        novelty[t - lo] = float(np.sqrt(np.mean((left - right) ** 2)))
        valid_positions.append(t - lo)
    valid = novelty[valid_positions]
    if len(valid) == 0:
        return []
    baseline = float(np.median(valid))
    mad = float(np.median(np.abs(valid - baseline)))
    threshold = max(0.42, baseline + sensitivity * max(mad, 0.04))
    prominence = max(0.18, 1.5 * mad)
    peaks, properties = find_peaks(novelty, distance=minimum_gap, prominence=prominence)
    result = []
    for peak, prom in zip(peaks, properties.get("prominences", [])):
        absolute = lo + int(peak)
        if peak not in valid_positions:
            continue
        if novelty[peak] >= threshold and prom >= prominence:
            result.append((absolute, float(novelty[peak])))
    return result


def window_similarity(features, duration, width=6.0, step=1.0, threshold=0.99):
    n = len(features)
    w = int(round(width))
    if n < 3 * w:
        return []
    starts = np.arange(0, n - w + 1, max(1, int(round(step))))
    windows = np.stack([features[s:s + w] for s in starts])
    moving = np.sqrt(np.mean(np.diff(windows, axis=1) ** 2, axis=(1, 2))) >= 0.08
    starts = starts[moving]
    windows = windows[moving]
    if len(starts) < 2:
        return []
    vectors = windows.reshape(len(windows), -1)
    norms = np.linalg.norm(vectors, axis=1)
    vectors = vectors / np.maximum(norms[:, None], 1e-8)
    similarity = vectors @ vectors.T
    pairs = []
    for i, j in zip(*np.where(np.triu(similarity, k=1) >= threshold)):
        if starts[j] - starts[i] >= w:
            pairs.append((float(similarity[i, j]), int(starts[i]), int(starts[j])))
    pairs.sort(reverse=True)
    selected = []
    occupied = []
    for score, a, b in pairs:
        if any(abs(a - x) < w / 2 or abs(b - x) < w / 2 for x in occupied):
            continue
        selected.append({"start": a, "end": min(a + w, duration), "repeat_start": b,
                         "repeat_end": min(b + w, duration), "similarity": round(score, 4),
                         "kind": f"similar {w}-second audio-texture window"})
        occupied.extend((a, b))
        if len(selected) >= 12:
            break
    return selected


def energy_label(energy_by_stem, start, end, view, duration):
    left = max(0, int(start))
    right = min(len(next(iter(energy_by_stem.values()))), max(left + 1, int(np.ceil(end))))
    means = {stem: float(np.mean(values[left:right])) for stem, values in energy_by_stem.items()}
    total = sum(means.values())
    if total <= 1e-9:
        return "Very low measured stem activity", "The separated stems have very little measured energy in this window."
    shares = {stem: value / total for stem, value in means.items()}
    if view == "instrumental":
        dominant = max(shares, key=shares.get)
        if shares[dominant] >= 0.48:
            label = f"{dominant.title()}-led texture"
        else:
            label = "Balanced instrumental texture"
        edge = max(1, min(3, (right - left) // 2))
        start_means = {s: float(np.mean(v[left:left + edge])) for s, v in energy_by_stem.items()}
        end_means = {s: float(np.mean(v[max(left, right - edge):right])) for s, v in energy_by_stem.items()}
        start_total, end_total = sum(start_means.values()), sum(end_means.values())
        changes = []
        if start_total > 1e-7 and end_total > 1e-7:
            for stem in STEMS:
                change = end_means[stem] / end_total - start_means[stem] / start_total
                if change >= 0.18 and end_means[stem] / end_total > 0.25:
                    changes.append(f"{stem} becomes more prominent")
                elif change <= -0.18 and start_means[stem] / start_total > 0.25:
                    changes.append(f"{stem} becomes less prominent")
        description = ", then ".join(changes[:2]) if changes else "Stem balance stays broadly similar across the window."
        return label, description
    stem = view
    values = energy_by_stem[stem][left:right]
    if not len(values):
        return f"{stem.title()} activity", "No usable energy summary for this window."
    edge = max(1, min(3, len(values) // 2))
    delta = float(np.mean(values[-edge:]) - np.mean(values[:edge]))
    reference = max(float(np.max(energy_by_stem[stem])), 1e-7)
    if delta > reference * 0.06:
        label, description = f"{stem.title()} grows", f"The {stem} stem gains energy toward the end of this window."
    elif delta < -reference * 0.06:
        label, description = f"{stem.title()} recedes", f"The {stem} stem loses energy toward the end of this window."
    else:
        label, description = f"{stem.title()} holds", f"The {stem} stem's average activity is relatively steady here."
    return label, description


def make_view(view, duration, features, energies, depth):
    levels = []
    parents = [(0.0, duration, 0)]
    # Keep the broad disc useful for short intros while asking for clearer,
    # less frequent evidence as the viewer moves inward.
    specs = [(9.0, 12, 3.4), (6.0, 10, 3.3), (4.0, 8, 3.4)][:depth]
    max_new_boundaries = (3, 3, 4)[:depth]
    labels = ["Broad arrangement shifts", "Phrase-scale changes", "Fine texture changes"]
    for level_index, (context, min_gap, sensitivity) in enumerate(specs, 1):
        segments = []
        candidates = []
        for parent_start, parent_end, parent_index in parents:
            cuts = detect_cuts(features, parent_start, parent_end, context, min_gap, sensitivity)
            candidates.extend((score, float(t), parent_start, parent_end, parent_index)
                              for t, score in cuts)
        # A song can only add a small number of new sections at each depth.
        # Pick its strongest supported changes instead of letting a noisy stem
        # multiply into dozens of tiny child windows.
        selected = []
        per_parent = {}
        for score, cut, parent_start, parent_end, parent_index in sorted(candidates, reverse=True):
            if len(selected) >= max_new_boundaries[level_index - 1]:
                break
            if per_parent.get(parent_index, 0) >= (2 if level_index == 1 else 1):
                continue
            selected.append((cut, score, parent_start, parent_end, parent_index))
            per_parent[parent_index] = per_parent.get(parent_index, 0) + 1
        selected_by_parent = {}
        for cut, score, parent_start, parent_end, parent_index in selected:
            selected_by_parent.setdefault(parent_index, []).append((cut, score))

        next_parents = []
        for parent_start, parent_end, parent_index in parents:
            parent_cuts = sorted(selected_by_parent.get(parent_index, []))
            boundaries = [parent_start] + [cut for cut, _ in parent_cuts] + [parent_end]
            refined = bool(parent_cuts)
            for start, end in zip(boundaries, boundaries[1:]):
                label, detail = energy_label(energies, start, end, view, duration)
                if not refined:
                    detail += " No finer split passed the conservative change threshold."
                cut_score = next((score for cut, score in parent_cuts if abs(cut - start) < 1e-6), None)
                segments.append({"index": len(segments), "start": round(start, 2), "end": round(end, 2),
                                 "label": label, "description": detail,
                                 "boundary_supported": bool(refined),
                                 "pattern_change_score": round(cut_score, 3) if cut_score is not None else None})
                next_parents.append((start, end, len(segments) - 1))
        levels.append({"depth": level_index, "name": labels[level_index - 1], "segments": segments,
                       "new_boundaries": max(0, len(segments) - len(parents))})
        parents = next_parents
    return {"name": "All three stems" if view == "instrumental" else view.title(),
            "preview": {"type": "mp3" if view == "instrumental" else "wav",
                        "file": "original.mp3" if view == "instrumental" else f"{view}.wav"},
            "layers": levels,
            "recurrences": window_similarity(features, duration)}


def analyze_track(track, assets):
    paths = {part: ROOT / assets[part]["path"] for part in STEMS}
    data = {part: summarize(path) for part, path in paths.items()}
    duration = min(float(track["duration"]), *(item[2] for item in data.values()))
    energies = {part: item[1][:int(np.ceil(duration))] for part, item in data.items()}
    instrument_features = np.concatenate([data[part][0][:int(np.ceil(duration))] for part in STEMS], axis=1)
    stacked = np.stack([energies[part] for part in STEMS], axis=1)
    shares = stacked / np.maximum(stacked.sum(axis=1, keepdims=True), 1e-8)
    instrument_features = np.concatenate([instrument_features, shares], axis=1)
    return {
        "track_id": track["track_id"], "title": track["title"], "artist": track["artist"],
        "duration": round(duration, 2), "method": VERSION,
        "views": {
            "instrumental": make_view("instrumental", duration, instrument_features, energies, 3),
            **{part: make_view(part, duration, data[part][0][:int(np.ceil(duration))], energies, 2) for part in STEMS},
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--refresh-ready", action="store_true", help="Recompute already ready records with the current analysis method")
    parser.add_argument("--watch", action="store_true", help="Analyze new separated stems until the download and separation queues finish")
    parser.add_argument("--watch-interval", type=int, default=90)
    parser.add_argument("--idle-timeout-minutes", type=int, default=30)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/data/instrumentals/songs")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    def query_tracks():
        status_filter = "status='separated'" + (" OR status='analysis_failed'" if args.retry_failed else "") + (" OR status='ready'" if args.refresh_ready else "")
        with connect() as db:
            return [dict(row) for row in db.execute(f"SELECT * FROM tracks WHERE {status_filter} ORDER BY artist,title")]

    processed = set()
    total = 0
    idle_since = time.monotonic()
    while True:
      tracks = [track for track in query_tracks() if track["track_id"] not in processed]
      if args.limit is not None:
          tracks = tracks[:max(0, args.limit - total)]
      if tracks:
        print(f"{len(tracks)} separated instrumentals queued for analysis", flush=True)
      for track in tracks:
        total += 1
        try:
            with connect() as db:
                assets = {row["part"]: dict(row) for row in db.execute("SELECT * FROM assets WHERE track_id=?", (track["track_id"],))}
            if not all(part in assets for part in STEMS):
                raise ValueError("One or more separated stem WAVs are missing from the asset catalog")
            result = analyze_track(track, assets)
            target = args.output / f"{track['track_id']}.json"
            target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            with connect() as db:
                db.execute("INSERT INTO analysis(track_id,method_version,result_json) VALUES(?,?,?) ON CONFLICT(track_id) DO UPDATE SET method_version=excluded.method_version,result_json=excluded.result_json",
                           (track["track_id"], VERSION, json.dumps(result, ensure_ascii=False)))
                db.execute("UPDATE tracks SET status='ready',reason=NULL,updated_at=? WHERE track_id=?",
                           (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), track["track_id"]))
            cuts = [layer["new_boundaries"] for layer in result["views"]["instrumental"]["layers"]]
            repeats = sum(len(view["recurrences"]) for view in result["views"].values())
            print(f"[{total}] {track['artist']} — {track['title']}: ready; main boundaries by depth {cuts}, texture recurrences {repeats}", flush=True)
        except Exception as error:
            reason = f"{type(error).__name__}: {error}"[:2000]
            with connect() as db:
                db.execute("UPDATE tracks SET status='analysis_failed',reason=?,updated_at=? WHERE track_id=?",
                           (reason, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), track["track_id"]))
            print(f"[{total}] {track['artist']} — {track['title']}: FAILED: {reason}", flush=True)
        processed.add(track["track_id"])
        idle_since = time.monotonic()
      if not args.watch or (args.limit is not None and total >= args.limit):
          break
      with connect() as db:
          unfinished = db.execute("SELECT count(*) FROM tracks WHERE status IN ('pending','source_found','downloaded','separating','separated')").fetchone()[0]
      if unfinished == 0:
          break
      if time.monotonic() - idle_since >= args.idle_timeout_minutes * 60:
          print(f"No new separated tracks for {args.idle_timeout_minutes} minutes; {unfinished} upstream stages remain.", flush=True)
          break
      print(f"Waiting {args.watch_interval}s for separated instrumentals; {unfinished} upstream stages remain", flush=True)
      time.sleep(max(5, args.watch_interval))


if __name__ == "__main__":
    main()
