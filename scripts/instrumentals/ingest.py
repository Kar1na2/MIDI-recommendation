"""Resolve instrumental recordings for the saved Spotify library into a separate database.

python -m scripts.instrumentals.ingest --pinned-only --download
python -m scripts.instrumentals.ingest --workers 4 --download
No original mix is silently substituted for a missing instrumental.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import sqlite3
import subprocess
import time
import unicodedata
from pathlib import Path

import yt_dlp

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "instrumentals"
DB = LIBRARY / "catalog.sqlite"
INSTRUMENTAL_MARKERS = ("instrumental", "inst ver", "off vocal", "offvocal", "no vocals", "伴奏", "纯音乐", "インスト", "mr removed")
UNWANTED = ("cover", "female key", "male key", "piano version", "guitar version", "orchestral version", "type beat", "1 hour", "1hour", "slowed", "sped up", "nightcore", "reaction", "tutorial", "remake", "8d audio")

def normalize(text):
    text=unicodedata.normalize("NFKC",str(text)).casefold()
    return " ".join(re.sub(r"[^\w]+"," ",text).split())

def contains_phrase(text, phrase):
    return f" {normalize(phrase)} " in f" {normalize(text)} "

def connect():
    LIBRARY.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DB)
    db.row_factory=sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
      CREATE TABLE IF NOT EXISTS tracks (
        track_id TEXT PRIMARY KEY, title TEXT NOT NULL, artist TEXT NOT NULL,
        duration_expected REAL, status TEXT NOT NULL DEFAULT 'pending',
        reason TEXT, source_url TEXT, source_title TEXT, source_kind TEXT,
        match_score REAL, duration REAL, mp3_path TEXT, sha256 TEXT,
        candidates_json TEXT, updated_at TEXT
      );
      CREATE TABLE IF NOT EXISTS assets (
        track_id TEXT NOT NULL, part TEXT NOT NULL, path TEXT NOT NULL,
        duration REAL, bytes INTEGER, PRIMARY KEY(track_id,part)
      );
      CREATE TABLE IF NOT EXISTS analysis (
        track_id TEXT PRIMARY KEY, method_version TEXT, result_json TEXT NOT NULL
      );
    """)
    return db

def initialize(db, songs):
    for song in songs:
        db.execute("INSERT INTO tracks(track_id,title,artist,duration_expected) VALUES(?,?,?,?) ON CONFLICT(track_id) DO UPDATE SET title=excluded.title,artist=excluded.artist,duration_expected=excluded.duration_expected",
                   (song["track_id"],song["title"],song["artist"],song.get("duration_ms",0)/1000))
    db.commit()

def evaluate(song, entry):
    title=normalize(entry.get("title",""))
    full=normalize(title+" "+entry.get("uploader","")+" "+entry.get("channel",""))
    target=normalize(song["title"])
    artist=normalize(song["artist"])
    target_tokens={t for t in target.split() if t not in {"instrumental","official","audio","feat","the","a","ver","version"}}
    if not any(normalize(marker) in title for marker in INSTRUMENTAL_MARKERS): return None
    if any(contains_phrase(title, marker) and not contains_phrase(target, marker) for marker in UNWANTED): return None
    overlap=sum(t in title.split() for t in target_tokens)/max(1,len(target_tokens))
    artist_tokens=[t for t in artist.split() if len(t)>1]
    artist_match=sum(t in full.split() for t in artist_tokens)/max(1,len(artist_tokens))
    if overlap<.8 or artist_match<.5: return None
    expected=(song.get("duration_ms") or 0)/1000
    actual=entry.get("duration") or 0
    duration_score=.5
    if expected and actual:
        if abs(expected-actual)>max(30,expected*.2): return None
        duration_score=max(0,1-abs(expected-actual)/max(30,expected*.2))
    score=.55*overlap+.25*artist_match+.2*duration_score
    if any(t in full for t in ("official instrumental","provided to youtube","topic")): score+=.03
    return min(score,1)

def ydl_options():
    return {"quiet":True,"no_warnings":True,"noprogress":True,"noplaylist":True,
            "socket_timeout":20,"retries":1,"extractor_retries":1}

def resolve(song, override, download, cached=None, search_count=20):
    key=song["track_id"]
    result={"track_id":key,"updated_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}
    try:
        choices=[]
        pinned_url = "https://www.youtube.com/watch?v=" + override["youtube_id"] if override else None
        if cached and cached.get("source_url") and (not pinned_url or pinned_url == cached["source_url"]):
            url=cached["source_url"]
            score=float(cached.get("match_score") or 0)
            result.update(source_kind=cached.get("source_kind"),candidates_json=cached.get("candidates_json"))
            result.update(status="source_found",source_url=url,source_title=cached.get("source_title"),match_score=score,reason=None)
        else:
            opts=ydl_options()
            opts.update({"extract_flat":"in_playlist","skip_download":True})
            with yt_dlp.YoutubeDL(opts) as ydl:
                if override:
                    url="https://www.youtube.com/watch?v="+override["youtube_id"]
                    entry=ydl.extract_info(url,download=False)
                    candidate=(1.0,entry)
                    result["source_kind"]="user_provided_instrumental"
                    choices=[{"id":entry.get("id"),"title":entry.get("title"),"duration":entry.get("duration"),"score":1.0}]
                else:
                    query=f"{song['artist']} - {song['title']} - instrumental"
                    response=ydl.extract_info(f"ytsearch{max(5,min(search_count,50))}:"+query,download=False)
                    entries=[e for e in (response or {}).get("entries",[]) if e and e.get("id")]
                    ranked=[]
                    for entry in entries:
                        score=evaluate(song,entry)
                        choices.append({"id":entry.get("id"),"title":entry.get("title"),"duration":entry.get("duration"),"score":score})
                        if score is not None: ranked.append((score,entry))
                    if not ranked:
                        return result|{"status":"not_found","source_url":None,"source_title":None,"source_kind":None,"match_score":None,"reason":"No search result passed instrumental marker, title, artist, and duration checks.","candidates_json":json.dumps(choices,ensure_ascii=False)}
                    candidate=max(ranked,key=lambda x:x[0])
                    result["source_kind"]="metadata_matched_instrumental"
            score,entry=candidate
            url="https://www.youtube.com/watch?v="+entry["id"]
            result.update(status="source_found",source_url=url,source_title=entry.get("title"),match_score=round(score,4),candidates_json=json.dumps(choices,ensure_ascii=False),reason=None)
        if not download: return result
        final=LIBRARY/"audio"/(key+".mp3")
        final.parent.mkdir(parents=True,exist_ok=True)
        # Successful records are skipped in main. A fresh attempt always replaces
        # its MP3 only after download succeeds, so a changed source can never
        # inherit bytes from an earlier rejected match.
        opts=ydl_options()
        opts.update({"format":"bestaudio/best","outtmpl":str(LIBRARY/".partial"/key/"source.%(ext)s"),
                     "postprocessors":[{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"192"}]})
        with yt_dlp.YoutubeDL(opts) as ydl: ydl.download([url])
        partial=LIBRARY/".partial"/key/"source.mp3"
        if not partial.is_file(): raise RuntimeError("Downloader returned without an MP3 file")
        partial.replace(final)
        duration=float(subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(final)],text=True).strip())
        digest=hashlib.sha256(final.read_bytes()).hexdigest()
        result.update(status="downloaded",mp3_path=final.relative_to(ROOT).as_posix(),duration=duration,sha256=digest)
        return result
    except Exception as error:
        result.update(status="download_failed" if result.get("source_url") else "search_failed",reason=f"{type(error).__name__}: {error}"[:1500])
        return result

def save(db, result):
    fields=[k for k in result if k!="track_id"]
    db.execute("UPDATE tracks SET "+",".join(f"{k}=?" for k in fields)+" WHERE track_id=?",[result[k] for k in fields]+[result["track_id"]])
    db.commit()

def export_status(db):
    records=[dict(r) for r in db.execute("SELECT * FROM tracks ORDER BY artist,title")]
    (LIBRARY/"catalog.json").write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding="utf-8")

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--playlist",type=Path,default=ROOT/"archive/liked_songs.json")
    parser.add_argument("--workers",type=int,default=4)
    parser.add_argument("--limit",type=int)
    parser.add_argument("--pinned-only",action="store_true")
    parser.add_argument("--download",action="store_true")
    parser.add_argument("--retry-failed",action="store_true")
    parser.add_argument("--retry-status",action="append",choices=("not_found","search_failed","download_failed","source_review_required"),default=[])
    parser.add_argument("--search-count",type=int,default=20)
    args=parser.parse_args()
    songs=json.loads(args.playlist.read_text(encoding="utf-8"))
    overrides=json.loads((LIBRARY/"source_overrides.json").read_text(encoding="utf-8"))
    db=connect(); initialize(db,songs)
    rows={r["track_id"]:dict(r) for r in db.execute("SELECT * FROM tracks")}
    states={key:value["status"] for key,value in rows.items()}
    todo=[]
    for song in songs:
        state=states[song["track_id"]]
        if args.retry_status and state not in args.retry_status:continue
        if args.pinned_only and song["track_id"] not in overrides:continue
        if state in {"downloaded","separating","separated","ready","separation_failed","analysis_failed"}:continue
        if state in {"not_found","search_failed","download_failed","source_review_required"} and not (args.retry_failed or state in args.retry_status):continue
        if state=="source_found" and not args.download:continue
        todo.append(song)
    todo.sort(key=lambda s:(s["track_id"] not in overrides,s["artist"],s["title"]))
    if args.limit is not None:todo=todo[:args.limit]
    print(f"{len(songs)} Spotify tracks; resolving {len(todo)} instrumental sources",flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,min(args.workers,8))) as pool:
        pending={pool.submit(resolve,s,overrides.get(s["track_id"]),args.download,
                             rows.get(s["track_id"]) if states.get(s["track_id"])=="source_found" else None,
                             args.search_count):s for s in todo}
        for i,future in enumerate(concurrent.futures.as_completed(pending),1):
            song=pending[future];result=future.result();save(db,result)
            print(f"[{i}/{len(todo)}] {song['artist']} — {song['title']}: {result['status']}",flush=True)
    export_status(db)
    print(dict(db.execute("SELECT status,count(*) FROM tracks GROUP BY status").fetchall()),flush=True)

if __name__=="__main__":main()
