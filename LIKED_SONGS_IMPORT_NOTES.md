# Liked Songs import notes

This note records the repository changes made for the local Spotify Liked
Songs expansion on 2026-10-30.

- Added `spotipy` and `yt-dlp` to the declared environment because the
  archived Spotify importer used an undeclared dependency and the original
  browser scraper was not resumable.
- Updated `archive/fetch_liked_songs.py` to use a separate OAuth cache,
  the Spotify-compatible loopback redirect, atomic writes, and track-ID
  deduplication.
- Replaced `archive/download_songs.py` with a resumable downloader that uses
  stable Spotify-ID filenames, match scoring for search results, retry/failure
  logging, metadata matches against the original corpus, and exact-audio
  deduplication. Its JSON, manifest, credentials, and MP3 output remain local
  and ignored.
- Added `liked-audio/` to the stem-separation input list. It is intentionally
  excluded from the published web-audio flow.
- Added an optional Demucs batch size (`make separate-stems ARGS="--batch-size
  2"`) so the model can be loaded once for a small group of songs; the
  per-song outputs and model configuration remain unchanged.
- Added scalable alignment candidate retrieval. It uses n-gram neighbors and
  deterministic background pairs, then sends those pairs through the existing
  validated chord and melodic Smith–Waterman scorers. This prevents a
  four-digit library from requiring an impractical all-pairs matrix.
- Fixed blank significance fields when shuffle nulls are disabled and added
  optional worker processes to chord and melodic alignment; the final run scored 20,303
  harmonic pairs and 21,116 melodic pairs with zero failures.
- Added `build_analysis_database.py` for a reproducible, ignored SQLite
  staging database and `generate_corpus_report.py` for the checked-in report.
- Removed the refreshed personal Liked Songs JSON from Git tracking. It stays
  available locally for the import and is ignored going forward.

The completed local run fetched 1,128 Spotify records, downloaded 1,053 new
audio files, matched 40 existing corpus files, skipped 17 exact duplicates,
and left 18 tracks unavailable from the search source. It processed 1,128
unique local songs through the pipeline, exporting 1,271,174 notes and
117,112 chord segments. The checked-in report is
`LIKED_SONGS_ANALYSIS_REPORT_2026-10-30.md`; the SQLite database remains
ignored and local.
