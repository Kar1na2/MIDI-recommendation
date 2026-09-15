#!/usr/bin/env python3
"""
Curated display-name table for the frontend (Phase 4 data layer only - see
PHASE4 brief section 2, "verify chord naming is actually usable for
display").

Why this exists instead of trusting `chord_vocabulary.csv`'s `common_name`
column directly: that column is music21's `Chord.commonName`, computed
PER PITCH-CLASS-SET (100 distinct sets in this corpus), and it turns out to
be inconsistent across sets that canonicalize to the SAME forte_class - the
exact same chord quality gets a clean name for some voicings/spellings and
an "enharmonic equivalent to X" / "enharmonic to X" prefixed variant for
others (a music21 spelling quirk, not a musical difference - see
scripts/webexport/build_web_data.py's investigation, or just:
    4-26 -> {'minor seventh chord': 1290, 'enharmonic equivalent to minor seventh chord': 360}
for the same forte_class). Using it as-is for the frontend would make the
same chord quality display two different ways depending on which segment
you're looking at.

The fix is small because the FULL corpus-wide canonical vocabulary (after
Phase 3A's transposition-invariant canonicalization to forte_class) is only
9 distinct chord qualities - NOT ~100 (100 is the count of raw,
pre-canonicalization pitch-class sets in chord_vocabulary.csv, one row per
transposition). Measured from output/analysis/chord_sequences.csv (8,342
segments total):

    forte_class   segment share   display quality chosen here
    -----------   -------------   ---------------------------
    3-11B         25.3%           major
    4-26          19.8%           minor7
    3-11A         18.0%           minor
    4-20          14.6%           major7
    4-27B         11.7%           dominant7
    4-27A          5.5%           half-diminished7
    3-12           2.8%           augmented
    4-22A          1.3%           add9   <- see note below
    3-10            1.0%           diminished

8/9 of these are unambiguous standard chord-quality names once you ignore
music21's enharmonic-spelling prefix (diminished triad / minor7 / major7 /
dominant7 all had this happen to SOME of their rows - stripping the prefix
and keying by forte_class instead of by exact pitch-class-set already fixes
those, no new musical judgment needed).

The one real gap: 4-22A. music21's commonName for it is "major-second major
tetrachord" - technically not wrong, but not a name anyone would recognize
as a chord in a UI. Its pitch classes, re-rooted to 0, are {0, 2, 4, 7}:
root + major 2nd/9th + major 3rd + perfect 5th - i.e. a plain major triad
{0,4,7} with an added 9th. "add9" is the standard pop/jazz name for exactly
that shape, so that's what's used here. Verified against
output/analysis/chord_vocabulary.csv's two example pitch-class sets for
4-22A (`3,5,7,10` rooted at 3 -> {0,2,4,7}; `0,2,5,10` rooted at 10 ->
{0,2,4,7}) via music21 directly (see build_web_data.py's inline check).

Net result: 9/9 forte classes (100% of the corpus's 8,342 chord segments)
now get a clean, standard display name - 0 bare Forte-class fallbacks
reach the frontend. Reported in docs/data/DATA_LAYER.md.
"""
from __future__ import annotations

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# forte_class -> display quality suffix. Covers exactly the 9 forte classes
# actually present in this corpus (output/analysis/chord_sequences.csv);
# not a general-purpose Forte-class-to-name table.
FORTE_QUALITY: dict[str, str] = {
    "3-11B": "major",
    "3-11A": "minor",
    "3-10": "diminished",
    "3-12": "augmented",
    "4-20": "major7",
    "4-26": "minor7",
    "4-27B": "dominant7",
    "4-27A": "half-diminished7",
    "4-22A": "add9",
}


def chord_display_name(forte_class: str, root_pc: int) -> str:
    """'C major', 'G minor7', etc. Falls back to the bare forte_class if an
    unexpected (out-of-corpus) forte class ever shows up, rather than
    raising - this is a display convenience, not validation logic."""
    quality = FORTE_QUALITY.get(forte_class)
    root = NOTE_NAMES[root_pc % 12]
    if quality is None:
        return f"{root} [{forte_class}]"
    return f"{root} {quality}"
