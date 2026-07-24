"""
Fetch all songs from a specific Spotify playlist.

Setup: Same Spotify credentials as fetch_liked_songs.py

Usage:
  # By playlist URL (grab from Spotify app: Share > Copy link)
  python fetch_playlist_songs.py "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"

  # By playlist ID
  python fetch_playlist_songs.py 37i9dQZF1DXcBWIGoYBM5M

  # Custom output path
  python fetch_playlist_songs.py 37i9dQZF1DXcBWIGoYBM5M --output my_playlist.json

  # List your own playlists to find IDs
  python fetch_playlist_songs.py --list
"""

import argparse
import json
import re
import sys
from pathlib import Path

import spotipy
from spotipy.oauth2 import SpotifyOAuth


SCOPE = "playlist-read-private,playlist-read-collaborative"
BATCH_SIZE = 100  # Spotify max for playlist tracks


def authenticate() -> spotipy.Spotify:
    try:
        sp = spotipy.Spotify(auth_manager=SpotifyOAuth(scope=SCOPE))
        sp.current_user()
        return sp
    except spotipy.SpotifyOauthError as e:
        print(f"Authentication failed: {e}")
        sys.exit(1)


def extract_playlist_id(input_str: str) -> str:
    """Extract playlist ID from a URL or return as-is if already an ID."""
    # Match URLs like https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=...
    match = re.search(r"playlist/([a-zA-Z0-9]+)", input_str)
    if match:
        return match.group(1)
    # Assume it's already a raw ID
    return input_str.strip()


def list_user_playlists(sp: spotipy.Spotify):
    """Print all playlists the user owns or follows."""
    print("Your playlists:\n")
    offset = 0
    total = 0

    while True:
        results = sp.current_user_playlists(limit=50, offset=offset)
        items = results.get("items", [])
        if not items:
            break

        for pl in items:
            total += 1
            owner = pl["owner"]["display_name"]
            count = pl["tracks"]["total"]
            print(f"  {total}. {pl['name']} ({count} tracks) — by {owner}")
            print(f"     ID: {pl['id']}")
            print()

        offset += 50
        if len(items) < 50:
            break

    print(f"Total: {total} playlists")


def fetch_playlist_songs(sp: spotipy.Spotify, playlist_id: str) -> tuple[dict, list[dict]]:
    """
    Fetch all tracks from a playlist.
    Returns (playlist_info, songs_list).
    """
    # Get playlist metadata
    try:
        playlist = sp.playlist(playlist_id, market="from_token")
    except spotipy.SpotifyException as e:
        if e.http_status == 404:
            print(f"Playlist not found: {playlist_id}")
            print("Make sure the playlist is public or you follow it.")
            sys.exit(1)
        raise

    playlist_info = {
        "id": playlist_id,
        "name": playlist.get("name", "Unknown"),
        "description": playlist.get("description", ""),
        "owner": playlist.get("owner", {}).get("display_name", "Unknown"),
        "total_tracks": playlist.get("tracks", {}).get("total", 0),
    }

    print(f"Playlist: {playlist_info['name']}")
    print(f"By: {playlist_info['owner']}")
    print(f"Tracks: {playlist_info['total_tracks']}\n")

    # Paginate through tracks
    songs = []
    offset = 0

    while True:
        results = sp.playlist_items(
            playlist_id, limit=BATCH_SIZE, offset=offset,
            additional_types=["track"],
        )
        items = results.get("items", [])
        if not items:
            break

        for item in items:
            # API returns track data under "track" or "item" depending on version
            track = item.get("track") or item.get("item")
            if track is None:
                continue
            # "track" can be a boolean flag in newer responses — skip if so
            if isinstance(track, bool):
                track = item.get("item")
            if track is None or not isinstance(track, dict):
                continue
            if track.get("id") is None:
                continue  # skip local files

            songs.append({
                "track_id": track["id"],
                "title": track["name"],
                "artist": track["artists"][0]["name"],
                "artists_all": [a["name"] for a in track["artists"]],
                "album": track["album"]["name"],
                "duration_ms": track.get("duration_ms"),
                "added_at": item.get("added_at"),
            })

        print(f"  Fetched {len(songs)} tracks...", flush=True)
        offset += BATCH_SIZE

        if len(items) < BATCH_SIZE:
            break

    return playlist_info, songs


def main():
    parser = argparse.ArgumentParser(description="Fetch songs from a Spotify playlist")
    parser.add_argument(
        "playlist", nargs="?",
        help="Playlist URL or ID (e.g. https://open.spotify.com/playlist/xxxxx or just xxxxx)",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output JSON file path (default: playlist_{id}.json)",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List your playlists and their IDs, then exit",
    )
    args = parser.parse_args()

    sp = authenticate()

    if args.list:
        list_user_playlists(sp)
        return

    if not args.playlist:
        parser.error("Provide a playlist URL/ID, or use --list to see your playlists")

    playlist_id = extract_playlist_id(args.playlist)
    playlist_info, songs = fetch_playlist_songs(sp, playlist_id)

    output_path = Path(args.output or f"playlist_{playlist_id}.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_data = {
        "playlist": playlist_info,
        "songs": songs,
    }
    output_path.write_text(json.dumps(output_data, indent=2, ensure_ascii=False))

    print(f"\nSaved {len(songs)} songs to {output_path}")

    # The songs list is compatible with extract_audio_analysis.py
    # Save a flat version for piping into the analysis script
    flat_path = output_path.with_stem(output_path.stem + "_songs")
    flat_path.write_text(json.dumps(songs, indent=2, ensure_ascii=False))
    print(f"Flat songs list (for extract_audio_analysis.py): {flat_path}")


if __name__ == "__main__":
    main()