"""Resumable ASR + known-text word timing on the isolated vocal stem.

The output with lyric text lives only in ignored output/analysis private data.
The site export contains section times and review measurements, never words.
"""
from __future__ import annotations

import argparse
import re
import time

import rapidfuzz.fuzz
import stable_whisper
import soundfile as sf

from .common import PRIVATE, inventory, read_json, write_json, ROOT


def compact(text):
    return re.sub(r"[^\w]", "", text, flags=re.UNICODE).casefold()


def clean_sections(record):
    return [{"name": section["name"], "text": " ".join(section["lines"])}
            for section in record.get("sections", []) if section.get("lines")]


def result_parts(result):
    data = result.to_dict()
    entries = []
    for segment in data.get("segments", []):
        words = segment.get("words") or []
        for word in words:
            start, end = word.get("start"), word.get("end")
            if start is None or end is None:
                continue
            entries.append({"text": word.get("word", "").strip(), "start": round(float(start), 3),
                            "end": round(float(end), 3), "confidence": word.get("probability", word.get("prob"))})
    return data, entries


def split_timed_sections(source_sections, words, raw_segments=None):
    if not source_sections or not words:
        return []
    if raw_segments and len(raw_segments) == len(source_sections) and all(
            compact(a["text"]) == compact(b.get("text", "")) for a, b in zip(source_sections, raw_segments)):
        grouped = []
        for source, raw in zip(source_sections, raw_segments):
            chosen = [{"text": w.get("word", "").strip(), "start": float(w["start"]), "end": float(w["end"]),
                       "confidence": w.get("probability", w.get("prob"))} for w in raw.get("words", [])]
            if not chosen:
                break
            probabilities = [float(w["confidence"]) for w in chosen if w["confidence"] is not None]
            grouped.append({"label": source["name"], "start": chosen[0]["start"], "end": chosen[-1]["end"],
                            "word_count": len(chosen), "mean_word_confidence": round(sum(probabilities) / len(probabilities), 3) if probabilities else None,
                            "section_grouping": "preserved Genius heading split", "timed_words": chosen})
        if len(grouped) == len(source_sections):
            return grouped
    token_pattern = re.compile(r"[\w]+(?:['’][\w]+)*", flags=re.UNICODE)
    token_totals = [len(token_pattern.findall(section["text"])) for section in source_sections]
    exact_token_grouping = sum(token_totals) == len(words) and all(token_totals)
    totals = token_totals if exact_token_grouping else [max(1, len(compact(section["text"]))) for section in source_sections]
    cumulative = 0
    boundaries = []
    timed_character_count = sum(len(compact(word["text"])) for word in words)
    for length in totals[:-1]:
        cumulative += length
        if exact_token_grouping:
            boundaries.append(cumulative)
            continue
        target = cumulative / sum(totals)
        char_count, cut, best_delta = 0, 0, 2
        for index, word in enumerate(words[:-1], 1):
            char_count += len(compact(word["text"]))
            delta = abs(char_count / max(timed_character_count, 1) - target)
            if delta < best_delta:
                best_delta, cut = delta, index
        boundaries.append(cut)
    result, first = [], 0
    for index, section in enumerate(source_sections):
        last = boundaries[index] if index < len(boundaries) else len(words)
        if last <= first:
            continue
        chosen = words[first:last]
        probabilities = [float(w["confidence"]) for w in chosen if w["confidence"] is not None]
        result.append({"label": section["name"], "start": chosen[0]["start"], "end": chosen[-1]["end"],
                       "word_count": len(chosen), "mean_word_confidence": round(sum(probabilities) / len(probabilities), 3) if probabilities else None,
                       "section_grouping": "one-to-one source token count" if exact_token_grouping else "proportional source-text grouping",
                       "timed_words": chosen})
        first = last
    return result


def align_one(track, record, model, force):
    tid = track["track_id"]
    override = read_json(ROOT / "scripts/rhythm_layers/source_overrides.json", {}).get(tid, {})
    target = PRIVATE / "alignments" / f"{tid}.json"
    if target.is_file() and not force:
        return read_json(target)
    source_sections = clean_sections(record)
    audio = track.get("original_parts", {}).get("vocals")
    if not audio or not (ROOT / audio).is_file():
        output = {"track_id": tid, "status": "vocal_stem_missing", "sections": []}
    elif record.get("recording_mismatch") or track.get("instrumental_recording"):
        output = {"track_id": tid, "status": "recording_has_no_lead_vocal_source", "sections": [],
                  "source_url": record.get("source_url"), "reason": record.get("recording_mismatch") or "Instrumental source title"}
    elif record.get("status") != "lyrics_ready":
        output = {"track_id": tid, "status": "lyrics_source_unavailable", "sections": [],
                  "source_status": record.get("status")}
    elif not source_sections:
        output = {"track_id": tid, "status": "no_structured_sections", "sections": [], "source_url": record.get("source_url")}
    else:
        language = override.get("language") or record.get("language")
        # Keep each source heading as a separate original line. Lyrics themselves
        # remain local; the output page receives only headings and millisecond spans.
        text = "\n".join(section["text"] for section in source_sections)
        aligned = model.align(str(ROOT / audio), text, language=language,
                              original_split=True, regroup=False, suppress_silence=False)
        raw, words = result_parts(aligned)
        language = raw.get("language") or language
        if not words:
            raise RuntimeError("Forced aligner produced no word timings")
        aligned_text = " ".join(word["text"] for word in words)
        sections = split_timed_sections(source_sections, words, raw.get("segments"))
        duration = float(sf.info(ROOT / audio).duration)
        leading = []
        if sections and sections[0]["start"] > 1.2:
            leading = [{"label": "Intro · no aligned lead vocal", "start": 0., "end": sections[0]["start"],
                        "word_count": 0, "mean_word_confidence": None, "timed_words": []}]
        for section in sections:
            section["transcription_match"] = None
            section["review_status"] = "review"
            section["timed_words"] = [{k: v for k, v in word.items() if k != "confidence"} for word in section["timed_words"]]
        output = {"track_id": tid, "status": "aligned_needs_asr_review", "language": language,
                  "source_url": record.get("source_url"), "word_alignment": "stable-ts large-v3 cross-attention + DTW",
                  "asr_vs_source_similarity": None, "asr_token_overlap_similarity": None, "asr_word_count": None,
                  "aligned_word_count": len(words), "duration": duration,
                  "section_boundary_method": sections[0].get("section_grouping") if sections else None,
                  "review_targets": override.get("review_anchors", []),
                  "sections": leading + sections, "alignment_text": raw.get("text"), "raw_alignment": raw,
                  "recording_id": tid}
        write_json(target, output)
        print(f"  {tid}: word alignment saved; checking independent transcription", flush=True)
        try:
            # The input is already an isolated vocal stem. Avoid the additional
            # Torch Hub VAD download and bound fallback decoding on sung audio.
            transcription = model.transcribe(str(ROOT / audio), language=language, word_timestamps=False,
                                             regroup=False, vad=False, verbose=False, temperature=0.0,
                                             condition_on_previous_text=False, beam_size=1, best_of=1)
            asr = transcription.to_dict()
            asr_text = " ".join(s.get("text", "") for s in asr.get("segments", []))
            score = rapidfuzz.fuzz.ratio(compact(aligned_text), compact(asr_text))
            output.update({"status": "aligned_needs_review", "asr_review_status": "complete",
                           "asr_vs_source_similarity": round(float(score), 1),
                           "asr_token_overlap_similarity": round(float(rapidfuzz.fuzz.token_set_ratio(aligned_text, asr_text)), 1),
                           "asr_word_count": len(asr_text.split()), "asr_transcript": asr})
            for section in output["sections"]:
                section["transcription_match"] = round(float(score), 1)
        except Exception as error:
            output["asr_review_status"] = f"failed: {type(error).__name__}: {error}"[:250]
    write_json(target, output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--track-id")
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    rows = inventory()
    if args.track_id:
        rows = [row for row in rows if row["track_id"] == args.track_id]
        if not rows:
            parser.error("Spotify track ID is not in the saved library")
    if args.limit:
        rows = rows[:args.limit]
    index = read_json(PRIVATE / "lyrics-index.json", {})
    eligible = [row for row in rows if row["track_id"] in index or OVERRIDE_SOURCE(row["track_id"])]
    model = stable_whisper.load_model(args.model, device=args.device, download_root=str(PRIVATE / "models"),
                                      in_memory=True)
    summary = read_json(PRIVATE / "alignment-index.json", {})
    for number, track in enumerate(eligible, 1):
        tid = track["track_id"]
        if tid in summary and not args.force:
            continue
        lyrics = read_json(PRIVATE / "lyrics" / f"{tid}.json", {})
        if not lyrics:
            summary[tid] = {"status": index.get(tid, {}).get("status", "lyrics_not_fetched")}
            continue
        try:
            result = align_one(track, lyrics, model, args.force)
            summary[tid] = {"status": result["status"], "sections": len(result.get("sections", [])),
                            "aligned_words": result.get("aligned_word_count", 0),
                            "similarity": result.get("asr_vs_source_similarity"), "source_url": result.get("source_url")}
            print(f"[{number}/{len(eligible)}] {track['artist']} — {track['title']}: {result['status']} ({result.get('aligned_word_count', 0)} words)", flush=True)
        except Exception as error:
            summary[tid] = {"status": "alignment_failed", "reason": f"{type(error).__name__}: {error}"[:300]}
            print(f"[{number}/{len(eligible)}] {track['artist']} — {track['title']}: alignment_failed: {error}", flush=True)
        write_json(PRIVATE / "alignment-index.json", summary)
    print(f"Alignment statuses: { {status: sum(r.get('status') == status for r in summary.values()) for status in sorted({r.get('status') for r in summary.values()})} }", flush=True)


def OVERRIDE_SOURCE(track_id):
    from .common import ROOT, read_json
    return bool(read_json(ROOT / "scripts/rhythm_layers/source_overrides.json", {}).get(track_id))


if __name__ == "__main__":
    main()
