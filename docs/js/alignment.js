/**
 * web/js/alignment.js - client-side sequence-alignment matching engine.
 *
 * Ported from (same algorithm, not a re-implementation - see those files
 * for the full rationale/derivation of every constant below):
 *   - lib/sequence_alignment.py   -> needlemanWunsch() / smithWaterman()
 *   - lib/chord_similarity.py     -> chordSubstitutionScore(), compoundChordScore(),
 *                                     bestTranspositionAlignment()
 *   - lib/note_similarity.py      -> intervalSubstitutionScore()
 *
 * One deliberate deviation from a line-by-line port: chordJaccardSimilarity()
 * does NOT reconstruct chord pitch content from a Forte-class string the way
 * the Python version does via music21 (music21.chord.fromForteClass +
 * transposition-to-root-0 + Jaccard). This corpus's canonicalized chord
 * vocabulary (output/analysis/chord_sequences.csv, all 75 songs) is fixed at
 * exactly 9 forte classes - see web/data/DATA_LAYER.md - so the 9x9 Jaccard
 * matrix is precomputed ONCE in Python (using the already-validated
 * music21-based lib/chord_similarity.py itself) and shipped below as a
 * static table (CHORD_JACCARD_TABLE). This is a table lookup, not new logic
 * - the numbers are byte-identical to what the Python module computes, and
 * it avoids porting music21's chord theory into JS for zero behavioral
 * benefit at a corpus with a fixed, already-enumerated vocabulary. If a
 * chord quality outside this 9-class table is ever encountered (e.g. a
 * future corpus expansion), chordJaccardSimilarity() falls back to 0
 * (full mismatch) rather than throwing - see its own comment.
 *
 * Validated against the Python originals on the same 9 hand-picked pairs
 * used in scripts/analysis/validate_alignment_sample.py and
 * validate_note_alignment_sample.py - see
 * scripts/webexport/validate_js_port.py + its logged output, and the note
 * in web/data/DATA_LAYER.md.
 */
'use strict';

// ============================================================================
// 1. Generic Needleman-Wunsch / Smith-Waterman (port of lib/sequence_alignment.py)
// ============================================================================

export const GAP = null;

/**
 * @typedef {Object} AlignmentResult
 * @property {number} score
 * @property {Array} alignedA
 * @property {Array} alignedB
 * @property {Array<[number|null, number|null]>} indexPairs
 * @property {[number, number]|null} spanA - Smith-Waterman only
 * @property {[number, number]|null} spanB - Smith-Waterman only
 */

function traceback(seqA, seqB, moves, i, j, { stopAtZeroScore = false, H = null } = {}) {
  const alignedA = [], alignedB = [], indexPairs = [];
  for (;;) {
    if (stopAtZeroScore && H[i][j] === 0) break;
    const move = moves[i][j];
    if (move === 'D') {
      alignedA.push(seqA[i - 1]); alignedB.push(seqB[j - 1]);
      indexPairs.push([i - 1, j - 1]);
      i -= 1; j -= 1;
    } else if (move === 'U') {
      alignedA.push(seqA[i - 1]); alignedB.push(GAP);
      indexPairs.push([i - 1, null]);
      i -= 1;
    } else if (move === 'L') {
      alignedA.push(GAP); alignedB.push(seqB[j - 1]);
      indexPairs.push([null, j - 1]);
      j -= 1;
    } else {
      break; // 'S' (Smith-Waterman local-alignment start) or i===0 && j===0
    }
  }
  alignedA.reverse(); alignedB.reverse(); indexPairs.reverse();
  return { alignedA, alignedB, indexPairs, i, j };
}

/**
 * Global alignment: every symbol in both sequences is placed in the output
 * (possibly opposite a gap). scoreFn(a, b) -> number; gapPenalty should be
 * <= 0, applied once per gapped position.
 */
export function needlemanWunsch(seqA, seqB, scoreFn, gapPenalty) {
  const n = seqA.length, m = seqB.length;
  const H = Array.from({ length: n + 1 }, () => new Float64Array(m + 1));
  const moves = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(null));

  for (let i = 1; i <= n; i++) { H[i][0] = H[i - 1][0] + gapPenalty; moves[i][0] = 'U'; }
  for (let j = 1; j <= m; j++) { H[0][j] = H[0][j - 1] + gapPenalty; moves[0][j] = 'L'; }

  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      const diag = H[i - 1][j - 1] + scoreFn(seqA[i - 1], seqB[j - 1]);
      const up = H[i - 1][j] + gapPenalty;
      const left = H[i][j - 1] + gapPenalty;
      const best = Math.max(diag, up, left);
      H[i][j] = best;
      moves[i][j] = best === diag ? 'D' : (best === up ? 'U' : 'L');
    }
  }

  const { alignedA, alignedB, indexPairs } = traceback(seqA, seqB, moves, n, m);
  return { score: H[n][m], alignedA, alignedB, indexPairs, spanA: null, spanB: null };
}

/**
 * Local alignment: finds the single highest-scoring contiguous subsequence
 * pair, ignoring unrelated flanking material on both sides.
 */
export function smithWaterman(seqA, seqB, scoreFn, gapPenalty) {
  const n = seqA.length, m = seqB.length;
  const H = Array.from({ length: n + 1 }, () => new Float64Array(m + 1));
  const moves = Array.from({ length: n + 1 }, () => new Array(m + 1).fill('S'));

  let bestScore = 0.0, bestI = 0, bestJ = 0;
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      const diag = H[i - 1][j - 1] + scoreFn(seqA[i - 1], seqB[j - 1]);
      const up = H[i - 1][j] + gapPenalty;
      const left = H[i][j - 1] + gapPenalty;
      const best = Math.max(0.0, diag, up, left);
      H[i][j] = best;
      if (best === 0.0) moves[i][j] = 'S';
      else if (best === diag) moves[i][j] = 'D';
      else if (best === up) moves[i][j] = 'U';
      else moves[i][j] = 'L';
      if (best > bestScore) { bestScore = best; bestI = i; bestJ = j; }
    }
  }

  const { alignedA, alignedB, indexPairs, i: startI, j: startJ } =
    traceback(seqA, seqB, moves, bestI, bestJ, { stopAtZeroScore: true, H });
  return {
    score: bestScore, alignedA, alignedB, indexPairs,
    spanA: [startI, bestI], spanB: [startJ, bestJ],
  };
}

// ============================================================================
// 2. Chord scoring (port of lib/chord_similarity.py)
// ============================================================================

// Precomputed once via Python (lib.chord_similarity.chord_jaccard_similarity,
// itself already validated in Phase 3A) over the corpus's actual 9-forte-class
// vocabulary - see module docstring above for why this is a table, not a port.
const CHORD_FORTE_CLASSES = ["3-10", "3-11A", "3-11B", "3-12", "4-20", "4-22A", "4-26", "4-27A", "4-27B"];
const CHORD_JACCARD_TABLE = {
  "3-10":  { "3-10": 1.0, "3-11A": 0.5, "3-11B": 0.2, "3-12": 0.2, "4-20": 0.16666666666666666, "4-22A": 0.16666666666666666, "4-26": 0.4, "4-27A": 0.75, "4-27B": 0.16666666666666666 },
  "3-11A": { "3-10": 0.5, "3-11A": 1.0, "3-11B": 0.5, "3-12": 0.2, "4-20": 0.4, "4-22A": 0.4, "4-26": 0.75, "4-27A": 0.4, "4-27B": 0.4 },
  "3-11B": { "3-10": 0.2, "3-11A": 0.5, "3-11B": 1.0, "3-12": 0.5, "4-20": 0.75, "4-22A": 0.75, "4-26": 0.4, "4-27A": 0.16666666666666666, "4-27B": 0.75 },
  "3-12":  { "3-10": 0.2, "3-11A": 0.2, "3-11B": 0.5, "3-12": 1.0, "4-20": 0.4, "4-22A": 0.4, "4-26": 0.16666666666666666, "4-27A": 0.16666666666666666, "4-27B": 0.4 },
  "4-20":  { "3-10": 0.16666666666666666, "3-11A": 0.4, "3-11B": 0.75, "3-12": 0.4, "4-20": 1.0, "4-22A": 0.6, "4-26": 0.3333333333333333, "4-27A": 0.14285714285714285, "4-27B": 0.6 },
  "4-22A": { "3-10": 0.16666666666666666, "3-11A": 0.4, "3-11B": 0.75, "3-12": 0.4, "4-20": 0.6, "4-22A": 1.0, "4-26": 0.3333333333333333, "4-27A": 0.14285714285714285, "4-27B": 0.6 },
  "4-26":  { "3-10": 0.4, "3-11A": 0.75, "3-11B": 0.4, "3-12": 0.16666666666666666, "4-20": 0.3333333333333333, "4-22A": 0.3333333333333333, "4-26": 1.0, "4-27A": 0.6, "4-27B": 0.6 },
  "4-27A": { "3-10": 0.75, "3-11A": 0.4, "3-11B": 0.16666666666666666, "3-12": 0.16666666666666666, "4-20": 0.14285714285714285, "4-22A": 0.14285714285714285, "4-26": 0.6, "4-27A": 1.0, "4-27B": 0.3333333333333333 },
  "4-27B": { "3-10": 0.16666666666666666, "3-11A": 0.4, "3-11B": 0.75, "3-12": 0.4, "4-20": 0.6, "4-22A": 0.6, "4-26": 0.6, "4-27A": 0.3333333333333333, "4-27B": 1.0 },
};

export const CHORD_MATCH_SCORE = 2.0;
export const CHORD_CORPUS_MEAN_JACCARD = 0.588; // measured corpus-wide, see lib/chord_similarity.py
export const CHORD_TARGET_RANDOM_SCORE = -0.25;
export const CHORD_SLOPE = (CHORD_MATCH_SCORE - CHORD_TARGET_RANDOM_SCORE) / (1 - CHORD_CORPUS_MEAN_JACCARD);
export const CHORD_OFFSET = CHORD_SLOPE - CHORD_MATCH_SCORE;

/** Same-root pitch-class overlap in [0, 1]; table lookup (see module docstring). */
export function chordJaccardSimilarity(forteA, forteB) {
  const row = CHORD_JACCARD_TABLE[forteA];
  if (!row || !(forteB in row)) return 0.0; // out-of-corpus forte class: full mismatch, not a crash
  return row[forteB];
}

export function chordSubstitutionScore(forteA, forteB) {
  const sim = chordJaccardSimilarity(forteA, forteB);
  return CHORD_SLOPE * sim - CHORD_OFFSET;
}

export const CHORD_ROOT_BONUS = 3.0;
const CHORD_ROOT_BIAS_CORRECTION = CHORD_ROOT_BONUS * 0.0856 + 0.15; // see lib/chord_similarity.py

/** score_fn over {forte, root} objects - use with bestTranspositionAlignment(). */
export function compoundChordScore(symA, symB) {
  let score = chordSubstitutionScore(symA.forte, symB.forte) - CHORD_ROOT_BIAS_CORRECTION;
  if (symA.root === symB.root) score += CHORD_ROOT_BONUS;
  return score;
}

/**
 * Tries all 12 rigid transpositions of seqB's root against seqA (seqA fixed)
 * and returns the highest-scoring alignment - the "key-invariant" entry
 * point chord matching should use. method: 'smithWaterman' (default) or
 * 'needlemanWunsch'.
 */
export function bestTranspositionAlignment(seqA, seqB, gapPenalty, method = 'smithWaterman') {
  const alignFn = method === 'smithWaterman' ? smithWaterman : needlemanWunsch;
  let bestT = null, bestResult = null;
  for (let t = 0; t < 12; t++) {
    const shiftedB = seqB.map((s) => ({ forte: s.forte, root: (s.root + t) % 12 }));
    const result = alignFn(seqA, shiftedB, compoundChordScore, gapPenalty);
    if (bestResult === null || result.score > bestResult.score) { bestT = t; bestResult = result; }
  }
  return { bestTransposition: bestT, result: bestResult };
}

// ============================================================================
// 3. Note-interval scoring (port of lib/note_similarity.py)
// ============================================================================

export const NOTE_MATCH_SCORE = 2.0;
export const NOTE_WINDOW = 6; // semitones (a tritone) - sim decays to 0 at this distance
export const NOTE_CORPUS_MEAN_SIMILARITY = 0.126; // measured corpus-wide, see lib/note_similarity.py
export const NOTE_TARGET_RANDOM_SCORE = -0.5;
export const NOTE_SLOPE = (NOTE_MATCH_SCORE - NOTE_TARGET_RANDOM_SCORE) / (1 - NOTE_CORPUS_MEAN_SIMILARITY);
export const NOTE_OFFSET = NOTE_SLOPE - NOTE_MATCH_SCORE;

/** Triangular-decay similarity in [0, 1]; 1.0 iff a===b, 0.0 once |a-b| >= NOTE_WINDOW. */
export function intervalSimilarity(a, b) {
  const d = Math.abs(a - b);
  if (d >= NOTE_WINDOW) return 0.0;
  return 1 - d / NOTE_WINDOW;
}

export function intervalSubstitutionScore(a, b) {
  const sim = intervalSimilarity(a, b);
  return NOTE_SLOPE * sim - NOTE_OFFSET;
}

// ============================================================================
// 4. Query-vs-corpus search (Phase 4: the actual gap this module closes -
//    live matching of an arbitrary user selection against the rest of the
//    corpus, which the precomputed Phase 3A/3B whole-song/fixed-window
//    matrices can't answer). Chord and note results are kept as two
//    independent, unmerged result sets per the brief.
// ============================================================================

const DEFAULT_GAP_PENALTY = -2.0; // matches scripts/analysis/run_alignment.py / run_note_alignment.py's production value

/**
 * @param {Array<{canonical_chord_id: string, root_pc: number}>} querySegments
 * @param {Object<string, {chords: Array, notes: Array}>} sequencesById - full sequences.json
 * @param {Object} [opts]
 * @param {string|null} [opts.excludeSongId] - typically the song the query was drawn from
 * @param {number} [opts.gapPenalty]
 * @returns {Array<{song_id: string, score: number, transposition: number, span: [number, number]}>}
 *   sorted by score descending, one entry per corpus song (excluding excludeSongId)
 */
export function searchChordQueryAgainstCorpus(querySegments, sequencesById, opts = {}) {
  const gapPenalty = opts.gapPenalty ?? DEFAULT_GAP_PENALTY;
  const query = querySegments.map((s) => ({ forte: s.canonical_chord_id, root: s.root_pc }));
  const results = [];
  for (const [songId, seq] of Object.entries(sequencesById)) {
    if (songId === opts.excludeSongId) continue;
    const target = seq.chords.map((c) => ({ forte: c.canonical_chord_id, root: c.root_pc }));
    if (target.length === 0) continue;
    const { bestTransposition, result } = bestTranspositionAlignment(query, target, gapPenalty);
    results.push({
      song_id: songId, score: result.score, transposition: bestTransposition,
      span: result.spanB, // [start, end) into the target song's chord array
      // Full pairwise trace (point 11's in-depth comparison view) - this is
      // NOT new computation, just exposing what smithWaterman()'s own
      // traceback() already builds internally to reconstruct its
      // best-scoring path before returning a score. alignedTarget's
      // {forte, root} values are the TRANSPOSED (bestTransposition-shifted)
      // target symbols actually compared against the query during scoring
      // - i.e. shown "in the query's key" so matching blocks are visually
      // identical - not the target song's real untransposed chords (those
      // are already shown elsewhere, e.g. the point-6 bullets).
      alignedQuery: result.alignedA,
      alignedTarget: result.alignedB,
    });
  }
  results.sort((a, b) => b.score - a.score);
  return results;
}

/**
 * @param {Array<number>} queryIntervals - signed semitone intervals
 * @param {Object<string, {chords: Array, notes: Array}>} sequencesById - full sequences.json
 * @param {Object} [opts]
 * @returns {Array<{song_id: string, score: number, span: [number, number]}>} sorted by score descending
 */
export function searchNoteQueryAgainstCorpus(queryIntervals, sequencesById, opts = {}) {
  const gapPenalty = opts.gapPenalty ?? DEFAULT_GAP_PENALTY;
  const results = [];
  for (const [songId, seq] of Object.entries(sequencesById)) {
    if (songId === opts.excludeSongId) continue;
    // note_sequences.json's first note per song has interval_from_prev===null
    // (no previous note) - drop it, same as the Python loaders do (they only
    // append rows where interval_from_prev != "").
    const target = seq.notes.map((n) => n.interval_from_prev).filter((v) => v !== null);
    if (target.length === 0) continue;
    const result = smithWaterman(queryIntervals, target, intervalSubstitutionScore, gapPenalty);
    // alignedQuery/alignedTarget: see searchChordQueryAgainstCorpus's comment
    // above - same thing, already-computed traceback, no new computation.
    // No transposition step for intervals (they're transposition-invariant
    // by construction), so alignedTarget's values are real semitone
    // intervals as-is, not shifted.
    results.push({
      song_id: songId, score: result.score, span: result.spanB,
      alignedQuery: result.alignedA, alignedTarget: result.alignedB,
    });
  }
  results.sort((a, b) => b.score - a.score);
  return results;
}
