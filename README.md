# MIDI-recommendation
Recommending new songs through MIDI files

## Recreation 

If you wish to recreate what I have done then do the following 

**SETUP**
1. `pip install -r requirements.txt` 
2. Go to [spotify dashboard](https://developer.spotify.com/dashboard) and create an app 
3. Export your credentials 
```bash
export SPOTIPY_CLIENT_ID=''
export SPOTIPY_CLIENT_SECRET=''
export SPOTIPY_REDIRECT_URI=''
```

**RUN**
- `python fetch_liked_songs.py --output liked_songs.json` 
- `python convert_to_midi.py --input-dir ./audio --output-dir ./midi`

## Approach 

In terms of system design I'll be doing the following 
- Postgres SQL will be my primary database as MIDI files are stored as binary and they'll be the main highlight of this project 
    - I'll also be using pgvector extension for similarity queries 

## Things I have done 

**[7/23 ~ 24]**
- Set up the project before starting on the web 
- 36 songs from my own spotify playlist to use as demo 
- 30 songs from my k-indie playlist to use as a more curated demo on the MIDI analysis

## Things I will do 