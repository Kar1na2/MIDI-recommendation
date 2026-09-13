#!/usr/bin/env python3
"""
Chord canonicalization: maps a pitch-class set (as produced by
output/chord_segments.csv, e.g. the string "0,3,6,8") to a
transposition-invariant canonical chord identity, using music21's
pitch-class-set-theory implementation (Forte 1973 classification).

Why this exists (Phase 3A design decision, see
output/analysis/CHORD_ANALYSIS_DESIGN.md section 1 for the full writeup):
the same progression transposed into a different key must map to the same
canonical symbols before cross-song pattern mining or alignment means
anything. Two invariances are in play and this module deliberately picks
only one of them:

- Transposition invariance (Tn): yes - {0,4,7} and {2,6,9} are "the same
  chord" (both major triads). This is essential for cross-key comparison.
- Set-theoretic inversion invariance (TnI, the mirror-image relation used
  in Forte's *numbering* scheme): explicitly NOT applied. TnI equivalence
  would collapse major and minor triads (and dominant-7th with
  half-diminished-7th, etc.) into the same class, since they are mirror
  images of each other. That distinction is exactly the kind of harmonic
  information this analysis needs to keep. See CHORD_ANALYSIS_DESIGN.md
  for the music21 experiment that surfaced this (primeFormString collapses
  major/minor; forteClass, with its A/B suffix, does not).

  Note this is unrelated to "chord inversion" in the everyday sense (which
  chord tone is in the bass, e.g. root position vs. first inversion) - that
  information was already discarded upstream, before this module ever runs:
  Chordino's chordnotes output and chord_segments.csv both represent a
  chord as an unordered pitch-class SET with no register or bass-note
  information, so voicing/bass-inversion invariance is a free consequence
  of the existing data representation, not something this module has to
  additionally enforce.

Canonical ID = music21 forteClass (e.g. "3-11B" for a major triad,
"3-11A" for minor, "4-27B" for dominant seventh, "4-27A" for
half-diminished seventh). This is Tn-invariant but not TnI-invariant,
matching the reasoning above.

`root_pc` is also exposed (music21's stacked-third root-finding heuristic)
alongside forte_class. It is NOT part of the transposition-invariant
canonical id - it is the actual (absolute, un-normalized) root pitch class
of this specific segment, kept for callers that need real root motion
(e.g. lib/chord_similarity.py's alignment scoring, added after Phase 3A's
alignment validation checkpoint showed quality-only symbols discard the
root-motion information that makes two progressions "the same" - see
CHORD_ANALYSIS_DESIGN.md section 4b).
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from music21 import chord as m21chord


@dataclass(frozen=True)
class CanonicalChord:
    forte_class: str      # e.g. "3-11B" - the canonical chord id used everywhere downstream
    prime_form: str        # e.g. "<037>" - TnI-invariant, kept for reference/debugging only
    common_name: str       # e.g. "major triad" - generic (non-pitched) music21 name
    cardinality: int       # number of distinct pitch classes (3 or 4 in this corpus)
    root_pc: int           # absolute root pitch class 0-11 of THIS segment (not transposition-invariant)


def parse_pitch_class_set(pcs_str: str) -> tuple[int, ...]:
    """'0,3,6,8' -> (0, 3, 6, 8). Matches the pitch_class_set column format
    used by output/chord_segments.csv (see extract_chords.py / PIPELINE_MAP.md)."""
    return tuple(sorted(int(x) for x in pcs_str.split(",") if x != ""))


@lru_cache(maxsize=None)
def canonicalize(pitch_classes: tuple[int, ...]) -> CanonicalChord:
    """pitch_classes must be a hashable (tuple) sequence of ints 0-11.
    Cached because the corpus revisits the same small vocabulary of pitch-class
    sets thousands of times (8,342 segments over ~a few hundred distinct sets)."""
    c = m21chord.Chord(list(pitch_classes))
    return CanonicalChord(
        forte_class=c.forteClass,
        prime_form=c.primeFormString,
        common_name=c.commonName,
        cardinality=len(pitch_classes),
        root_pc=c.root().pitchClass,
    )


def canonicalize_str(pcs_str: str) -> CanonicalChord:
    """Convenience wrapper taking the raw CSV string form directly."""
    return canonicalize(parse_pitch_class_set(pcs_str))
