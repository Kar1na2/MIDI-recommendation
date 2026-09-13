# Thin runner for the pipeline scripts under scripts/ and lib/.
#
# Every script is a real Python module now (scripts/<stage>/<name>.py or
# lib/<name>.py), not a standalone file - it has to be invoked as `-m
# <dotted.path>` from the repo root so that `from lib.x import ...` /
# `from scripts.correction.y import ...` resolve. That's what every target
# below does; run `make <target>` from the repo root, same as the old flat
# `uv run <script>.py` commands.
#
# Pass extra CLI flags via ARGS, e.g.:
#     make extract-chords ARGS="--limit 3"
#     make correct-notes ARGS="my_corrections.csv --dry-run"
#
# transcribe-drums is the one exception: it must run under the isolated
# ADTLib environment's own Python 3.7 interpreter, not the main `uv` venv
# (see scripts/transcription/transcribe_drums.py's docstring and the
# README's "Drum transcription" section for why).

.PHONY: separate-stems transcribe-harmonic transcribe-drums merge-stems \
        inspect-basic-pitch-npz \
        extract-chords tag-chord-tones rank-review-candidates inspect-range \
        correct-notes compare-chord-tone-rates \
        compare-corrections summarize-corrections \
        evaluate-transcription select-eval-sample select-anchor-excerpts \
        export-note-events \
        build-chord-vocabulary build-chord-sequences mine-ngrams \
        validate-alignment-sample run-alignment recompute-significance extract-motifs \
        build-note-sequences validate-note-alignment-sample run-note-alignment \
        compute-note-corpus-z extract-note-motifs

# --- separation ---
separate-stems:
	uv run python -m scripts.separation.separate_stems $(ARGS)

# --- transcription ---
transcribe-harmonic:
	uv run python -m scripts.transcription.transcribe_harmonic $(ARGS)

transcribe-drums:
	tools/adtlib-env/bin/python -m scripts.transcription.transcribe_drums $(ARGS)

merge-stems:
	uv run python -m scripts.transcription.merge_stems $(ARGS)

inspect-basic-pitch-npz:
	uv run python -m scripts.transcription.inspect_basic_pitch_npz $(ARGS)

# --- chords (extraction/tagging/ranking) ---
extract-chords:
	uv run python -m scripts.chords.extract_chords $(ARGS)

tag-chord-tones:
	uv run python -m lib.tag_chord_tones $(ARGS)

rank-review-candidates:
	uv run python -m scripts.chords.rank_review_candidates $(ARGS)

inspect-range:
	uv run python -m lib.inspect_range $(ARGS)

# --- correction (manual + Chordino-guided auto-correction + verification) ---
correct-notes:
	uv run python -m scripts.correction.correct_notes $(ARGS)

compare-chord-tone-rates:
	uv run python -m scripts.correction.compare_chord_tone_rates $(ARGS)

compare-corrections:
	uv run python -m scripts.correction.compare_corrections $(ARGS)

summarize-corrections:
	uv run python -m scripts.correction.summarize_corrections $(ARGS)

# --- evaluation ---
evaluate-transcription:
	uv run python -m scripts.evaluation.evaluate_transcription $(ARGS)

select-eval-sample:
	uv run python -m scripts.evaluation.select_eval_sample $(ARGS)

select-anchor-excerpts:
	uv run python -m scripts.evaluation.select_anchor_excerpts $(ARGS)

# --- export ---
export-note-events:
	uv run python -m scripts.export.export_note_events $(ARGS)

# --- analysis (Phase 3A: chord progressions, Phase 3B: note/melodic intervals) ---
# All read-only against output/ and write only into output/analysis/ - see
# output/analysis/CHORD_ANALYSIS_DESIGN.md and NOTE_ANALYSIS_DESIGN.md.
build-chord-vocabulary:
	uv run python -m scripts.analysis.build_chord_vocabulary $(ARGS)

build-chord-sequences:
	uv run python -m scripts.analysis.build_chord_sequences $(ARGS)

mine-ngrams:
	uv run python -m scripts.analysis.mine_ngrams $(ARGS)

validate-alignment-sample:
	uv run python -m scripts.analysis.validate_alignment_sample $(ARGS)

run-alignment:
	uv run python -m scripts.analysis.run_alignment $(ARGS)

recompute-significance:
	uv run python -m scripts.analysis.recompute_significance $(ARGS)

extract-motifs:
	uv run python -m scripts.analysis.extract_motifs $(ARGS)

build-note-sequences:
	uv run python -m scripts.analysis.build_note_sequences $(ARGS)

validate-note-alignment-sample:
	uv run python -m scripts.analysis.validate_note_alignment_sample $(ARGS)

run-note-alignment:
	uv run python -m scripts.analysis.run_note_alignment $(ARGS)

compute-note-corpus-z:
	uv run python -m scripts.analysis.compute_note_corpus_z $(ARGS)

extract-note-motifs:
	uv run python -m scripts.analysis.extract_note_motifs $(ARGS)
