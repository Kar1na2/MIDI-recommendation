"""Write corpus observations for the instrumental-only layer explorer."""
from __future__ import annotations

from collections import Counter
from pathlib import Path


def write_report(manifest_rows, analyses, tracks, assets, upload_rows, destination: Path):
    total = len(manifest_rows)
    statuses = Counter(row["status"] for row in manifest_rows)
    ready = [row for row in manifest_rows if row["track_id"] in analyses]
    original_missing = [row for row in manifest_rows if not row.get("original_audio_available", False)]
    instrumental_sources = [row for row in manifest_rows if row.get("instrumental_source_available", False)]
    source_missing_original = [row for row in instrumental_sources if not row.get("original_audio_available", False)]
    per_depth = []
    for depth in range(3):
        per_depth.append([song["views"]["instrumental"]["layers"][depth]["new_boundaries"]
                          for song in analyses.values()])
    observed_depths = [max((depth + 1 for depth, count in enumerate(counts) if count > 0), default=1)
                       for counts in zip(*per_depth)] if per_depth and analyses else []
    outer_labels = Counter(segment["label"] for song in analyses.values()
                           for segment in song["views"]["instrumental"]["layers"][0]["segments"])
    repeat_counts = Counter(view for song in analyses.values() for view, data in song["views"].items()
                            if data["recurrences"])
    boundary_totals = [sum(counts) for counts in per_depth]
    audio_bytes = sum(row[4] for row in upload_rows)
    monthly_charge = max(0, audio_bytes / 1_000_000_000 - 10) * 0.015
    failures = Counter()
    for row in manifest_rows:
        if row["status"] in {"not_found", "search_failed", "download_failed", "source_review_required", "separation_failed", "analysis_failed", "asset_missing", "pending", "source_found", "downloaded", "separating", "separated"}:
            failures[row["status"]] += 1

    lines = [
        "# Instrumental pattern database: processing report", "",
        "Generated from the saved Spotify library by the resumable instrumental search, Demucs separation, and stem-feature pipeline.", "",
        "## Source collection", "",
        "The search covered the same saved Spotify list and used the query form `[artist] - [song] - instrumental`, with title, artist, instrumental-marker, and duration checks before a result was accepted. This repository does not contain an MP3Juice script; it contains a yt-dlp YouTube downloader, which the instrumental intake uses for the same search-and-download step. No original vocal mix was substituted when a matching instrumental was missing.",
        "The Spotify example link supplied for Silo (`1PirBnF2xXUyf9yduhzrIt`) is not in the saved 1,128-track playlist. The playlist has a different Silo entry, `meaningful love - instrumental` (`6rQCPgrLJMQavladOGpwZt`, 291.17 seconds), and the two recordings' timestamps are not assumed to match.", "",
        "## Corpus coverage", "",
        f"- Spotify tracks in scope: **{total:,}**.",
        f"- Instrumental analyses ready locally: **{len(ready):,}**.",
        f"- Verified instrumental MP3s available as separation inputs: **{len(instrumental_sources):,}**; among them, **{len(source_missing_original):,}** lack the original-song MP3 used by the combined-view player.",
        f"- Original-song MP3s matched to the playlist by Spotify ID: **{total - len(original_missing):,}/{total:,}**; **{len(original_missing):,}** have no original MP3 for the combined-view preview.",
        f"- Status totals: {', '.join(f'**{name}** {count:,}' for name, count in sorted(statuses.items()))}.",
        f"- Audio objects included in the hosting upload list: **{len(upload_rows):,} files**, totaling **{audio_bytes / (1024 ** 3):.2f} GiB** ({audio_bytes:,} bytes).",
        f"- Estimated R2 Standard storage component at the current Cloudflare list rate, after the 10 GB-month free allowance: **about ${monthly_charge:.2f}/month** for this captured inventory. This excludes any future growth and rounds differently from the provider's billing calculation.",
        "",
        "The public page can only play previews after an audio host URL is configured and the listed files have been uploaded. The combined view uses the original MP3 from the existing library; the three WAVs come from instrumental separation. Tracks missing that original MP3 can still play their stems after hosting. The read-only Cloudflare Worker/R2 draft is in [`instrumental-audio-host/`](../../instrumental-audio-host/); it is not deployed and no bucket has been provisioned. The GitHub Pages interface remains separate.",
        "",
        "## Pattern observations", "",
    ]
    if analyses:
        avg_segments = [sum(values) / len(values) for values in per_depth]
        avg_depth = sum(observed_depths) / len(observed_depths) if observed_depths else 0
        depth_counts = Counter(observed_depths)
        lines += [
            f"- Across **{len(analyses):,}** analyzed songs, the mean number of supported structural depths was **{avg_depth:.2f}** (minimum {min(observed_depths)}, maximum {max(observed_depths)}). The viewer reserves three rings for the combined instrumental and two for each stem, but an unchanged span carries inward whenever no conservative boundary is supported.",
            f"- Supported-depth distribution: **{', '.join(f'{depth} depth(s): {depth_counts[depth]:,} songs' for depth in range(1, 4))}**.",
            f"- Mean number of newly detected boundaries at combined-stem depths 1, 2, and 3: **{avg_segments[0]:.2f}**, **{avg_segments[1]:.2f}**, **{avg_segments[2]:.2f}** per song.",
            f"- Combined-stem boundaries retained by depth: **{', '.join(f'{n:,}' for n in boundary_totals)}**.",
        f"- Similar six-second audio-texture candidates appear in **{sum(len(view['recurrences']) for song in analyses.values() for view in song['views'].values()):,} windows** across **{sum(repeat_counts.values()):,} view-song pairs**. This is timbre/chroma/onset similarity, not proof that exact notes or rhythm repeat.",
            "- Most common broad-view descriptions (measured relative stem energy; not instrument identification):",
            "",
        ]
        lines.extend(f"  - {label}: {count:,} window(s)" for label, count in outer_labels.most_common(10))
        stem_labels = Counter(segment["label"] for song in analyses.values()
                              for view_name in ("bass", "drums", "other")
                              for level in song["views"][view_name]["layers"]
                              for segment in level["segments"])
        lines += ["", "- Most common isolated-stem descriptions:", ""]
        lines.extend(f"  - {label}: {count:,} window(s)" for label, count in stem_labels.most_common(10))
    else:
        lines.append("No stem analyses have completed yet, so pattern averages and frequent descriptors are intentionally withheld until there is measured data.")

    lines += ["", "## Preview availability and recommended fixes", ""]
    if original_missing:
        lines += [f"- **Main-view audio unavailable for {len(original_missing):,} track(s)** because no original MP3 matched its Spotify ID in `liked-audio/`. The three separated-stem previews remain usable for analyzed tracks after hosting. Recover each original MP3 and save it with the matching Spotify ID in its filename; the track-by-track list below names every missing item.", ""]
    remedies = {
        "not_found": "No candidate passed the instrumental marker, title/artist overlap, and duration guards. Verify the exact track manually or add its official instrumental URL to `instrumentals/source_overrides.json`; the downloader does not substitute the original vocal recording.",
        "search_failed": "The metadata search failed before candidate review, commonly from throttling, connectivity, or YouTube bot checks. Retry later at a lower worker count, update yt-dlp, or use a manually verified source.",
        "download_failed": "A candidate was selected, but its stream could not be saved. Retry with updated yt-dlp or replace it with a verified accessible source.",
        "source_review_required": "The candidate is a substituted cover, transposition, or arrangement. It has been excluded; replace it with a source that preserves the original instrumental arrangement.",
        "separation_failed": "Demucs did not produce three aligned stems. Retry the track individually; use CPU if the GPU run fails consistently and inspect the captured reason.",
        "analysis_failed": "The stems could not be summarized. Verify the local WAV files and duration, then rerun analysis for the failed track.",
        "asset_missing": "The analysis exists but one or more local preview files are missing. Rerun the failed download/separation stage before upload.",
        "pending": "The library item has not been processed yet. Resume the downloader and report generator.",
        "source_found": "A candidate is recorded but the audio download has not completed. Resume the downloader.",
        "downloaded": "The instrumental MP3 exists but stem separation is still outstanding. Resume Demucs separation.",
        "separating": "Demucs is currently running for this track. If the run was interrupted, resume the persistent separator.",
        "separated": "The WAV stems exist but analysis is still outstanding. Resume the pattern analyzer.",
    }
    for status, count in sorted(failures.items()):
        lines += [f"- **{status}: {count:,}** — {remedies[status]}"]
    if statuses.get("ready", 0) and any(row["status"] == "ready" and not row["audio_host_configured"] for row in manifest_rows):
        lines.append(f"- **awaiting audio host: {statuses['ready']:,} analyzed track(s)** — deploy the reviewed Worker, create the R2 bucket, upload verified entries from `instrumental-audio-host/upload_manifest.csv`, set `INSTRUMENTAL_AUDIO_BASE_URL`, and rebuild the page. Until then, the static page will show a clear preview-unavailable state rather than a dead URL.")
    if not failures:
        lines.append("- No per-song source, separation, or analysis failures were recorded in this export.")

    issue_rows = [row for row in manifest_rows if row["status"] in failures or not row.get("original_audio_available", False)]
    if issue_rows:
        lines += ["", "## Track-by-track follow-up", "",
                  "The recorded reason is preserved per track. The recommendation follows the current status; a source-search miss should be manually checked before retrying.", ""]
        for row in sorted(issue_rows, key=lambda item: (item["artist"].casefold(), item["title"].casefold())):
            reason = (row.get("reason") or "No additional failure detail was recorded.").replace("|", "\\|").replace("\n", " ").replace("\r", " ")
            fix = remedies.get(row["status"], "")
            if not row.get("original_audio_available", False):
                original_fix = "Recover the original-song MP3 and store it in `liked-audio/` with this Spotify ID in square brackets; the combined-view preview needs that file."
                fix = f"{original_fix} {fix}".strip()
            lines.append(f"- **{row['title']} — {row['artist']}** (`{row['track_id']}`): `{row['status']}`. {reason} Recommended fix: {fix}")

    lines += [
        "", "## Method and limits", "",
        "Each instrumental MP3 is separated with Demucs `htdemucs_ft`; the vocal residual is discarded from this database. The three WAV previews are mono 22.05 kHz PCM-16. Stem features summarize MFCCs, chroma, RMS, spectral centroid, and onset activity once per second. Candidate transitions compare context windows on both sides and must exceed a robust, within-track threshold. Later depths only refine supported parent windows; unchanged parents are carried inward, never split merely to fill a ring.",
        "",
        "The window-recurrence pass marks highly similar six-second feature sequences as audio-texture candidates. The revised method caps newly added boundaries at three for the broad depth, three across the phrase depth, and four across the fine depth; phrase and fine depths can add no more than one cut within each parent. These limits reduce tiny, generic sections while preserving three browseable depths. It does not establish an exact repeated note sequence. Chroma and average spectral features cannot reliably identify ascending arpeggios, individual synthesizer lines, or precise rhythmic note patterns. A later transcription/alignment pass and listening review are needed to make those claims; the current page intentionally does not compare songs or train a classifier.",
        "",
        "A missing instrumental is different from a source-search failure. `not_found` means all returned results failed the explicit matching checks; `search_failed` and `download_failed` record access/transport problems, which are not evidence that an instrumental does not exist.",
        "",
        "## Hosting estimate and next steps", "",
        "The current inventory size is estimated using Cloudflare R2 Standard's published $0.015 per GB-month rate, a 10 GB-month free allowance, and free egress. Request counts are expected to remain within the published free allowance at modest traffic; storage and request totals must be rechecked after the full upload. R2 public development URLs are rate-limited, so production should use a controlled Worker route or an owned custom domain.",
        "",
        "1. Finish/retry YouTube bot-check and transport failures without loosening candidate-match rules.",
        "2. Complete separation and feature analysis for each successfully downloaded instrumental, then review a varied sample of change points by listening.",
        "3. Compare the final R2 upload inventory against every published URL and issue a byte-range test for MP3 and WAV previews.",
        "4. Add human-verified note/rhythm annotations only where phrase boundaries and repeated cells are confirmed.",
        "",
        "Pricing reference: [Cloudflare R2 pricing](https://developers.cloudflare.com/r2/pricing/). Public access guidance: [R2 public buckets](https://developers.cloudflare.com/r2/buckets/public-buckets/). Range reads: [R2 Workers API](https://developers.cloudflare.com/r2/api/workers/workers-api-reference/).",
        "",
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines), encoding="utf-8")
