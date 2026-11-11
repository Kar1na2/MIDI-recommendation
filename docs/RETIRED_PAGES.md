# Retired Bepop pages

The public site was consolidated into the song-layer homepage. These earlier
pages are retired from the public navigation and published site; their source
and historical material remain available in the repository history.

- **Original search and comparison homepage (`docs/index.html`)**: searched the
  earlier 75-song corpus, displayed audio waveforms, and compared selected
  chord and note passages. Its UI and alignment logic are in `docs/js/app.js`,
  `docs/js/alignment.js`, and `docs/css/styles.css`; the earlier export format
  and assets are documented in `docs/data/DATA_LAYER.md` and the previous
  `docs/README.md` in Git history. The replacement homepage uses `frontend/` and the MVP data.
- **Instrumental pattern explorer (`docs/layers.html`)**: provided a main disc
  and bass, drums, and other discs, with segmented audio previews. Its UI is in
  `docs/js/instrumentals.js` and `docs/css/instrumentals.css`. Its exported
  analysis is in `docs/data/instrumentals/`, generated from the catalog and
  pipeline under `instrumentals/` and the former `scripts/instrumentals/`
  (the latter is available in Git history). Media hosting configuration and
  upload records are in `instrumental-audio-host/`.
- **Instrumental preview (`docs/instrumental-preview.html`)**: an earlier
  version of the same four-disc explorer, using the same CSS, JavaScript and
  instrumental export as the page above.
- **Classical listening lab (`docs/experiment.html`)**: compared three rising
  figures in 1nonly's “Meaningless Love” with 15 classical score excerpts,
  with MIDI views, synthesized playback and the screening method. Its UI is in
  `docs/js/experiment.js` and `docs/css/experiment.css`; its exported source
  and screening report are `docs/data/experiment/arpeggio_clips.json` and
  `docs/data/experiment/CLASSICAL_REPORT.md`. Historical analysis and source
  preparation are under `output/analysis/arpeggio_experiment/` and the former
  `scripts/webexport/build_arpeggio_experiment_page_data.py` in Git history.

The current homepage and local review instructions are in `frontend/`; its
song analysis is sourced read-only from `mvp/`, `mvp_325/`, and `outputs/`.
Historical files that are absent from a fresh checkout can be recovered from
the Pages branch history. The retired page documentation does not imply that
their analysis or media files were deleted.
