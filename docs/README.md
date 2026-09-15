# bepop — frontend (Phase 5)

Static site, published via GitHub Pages from a **dedicated `pages` branch,
`/docs` folder**. No build step, no framework, no bundler — this whole
directory *is* the published site; editing a file here and pushing an
update to `pages` is the entire deploy process.

## Why a dedicated branch (not `main`/docs)

Both are valid GitHub Pages sources (the brief allowed either). A dedicated
branch was chosen to keep `main`'s history free of the ~239 MB published
payload (mostly audio, see `data/DATA_LAYER.md` §5) and because this repo's
working convention is to never commit straight to `main`. `pages` was
branched from `main` once, at the point Phase 4+5 were ready to ship, and
gets fast-forwarded/updated the same way for future publishes — it isn't a
generated/orphan branch, just the ordinary branch this content lives on.

This directory was built as `web/` during Phase 4 and renamed to `docs/`
verbatim (content unchanged) when Phase 5 settled on this publish target —
every path reference in `data/DATA_LAYER.md` and the build scripts under
`scripts/webexport/` was updated to match. `.nojekyll` sits at this
directory's root so GitHub Pages serves every file as-is (Jekyll's default
processing otherwise ignores/mangles anything starting with `_`, which
`data/_song_id_map.csv` does).

## Structure

```
docs/
├── index.html          - the entire page shell (search bar, home screen, song view, analysis panel)
├── css/styles.css       - all styling (dark/Spotify-ish palette, layout transitions)
├── js/
│   ├── app.js            - UI logic: search, wavesurfer wiring, selection handling, panel rendering
│   └── alignment.js       - Phase 4's ported matching engine (query-vs-corpus chord/note search)
├── data/
│   ├── manifest.json      - {song_id, title, duration} × 75, loaded at boot
│   ├── sequences.json      - compact chord/note sequences for ALL songs, loaded at boot (matching engine input)
│   ├── songs/<id>.json      - full per-song note/chord detail, lazy-loaded when a song is opened
│   └── DATA_LAYER.md         - full schema reference for every file above (Phase 4 deliverable)
└── audio/<id>.mp3        - 75 tracks, 128kbps (Phase 4 deliverable)
```

`index.html` has no build-time templating — it's the literal file the
browser receives. `app.js` is loaded as `<script type="module">` (so it can
`import` from `alignment.js`); the two `wavesurfer.js` files are loaded via
plain `<script>` tags from jsdelivr **before** the module script, pinned to
an exact version (`7.12.12`) — see the CDN allowlist note below.

## How the pieces fit together

1. **Boot** (`app.js` `boot()`): fetches `manifest.json` + `sequences.json`
   once, in parallel, and keeps both resident in memory for the rest of the
   session — per `DATA_LAYER.md`, `sequences.json` is sized (~6.3 MB raw /
   ~667 KB gzip) specifically so this is safe to do upfront rather than
   per-search.
2. **Search** filters `manifest.json` client-side (substring match on
   `title`, case-insensitive) — no backend, no index file, just an
   `Array.filter` over 75 entries. The same search bar handles both the
   initial home-screen search and switching songs later (one input, one
   code path, not two).
3. **Opening a song** (`loadSong()`): tears down any previous WaveSurfer
   instance/state completely, fetches `songs/<id>.json`, and creates a fresh
   `WaveSurfer` instance pointed at `audio_url`. (A MIDI pitch-curve overlay
   drawn over the waveform existed through Phase 5 but was removed in the
   post-Phase-5 visual revision pass — not currently rendered.)
4. **Selection** uses wavesurfer's official Regions plugin
   (`enableDragSelection`). On `region-created` (drag finished) with a
   non-trivial duration, the layout shrinks the waveform and opens the
   analysis panel; on `region-updated` (the user resizes/redrags the same
   selection) the analysis re-runs with the new bounds.
5. **Analysis** slices `sequences.json`'s entry for the open song down to
   the selection's time range and calls `alignment.js`'s
   `searchChordQueryAgainstCorpus()` / `searchNoteQueryAgainstCorpus()`
   against the full in-memory `sequences.json` — synchronous, no fetch,
   typically tens of milliseconds (see `DATA_LAYER.md` §6 for measured
   numbers). The two result lists are rendered separately and never merged.
6. **Escape / click-outside** (a single `document`-level `keydown`/`click`
   listener, only active while a selection exists) clears the region and
   collapses the panel.

## A real CSS bug found and fixed during Phase 5's build-order testing

`#homeScreen`/`#songView` are toggled with the HTML `hidden` attribute, but
each also has its own `display: flex` rule for layout. A bare `display`
declaration on an id selector **outranks** the browser's built-in
`[hidden] { display: none }` rule (higher specificity), which silently
defeats `hidden` — the element stays visible. Fixed by scoping those rules
to `#homeScreen:not([hidden])` / `#songView:not([hidden])` instead. Caught
by the automated click-through test (`isHidden()` returning `false` right
after page load, before any interaction) — see the Phase 5 report for the
rest of that test pass. **If a future edit adds `display` to any other
element that's also toggled via `hidden`, use this same `:not([hidden])`
pattern**, or it will silently stop hiding.

## Making a change here

- **Content/data changes** (new songs, re-tuned matching): regenerate via
  `scripts/webexport/` (see `data/DATA_LAYER.md`'s reproduce commands), not
  by hand-editing anything under `data/` or `audio/`.
- **UI/behavior changes**: edit `index.html` / `css/styles.css` / `js/app.js`
  directly — there's nothing to compile. Open `index.html` through a local
  static server (`python3 -m http.server` from inside `docs/`, not
  `file://`, since the `fetch()` calls for the JSON data need real HTTP) and
  reload.
- **`js/alignment.js`**: don't hand-edit the scoring/DP logic — it's a
  validated port of `lib/sequence_alignment.py` /
  `lib/chord_similarity.py` / `lib/note_similarity.py` (see
  `DATA_LAYER.md` §6). If the Python originals change, re-port and re-run
  `scripts/webexport/dump_validation_cases.py` +
  `scripts/webexport/run_js_validation.mjs` before trusting the new version.
- **CDN dependency**: `wavesurfer.js` is pinned to `7.12.12` in
  `index.html`'s two `<script>` tags (core + Regions plugin, both loaded as
  UMD builds exposing the globals `WaveSurfer` and `WaveSurfer.Regions`).
  Bump the version in both tags together if you ever need to; nothing else
  in this repo references that version number.
