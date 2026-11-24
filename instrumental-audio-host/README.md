# Instrumental audio hosting

The Cloudflare Worker + R2 host serves the preview files separately from the GitHub Pages interface. Its configured base URL is `https://song-pattern-audio.bepop.workers.dev`. The Worker only allows reads for the four expected filenames under a Spotify track ID; it has no upload, delete, or bucket-list endpoint. The static export now includes this URL for every locally available preview.

The upload inventory contains 2,853 objects totaling 21.03 GiB. A full HEAD audit on November 4, 2026 confirmed every object has the expected size and audio type, supports byte ranges, and allows GitHub Pages access. The results are recorded in [`verification.json`](verification.json). Small range requests for Silo's original MP3 and bass WAV also returned `206 Partial Content` with the expected content types and CORS headers.

## Layout

The combined view uses the saved original song MP3 matched by Spotify ID. The separated instrumental MP3 is only the analysis input and is not uploaded as the main preview. Each isolated-stem view uses the WAV separated from that instrumental. R2 objects use these keys:

```text
songs/<spotify-track-id>/original.mp3
songs/<spotify-track-id>/bass.wav
songs/<spotify-track-id>/drums.wav
songs/<spotify-track-id>/other.wav
```

The endpoint maps to `https://<worker-subdomain>.workers.dev/v1/<spotify-track-id>/<filename>`. It streams `Range` requests through to R2, so the page's bounded MP3/WAV previews can seek without decoding an entire file. The UI uses the browser's native audio element, seeks to the chosen window, and pauses at its end.

## Setup for a new host

1. Install Wrangler and sign in to the Cloudflare account that should own the bucket.
2. Create the R2 bucket named `song-pattern-previews` (or change the matching name in this file).
3. Set up the `instrumental-audio-host/` directory with Wrangler, deploy the Worker, and verify a byte-range request for an original MP3 and a stem WAV.
4. Upload the per-object paths and hashes in `instrumental-audio-host/upload_manifest.csv`. Original MP3s are matched from `liked-audio/` by Spotify ID; tracks without one retain their three stem previews but have no main-view audio. Use `rclone` or another S3-compatible bulk client rather than one Wrangler process per file; Cloudflare documents Wrangler's one-object-at-a-time upload as a small-file workflow.
5. Put the returned Worker base URL in `docs/data/instrumentals/media-config.json`, rebuild the static export, then publish the GitHub Pages changes.

R2 public buckets are private until public access is explicitly enabled. A Worker binding can expose a narrow read-only route without exposing bucket listing or mutation. Cloudflare's `r2.dev` address is described as rate-limited development access; for production, use an owned custom domain or a Worker route. See [R2 public buckets](https://developers.cloudflare.com/r2/buckets/public-buckets/), [R2 Workers API](https://developers.cloudflare.com/r2/api/workers/workers-api-usage/), and [R2 pricing](https://developers.cloudflare.com/r2/pricing/).

Current Standard pricing lists 10 GB-month and request allowances in its monthly free tier, $0.015/GB-month for additional storage, and no egress charge. For the current 22.58 GB inventory, the storage component is about $0.19/month after the free storage allowance. Request charges depend on traffic; see the analysis report for the measured inventory and pricing references.
