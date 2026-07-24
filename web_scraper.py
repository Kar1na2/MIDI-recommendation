from playwright.sync_api import sync_playwright
import json

def json_to_arr(): 
    with open("K-indie_songs.json") as f: 
        data = json.load(f)
        
    songs = [(entry["title"], entry["artist"]) for entry in data]
    return songs

def main():
    songs = json_to_arr()
    
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
            download.save_as(f"./audio/{download.suggested_filename}")
            
            if len(page.context.pages) > pages_before: 
                for new_page in page.context.pages:
                    if new_page != page:
                        new_page.close()
            
        browser.close()
        
if __name__ == "__main__":
    main()