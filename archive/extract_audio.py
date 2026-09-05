"""
Extract Spotify Audio Analysis data for your liked songs.

Spotify's audio analysis endpoint returns rich data per track:
  - Bars, beats, tatums (rhythmic structure with timestamps)
  - Sections (large-scale structure: tempo, key, mode, loudness, time_signature)
  - Segments (short sound entities: pitch vectors, timbre vectors, loudness)

This is useful for cross-comparing against your own MIDI-based sequence
alignment results. Sections and segments are the most relevant for
similarity work.

Setup: Same Spotify credentials as fetch_liked_songs.py

Usage:
  python extract_audio_analysis.py --songs liked_songs.json --output-dir ./analysis
  python extract_audio_analysis.py --songs liked_songs.json --output-dir ./analysis --tracks-only
  python extract_audio_analysis.py --track-id 4iV5W9uYEdYUVa79Axb7Rh --output-dir ./analysis

Output structure (per song):
  analysis/
    {track_id}_analysis.json     Full analysis (sections, segments, bars, etc.)
    {track_id}_features.json     Audio features (tempo, key, energy, valence, etc.)
  analysis/
    summary.json                 Aggregated features for all tracks (for quick comparison)
"""

import argparse
import json
import sys
import time
from pathlib import Path

import spotipy
from spotipy.oauth2 import SpotifyOAuth


SCOPE = "user-library-read"

# Spotify rate limits: ~100 requests/minute for audio-analysis
# We add a small delay to stay well under
REQUEST_DELAY = 0.3  # seconds between API calls


def authenticate() -> spotipy.Spotify:
    try:
        sp = spotipy.Spotify(auth_manager=SpotifyOAuth(scope=SCOPE))
        sp.current_user()
        return sp
    except spotipy.SpotifyOauthError as e:
        print(f"Authentication failed: {e}")
        sys.exit(1)


def fetch_audio_features_batch(sp: spotipy.Spotify, track_ids: list[str]) -> dict:
    """
    Fetch audio features for up to 100 tracks at once.
    Returns dict mapping track_id -> features dict.
    
    Audio features include:
      acousticness, danceability, energy, instrumentalness,
      key, liveness, loudness, mode, speechiness, tempo,
      time_signature, valence
    """
    features_map = {}

    # Spotify allows 100 IDs per request
    for i in range(0, len(track_ids), 100):
        batch = track_ids[i:i + 100]
        try:
            results = sp.audio_features(tracks=batch)
            if results:
                for feat in results:
                    if feat is not None:
                        features_map[feat["id"]] = feat
        except Exception as e:
            print(f"  Warning: audio_features batch failed: {e}")

        time.sleep(REQUEST_DELAY)

    return features_map


def fetch_audio_analysis(sp: spotipy.Spotify, track_id: str) -> dict | None:
    """
    Fetch detailed audio analysis for a single track.
    
    Returns sections, segments, bars, beats, tatums, plus track-level metadata.
    This is the richest data Spotify provides — segments contain
    12-dimensional pitch and timbre vectors at ~0.01s resolution.
    """
    try:
        analysis = sp.audio_analysis(track_id)
        return analysis
    except spotipy.SpotifyException as e:
        if e.http_status == 404:
            return None  # track has no analysis available
        raise
    except Exception as e:
        print(f"  Warning: audio_analysis failed for {track_id}: {e}")
        return None


def extract_sections_summary(analysis: dict) -> list[dict]:
    """
    Pull out sections with the fields most useful for similarity comparison:
    start, duration, tempo, key, mode, loudness, time_signature.
    
    Sections represent large musical segments (verse, chorus, bridge, etc.)
    and are the most natural unit to compare against MIDI section analysis.
    """
    sections = analysis.get("sections", [])
    return [
        {
            "start": s["start"],
            "duration": s["duration"],
            "tempo": s["tempo"],
            "key": s["key"],            # 0-11, pitch class (0=C, 1=C#, ...)
            "mode": s["mode"],           # 0=minor, 1=major
            "loudness": s["loudness"],
            "time_signature": s["time_signature"],
            "tempo_confidence": s.get("tempo_confidence"),
            "key_confidence": s.get("key_confidence"),
        }
        for s in sections
    ]


def extract_segment_pitches(analysis: dict) -> list[dict]:
    """
    Extract pitch vectors from segments — these are 12-dimensional
    chroma vectors (one value per pitch class, 0.0-1.0) that represent
    the harmonic content at each moment.
    
    This is the closest analog to what you'll extract from MIDI and
    the most useful data for cross-comparison with sequence alignment.
    """
    segments = analysis.get("segments", [])
    return [
        {
            "start": seg["start"],
            "duration": seg["duration"],
            "pitches": seg["pitches"],       # 12-dim chroma vector
            "timbre": seg["timbre"],         # 12-dim timbre vector
            "loudness_start": seg["loudness_start"],
            "loudness_max": seg["loudness_max"],
        }
        for seg in segments
    ]


def process_tracks(
    sp: spotipy.Spotify,
    songs: list[dict],
    output_dir: Path,
    full_analysis: bool = True,
    skip_existing: bool = True,
):
    """Process all tracks: fetch features + optionally full analysis."""
    output_dir.mkdir(parents=True, exist_ok=True)

    track_ids = [s["track_id"] for s in songs]
    id_to_song = {s["track_id"]: s for s in songs}

    # --- Batch fetch audio features (fast) ---
    print(f"Fetching audio features for {len(track_ids)} tracks...")
    features_map = fetch_audio_features_batch(sp, track_ids)
    print(f"  Got features for {len(features_map)} tracks")

    # Save per-track features
    for tid, feat in features_map.items():
        feat_path = output_dir / f"{tid}_features.json"
        feat_path.write_text(json.dumps(feat, indent=2))

    # --- Build summary table ---
    summary = []
    for song in songs:
        tid = song["track_id"]
        feat = features_map.get(tid, {})
        summary.append({
            "track_id": tid,
            "title": song["title"],
            "artist": song["artist"],
            "tempo": feat.get("tempo"),
            "key": feat.get("key"),
            "mode": feat.get("mode"),
            "time_signature": feat.get("time_signature"),
            "energy": feat.get("energy"),
            "valence": feat.get("valence"),
            "danceability": feat.get("danceability"),
            "acousticness": feat.get("acousticness"),
            "instrumentalness": feat.get("instrumentalness"),
            "loudness": feat.get("loudness"),
            "duration_ms": song.get("duration_ms"),
        })

    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"  Saved summary to {summary_path}")

    # --- Full analysis per track (slow, one request each) ---
    if not full_analysis:
        print("Skipping full analysis (use without --tracks-only for full data)")
        return

    print(f"\nFetching full audio analysis (this takes a while)...")
    success = 0
    skipped = 0

    for i, song in enumerate(songs, 1):
        tid = song["track_id"]
        analysis_path = output_dir / f"{tid}_analysis.json"

        if skip_existing and analysis_path.exists():
            skipped += 1
            continue

        label = f"{song['artist']} - {song['title']}"
        print(f"  [{i}/{len(songs)}] {label[:60]}... ", end="", flush=True)

        analysis = fetch_audio_analysis(sp, tid)
        if analysis is None:
            print("no data")
            continue

        # Save full analysis
        analysis_path.write_text(json.dumps(analysis, indent=2))

        # Also save extracted sections and pitch data for easy access
        sections = extract_sections_summary(analysis)
        pitches = extract_segment_pitches(analysis)

        (output_dir / f"{tid}_sections.json").write_text(
            json.dumps(sections, indent=2)
        )
        (output_dir / f"{tid}_pitches.json").write_text(
            json.dumps(pitches, indent=2)
        )

        success += 1
        print("done")
        time.sleep(REQUEST_DELAY)

    print(f"\nAnalysis complete: {success} fetched, {skipped} skipped")


def main():
    parser = argparse.ArgumentParser(
        description="Extract Spotify audio analysis for liked songs"
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--songs", type=Path,
        help="Path to liked_songs.json from fetch_liked_songs.py",
    )
    input_group.add_argument(
        "--track-id", type=str,
        help="Analyze a single track by Spotify ID",
    )

    parser.add_argument(
        "--output-dir", "-o", type=Path, default=Path("./analysis"),
        help="Directory to save analysis files (default: ./analysis)",
    )
    parser.add_argument(
        "--tracks-only", action="store_true",
        help="Only fetch audio features (fast), skip full per-track analysis",
    )
    parser.add_argument(
        "--no-skip", action="store_true",
        help="Re-fetch even if analysis files already exist",
    )
    args = parser.parse_args()

    sp = authenticate()

    if args.track_id:
        # Single track mode
        songs = [{"track_id": args.track_id, "title": "unknown", "artist": "unknown"}]
        # Try to get track info
        try:
            track = sp.track(args.track_id)
            songs[0]["title"] = track["name"]
            songs[0]["artist"] = track["artists"][0]["name"]
            songs[0]["duration_ms"] = track["duration_ms"]
        except Exception:
            pass
    else:
        if not args.songs.exists():
            print(f"Songs file not found: {args.songs}")
            print("Run fetch_liked_songs.py first.")
            sys.exit(1)
        songs = json.loads(args.songs.read_text())

    print(f"Processing {len(songs)} track(s)\n")

    process_tracks(
        sp, songs, args.output_dir,
        full_analysis=not args.tracks_only,
        skip_existing=not args.no_skip,
    )

    print(f"\nAll data saved to: {args.output_dir.resolve()}")
    print("\nKey files for your similarity analysis:")
    print("  summary.json          - all tracks' features at a glance")
    print("  {id}_sections.json    - structural sections (verse/chorus/bridge)")
    print("  {id}_pitches.json     - segment-level pitch & timbre vectors")
    print("  {id}_analysis.json    - complete raw analysis from Spotify")


if __name__ == "__main__":
    main()