"""Build a local rhythm/vocal review catalog without exporting lyric text.

Use --samples for the three requested examples. The full-library option is
resumable; absent lyric alignments are reported as pending, never invented.
"""
from __future__ import annotations

import argparse
import copy
from collections import Counter
from datetime import datetime, timezone
import time

import numpy as np
import soundfile as sf

from .common import ROOT, OUT, PRIVATE, PREVIEW, PARTS, SAMPLES, inventory, read_json, write_json
from .patterns import analyze_parts, extract, pulse, grid, signatures, signature_distance, cycle_summary, timeline_map, map_time


def duration(path, fallback):
    try:
        return round(float(sf.info(ROOT / path).duration), 3)
    except Exception:
        return float(fallback)


def vocal_layer(track, total):
    alignment = read_json(PRIVATE / "alignments" / f"{track['track_id']}.json", {})
    override = read_json(ROOT / "scripts/rhythm_layers/source_overrides.json", {}).get(track["track_id"], {})
    if track["instrumental_recording"]:
        alignment = {"status": "recording_has_no_lead_vocal_source", "reason": override.get("recording_mismatch"),
                     "source_url": override.get("genius_url"), "sections": []}
    sections = []
    for source in alignment.get("sections", []):
        sections.append({key: source.get(key) for key in
                         ("label", "start", "end", "word_count", "mean_word_confidence", "transcription_match", "review_status", "section_grouping")})
    valid = bool(sections) and all(float(a["start"]) <= float(b["start"]) for a, b in zip(sections, sections[1:]))
    if not valid:
        label = "Instrumental recording · no lead vocal" if track["instrumental_recording"] else "Vocal sections awaiting reliable alignment"
        sections = [{"label": label, "start": 0., "end": total, "word_count": 0, "review_status": "not applicable" if track["instrumental_recording"] else "pending"}]
    windows = []
    for index, section in enumerate(sections):
        section["aligned_end"] = section["end"]
        next_start = min(total, float(sections[index + 1]["start"])) if index + 1 < len(sections) else total
        # Small inter-phrase gaps belong to the preceding source section. Longer
        # instrumental gaps/tails get their own explicitly unaligned window.
        gap = next_start - float(section["end"])
        section["end"] = next_start if gap <= 4. else min(total, float(section["end"]))
        section["start"] = min(total, max(0., float(section["start"])))
        section["description"] = ("Genius section heading timed on the separate vocals.wav. Boundaries are alignment candidates and need listening review."
                                  if section.get("word_count") else alignment.get("reason") or "No aligned lead vocal in this window.")
        section["playable"] = True
        section["clock"] = "original"
        windows.append(section)
        if gap > 4.:
            windows.append({"label": "Outro · no aligned lead lyric" if index + 1 == len(sections) else "Instrumental gap · no aligned lead lyric",
                            "start": section["end"], "end": next_start, "aligned_end": section["end"], "word_count": 0,
                            "description": "No source lyric words were aligned in this window. Ad-libs or background vocals may still be present; listen to review.",
                            "review_status": "review", "playable": True, "clock": "original"})
    for index, section in enumerate(windows):
        section["index"] = index
    meta = {key: alignment.get(key) for key in
            ("status", "source_url", "language", "word_alignment", "asr_vs_source_similarity", "asr_token_overlap_similarity", "aligned_word_count", "section_boundary_method", "reason")}
    meta["review_targets"] = override.get("review_anchors", [])
    meta["reference_url"] = override.get("reference_url")
    return {"depth": 1, "name": "Vocal form", "kind": "vocal", "duration": total, "segments": windows}, meta


def relationships(track, sections):
    vocal = [s for s in sections if s.get("word_count", 0) > 0]
    if not vocal or not all(p in track["original_parts"] for p in PARTS):
        return {"status": "not_applicable" if track["instrumental_recording"] else "pending_alignment", "transitions": []}
    features = {p: extract(ROOT / track["original_parts"][p]) for p in PARTS}
    total = min(float(f["duration"]) for f in features.values())
    beats, bpm, _ = pulse(features, total)
    times, values = grid(features, beats, total)

    def transition(at, names):
        index = int(np.searchsorted(times, at))
        subset = {p: values[p] for p in names}
        left, right = signatures(subset, index - 32, index), signatures(subset, index, index + 32)
        if left is None or right is None:
            return None
        delta = signature_distance(left, right)
        return {"rhythm_shift": round(float(delta[0]), 3), "harmony_shift": round(float(delta[1]), 3)}

    groups = {"combined": PARTS, **{p: (p,) for p in PARTS}}
    transitions = []
    for section in vocal:
        transitions.append({"at": round(section["start"], 3), "section": section["label"],
                            "views": {name: transition(section["start"], names) for name, names in groups.items()}})
    baseline = {name: [] for name in groups}
    vocal_starts = [s["start"] for s in vocal]
    for index in range(32, len(times) - 32, 16):
        at = float(times[index])
        if any(abs(at - start) < 4. for start in vocal_starts):
            continue
        for name, names in groups.items():
            value = transition(at, names)
            if value:
                baseline[name].append(value["rhythm_shift"])
    summary = {}
    for name in groups:
        values_at_boundaries = [row["views"][name]["rhythm_shift"] for row in transitions if row["views"].get(name)]
        summary[name] = {"mean_vocal_boundary_rhythm_shift": round(float(np.mean(values_at_boundaries)), 3) if values_at_boundaries else None,
                         "mean_other_checkpoint_rhythm_shift": round(float(np.mean(baseline[name])), 3) if baseline[name] else None,
                         "boundary_count": len(values_at_boundaries), "other_checkpoint_count": len(baseline[name])}
    return {"status": "candidate_measurements", "method": "Compare attack-location and pitch-class templates for the eight estimated beats on each side of a vocal-section start. Combined is an equal-weight mean of three stems, without vocal audio.",
            "clock": "Original separated bass/drums/other, on the vocals.wav clock", "pulse_bpm": round(bpm, 2),
            "transitions": transitions, "summary": summary}


def annotate(layers, notes):
    for layer in layers:
        for segment in layer["segments"]:
            exact = next((note for note in notes if abs(note["start"] - segment["start"]) < .05 and abs(note["end"] - segment["end"]) < .05), None)
            if exact:
                segment["label"] = exact["label"]
                segment["annotation"] = f"{exact['basis']}: {exact['label']}."
                segment["description"] = segment["annotation"] + " The measured recurrence values remain available below; this label is not an independent transcription."


def build_one(track):
    tid = track["track_id"]
    override = read_json(ROOT / "scripts/rhythm_layers/source_overrides.json", {}).get(tid, {})
    if not all(p in track["parts"] for p in PARTS):
        raise ValueError("All three instrumental stems are required")
    analysis = analyze_parts(track["parts"], override.get("pattern_anchors"))
    total = duration(track.get("original_mp3"), track["duration"])
    same_clock = all(track["parts"].get(p) == track["original_parts"].get(p) for p in PARTS)
    if same_clock:
        mapping = {"status": "matched", "confidence": 1., "method": "Same original recording clock",
                   "instrumental_time": [0., analysis["duration"]], "original_time": [0., total]}
    else:
        mapping = timeline_map(track["parts"], track["original_parts"])
    vocals, vocal_meta = vocal_layer(track, total)
    notes = override.get("listening_notes", [])
    annotate(analysis["views"]["instrumental"]["layers"], notes)
    annotate(analysis["views"]["other"]["layers"], notes)
    main_layers = [vocals]
    for layer in analysis["views"]["instrumental"]["layers"]:
        layer = copy.deepcopy(layer)
        layer["depth"] += 1
        layer["kind"] = "instrumental_pattern"
        layer["duration"] = total if mapping["status"] == "matched" else analysis["duration"]
        for segment in layer["segments"]:
            segment["source_start"], segment["source_end"] = segment["start"], segment["end"]
            mapped_start, mapped_end = map_time(mapping, segment["start"]), map_time(mapping, segment["end"])
            segment["playable"] = mapped_start is not None and mapped_end is not None and mapped_end - mapped_start > .15
            segment["clock"] = "original" if segment["playable"] else "instrumental_unmatched"
            if segment["playable"]:
                segment["start"], segment["end"] = mapped_start, mapped_end
        main_layers.append(layer)
    views = {"main": {"name": "Full song", "duration": total, "layers": main_layers}}
    for part in PARTS:
        view = analysis["views"][part]
        view["duration"] = analysis["duration"]
        for layer in view["layers"]:
            layer["kind"] = "instrumental_pattern"
            layer["duration"] = analysis["duration"]
            for segment in layer["segments"]:
                segment["playable"], segment["clock"] = True, "instrumental"
        views[part] = view
    source = read_json(ROOT / "docs/data/instrumentals/songs" / f"{tid}.json", {}).get("source", {})
    return {"track_id": tid, "title": track["title"], "artist": track["artist"], "duration": total,
            "method": analysis["method"], "pattern_source": track["pattern_source"], "pulse": analysis["pulse"],
            "views": views, "vocal": vocal_meta, "timeline_match": mapping,
            "relationships": relationships(track, vocals["segments"]), "listening_notes": notes, "source": source,
            "audio": {"main": bool(track.get("original_mp3")), **{p: bool(track["parts"].get(p)) for p in PARTS}}}


def assert_no_lyrics(value):
    forbidden = {"lyrics", "lines", "timed_words", "alignment_text", "raw_alignment", "asr_transcript", "text"}
    if isinstance(value, dict):
        if forbidden.intersection(value):
            raise ValueError("Private lyrics were found in the proposed public export")
        for item in value.values(): assert_no_lyrics(item)
    elif isinstance(value, list):
        for item in value: assert_no_lyrics(item)


def write_report(records, catalog):
    counts = Counter()
    lines = ["# Rhythm and vocal layers — local review", "", f"Generated {datetime.now(timezone.utc).isoformat()}", "",
             f"This review contains **{len(records)} of {len(catalog)} catalog songs**. It is a sample validation, not a claim that the full library has been reanalysed. The published Pages export is untouched.", "",
             "## Method and interpretation", "",
             "- Main disc: exactly three presentation rings (vocal form, broad instrumental pattern, finer motif). Each stem disc: exactly two rings. These counts are a design choice, so the useful observed statistic is the number of supported windows, not average ring count.",
             "- Instrumental patterns use binary onset locations, normalized pitch-class sequences, and a small spectral-shape contribution. RMS, velocity, loudness trends, and stem energy shares do not define labels or boundaries.",
             "- Broad rings retain at most four automatically ranked changes; finer rings retain at most six before inheriting broad cuts and supplied annotations. Unsplit rings are allowed. Beat grids and recurrence labels remain candidates, not verified note transcriptions or detected meter.",
             "- Genius section text and word timings remain in ignored local data. The page contains headings, source links, timing/confidence measurements, and no full lyrics. Word alignment and section grouping need listening review; model agreement is not an accuracy guarantee.",
             "- Section windows continue until the next section starts. Actual last-word times are retained separately. Vocals never enter the instrumental feature computation.", "",
             "## Coverage", "", f"Catalog inventory: {sum(bool(t.get('original_mp3')) for t in catalog)} original MP3s; {sum('vocals' in t['original_parts'] for t in catalog)} vocal stems; {sum(t['pattern_source']=='dedicated_instrumental' for t in catalog)} dedicated instrumental sets. The older recordings are linked using the ingestion manifest, rather than filename guesses.", "",
             "| Song | Vocal status | Source headings | Ordered ASR/source similarity | Instrumental/original clock match |",
             "| --- | --- | ---: | ---: | --- |"]
    for record in records:
        sections = record["views"]["main"]["layers"][0]["segments"]
        headings = sum(s.get("word_count", 0) > 0 for s in sections)
        vocal, mapping = record["vocal"], record["timeline_match"]
        lines.append(f"| {record['artist']} — {record['title']} | {vocal.get('status')} | {headings} | {vocal.get('asr_vs_source_similarity', 'n/a')} | {mapping['status']} ({mapping.get('confidence', 0):.2f}) |")
        for view in record["views"].values():
            for layer in view["layers"]:
                if layer.get("kind") == "vocal": continue
                for segment in layer["segments"]:
                    if segment.get("cycle", {}).get("beats"):
                        counts[segment["cycle"]["beats"]] += 1
    lines += ["", "## Pattern windows", "", "| View | Mean broad windows | Mean fine windows |", "| --- | ---: | ---: |"]
    for name in ("main", *PARTS):
        broad = [len(r["views"][name]["layers"][-2]["segments"]) for r in records]
        fine = [len(r["views"][name]["layers"][-1]["segments"]) for r in records]
        lines.append(f"| {name} | {np.mean(broad):.2f} | {np.mean(fine):.2f} |")
    lines += ["", "Cycle candidates across displayed instrumental windows (including repeated presentation of the main/Other user annotations): " + ", ".join(f"{beats}-beat: {count}" for beats, count in sorted(counts.items())) + ".", "",
              "## Vocal / instrumental relationships", "", "Attack-pattern difference ranges from 0 (same template) to 1 (completely different). The baseline uses other four-beat checkpoints away from vocal starts. These are descriptive within-song associations, not causal claims or cross-song comparisons.", ""]
    for record in records:
        relation = record["relationships"]
        lines.append(f"- **{record['artist']} — {record['title']}**: {relation['status']}.")
        for name, stats in relation.get("summary", {}).items():
            lines.append(f"  - {name}: mean vocal-boundary rhythm difference {stats['mean_vocal_boundary_rhythm_shift']}; other checkpoints {stats['mean_other_checkpoint_rhythm_shift']}; {stats['boundary_count']} vocal boundaries.")
    missing = [(r["title"], v) for r in records for v, available in r["audio"].items() if not available]
    blocked = [(r["title"], r["timeline_match"]["status"]) for r in records if r["timeline_match"]["status"] != "matched"]
    lines += ["", "## Preview limitations and recommended fixes", "", f"Missing sample audio files: {missing or 'none'}. Main-disc pattern windows with an unmatched instrumental clock are disabled: {blocked or 'none'}. Stem WAV previews use their own clock.", "",
              "Silo's saved entry is explicitly instrumental. It has no invented vocal form. The supplied 0:06/0:18/0:30/0:42/0:55/1:08 listening anchors and descriptive arpeggio/synth notes are shown as user annotations, not independent detections.", "",
              "Review AESPA's first verse/pre-chorus/chorus against the user's ~0:13/~0:38/~0:49 anchors, and audition Milena's section transitions. Reject or revise any mismatched source recording or poorly aligned lyric heading before expanding the run.", "",
              "Next steps: review the three examples and stylus playback; listen-check multilingual alignment and version matching; then run the resumable corpus pipeline and source lookup with explicit failure statuses. No classifier, cross-song recommendations, or publication is included in this preview.", ""]
    unavailable = [track for track in catalog if not track.get("original_mp3")]
    lines += ["## Library input gaps (before the full run)", "", f"{len(unavailable)} catalog entries have no linked original MP3/vocal stem. These are existing input gaps, not sample-player failures.", "",
              "| Song | Existing ingestion reason | Recommended fix |", "| --- | --- | --- |"]
    for track in unavailable:
        name = f"{track['artist']} — {track['title']}".replace("|", "/")
        reason = (track.get("original_source_reason") or track.get("original_source_status") or "No recorded source path").replace("|", "/")
        lines.append(f"| {name} | {reason} | Supply/verify the correct original MP3, link its Spotify ID, then separate vocals. Dedicated instrumental stems can still provide their own preview where available. |")
    target = ROOT / "output/analysis/RHYTHM_VOCAL_PREVIEW_REPORT.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--track-id")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    catalog = inventory()
    rows = [t for t in catalog if not args.samples or t["track_id"] in SAMPLES]
    if args.track_id: rows = [t for t in rows if t["track_id"] == args.track_id]
    if args.limit: rows = rows[:args.limit]
    if not rows: parser.error("No catalog songs match the requested selection")
    records, errors, media = [], [], {}
    for number, track in enumerate(rows, 1):
        tid = track["track_id"]
        target = OUT / "records" / f"{tid}.json"
        started = time.time()
        try:
            record = read_json(target) if args.resume and target.is_file() else build_one(track)
            assert_no_lyrics(record)
            write_json(target, record)
            records.append(record)
            media[tid] = {"main": track.get("original_mp3"), **track["parts"]}
            print(f"[{number}/{len(rows)}] {track['artist']} — {track['title']}: ready ({time.time()-started:.1f}s)", flush=True)
        except Exception as error:
            errors.append({"track_id": tid, "title": track["title"], "reason": f"{type(error).__name__}: {error}"})
            print(f"[{number}/{len(rows)}] {track['title']}: failed: {error}", flush=True)
    exported = {"generated_at": datetime.now(timezone.utc).isoformat(), "scope": "local review", "catalog_count": len(catalog), "tracks": records, "errors": errors}
    assert_no_lyrics(exported)
    write_json(PREVIEW / "data.json", exported)
    write_json(OUT / "media-index.json", media)
    write_json(OUT / "build-status.json", {"ready": len(records), "requested": len(rows), "errors": errors})
    if records: print(f"Report: {write_report(records, catalog)}", flush=True)
    print(f"Local preview: {len(records)} ready; {len(errors)} errors", flush=True)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
