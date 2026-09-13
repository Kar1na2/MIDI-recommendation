#!/usr/bin/env python3
"""
Phase 3B significance step: adds a corpus-relative ("row-normalized")
z-score to output/analysis/note_similarity_matrix.csv - the note-domain
analog of Phase 3A's scripts/analysis/recompute_significance.py, but with
an extra correction this domain needed that chords didn't. See
output/analysis/NOTE_ANALYSIS_DESIGN.md section 7 for the full story;
short version below.

**Naive attempt (copy Phase 3A's formula directly) failed a sanity check.**
Phase 3A's `normalized_score = raw_score / (min(len_a, len_b) * MAX_POSITION_SCORE)`
then row-normalized within each song's own background. Doing the same here
produced a corpus_z ranking dominated by one very short song (102 notes)
paired with 8 of the top 15 pairs against completely unrelated
songs/genres/artists - and the validated true positive from the small-sample
checkpoint (Kanye West Heartless x Stronger, permutation z=15.32, a
genuinely striking near-exact 8-interval shared run) came back at
corpus_z=-0.117, indistinguishable from noise. Diagnosed: Phase 3A's chord
segments were all a few hundred per song (a narrow length range); Phase 3B's
note sequences span 103-4,684 (>45x), and Smith-Waterman's expected-maximum
local-alignment score under pure chance grows with BOTH sequence lengths
(more candidate start positions = higher expected max via extreme-value
statistics - the same reason BLAST's E-values are a function of both
sequence lengths, not just the shorter one, per Karlin-Altschul theory).
Normalizing by `min(len_a, len_b)` alone leaves this length effect baked
unevenly into each song's own background, most severely for whichever song
is shortest (it's the "min" in nearly all its own pairs, so its whole row
is uniformly inflated with an artificially tight variance).

**Fix: regress raw_score against BOTH sequence lengths (log scale) across
the full matrix first, and row-normalize the RESIDUALS, not the naive
length-min-normalized score.** A second covariate was added after the
first fix (length-only) still left a real artifact: pairs where BOTH songs
have an unusually repetitive/low-vocabulary interval sequence (measured as
Shannon entropy of each song's own interval multiset) score high almost
regardless of real content, because a narrow interval alphabet makes chance
local matches cheap to find - directly analogous to why BLAST masks
low-complexity regions (SEG/DUST) before alignment. Added `entropy_min`/
`entropy_max` (of the two songs' own interval-sequence entropies) as two
more regression covariates - improved R² from 0.019 (length-only) to 0.209
and brought the z-distribution's calibration close to Phase 3A's own final
numbers (corpus_z>=2: 2.23% here vs Phase 3A's 2.23%; >=3: 0.68% vs 0.43%;
>=4: 0.29% vs 0.07%) - but did NOT fully remove the low-complexity-pair
artifact (a handful of low-entropy song pairs, e.g. Yaeji - Raingurl x
유라 - Rawww, still align across ~99% of the shorter song's full length,
a residual degenerate case where the corpus-wide-average calibration in
lib/note_similarity.py doesn't hold for this specific low-entropy
sub-population). **A full low-complexity masking algorithm was judged out
of scope for this pass** (real added engineering - windowed entropy
masking with a re-derived null, the actual SEG/DUST approach - on top of an
already-large phase); instead this script computes each song's interval
entropy and extract_note_motifs.py surfaces it as an explicit
`a_entropy`/`b_entropy`/`low_complexity_flag` column on every extracted
pair, so a human reviewing the shortlist sees the caveat right on the row
rather than trusting the z-score blindly for exactly the cases where it's
least trustworthy. This is the same judgment-call principle Phase 3A used
for its own top pair ("treat every pair as a lead for a human to judge,
not a confirmed borrowing").

    predicted_raw = OLS(raw_score ~ 1 + ln(min_len) + ln(max_len) + entropy_min + entropy_max)
    residual      = raw_score - predicted_raw
    corpus_z_a    = (residual_AB - mean(A's residuals vs all others)) / std(...)
    corpus_z_b    = (residual_AB - mean(B's residuals vs all others)) / std(...)
    corpus_z      = mean(corpus_z_a, corpus_z_b)

Reads:  output/analysis/note_similarity_matrix.csv (must exist - run
        run_note_alignment.py first), output/analysis/note_sequences.csv
        (for per-song interval entropy)
Writes: output/analysis/note_similarity_matrix.csv in place (adds
        len_regression_residual, corpus_z_a, corpus_z_b, corpus_z columns)

Usage:
    python -m scripts.analysis.compute_note_corpus_z
"""
import csv
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

MATRIX_CSV = Path("output/analysis/note_similarity_matrix.csv")
SEQUENCES_CSV = Path("output/analysis/note_sequences.csv")


def song_entropies():
    by_song = defaultdict(list)
    with SEQUENCES_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["interval_from_prev"] != "":
                by_song[row["song"]].append(int(row["interval_from_prev"]))
    ent = {}
    for song, seq in by_song.items():
        c = Counter(seq)
        n = len(seq)
        ent[song] = -sum((v / n) * math.log2(v / n) for v in c.values())
    return ent


def main():
    rows = list(csv.DictReader(MATRIX_CSV.open(newline="", encoding="utf-8")))
    ent = song_entropies()

    X, y = [], []
    for r in rows:
        la, lb = int(r["len_a"]), int(r["len_b"])
        lo, hi = math.log(min(la, lb)), math.log(max(la, lb))
        ea, eb = ent[r["song_a"]], ent[r["song_b"]]
        X.append([1.0, lo, hi, min(ea, eb), max(ea, eb)])
        y.append(float(r["raw_score"]))
    X = np.array(X)
    y = np.array(y)
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    pred = X @ coef
    resid = y - pred
    r2 = 1 - np.sum(resid**2) / np.sum((y - y.mean()) ** 2)
    print(f"length+entropy regression: coef(intercept, ln_minlen, ln_maxlen, entropy_min, entropy_max)={coef}")
    print(f"R^2={r2:.4f}  resid mean={resid.mean():.4g} std={resid.std():.4f}")

    by_song_resid = defaultdict(list)  # song -> [(partner, residual)]
    for r, e in zip(rows, resid):
        by_song_resid[r["song_a"]].append((r["song_b"], e))
        by_song_resid[r["song_b"]].append((r["song_a"], e))

    def row_z(song, partner, val):
        vals = [v for p, v in by_song_resid[song] if p != partner]
        m = statistics.mean(vals)
        sd = statistics.pstdev(vals) or 1e-9
        return (val - m) / sd

    out_rows = []
    for r, e in zip(rows, resid):
        za = row_z(r["song_a"], r["song_b"], e)
        zb = row_z(r["song_b"], r["song_a"], e)
        new_row = dict(r)
        new_row["len_regression_residual"] = round(float(e), 3)
        new_row["corpus_z_a"] = round(za, 3)
        new_row["corpus_z_b"] = round(zb, 3)
        new_row["corpus_z"] = round((za + zb) / 2, 3)
        out_rows.append(new_row)

    fieldnames = [
        "song_a", "song_b", "len_a", "len_b",
        "raw_score", "normalized_score", "len_regression_residual",
        "corpus_z_a", "corpus_z_b", "corpus_z",
        "span_a_start", "span_a_end", "span_b_start", "span_b_end", "aligned_len",
    ]
    with MATRIX_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(out_rows)

    zs = [r["corpus_z"] for r in out_rows]
    print(f"Rewrote {len(out_rows)} rows with corpus_z.")
    print(f"corpus_z: mean={statistics.mean(zs):.3f} median={statistics.median(zs):.3f} "
          f"min={min(zs):.3f} max={max(zs):.3f}")
    for t in (2, 2.5, 3, 4):
        n = sum(1 for z in zs if z >= t)
        print(f"  corpus_z >= {t}: {n} pairs ({100*n/len(zs):.2f}%)")


if __name__ == "__main__":
    main()
