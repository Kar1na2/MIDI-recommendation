#!/usr/bin/env node
/**
 * Phase 4 JS-port validation, step 2/2: feeds the 9 cases dumped by
 * scripts/webexport/dump_validation_cases.py through the real
 * docs/js/alignment.js and compares scores/spans/transposition against the
 * Python-computed expected values. Exits non-zero on any mismatch.
 *
 * Usage: node scripts/webexport/run_js_validation.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import {
  bestTranspositionAlignment, smithWaterman, intervalSubstitutionScore,
} from '../../docs/js/alignment.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(__dirname, '..', '..');
const casesPath = join(repoRoot, 'scripts/webexport/_validation_cases.json');
const { gap_penalty: gapPenalty, cases } = JSON.parse(readFileSync(casesPath, 'utf-8'));

const SCORE_TOL = 1e-6;
let failures = 0;

function arraysEqual(a, b) {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

console.log(`Running ${cases.length} validation cases at gap_penalty=${gapPenalty}...\n`);

for (const c of cases) {
  console.log(`[${c.label}] ${c.song_a} x ${c.song_b}`);

  // --- chord: bestTranspositionAlignment ---
  const { bestTransposition, result: chordResult } = bestTranspositionAlignment(
    c.chord.seq_a, c.chord.seq_b, gapPenalty,
  );
  const chordScoreOk = Math.abs(chordResult.score - c.chord.expected_score) < SCORE_TOL;
  const chordTOk = bestTransposition === c.chord.expected_best_transposition;
  const chordSpanAOk = arraysEqual(chordResult.spanA, c.chord.expected_span_a);
  const chordSpanBOk = arraysEqual(chordResult.spanB, c.chord.expected_span_b);
  const chordOk = chordScoreOk && chordTOk && chordSpanAOk && chordSpanBOk;
  console.log(
    `  chord: JS=${chordResult.score.toFixed(4)} (t=${bestTransposition}) `
    + `vs PY=${c.chord.expected_score.toFixed(4)} (t=${c.chord.expected_best_transposition}) `
    + (chordOk ? 'OK' : 'MISMATCH <<<<<'),
  );
  if (!chordOk) {
    failures++;
    console.log(`    score_ok=${chordScoreOk} t_ok=${chordTOk} spanA_ok=${chordSpanAOk} spanB_ok=${chordSpanBOk}`);
    console.log(`    JS spanA=${JSON.stringify(chordResult.spanA)} spanB=${JSON.stringify(chordResult.spanB)}`);
    console.log(`    PY spanA=${JSON.stringify(c.chord.expected_span_a)} spanB=${JSON.stringify(c.chord.expected_span_b)}`);
  }

  // --- note: smithWaterman + intervalSubstitutionScore ---
  const noteResult = smithWaterman(c.note.seq_a, c.note.seq_b, intervalSubstitutionScore, gapPenalty);
  const noteScoreOk = Math.abs(noteResult.score - c.note.expected_score) < SCORE_TOL;
  const noteSpanAOk = arraysEqual(noteResult.spanA, c.note.expected_span_a);
  const noteSpanBOk = arraysEqual(noteResult.spanB, c.note.expected_span_b);
  const noteOk = noteScoreOk && noteSpanAOk && noteSpanBOk;
  console.log(
    `  note:  JS=${noteResult.score.toFixed(4)} vs PY=${c.note.expected_score.toFixed(4)} `
    + (noteOk ? 'OK' : 'MISMATCH <<<<<'),
  );
  if (!noteOk) {
    failures++;
    console.log(`    score_ok=${noteScoreOk} spanA_ok=${noteSpanAOk} spanB_ok=${noteSpanBOk}`);
    console.log(`    JS spanA=${JSON.stringify(noteResult.spanA)} spanB=${JSON.stringify(noteResult.spanB)}`);
    console.log(`    PY spanA=${JSON.stringify(c.note.expected_span_a)} spanB=${JSON.stringify(c.note.expected_span_b)}`);
  }
  console.log('');
}

if (failures > 0) {
  console.error(`FAILED: ${failures} mismatch(es) out of ${cases.length * 2} checks.`);
  process.exit(1);
} else {
  console.log(`PASSED: all ${cases.length} cases (chord + note, ${cases.length * 2} checks) match the Python originals exactly.`);
}
