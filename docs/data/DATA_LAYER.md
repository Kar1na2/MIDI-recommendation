# Phase 4 Data Layer — Schema Reference

Everything a static GitHub Pages frontend (Phase 5) needs to fetch and
render this corpus, plus the client-side alignment engine that answers
"what else matches this specific selection" live in the browser. Written so
Phase 5 can build against these files without re-deriving anything from
`output/`, `chords/`, or the Python pipeline.

**Isolation:** everything here lives under `docs/`. Nothing under `output/`,
`chords/`, `merged/`, `audio/`, `k-indie/`, or `lib/` was modified to build
it — `lib/*.py` was only imported/read. Build tooling lives in
`scripts/webexport/` (read-only against Phase 1–3 output; writes only
under `docs/`), following this repo's existing `scripts/<stage>/` + `lib/`
convention. Reproduce everything with:

```
uv run python -m scripts.webexport.build_web_data      # manifest/sequences/songs JSON
uv run python -m scripts.webexport.convert_audio        # docs/audio/*.mp3 (128kbps)
uv run python -m scripts.webexport.dump_validation_cases && node scripts/webexport/run_js_validation.mjs   # validates docs/js/alignment.js
```

**Site-root assumption:** every `audio_url` and every path below is
written relative to `docs/`. Phase 5 settled this: the repo's GitHub Pages
source is `main` branch, `/docs` folder — so `docs/` *is* the deployed
document root. This directory was originally built as `web/` during Phase
4 and renamed to `docs/` (content unchanged, only the folder name and every
path reference to it) when Phase 5 picked the publish target — see
`docs/README.md` for the rest of that decision. Local dev: serve from
inside `docs/`, or prefix every path with `docs/` if serving from the repo
root instead.

---

## 1. `manifest.json`

One entry per song (75 total), loaded once at app start; filter this
client-side for the search-suggestion dropdown (no backend at this corpus
size). ~7.7 KB.

```jsonc
[
  { "song_id": "163braces-lyrics-music-video",
    "title": "163braces - 過期 (lyrics music video)",
    "duration": 233.143 },
  ...
]
```

- `song_id` — stable slug, ASCII `[a-z0-9-]`, derived from the source
  filename stem (see "song_id derivation" below). Use this to build every
  other URL/lookup key in this doc.
- `title` — the raw source filename stem (video/track title exactly as it
  appears in `audio/`/`k-indie/`), unmodified. Use for display and search.
- `duration` — seconds, float, from `ffprobe` on the actual source audio
  (i.e. real playback duration, not derived from the last MIDI note).

### song_id derivation (if you ever need to regenerate or cross-check)

`title` → NFKD-normalize → strip to ASCII → lowercase → replace runs of
non-`[a-z0-9]` with `-` → trim `-` → truncate to 60 chars → collision
suffix `-2`, `-3`, ... if two titles land on the same slug (none actually
collided across the real 75 titles). **One title is entirely non-ASCII**
(`ひとひら`) and slugifies to the generic fallback `song` — it did not
collide with anything, but if the corpus ever grows, don't assume `song_id`
values are always derived from recognizable Latin characters in the title.
The full title↔song_id mapping used for this build is also in
`docs/data/_song_id_map.csv` (a build-debug artifact, not part of the
frontend contract — don't fetch it from the app).

---

## 2. `sequences.json`

The **only file the matching engine needs loaded** — every song's compact
chord-segment and note-interval sequence, keyed by `song_id`. 75 songs,
8,342 chord segments, 113,938 note intervals total. **6.3 MB raw / ~667 KB
gzip** (GitHub Pages serves gzip automatically — budget against the gzip
figure for real-world load time).

```jsonc
{
  "163braces-lyrics-music-video": {
    "chords": [
      { "start": 0.2322, "end": 0.9288, "canonical_chord_id": "4-27B", "root_pc": 8 },
      ...
    ],
    "notes": [
      { "onset": 0.2795, "interval_from_prev": null },   // first note of the song: no previous note
      { "onset": 0.2909, "interval_from_prev": 7 },
      ...
    ]
  },
  ...
}
```

- `chords[].canonical_chord_id` — Phase 3A's `forte_class` (e.g. `"4-27B"`),
  transposition-invariant chord **quality** id. **Not** a display name — see
  §4 for that, and §5/§6 for why chord *quality* alone isn't enough to
  align on.
- `chords[].root_pc` — absolute root pitch class 0–11 of that specific
  segment (**not** transposition-invariant). **Deliberate addition beyond
  the original field list** (`start, end, canonical_chord_id`): Phase 3A's
  own validation found quality-only alignment (`canonical_chord_id` alone)
  surfaces **no significant matches at all** — root motion (which chord
  follows which, not just which quality follows which) is most of what
  makes two progressions "the same" (see `lib/chord_similarity.py`'s
  "Root-aware scoring" section). `docs/js/alignment.js`'s chord search needs
  `root_pc` to reuse the scoring approach that actually validated; leaving
  it out of `sequences.json` would just mean re-deriving it from somewhere
  else. Kept as a 5th int per segment — a small size cost (~1–2 bytes/int
  after gzip) for something the matching engine cannot work correctly
  without.
- `notes[].interval_from_prev` — signed semitone interval from the previous
  note in the song (`pitch[i] - pitch[i-1]`); `null` for each song's first
  note (no previous note to diff against — this is the JSON encoding of
  `output/analysis/note_sequences.csv`'s empty-string convention for the
  same case). **Filter out `null` entries before feeding a sequence into
  the alignment engine** — `searchNoteQueryAgainstCorpus()` in
  `alignment.js` already does this for corpus-side sequences; do the same
  to any user-selected query sequence before calling it.
- Array order is the original onset/segment order (already sorted on
  export) — array index is *not* a stable id across songs, only within one
  song's own array.

---

## 3. `songs/<song_id>.json` (× 75)

Full per-song detail — **lazy-load this only when a song is actually
opened**, not upfront. ~9.4 MB total across all 75 files (~126 KB/song
average; ranges with song length).

```jsonc
{
  "song_id": "163braces-lyrics-music-video",
  "title": "163braces - 過期 (lyrics music video)",
  "duration": 233.143,
  "audio_url": "audio/163braces-lyrics-music-video.mp3",
  "notes": [
    { "onset": 0.2795, "offset": 0.6841, "pitch": 44, "velocity": 61 },
    ...
  ],
  "chords": [
    { "start": 0.2322, "end": 0.9288, "canonical_chord_id": "4-27B", "chord_name": "G# dominant7" },
    ...
  ]
}
```

- `notes` — full note-event list (`onset, offset, pitch, velocity`, seconds
  / MIDI pitch 0–127 / MIDI velocity 0–127), straight from
  `output/note_events_other.csv` for this song, sorted by onset. `other`
  stem only (bass/drums/vocals are out of scope — see `PIPELINE_MAP.md`;
  same caveat as every Phase 3A/3B deliverable). Pitch reflects
  Chordino-guided auto-correction where it ran — see
  `output/note_events_other.csv`'s own `was_corrected` column if you ever
  need that detail; it was **not** carried into this export (not needed for
  playback/display).
- `chords` — full chord-segment list including `chord_name`, a clean
  human-readable display string (`"<root note letter> <quality>"`, e.g.
  `"C major"`, `"G minor7"`) — see §4 for exactly how this was derived and
  its coverage. `canonical_chord_id` is repeated here (same value as
  `sequences.json`) for convenience so a consumer of this file alone
  doesn't need to cross-reference `sequences.json` just to get the raw id.
- `audio_url` — relative to the site root as `docs/` (see the assumption at
  the top of this doc); points at §5's mp3.

---

## 4. Chord-naming coverage (the brief's "verify before finalizing" check)

**Result: 100% clean, standard display names — 0 fallback labels reach the
frontend**, across all 8,342 chord segments in the corpus.

The brief flagged `chord_vocabulary.csv`'s `common_name` (music21's
`Chord.commonName`) as a "stretch goal, not a guarantee" and asked to check
coverage before finalizing. Two things came out of that check:

1. **The real canonical vocabulary is 9 chord qualities, not ~100.** The
   ~100 rows in `chord_vocabulary.csv` are pre-canonicalization, one row
   per distinct raw pitch-class SET (i.e. one row per transposition of the
   same quality). After Phase 3A's transposition-invariant canonicalization
   to `forte_class` — the actual id used everywhere downstream, including
   here — the corpus's 8,342 chord segments collapse to exactly **9**
   distinct `canonical_chord_id` values.
2. Of those 9, **music21's `commonName` was internally inconsistent for 4
   of them** — the identical chord quality (same `forte_class`) got a clean
   name for some voicings/pitch-spellings and an `"enharmonic equivalent to
   X"` / `"enharmonic to X"` prefixed variant of the *same* name for others
   (e.g. `4-26` → `"minor seventh chord"` for 1,290 segments but
   `"enharmonic equivalent to minor seventh chord"` for the other 360 — a
   music21 spelling quirk, not a musical difference). Trusting
   `common_name` as-is per-segment would make the same chord display two
   different ways depending on which specific segment you're looking at.
3. **One quality had no good name at all**: `4-22A`, music21's
   `"major-second major tetrachord"` (1.3% of segments) — technically
   accurate but not a name anyone would recognize as a chord. Its pitch
   classes, re-rooted to 0, are `{0, 2, 4, 7}`: a major triad `{0,4,7}` plus
   an added 9th `{2}` — i.e. an **add9** chord, the standard pop/jazz name
   for that shape.

**Fix:** `scripts/webexport/chord_display_names.py` — a 9-entry curated
table keyed by `forte_class` (not by raw pitch-class-set), used to build
every `chord_name` in `songs/<id>.json`:

| `canonical_chord_id` | share of segments | `chord_name` quality |
|---|---|---|
| `3-11B` | 25.3% | major |
| `4-26`  | 19.8% | minor7 |
| `3-11A` | 18.0% | minor |
| `4-20`  | 14.6% | major7 |
| `4-27B` | 11.7% | dominant7 |
| `4-27A` | 5.5%  | half-diminished7 |
| `3-12`  | 2.8%  | augmented |
| `4-22A` | 1.3%  | add9 |
| `3-10`  | 1.0%  | diminished |

`chord_name = "<note letter for root_pc> <quality>"` (e.g. root_pc=8,
`4-27B` → `"G# dominant7"`). Verified 0 occurrences of the fallback form
(`"<root> [<forte_class>]"`, only used if a `forte_class` outside this
9-entry table were ever encountered) across all 8,342 segments in the
actual export.

---

## 5. Audio (`docs/audio/<song_id>.mp3` × 75)

Re-encoded from `audio/`/`k-indie/` (75 unique sources, 23 filenames
present in both dirs as confirmed byte-duplicates — deduped) via
`ffmpeg -codec:a libmp3lame -b:a 128k -ar 44100`, stripped of embedded
metadata/artwork. Built by `scripts/webexport/convert_audio.py`.

**Total folder size: 223.5 MB** (75 files). Source material was 377 MB
combined (`audio/` 258 MB + `k-indie/` 119 MB, pre-dedup). Comfortably
within GitHub's practical limits for a Pages-served repo (soft guidance is
to stay well under ~1 GB; GitHub's hard per-file limit is 100 MB and no
single file here is anywhere close) — **128kbps did not need to be lowered
further.**

---

## 6. `docs/js/alignment.js` — client-side matching engine

ES module, no dependencies, no build step. Ported from (same algorithm,
not a re-implementation):

- `lib/sequence_alignment.py` → `needlemanWunsch()`, `smithWaterman()`
- `lib/chord_similarity.py` → `chordSubstitutionScore()`,
  `compoundChordScore()`, `bestTranspositionAlignment()`
- `lib/note_similarity.py` → `intervalSubstitutionScore()`

**One deliberate deviation**, documented in the module's own header comment
too: `chordJaccardSimilarity()` doesn't reconstruct chord pitch content
from a `forte_class` string in JS the way the Python version does via
music21. Since the corpus's canonical chord vocabulary is fixed at exactly
9 qualities (§4), the 9×9 Jaccard similarity matrix was computed **once**
in Python using the real, already-validated `lib/chord_similarity.py` and
is shipped as a static lookup table — same numbers, not new logic, and it
avoids porting music21's chord theory into JS for zero behavioral benefit.

### Validated against the Python originals

Per the brief's own guardrail ("a silent porting bug here would surface as
'wrong similar songs' in the UI — catch it now, not later"): the same 9
hand-picked pairs from `scripts/analysis/validate_alignment_sample.py` /
`validate_note_alignment_sample.py` (3 reference×reference, 4 same-artist,
2 negative-control) were run through both the real Python modules (at the
production gap penalty, `-2.0`, matching
`scripts/analysis/run_alignment.py` / `run_note_alignment.py`) and the JS
port, comparing score, best-transposition, and alignment span.

**Result: all 9 pairs × (chord + note) = 18 checks match exactly**
(score difference `< 1e-6`, transposition and span identical). Reproduce:

```
uv run python -m scripts.webexport.dump_validation_cases
node scripts/webexport/run_js_validation.mjs
```

### API

```js
import {
  needlemanWunsch, smithWaterman,                          // generic DP (§ lib/sequence_alignment.py)
  chordSubstitutionScore, compoundChordScore,
  bestTranspositionAlignment,                               // chord scoring (§ lib/chord_similarity.py)
  intervalSubstitutionScore,                                // note scoring (§ lib/note_similarity.py)
  searchChordQueryAgainstCorpus, searchNoteQueryAgainstCorpus, // Phase 4: query-vs-corpus search
} from './alignment.js';
```

**`searchChordQueryAgainstCorpus(querySegments, sequencesById, opts?)`**
`querySegments`: `[{canonical_chord_id, root_pc}, ...]` — pull this
straight out of a `sequences.json[song_id].chords` slice for a
user-selected time range (or an on-the-fly detection, if Phase 5 ever adds
one). `sequencesById`: pass the full parsed `sequences.json`. `opts`:
`{ excludeSongId, gapPenalty = -2.0 }`. Returns one ranked entry per corpus
song (score descending):
`{ song_id, score, transposition, span: [startIdx, endIdxExclusive] }`
— `span` indexes into that song's own `chords` array in `sequences.json`
(use it to slice out the matched segments, then look up display names in
that song's `songs/<id>.json` if needed).

**`searchNoteQueryAgainstCorpus(queryIntervals, sequencesById, opts?)`**
`queryIntervals`: `number[]` of signed semitone intervals (already filtered
of `null`s). Same `opts`/return shape as above minus `transposition`
(note intervals are transposition-invariant already, no search needed).

Both are **query-vs-corpus, not the full 75×75 matrix** — the query side is
always short (a handful of chords / up to a few dozen note intervals), so
this is one Smith-Waterman pass (×12 transpositions for chords) per corpus
song, not a precomputed matrix. Measured on a 5-chord / 20-interval query
against the full 74-song corpus in Node: **chord search ~33 ms, note search
~53 ms** — safe to run synchronously on a user-triggered "find matches"
action; not fast enough to run on every keystroke/drag-frame of a selection
UI without debouncing.

**Chord and note results are two independent, unmerged ranked lists** — per
the brief, don't combine them into one relevance-ranked list; they're
different signals (harmonic-progression similarity vs. melodic-contour
similarity) and the UI should label them separately.

Reasonable default gap penalty is baked in (`-2.0`, both modes) — matches
the corpus-wide production runs from Phase 3A/3B, not the validation
scripts' own sweep candidates. Override via `opts.gapPenalty` if Phase 5
wants to expose that as a tunable.
