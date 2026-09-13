"""
Step 2: Download audio for a list of (title, artist) songs by scraping
mp3juice.sc via a headless(-able) browser.

Takes the JSON song list produced by fetch_liked_songs.py / fetch_playlist_songs.py
(a list of {"title": ..., "artist": ...} entries) and downloads an mp3 for
each into an output directory, to be fed into convert.py / extract_audio.py.

Usage:
  python download_songs.py liked_songs.json ./audio

Moved here from the repo root (was `web_scraper.py`, undocumented) - same
one-off-ingestion-tool category as its siblings in this directory, not part
of the resumable scripts/ pipeline.
"""
from playwright.sync_api import sync_playwright
import json
import argparse
import os

def json_to_arr(json_file): 
    with open(json_file) as f: 
        data = json.load(f)
        
    songs = [(entry["title"], entry["artist"]) for entry in data]
    return songs

def main():
    parser = argparse.ArgumentParser(description="Download songs from a JSON list.")
    parser.add_argument("json_file", help="Path to the JSON file containing song entries")
    parser.add_argument("output_dir", help="Directory to save downloaded files")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    songs = json_to_arr(args.json_file)
    
    with sync_playwright() as p: 
        print("Staring...")
        browser = p.chromium.launch(headless=False) 
        page = browser.new_page()
        
        for entry in songs: 
            title, artist = entry
            page.goto("https://mp3juice.sc/")
            page.fill('form input#query', f"{title} {artist}")
            page.click('form div.form__action button[type="submit"]')
            
            page.wait_for_selector('div.results div.result')
            page.click('div.result:first-child a[data-format="mp3"]')
            
            try: 
                page.wait_for_selector('div.result:first-child a[data-format="mp3"].started', timeout=30000)
            except:
                print(f"loading never started for {title} by {artist}")
                browser.close()
                exit()
            
            try:
                page.wait_for_selector('div.result:first-child a[data-format="mp3"]:not(.started)[href^="http"]', timeout=30000)
            except: 
                print(f"loading took longer than 30 seconds for {title} by {artist}")
                browser.close()
                exit()
                
            pages_before = len(page.context.pages)
            
            with page.expect_download(timeout=30000) as download_info:
                page.click('div.result:first-child a[data-format="mp3"]')

            download = download_info.value
            download.save_as(os.path.join(args.output_dir, download.suggested_filename))
            
            if len(page.context.pages) > pages_before: 
                for new_page in page.context.pages:
                    if new_page != page:
                        new_page.close()
            
        browser.close()
        
if __name__ == "__main__":
    main()