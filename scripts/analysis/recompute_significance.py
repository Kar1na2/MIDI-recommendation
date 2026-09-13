#!/usr/bin/env python3
"""
Phase 3A step 4, correction pass: replaces the permutation-test z-score in
output/analysis/chord_similarity_matrix.csv with a corpus-relative
("row-normalized") z-score, after the permutation approach was found to be
badly miscalibrated on the full corpus - see CHORD_ANALYSIS_DESIGN.md
section 4c/4d for the full story. Short version:

The validation-checkpoint sample (9 hand-picked pairs) looked fine with the
within-pair shuffle-permutation null, but running it on the full 2,775-pair
matrix exposed a systematic bias: mean z was +1.70 (should be ~0 for a
correctly calibrated null) and 9.7% of ALL pairs cleared z>=4 (should be
~0.003%). Diagnosis: shuffling a song's own segment order destroys that
song's own internal repetition (many songs in this corpus are short vamped
loops - see section 2's repeat-run finding), making the null systematically
WEAKER than reality and inflating z for nearly every pair, not just truly
similar ones. A circular-shift null (preserves internal order, just changes
the alignment phase) was tried as a fix and made the opposite mistake: it
preserves periodic internal structure so well that a shifted copy of a
looped song aligns almost as well as the real one, which destroyed the
signal entirely (the known-true-positive Kanye West Heartless/Stronger pair
dropped from z=9.81 to z=0.80).

**Fix that was kept:** treat the ~74 real alignment scores every song
already has (against every other song in the corpus, all already computed)
as that song's own empirical background distribution - "how well does this
song generically align with a typical other song in this corpus" - and
score a pair relative to BOTH songs' own backgrounds (row-normalized,
excluding the pair itself from its own background). This sidesteps both
permutation confounds because the background is built from other REAL
songs (which have realistic internal structure and periodicity already),
not synthetic shuffles/rotations of one song. It also requires no new
alignments - it's a pure post-process over already-computed
`normalized_score` values.

    corpus_z_a  = (normalized_score - mean(A's scores vs all others)) / std(...)
    corpus_z_b  = (normalized_score - mean(B's scores vs all others)) / std(...)
    corpus_z    = mean(corpus_z_a, corpus_z_b)

Reads/writes output/analysis/chord_similarity_matrix.csv in place (adds
corpus_z_a/corpus_z_b/corpus_z columns, renames the old permutation columns
with a _deprecated suffix rather than deleting them, so the record of what
was tried and rejected stays inspectable).

Usage:
    python -m scripts.analysis.recompute_significance
"""
import csv
import statistics
from collections import defaultdict
from pathlib import Path

MATRIX_CSV = Path("output/analysis/chord_similarity_matrix.csv")


def main():
    rows = list(csv.DictReader(MATRIX_CSV.open(newline="", encoding="utf-8")))

    by_song_scores = defaultdict(list)  # song -> [(partner, normalized_score), ...]
    for r in rows:
        ns = float(r["normalized_score"])
        by_song_scores[r["song_a"]].append((r["song_b"], ns))
        by_song_scores[r["song_b"]].append((r["song_a"], ns))

    def row_z(song, partner, score):
        vals = [s for p, s in by_song_scores[song] if p != partner]
        m = statistics.mean(vals)
        sd = statistics.pstdev(vals) or 1e-9
        return (score - m) / sd

    out_rows = []
    for r in rows:
        ns = float(r["normalized_score"])
        za = row_z(r["song_a"], r["song_b"], ns)
        zb = row_z(r["song_b"], r["song_a"], ns)
        new_row = dict(r)
        new_row["perm_null_mean_deprecated"] = new_row.pop("null_mean")
        new_row["perm_null_sd_deprecated"] = new_row.pop("null_sd")
        new_row["perm_z_score_deprecated"] = new_row.pop("z_score")
        new_row["corpus_z_a"] = round(za, 3)
        new_row["corpus_z_b"] = round(zb, 3)
        new_row["corpus_z"] = round((za + zb) / 2, 3)
        out_rows.append(new_row)

    fieldnames = [
        "song_a", "song_b", "len_a", "len_b", "best_transposition",
        "raw_score", "normalized_score",
        "perm_null_mean_deprecated", "perm_null_sd_deprecated", "perm_z_score_deprecated",
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
    for t in (2, 3, 4, 5):
        n = sum(1 for z in zs if z >= t)
        print(f"  corpus_z >= {t}: {n} pairs ({100*n/len(zs):.2f}%)")


if __name__ == "__main__":
    main()
