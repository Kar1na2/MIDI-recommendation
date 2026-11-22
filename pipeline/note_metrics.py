"""Measures on unquantized note events, separate from pattern matching."""
from collections import defaultdict


def onset_texture(notes, tolerance_s=.03):
    """Count note-onset events with another same-label onset within tolerance.

    Consecutive onset gaps <= tolerance form a connected group. Chord groups
    interrupt a melodic run; maximal strictly ascending singleton runs of at
    least four notes are counted once each.
    """
    by_instrument = defaultdict(list)
    for note in notes:
        by_instrument[note["instrument"]].append(note)
    simultaneous = 0
    ascending = []
    for instrument, rows in sorted(by_instrument.items()):
        rows = sorted(rows, key=lambda n: (n["onset_s"], n["pitch"]))
        groups = []
        for note in rows:
            if groups and note["onset_s"] - groups[-1][-1]["onset_s"] <= tolerance_s + 1e-9:
                groups[-1].append(note)
            else:
                groups.append([note])
        simultaneous += sum(len(group) for group in groups if len(group) > 1)
        run = []

        def finish():
            if len(run) >= 4:
                ascending.append({"instrument": instrument, "note_count": len(run),
                                  "start_s": run[0]["onset_s"],
                                  "end_s": run[-1]["offset_s"],
                                  "pitches": [n["pitch"] for n in run]})

        for group in groups:
            if len(group) != 1:
                finish()
                run = []
                continue
            note = group[0]
            if run and note["pitch"] <= run[-1]["pitch"]:
                finish()
                run = []
            run.append(note)
        finish()
    count = len(notes)
    share = simultaneous / count if count else None
    return {"note_count": count, "simultaneous_onset_count": simultaneous,
            "simultaneous_onset_share": share, "coincidence_tolerance_ms": tolerance_s * 1000,
            "ascending_single_note_run_count": len(ascending),
            "ascending_single_note_runs": ascending,
            "failure_classification": "transcription" if not count or share > .5 else "matching",
            "definition": "Each note onset counts once; same-instrument neighbour within 30 ms is simultaneous. "
                          "Ascending runs are maximal strictly increasing singleton onset-group sequences, "
                          "interrupted by a chord, repeat or descent; minimum 4 notes."}
