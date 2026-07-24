"""
Step 1: Fetch all liked songs from your Spotify account.

Setup:
  1. Go to https://developer.spotify.com/dashboard
  2. Create an app (set redirect URI to http://localhost:8888/callback)
  3. Copy your Client ID and Client Secret
  4. Set environment variables:
       export SPOTIPY_CLIENT_ID='your_client_id'
       export SPOTIPY_CLIENT_SECRET='your_client_secret'
       export SPOTIPY_REDIRECT_URI='http://localhost:8888/callback'

Usage:
  python fetch_liked_songs.py [--output liked_songs.json]

On first run, a browser window opens for Spotify login. After that,
credentials are cached locally (.cache file).
"""

import argparse
import json
import sys
from pathlib import Path

import spotipy
from spotipy.oauth2 import SpotifyOAuth


SCOPE = "user-library-read"
BATCH_SIZE = 50  # Spotify API max per request


def authenticate() -> spotipy.Spotify:
    """Authenticate with Spotify using OAuth2 PKCE flow."""
    try:
        sp = spotipy.Spotify(auth_manager=SpotifyOAuth(scope=SCOPE))
        # Verify credentials work
        sp.current_user()
        return sp
    except spotipy.SpotifyOauthError as e:
        print(f"Authentication failed: {e}")
        print("Make sure SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, and "
              "SPOTIPY_REDIRECT_URI are set.")
        sys.exit(1)


def fetch_all_liked_songs(sp: spotipy.Spotify) -> list[dict]:
    """
    Paginate through all liked songs and return a list of dicts with:
      - track_id: Spotify track ID
      - title: song title
      - artist: primary artist name
      - artists_all: list of all artist names
      - album: album name
      - duration_ms: track duration in milliseconds
      - added_at: when the user liked the song (ISO timestamp)
    """
    songs = []
    offset = 0

    print("Fetching liked songs", end="", flush=True)
    while True:
        results = sp.current_user_saved_tracks(limit=BATCH_SIZE, offset=offset)
        items = results.get("items", [])
        if not items:
            break

        for item in items:
            track = item["track"]
            if track is None:
                continue  # skip unavailable tracks

            songs.append({
                "track_id": track["id"],
                "title": track["name"],
                "artist": track["artists"][0]["name"],
                "artists_all": [a["name"] for a in track["artists"]],
                "album": track["album"]["name"],
                "duration_ms": track["duration_ms"],
                "added_at": item["added_at"],
            })

        offset += BATCH_SIZE
        print(f"\rFetching liked songs... {len(songs)} so far", end="", flush=True)

        if len(items) < BATCH_SIZE:
            break

    print(f"\rFetched {len(songs)} liked songs.                ")
    return songs


def main():
    parser = argparse.ArgumentParser(description="Fetch Spotify liked songs")
    parser.add_argument(
        "--output", "-o",
        default="liked_songs.json",
        help="Output JSON file path (default: liked_songs.json)",
    )
    args = parser.parse_args()

    sp = authenticate()
    songs = fetch_all_liked_songs(sp)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(songs, indent=2, ensure_ascii=False))

    print(f"Saved {len(songs)} songs to {output_path}")

    # Print a quick summary
    artists = {}
    for s in songs:
        artists[s["artist"]] = artists.get(s["artist"], 0) + 1
    top = sorted(artists.items(), key=lambda x: -x[1])[:10]
    print("\nTop 10 artists in your library:")
    for artist, count in top:
        print(f"  {artist}: {count} songs")


if __name__ == "__main__":
    main()