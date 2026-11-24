#!/usr/bin/env python3
"""
Phase 4 JS-port validation, step 1/2: runs the SAME 9 hand-picked pairs used
in scripts/analysis/validate_alignment_sample.py (chords) and
validate_note_alignment_sample.py (notes) through the real Python
lib/chord_similarity.py + lib/note_similarity.py + lib/sequence_alignment.py
at the PRODUCTION gap penalty (-2.0, per scripts/analysis/run_alignment.py /
run_note_alignment.py - not the validation scripts' own sweep candidates),
and dumps {input sequences, expected output} to a JSON file that
scripts/webexport/run_js_validation.mjs feeds through docs/js/alignment.js
for comparison. Read-only against output/analysis/*.csv.

Usage:
    uv run python -m scripts.webexport.dump_validation_cases
"""
import csv
import json
import os
from collections import defaultdict
from dataclasses import asdict

from lib.chord_similarity import best_transposition_alignment
from lib.sequence_alignment import smith_waterman
from lib.note_similarity import interval_substitution_score

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHORD_SEQUENCES_CSV = os.path.join(REPO_ROOT, "output/analysis/chord_sequences.csv")
NOTE_SEQUENCES_CSV = os.path.join(REPO_ROOT, "output/analysis/note_sequences.csv")
OUT_JSON = os.path.join(REPO_ROOT, "scripts/webexport/_validation_cases.json")

GAP_PENALTY = -2.0  # production value, both phases (see docstring above)

PAIRS = [
    ("163braces - 過期 (lyrics music video)", "1nonly - Meaningless Love (Official Music Video)", "reference x reference"),
    ("163braces - 過期 (lyrics music video)", "Alice U (앨리스유) - 도주 (Official Video)", "reference x reference"),
    ("1nonly - Meaningless Love (Official Music Video)", "Alice U (앨리스유) - 도주 (Official Video)", "reference x reference"),
    ("Kanye West - Heartless", "Kanye West - Stronger", "same artist"),
    ("Kanye West - Heartless", "Kanye West - POWER", "same artist"),
    ("Imagine Dragons - Bones (Official Music Video)", "Imagine Dragons - Natural", "same artist"),
    ("헤이즈 (Heize) - And July (Feat. DEAN, DJ Friz) MV", "헤이즈 (Heize) - Jenga (Feat. Gaeko) MV", "same artist"),
    ("Nocturne in B flat minor, Op. 9 no. 1", "1nonly - Meaningless Love (Official Music Video)", "negative control"),
    ("Nocturne in B flat minor, Op. 9 no. 1", "Alice U (앨리스유) - 도주 (Official Video)", "negative control"),
]


def load_chord_sequences():
    by_song = defaultdict(list)
    with open(CHORD_SEQUENCES_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_song[row["song"]].append((int(row["segment_index"]), row["canonical_chord_id"], int(row["root_pc"])))
    for song in by_song:
        by_song[song].sort(key=lambda t: t[0])
        by_song[song] = [(q, r) for _, q, r in by_song[song]]
    return by_song


def load_note_sequences():
    by_song = defaultdict(list)
    with open(NOTE_SEQUENCES_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["interval_from_prev"] != "":
                by_song[row["song"]].append((int(row["note_index"]), int(row["interval_from_prev"])))
    for song in by_song:
        by_song[song].sort(key=lambda t: t[0])
        by_song[song] = [v for _, v in by_song[song]]
    return by_song


def main():
    chord_seqs = load_chord_sequences()
    note_seqs = load_note_sequences()

    cases = []
    for song_a, song_b, label in PAIRS:
        seq_a_chords = chord_seqs[song_a]
        seq_b_chords = chord_seqs[song_b]
        best_t, chord_result = best_transposition_alignment(seq_a_chords, seq_b_chords, GAP_PENALTY)

        seq_a_notes = note_seqs[song_a]
        seq_b_notes = note_seqs[song_b]
        note_result = smith_waterman(seq_a_notes, seq_b_notes, interval_substitution_score, GAP_PENALTY)

        cases.append({
            "song_a": song_a, "song_b": song_b, "label": label,
            "chord": {
                "seq_a": [{"forte": q, "root": r} for q, r in seq_a_chords],
                "seq_b": [{"forte": q, "root": r} for q, r in seq_b_chords],
                "expected_score": chord_result.score,
                "expected_best_transposition": best_t,
                "expected_span_a": list(chord_result.span_a),
                "expected_span_b": list(chord_result.span_b),
            },
            "note": {
                "seq_a": seq_a_notes,
                "seq_b": seq_b_notes,
                "expected_score": note_result.score,
                "expected_span_a": list(note_result.span_a),
                "expected_span_b": list(note_result.span_b),
            },
        })
        print(f"{label:20s} {song_a!r} x {song_b!r}: chord_score={chord_result.score:.4f} "
              f"(t={best_t}), note_score={note_result.score:.4f}")

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"gap_penalty": GAP_PENALTY, "cases": cases}, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {OUT_JSON} ({len(cases)} cases).")


if __name__ == "__main__":
    main()
