# Song pattern layers: corpus observations

Generated from the complete local corpus on 2026-11-03. **1128 songs** were processed from 1128 local audio files and matching note-event exports.

## How to read these results

The map is an exploratory hierarchy with three to five levels. Boundaries are candidate change points from one-second audio summaries of chroma, MFCC timbre, loudness, spectral centroid, and onset strength. Each segment is described using the catalog's transcribed `other`-stem note-event density, adjacent-event pitch movement, and mean register. Similar audio-feature profiles at the same level are marked as recurrence candidates; this does not verify that the same notes or rhythm repeat. The boundaries can reflect arrangement, harmony, timbre, or production changes and need listening-based review before they are treated as musical section labels.

Bass, drums, and vocals are not separately included in these web note-event exports. Full-mix audio features can suggest that the sound changed but cannot reliably name which instrument entered. The pattern catalog covers the complete local corpus of 1,128 songs. The existing listening catalog still has 75 published audio previews; other entries show their layer maps without publishing additional audio.

## Corpus observations

- Average generated hierarchy depth (viewer design, not a measured natural number of musical layers): **5.00 layers/song** (range 5–5).
- Average number of broad arc windows: **6.0 per song**.
- Total transcribed `other`-stem note events inspected: **1,271,174**.
- Frequently observed segment descriptors (these are coarse event-shape tags, not genre labels):

  - `dense · undulating · mid register` — 12,023 segments at hierarchy level 3
  - `dense · rising · mid register` — 2,608 segments at hierarchy level 3
  - `active · undulating · mid register` — 1,903 segments at hierarchy level 3
  - `dense · undulating · high register` — 888 segments at hierarchy level 3
  - `active · rising · mid register` — 765 segments at hierarchy level 3
  - `active · falling · mid register` — 507 segments at hierarchy level 3
  - `open · undulating · mid register` — 505 segments at hierarchy level 3
  - `dense · falling · mid register` — 475 segments at hierarchy level 3

- Songs with at least one conservative recurring 8-onset note shape: **78 / 1,128**.
- Saved within-song note-shape candidates: **146** (up to four per song).

## Method and evidence limits

Audio is reduced to mono at 11,025 Hz. Features are summarized every second and standardized within each song. Novelty uses cosine distance between the four-second feature means on each side of a candidate boundary. The outer ring retains up to five strong, separated boundaries; inner rings recursively refine them. Thus the depth is a designed view capped at five, not a discovery that a song intrinsically has that many musical layers. Timing resolution is about one second.

The note-shape pass reduces polyphony to the highest note near each onset (50 ms grouping). It searches eight-onset phrases with at least three different pitches and no inter-onset gap above 1.5 seconds. A match retains all seven semitone intervals plus inter-onset gaps quantized to quarters of the local median gap. Matches can be transposed and proportionally stretched in time; at least two nonoverlapping occurrences are required. The JSON retains pitches and relative onset times from the first instance and the times of every occurrence. This is symbolic evidence for a candidate top-line pattern, not a verified isolated melody. A rising event tag by itself is not proof of an ascending arpeggio.

No human boundary-accuracy score has been measured for these maps. The specific Spotify instrumental in the prompt is not a separately ingested audio file here; the catalog's 1nonly music-video version has different source timing and should not inherit those timestamps without checking.


## Tidal, MIDI, and audio

TidalCycles is a live pattern language and playback/control environment. It can express note/event patterns and send MIDI through SuperDirt/SuperCollider; a MIDI-to-Tidal converter translates symbolic note events to that representation. Tidal's default SuperDirt sampler can also trigger audio samples, including user-provided WAV samples, but this is sample playback rather than structural analysis of an arbitrary song. Tidal does not natively analyze or transcribe an arbitrary WAV/MP3 into musical sections. Use an audio-analysis pipeline (for example, [librosa's recurrence and segmentation tools](https://librosa.org/doc/main/segment.html) with spectral/chroma/onset features) to find candidate audio sections, then align those times with stem audio and MIDI. MIDI is useful for pitch/rhythm detail; the original audio remains necessary for timbre, layer-entry, and mix changes. See [Tidal's MIDI guide](https://tidalcycles.org/docs/configuration/MIDIOSC/midi/), [Tidal's sample library guide](https://tidalcycles.org/docs/configuration/AudioSamples/default_library/), and the [Tyler McLaughlin MIDI-to-Tidal converter](https://github.com/TylerMclaughlin/midi_to_tidalcycles).

## Next accuracy step

Listen through the candidate boundaries and annotate a diverse validation sample, beginning with the reference example's 0:00–0:55 outline and repeated arpeggio phrases. Add per-stem activity features so detected sound changes can be attributed to bass, drums, or other instrumentation. Only treat a repeated passage as a musical motif after its note/rhythm sequence is aligned and verified.

## Other pattern languages with audio support

- [Strudel](https://strudel.cc/learn/samples/) provides Tidal-style patterns in JavaScript and can load WAV, MP3, and OGG samples. Its slice, chop, and arrangement functions are well suited to representing a song as reusable audio excerpts. It does not automatically discover a song's structure.
- [Sonic Pi](https://sonic-pi.net/tutorial-03.html) can sequence and transform external audio samples; its current tutorial lists WAV, AIFF, OGG, FLAC, and MP3. Format support may depend on the installed build.
- [SuperCollider buffers](https://doc.sccode.org/Classes/Buffer.html) support loading and processing audio, and its Patterns system can sequence sound events. It offers low-level audio analysis building blocks, rather than a ready-made hierarchical song labeler.

For this phase, a neutral JSON hierarchy is more useful than choosing a playback language: it retains explicit segment times, descriptions, and recurrence evidence, and can later be exported to Tidal/Strudel or used for model features. No classifier or cross-song comparison was added.
