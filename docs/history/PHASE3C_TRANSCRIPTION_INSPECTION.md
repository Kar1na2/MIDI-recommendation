# Phase 3C transcription inspection: Silo — Meaningful Love

**Focus:** 6.000–18.000 seconds on the original recording clock  
**Diagnostic:** `output/midi_pipeline/phase3/meaningful-love-v1/report.html`  
**Backend:** YourMT3+; this phase repairs inspection only and does not implement Phase 4.

## Playback defect and repair

The previous Phase 3 HTML used a bare relative `reference.wav` URL and Phase 3 had no dedicated local web server, audio route, byte-range handler, or server launch command. If that HTML was hosted outside its artifact directory, the relative asset did not resolve to the WAV; if opened as a local file, browser/app file-URL policy determined whether it could load. No Phase 3 listener was present on the inspection ports when audited. The precise browser console/network record from the original `0:00 / 0:00` screenshot was not available, so the report does not claim a retrospective HTTP status for that exact viewing session. The reproducibility defect was the unserved, context-dependent relative asset.

Phase 3C serves the report and only named inspection artifacts from a loopback-only HTTP server at `http://127.0.0.1:8768/`. The reference player URL is `/reference.wav`; the route sends `audio/wav`, content length, byte-range support (`206`/`Content-Range`), and a health record at `/api/health`. Browser playback errors remain visible; the player never substitutes another audio file.

The reference file is 30.000 seconds, 48000 Hz, 2 channels, WAV/PCM_16; SHA-256 `c496918598787218577b11fbb852f6c5886d27dae0818a5a447df2ed729f085d`.

## What the raw MIDI contains

The build loaded each existing Phase 3 run through the strict current-run/hash reader, reparsed its unmodified `raw.mid`, and compared every event to canonical JSON by track, pitch, onset, offset, velocity, program and percussion flag. Both files had positive event durations and source-clock offset zero. No note was dropped by this inspection. The run log separately contains decoder token errors, which occurred before a note event could be exported and are listed below; they are not a count of missing ground-truth notes.

A second parser read the raw MIDI messages and tick clock directly. It checked explicit program-change messages, paired every note-on/note-off, applied the file's tempo map, and compared the resulting pitches, programs and seconds with the parsed events. Both files have 206/206 and 54/54 note-on/off pairs respectively, no dangling notes, explicit program changes for every melodic/drum track, 480 ticks per beat, and no tempo meta-events. The writer uses its 500,000 μs/beat (120 BPM) default. Each file has one empty conductor track plus 6 or 4 note tracks. No tempo-dependent duration is silently estimated from a screenshot.

## Audio preprocessing and source timing

The reference mix is a verified 30-second, 48 kHz, stereo PCM16 crop from original source time 0.000 seconds. YourMT3+ converts it to mono and uses polyphase resampling from 48 kHz to its 16 kHz model rate. The separate Other-stem crop is 30 seconds, 22.05 kHz, mono PCM16 and is likewise converted to 16 kHz. Both crops begin at original recording time 0.000 seconds. The canonical event writer adds that zero-second offset once. The local HTML plays the unresampled 48 kHz reference and the 24 kHz mono reconstructions.

### YourMT3+ Mix

- Input: `reference.wav`; **Original mix**; 48000 Hz, 30 seconds; source SHA-256 `c496918598787218577b11fbb852f6c5886d27dae0818a5a447df2ed729f085d`.
- Backend/checkpoint: `yourmt3-plus` / `mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops@last.ckpt` (SHA-256 `ae38e415c79efd5592dcb9b658cdb99ddb11d4c4e1eaa364cab04a052473fc25`). Device: prediction_available_unvalidated run, CUDA runtime 19.06 s.
- Raw MIDI: 6 instrument tracks, 206 notes. Events start from 0.030 s; latest note-off is 29.980 s.
- Focus 6–18 s: 89 events start in the interval (38 pitched; 89 events touch the interval). Pitched range: B1 to F#5. Most frequent pitches: F4 (4), B4 (3), E5 (3), F#5 (3), B3 (3), C5 (3), D2 (2), B1 (2), D#4 (2), G#4 (2).
- All focus events: shortest 9.37 ms, median 10.42 ms, longest 1520.83 ms; 82 are below 50 ms. Pitched focus events only: shortest 29.17 ms, median 30.21 ms, longest 1520.83 ms; 31 are below 50 ms.
- Concurrency: 38 continuous overlap regions and 71 distinct overlapping note pairs. Percussion: 154 events. Focus events filtered by this inspector: 0.
- Decoder diagnostics: `{"Err/merging segment tie": 25, "Err/onset not found": 27}`. These indicate token-decoding problems, not independently verified missing notes.
- Tracks/programs: track 0 Acoustic Piano (program 0, 31 notes); track 1 Guitar (clean) (program 24, 5 notes); track 2 Bass (program 33, 2 notes); track 3 Strings (program 48, 12 notes); track 4 Synth Lead (program 80, 2 notes); track 5 Drums (program 0, 154 notes, percussion).
- The raw MIDI has no tempo meta-event. The writer's default tempo is 500,000 μs/beat (120 BPM); both parsing and writing use that default, and the stored note times are the absolute decoded seconds. The original-clock offset is 0.000 s and was applied once.
- Confidence is unavailable. MIDI velocity is constant 100 from the writer and must not be read as confidence.

### YourMT3+ Other

- Input: `reference_other.wav`; **Existing Other stem**; 22050 Hz, 30 seconds; source SHA-256 `bc5a2d607720acbfc992195fee2bc770f051a996181acaf4f558cf6ea034c0cb`.
- Backend/checkpoint: `yourmt3-plus` / `mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops@last.ckpt` (SHA-256 `ae38e415c79efd5592dcb9b658cdb99ddb11d4c4e1eaa364cab04a052473fc25`). Device: prediction_available_unvalidated run, CUDA runtime 10.06 s.
- Raw MIDI: 4 instrument tracks, 54 notes. Events start from 6.200 s; latest note-off is 29.240 s.
- Focus 6–18 s: 49 events start in the interval (49 pitched; 49 events touch the interval). Pitched range: B1 to A5. Most frequent pitches: B2 (7), B3 (6), D#2 (4), D#3 (4), F#3 (4), F4 (4), B4 (3), E5 (2), F5 (2), B1 (2).
- All focus events: shortest 29.17 ms, median 30.21 ms, longest 1530.21 ms; 29 are below 50 ms. Pitched focus events only: shortest 29.17 ms, median 30.21 ms, longest 1530.21 ms; 29 are below 50 ms.
- Concurrency: 13 continuous overlap regions and 49 distinct overlapping note pairs. Percussion: 0 events. Focus events filtered by this inspector: 0.
- Decoder diagnostics: `{"Err/merging segment tie": 15, "Err/onset not found": 15}`. These indicate token-decoding problems, not independently verified missing notes.
- Tracks/programs: track 0 Acoustic Piano (program 0, 31 notes); track 1 Guitar (clean) (program 24, 18 notes); track 2 Bass (program 33, 1 notes); track 3 Strings (program 48, 4 notes).
- The raw MIDI has no tempo meta-event. The writer's default tempo is 500,000 μs/beat (120 BPM); both parsing and writing use that default, and the stored note times are the absolute decoded seconds. The original-clock offset is 0.000 s and was applied once.
- Confidence is unavailable. MIDI velocity is constant 100 from the writer and must not be read as confidence.

## Why the old roll looked like short vertical bars

The durations are genuinely short in the prediction. The raw MIDI and canonical JSON agree; typical pitched focus events last roughly 30 ms. At a 30-second overview, a 30 ms note gets only a few horizontal pixels. The old diagnostic compounded that by drawing thin line segments rather than pitch-row rectangles. This is therefore both an AMT-duration characteristic and a rendering/scaling problem; it is not an onset/offset parser mismatch.

The new roll focuses on 6–18 seconds by default, labels every pitch row, uses exact event onset and offset, and offers 0–30 overview, phrase views, custom zoom/pan and pitch bounds. The 4 px minimum width applies only to display. Tooltips, the note table, CSV and MIDI retain actual seconds and durations.

## Instrument program provenance

The acoustic-piano/program-0 label was not filled in by `pretty_midi` because the MIDI track was empty or missing a program. YourMT3+ decodes a program class; its configured `gm_ext_plus` inverse vocabulary maps that class and the upstream writer emits an explicit `program_change` and track name. The raw MIDI and parser both report program 0 for that model output. It remains an AMT instrument-class prediction, not proof that the source audio contains an acoustic piano. The drum channel also has MIDI program field 0 as required by the MIDI drum-channel convention; it is separately flagged as percussion.

## Reconstruction, comparison and descriptive note evidence

The MIDI reconstruction is a local deterministic three-partial additive synthesizer generated from the actual parsed MIDI pitches, onsets and offsets. It is reproducible without a soundfont download, but does not imitate General MIDI instrument timbres. Percussion is excluded from that pitched-note render and counted explicitly. The reference WAV and each reconstruction share a 0–30 second clock, with no six-second rebase.

Mix-vs-Other yielded 21 one-to-one same-pitch onset candidates within 50 ms in 6–18 seconds. 17 Mix and 28 Other pitched events had no candidate pair. These are predictions from the same model on different audio inputs; they are agreement candidates only, not accuracy.

The screen also lists descriptive rising/falling triples, repeated pitch triples, short and sustained events, gaps in a model track, and same-pitch overlaps. Each candidate cites note IDs and source-clock seconds. A gap means no note was emitted on that model track; it is not evidence of an audible omission. A repeated pitch triple is not a verified arpeggio or musical motif.

## Human evaluation and next decision

The sorted/filterable focus table includes every event whose onset is in 6–18 seconds, with source, track/program, named pitch, MIDI pitch, exact onset/offset, duration, velocity and flags; confidence is shown as unavailable. It supports track/source/pitch filtering, seeking/highlighting, and CSV export. Mix and Other rolls share the same axes.

The Songscription screenshot-assisted Phase 3B comparison and its human annotation template are preserved and linked from the new view. No independent note truth is supplied here. The revised inspection makes the events usable for listening, but it does not establish transcription accuracy or authorize Phase 4. Review candidate short events, instrument labels and missing-note suspicions against the reference audio, and only then record supported annotations.

## Reproduction

Build this report and reconstructions in WSL with `output/amt/env/bin/python -B -m scripts.midi_pipeline.phase3c build`. Start the local browser server with `python -m scripts.midi_pipeline.phase3c serve --port 8768` from the repository root using the installed Windows Python, then open `http://127.0.0.1:8768/`. Health: `http://127.0.0.1:8768/api/health`. Stop the foreground server with Ctrl+C.

## Verification

On 2026-11-05, all 40 `scripts.midi_pipeline.test_pipeline` tests passed in the existing WSL Python 3.11 environment; this included a synthetic explicit-tempo MIDI test proving independent tick-to-second conversion, note pairing, program auditing, and detection of a missing note-off. Node's `--check` accepted the diagnostic JavaScript. The generated event table contained 138 onset-in-window events, and filtering to Other / track 0 showed 30 events; selecting note 0 displayed F4, MIDI 65, onset 8.240 s, offset 8.270 s, and 30.21 ms exactly, and sought both players to 8.240 s.

The Windows localhost server returned 200 for the 30.000-second 48 kHz stereo PCM16 reference and both 30.000-second 24 kHz mono reconstructions, with `audio/wav`. It returned 206 with correct `Content-Range` for byte ranges on all three WAVs. The same headers and media durations loaded in the browser. I verified original WAV playback, Mix and Other synthesis playback, seek/volume, 0.5× speed, focus loop wrap, A/B source switching, screenshot loading, and the qualitative annotation control. No verified reference notes were created; the annotation smoke test remained only in the temporary browser tab and was not exported. The browser console had no warning/error entries. The page's volume/speed/timeline can be checked in the served report.

Piano-roll time/pitch rectangles are computed directly from the validated JSON events, use a 4-pixel width only below display resolution, and reveal exact data on hover/click or selected table rows. The browser screenshot showed legible table text and labels at the responsive canvas width. A screenshot-only coordinate-to-audio timing and direct canvas-note hover were not used as transcription evidence; actual event details are shown in the page. Basic Pitch, its caches and the legacy event detector are not read by the inspection or reconstruction.
