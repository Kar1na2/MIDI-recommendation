# bepop — published song-layer homepage

GitHub Pages serves this site from the `pages` branch's `docs/` directory. The
homepage presents the 300-song MVP plus 23 eligible additions. Select a song and
choose a segment to see vocal sections, rhythmic-section summaries and bar
comparison details. Playback uses a full-song timeline.

The source for the homepage is `frontend/`; `frontend/export_site.mjs` creates
a static export from the read-only datasets `mvp/`, `mvp_325/` and `outputs/`.
The published `app-data/` files contain only the fields used by the page and
omit lyric text, source paths, and raw audio. No analysis is recalculated.

Original previews with verified matching source hashes are served from the
existing host described in `instrumental-audio-host/README.md`. Tracks not
listed on that host show an explicit unavailable-audio message; see
`app-data/audio-unavailable.json` for the current list. The two additional
songs without sufficient regular pattern data are skipped and explained in
`frontend/UPDATE_REPORT.md`.

See [RETIRED_PAGES.md](RETIRED_PAGES.md) for the content and supporting files
of the previous public pages. Their supporting files remain in the repository
and its history.
