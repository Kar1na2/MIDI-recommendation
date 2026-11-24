#!/usr/bin/env python3
"""Explore cycle-grid repetition and cross-song patterns across MIDI stems.

Reads merged/<song>.mid and writes only to output/analysis/tidal_patterns/.
For each of the 7 non-empty stem combinations, records a zlib compression
ratio and mines transposition-invariant two-bar fingerprints on a 16th-note
grid. The fingerprints preserve stem identity and onset; melodic pitches are
stored relative to each stem's modal pitch class, while drums retain their GM
class. Tempo is estimated from the MIDI note onsets and normalized to 60-180
BPM (half-time correction above that range). This is an exploratory metric,
not a verified transcription-accuracy score.

Run: python -m scripts.analysis.analyze_tidal_patterns
"""
from __future__ import annotations

import csv
import argparse
import itertools
import math
import random
import zlib
from collections import Counter, defaultdict
from pathlib import Path

import pretty_midi

MIDI_DIR = Path("merged")
OUT_DIR = Path("output/analysis/tidal_patterns")
STEMS = ("other", "bass", "drums")
COMBOS = [combo for n in range(1, 4) for combo in itertools.combinations(STEMS, n)]
TICKS_PER_BAR = 16
PHRASE_BARS = 2
PHRASE_TICKS = TICKS_PER_BAR * PHRASE_BARS
HOP_TICKS = TICKS_PER_BAR


def stem_instruments(midi):
    result = {s: [] for s in STEMS}
    for inst in midi.instruments:
        name = (inst.name or "").strip().lower()
        if name in result:
            result[name].append(inst)
    return result


def estimate_bpm(midi, instruments):
    if sum(len(inst.notes) for insts in instruments.values() for inst in insts) < 8:
        return 120.0
    try:
        # PrettyMIDI estimates tempo from its internally sorted unique note
        # onsets. Using the standalone API on concatenated per-track onsets is
        # invalid because that list is not chronological.
        bpm = float(midi.estimate_tempo())
    except Exception:
        bpm = 120.0
    if not math.isfinite(bpm) or bpm <= 0:
        bpm = 120.0
    # Basic Pitch event density often yields a double-time estimate. Keep a
    # conventional beat range; this leaves unavoidable half/double ambiguity
    # explicit in the methodology and report.
    while bpm > 180:
        bpm /= 2
    while bpm < 60:
        bpm *= 2
    return bpm


def modal_pc(instruments):
    counts = Counter(n.pitch % 12 for inst in instruments for n in inst.notes)
    return counts.most_common(1)[0][0] if counts else 0


def build_events(instruments, bpm):
    beat_sec = 60.0 / bpm
    events = {s: [] for s in STEMS}
    for stem, insts in instruments.items():
        base = modal_pc(insts)
        for inst in insts:
            for n in inst.notes:
                tick = int(round((n.start / beat_sec) * 4)) % TICKS_PER_BAR
                # Pitch classes preserve transposition-invariant contour/harmony.
                # Drums preserve the GM class as a part-specific discrete label.
                pitch = n.pitch if stem == "drums" else (n.pitch - base) % 12
                events[stem].append((n.start, tick, pitch))
        events[stem].sort()
    return events


def encode_combo(events, combo, bpm):
    beat_sec = 60.0 / bpm
    all_events = []
    for stem in combo:
        for seconds, tick_in_bar, pitch in events[stem]:
            abs_tick = int(round((seconds / beat_sec) * 4))
            all_events.append((abs_tick, stem, pitch))
    if not all_events:
        return [], 0
    last_tick = max(e[0] for e in all_events)
    buckets = [[] for _ in range(last_tick + 1)]
    for tick, stem, pitch in all_events:
        buckets[tick].append((stem, pitch))
    stream = [tuple(sorted(cell)) for cell in buckets]
    # Event-onset stream: empty ticks are retained so rhythmic gaps contribute
    # to both repetition and compression. Absolute pitch is omitted by design.
    return stream, last_tick + 1


def phrases(stream, combo):
    if len(stream) < PHRASE_TICKS:
        return []
    out = []
    for start in range(0, len(stream) - PHRASE_TICKS + 1, HOP_TICKS):
        # Canonicalize relative pitch per stem within each phrase, so the same
        # pattern can match in a new key without erasing intervals within it.
        flat = []
        offsets = {}
        for rel, cell in enumerate(stream[start:start + PHRASE_TICKS]):
            for stem, pitch in cell:
                if stem != "drums":
                    offsets.setdefault(stem, pitch)
                    pitch = (pitch - offsets[stem]) % 12
                flat.append(f"{rel}:{stem}:{pitch}")
        per_stem = Counter(s for cell in stream[start:start + PHRASE_TICKS] for s, _ in cell)
        active_ticks = sum(bool(cell) for cell in stream[start:start + PHRASE_TICKS])
        min_each = 2 if len(combo) > 1 else 0
        if (len(flat) >= max(5, 4 * len(combo)) and active_ticks >= 4
                and all(per_stem[s] >= min_each for s in combo)):
            out.append((start // TICKS_PER_BAR, "|".join(flat)))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N MIDI files")
    args = parser.parse_args()
    files = sorted(MIDI_DIR.glob("*.mid"))
    if args.limit is not None:
        files = files[:args.limit]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    song_meta = {}
    combo_sequences = defaultdict(dict)
    combo_phrases = defaultdict(lambda: defaultdict(list))
    score_rows = []
    for index, path in enumerate(files, 1):
        midi = pretty_midi.PrettyMIDI(str(path))
        instruments = stem_instruments(midi)
        bpm = estimate_bpm(midi, instruments)
        events = build_events(instruments, bpm)
        song_meta[path.stem] = {"bpm": bpm, "duration": midi.get_end_time(),
                                "counts": {s: sum(len(i.notes) for i in instruments[s]) for s in STEMS}}
        for combo in COMBOS:
            label = "+".join(combo)
            stream, ticks = encode_combo(events, combo, bpm)
            raw = "\n".join(";".join(f"{s}:{p}" for s, p in cell) for cell in stream).encode()
            compressed = zlib.compress(raw, level=9)
            shuffled = list(stream)
            random.Random(path.stem + label).shuffle(shuffled)
            shuffled_raw = "\n".join(";".join(f"{s}:{p}" for s, p in cell) for cell in shuffled).encode()
            shuffled_compressed = zlib.compress(shuffled_raw, level=9)
            nonempty = sum(bool(c) for c in stream)
            score_rows.append({"song": path.stem, "parts": label, "bpm_estimate": f"{bpm:.2f}",
                               "duration_sec": f"{midi.get_end_time():.2f}",
                               "event_count": sum(song_meta[path.stem]["counts"][s] for s in combo),
                               "grid_ticks": ticks, "active_grid_share": f"{nonempty / max(ticks,1):.5f}",
                               "raw_bytes": len(raw), "zlib_bytes": len(compressed),
                               "compression_ratio": f"{len(compressed) / max(len(raw),1):.5f}",
                               "shuffled_zlib_bytes": len(shuffled_compressed),
                               "repetition_compression_gain": f"{1 - len(compressed) / max(len(shuffled_compressed),1):.5f}"})
            combo_sequences[label][path.stem] = stream
            combo_phrases[label + "|pitch"][path.stem] = phrases(stream, combo)
            rhythm_stream = [tuple(sorted(set((stem, 0) for stem, _ in cell))) for cell in stream]
            combo_phrases[label + "|rhythm"][path.stem] = phrases(rhythm_stream, combo)
        if index % 100 == 0:
            print(f"processed {index}/{len(files)} songs", flush=True)

    fields = list(score_rows[0]) if score_rows else []
    with (OUT_DIR / "song_compression.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(score_rows)

    common_rows = []
    pair_rows = []
    for label, by_song in combo_phrases.items():
        parts, feature = label.rsplit("|", 1)
        index = defaultdict(lambda: defaultdict(list))
        for song, items in by_song.items():
            for bar, key in items:
                index[key][song].append(bar)
        # Phrase evidence must recur twice in each song to suppress one-off
        # coincidences; phrase windows have already passed an event-density
        # and selected-stem coverage filter.
        pair_support = defaultdict(list)
        for key, song_bars in index.items():
            repeated = {s: bars for s, bars in song_bars.items() if len(bars) >= 2}
            if len(repeated) < 2:
                continue
            songs = sorted(repeated)
            for a, b in itertools.combinations(songs, 2):
                pair_support[(a, b)].append((key, repeated[a], repeated[b], len(song_bars)))
        for (a, b), matches in pair_support.items():
            uniq = {m[0] for m in matches}
            evidence = sorted(matches, key=lambda m: (m[3], m[0]))
            pair_rows.append({"parts": parts, "feature": feature, "song_a": a, "song_b": b,
                              "shared_repeated_phrases": len(uniq),
                              "a_repeated_phrase_instances": sum(len(m[1]) for m in matches),
                              "b_repeated_phrase_instances": sum(len(m[2]) for m in matches),
                              "rarest_phrase_song_count": min(m[3] for m in matches),
                              "example_phrase": evidence[0][0],
                              "example_a_bars": ",".join(map(str, evidence[0][1][:8])),
                              "example_b_bars": ",".join(map(str, evidence[0][2][:8]))})
        for key, song_bars in index.items():
            songs = sorted(song_bars)
            if len(songs) >= 2:
                common_rows.append({"parts": parts, "feature": feature, "song_count": len(songs),
                                    "song_examples": " | ".join(songs[:8]),
                                    "phrase": key, "instances": sum(len(v) for v in song_bars.values())})

    pair_rows.sort(key=lambda r: (-r["shared_repeated_phrases"], r["rarest_phrase_song_count"], r["parts"]))
    common_rows.sort(key=lambda r: (r["song_count"], -r["instances"], r["parts"]))
    for name, rows in (("cross_song_pairs.csv", pair_rows), ("cross_song_phrases.csv", common_rows)):
        with (OUT_DIR / name).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["parts"])
            w.writeheader(); w.writerows(rows)

    print(f"songs={len(files)} combo-scores={len(score_rows)}")
    for combo in ("other", "bass", "drums", "other+bass", "other+drums", "bass+drums", "other+bass+drums"):
        selected = [r for r in score_rows if r["parts"] == combo]
        ratios = sorted(float(r["compression_ratio"]) for r in selected)
        gains = sorted(float(r["repetition_compression_gain"]) for r in selected)
        for feature in ("pitch", "rhythm"):
            matches = [r for r in pair_rows if r["parts"] == combo and r["feature"] == feature]
            phrases_found = [r for r in common_rows if r["parts"] == combo and r["feature"] == feature]
            print(f"  {feature:6s} repeated-pairs={len(matches)} shared-phrases={len(phrases_found)}")
            for r in sorted(matches, key=lambda r: (-r["shared_repeated_phrases"], r["rarest_phrase_song_count"]))[:2]:
                print("    MATCH", r["song_a"], "<->", r["song_b"], "phrases", r["shared_repeated_phrases"], "rarity", r["rarest_phrase_song_count"])
    print(f"wrote {OUT_DIR}")


if __name__ == "__main__":
    main()
