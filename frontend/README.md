# Bepop front page — local preview

From the repository root, run `node frontend/server.mjs` using Windows Node.js,
then open http://127.0.0.1:8771/. The server is read-only and serves exact MP3s
from `liked-audio/` using their recorded source paths. Nothing is published.

Search combines the original 300-song list with eligible pilot entries from
`mvp_325/summary.csv`. Per-song files for additions come from
`mvp_325/songs/<id>/`; original files come from `outputs/<id>/`. Catalog
membership is checked at startup. Ineligible additions and their reasons are
shown for matching searches and listed in `UPDATE_REPORT.md`. Sources and lyric
labels remain as supplied; unverified `Unlabeled N` labels display as `Vocal
segment N`.

Select a rhythmic section for its condensed summary, then open **Show bars** to
inspect or play individual bars. **Pattern IDs** exposes the raw identifiers.
The rings, stylus, slider, and audio use the original song timeline. The supplied
rhythm boundaries are full-mix analysis and can include voice. The summaries
exclude voice, inactive rows, and irregular layers without changing those
boundaries. Irregular bars are not compared.

Checks:

```
node --test frontend/test.mjs frontend/test_summaries.mjs
node frontend/audit.mjs
python -B -m unittest discover -s frontend -p test_notation.py -v
python -B -m unittest tests.test_pipeline.RepetitionTests -v
```
