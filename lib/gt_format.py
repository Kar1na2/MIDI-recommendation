"""
Shared ground-truth annotation format used by select_eval_sample.py and
evaluate_transcription.py.

ground_truth/<song_name>/<stem>.csv, columns: onset,offset,pitch,label

    onset   required, seconds (float)
    offset  optional; blank means this is an onset-only annotation - still
            used for onset-accuracy scoring, excluded from note-transcription
            scoring (which needs a full onset/offset/pitch note)
    pitch   optional, MIDI note number (e.g. 60 = middle C). For the drums
            stem, use 36=kick / 38=snare / 42=hihat to match the GM mapping
            transcribe_drums.py writes, so pitch-tolerance matching in
            mir_eval actually checks "right drum class", not just "a hit
            occurred".
    label   optional free text, ignored by scoring (e.g. "kick", "unsure",
            a note name) - for the annotator's own bookkeeping.

A blank offset or pitch is fine (an onset-only row); a present offset must
be numeric and after the onset. Rows may be in any order in the file - both
scripts sort by onset.
"""
import csv
from pathlib import Path

CSV_HEADER = ["onset", "offset", "pitch", "label"]


class GroundTruthError(ValueError):
    pass


def load_ground_truth(path: Path):
    """Parse a ground-truth CSV into a list of dicts, sorted by onset:
    {'onset': float, 'offset': float|None, 'pitch': float|None, 'label': str}

    Returns [] if the file doesn't exist or has no data rows. Raises
    GroundTruthError (naming the file and line) on a malformed row.
    """
    if not path.exists():
        return []
    rows = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or "onset" not in reader.fieldnames:
            raise GroundTruthError(f"{path}: expected a header row with at least an 'onset' column")
        for lineno, row in enumerate(reader, start=2):
            onset_raw = (row.get("onset") or "").strip()
            if not onset_raw:
                continue  # blank line
            try:
                onset = float(onset_raw)
            except ValueError:
                raise GroundTruthError(f"{path}:{lineno}: 'onset' is not a number: {onset_raw!r}")

            offset = None
            offset_raw = (row.get("offset") or "").strip()
            if offset_raw:
                try:
                    offset = float(offset_raw)
                except ValueError:
                    raise GroundTruthError(f"{path}:{lineno}: 'offset' is not a number: {offset_raw!r}")
                if offset <= onset:
                    raise GroundTruthError(f"{path}:{lineno}: offset ({offset}) must be after onset ({onset})")

            pitch = None
            pitch_raw = (row.get("pitch") or "").strip()
            if pitch_raw:
                try:
                    pitch = float(pitch_raw)
                except ValueError:
                    raise GroundTruthError(f"{path}:{lineno}: 'pitch' is not a number: {pitch_raw!r}")

            rows.append({"onset": onset, "offset": offset, "pitch": pitch,
                         "label": (row.get("label") or "").strip()})
    rows.sort(key=lambda r: r["onset"])
    return rows


def full_note_rows(rows):
    """Rows with both offset and pitch filled in - the subset usable for
    mir_eval.transcription note-level scoring."""
    return [r for r in rows if r["offset"] is not None and r["pitch"] is not None]


def write_template(path: Path):
    """Write an empty (header-only) ground-truth CSV, if one doesn't already exist."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        csv.writer(f).writerow(CSV_HEADER)
