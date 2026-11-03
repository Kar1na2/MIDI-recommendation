#!/usr/bin/env python3
"""Build exploratory audio/MIDI pattern maps for the complete local corpus.

The source note events are transcription-derived and are not ground truth.
This export labels measurable local traits (event density, pitch register and
pitch motion); it does not claim semantic instrumentation or verified sections.
"""
from __future__ import annotations

import json
import csv
import hashlib
import re
from collections import Counter
from pathlib import Path

import numpy as np
import librosa

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/data/layers"
REPORT = ROOT / "output/analysis/SONG_PATTERN_LAYERS_REPORT.md"

def describe(notes, start, end):
    local = [n for n in notes if start <= n[0] < end]
    density = len(local) / max(end - start, 0.001)
    pitches = [n[1] for n in local]
    if len(local) < 3:
        motion = "undetermined motion"
    else:
        ordered = sorted(local)
        delta = [b[1]-a[1] for a,b in zip(ordered, ordered[1:])]
        up, down = sum(x > 0 for x in delta), sum(x < 0 for x in delta)
        motion = "rising" if up > down * 1.35 else "falling" if down > up * 1.35 else "undulating"
    energy = "dense" if density >= 3.2 else "active" if density >= 1.4 else "open" if density >= .35 else "sparse"
    register = "high register" if pitches and sum(pitches)/len(pitches) >= 67 else "low register" if pitches and sum(pitches)/len(pitches) <= 48 else "mid register"
    return f"{energy} · {motion} · {register}", len(local)

def find_motifs(notes):
    """Find repeated 8-onset top-line interval/rhythm shapes within one song."""
    groups=[]
    for onset,pitch in sorted(notes):
        if groups and onset-groups[-1][0]<=.05:
            groups[-1]=(groups[-1][0],max(groups[-1][1],pitch))
        else:
            groups.append((onset,pitch))
    candidates={}
    for i in range(max(0,len(groups)-7)):
        phrase=groups[i:i+8]
        times=np.diff([n[0] for n in phrase])
        pitches=[n[1] for n in phrase]
        if times.min()<.06 or times.max()>1.5 or len(set(pitches))<3: continue
        beat=float(np.median(times))
        intervals=tuple(np.diff(pitches).tolist())
        rhythm=tuple(int(round(float(t)/beat*4)) for t in times)
        key=(intervals,rhythm)
        candidates.setdefault(key,[]).append(phrase)
    accepted=[]
    for key,phrases in candidates.items():
        kept=[]
        for phrase in phrases:
            if not kept or phrase[0][0]>=kept[-1][-1][0]+.06: kept.append(phrase)
        if len(kept)<2: continue
        first=kept[0]
        intervals=key[0]
        up=sum(d>0 for d in intervals);down=sum(d<0 for d in intervals)
        contour="rising" if up>=5 else "falling" if down>=5 else "turning"
        accepted.append({"label":f"repeated {contour} 8-onset shape","count":len(kept),"pitches":[n[1] for n in first],"onsets":[round(n[0]-first[0][0],3) for n in first],"occurrences":[{"start":round(p[0][0],3),"end":round(p[-1][0],3)} for p in kept],"intervals":list(intervals),"rhythm_quarters_of_median_gap":list(key[1])})
    # Avoid presenting multiple shifted views of the same physical phrase.
    result=[]
    for candidate in sorted(accepted,key=lambda p:(p["count"],p["occurrences"][0]["end"]-p["occurrences"][0]["start"]),reverse=True):
        if any(abs(candidate["occurrences"][0]["start"]-p["occurrences"][0]["start"])<2 for p in result):continue
        result.append(candidate)
        if len(result)==4:break
    return result

def audio_features(path, duration):
    """Return one standardized timbre/harmony/onset feature vector per second."""
    y, sr = librosa.load(path, sr=11025, mono=True)
    hop = 512
    chroma = librosa.feature.chroma_stft(y=y, sr=sr, hop_length=hop, n_fft=2048)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, hop_length=hop, n_fft=2048, n_mfcc=13)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr, n_fft=2048, hop_length=hop)
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)[None, :]
    frames = min(x.shape[1] for x in (chroma, mfcc, rms, centroid, onset))
    raw = np.vstack((chroma[:, :frames], mfcc[:, :frames], rms[:, :frames], centroid[:, :frames], onset[:, :frames]))
    seconds = max(1, int(np.ceil(duration)))
    rows=[]
    for sec in range(seconds):
        a=int(sec*sr/hop); b=min(frames,int((sec+1)*sr/hop))
        rows.append(np.mean(raw[:,a:b],axis=1) if b>a else np.zeros(raw.shape[0]))
    matrix=np.asarray(rows,dtype=float)
    matrix=(matrix-matrix.mean(axis=0))/(matrix.std(axis=0)+1e-6)
    return matrix

def split_score(features, start, end, minimum):
    lo=max(1,int(np.ceil(start+minimum)))
    hi=min(len(features)-1,int(np.floor(end-minimum)))
    best=None
    for cut in range(lo,hi+1):
        left=features[max(0,cut-4):cut].mean(axis=0)
        right=features[cut:min(len(features),cut+4)].mean(axis=0)
        denom=np.linalg.norm(left)*np.linalg.norm(right)
        score=1-float(np.dot(left,right)/denom) if denom>1e-8 else 0.0
        if best is None or score>best[1]: best=(cut,score)
    return best

def hierarchical_boundaries(features, duration):
    # Broad cuts are the strongest separated novelty peaks; each inner ring
    # refines each current interval by its strongest local feature transition.
    candidates=[]
    for cut in range(12,min(len(features)-10,int(duration)-10)):
        left=features[max(0,cut-4):cut].mean(axis=0)
        right=features[cut:min(len(features),cut+4)].mean(axis=0)
        denom=np.linalg.norm(left)*np.linalg.norm(right)
        score=1-float(np.dot(left,right)/denom) if denom>1e-8 else 0.0
        candidates.append((score,cut))
    selected=[]
    for score,cut in sorted(candidates,reverse=True):
        if score < .08: break
        if all(abs(cut-other)>=12 for other in selected): selected.append(cut)
        if len(selected)>=5: break
    boundaries=sorted(set([0.0,*[float(x) for x in selected],float(duration)]))
    levels=[boundaries]
    for minimum in (8.0,4.0,2.5,1.5):
        refined=set(boundaries)
        for start,end in zip(boundaries,boundaries[1:]):
            if end-start < minimum*2: continue
            candidate=split_score(features,int(start),min(int(end),len(features)),minimum)
            if candidate and candidate[1]>=.06 and start+minimum<=candidate[0]<=end-minimum:
                refined.add(float(candidate[0]))
        boundaries=sorted(refined)
        if len(boundaries)-1>96: break
        if len(refined)==len(levels[-1]) and len(levels)>=3: break
        levels.append(boundaries)
    return levels

def main():
    published = json.loads((ROOT / "docs/data/manifest.json").read_text(encoding="utf-8"))
    by_title={s["title"]:s for s in published}
    sources={p.stem:p for folder in ("audio","k-indie","liked-audio") for p in (ROOT/folder).glob("*.mp3")}
    events={}
    with (ROOT/"output/note_events_other.csv").open(encoding="utf-8",newline="") as stream:
        for row in csv.DictReader(stream):
            events.setdefault(row["song"],[]).append((float(row["onset"]),int(row["pitch"])))
    manifest=[]
    for midi in sorted((ROOT/"merged").glob("*.mid")):
        title=midi.stem
        if title not in sources:
            raise FileNotFoundError(f"No source audio for {title}")
        item=dict(by_title.get(title,{"song_id":"layer-"+hashlib.sha1(title.encode("utf-8")).hexdigest()[:14],"title":re.sub(r" \[[A-Za-z0-9]{22}\]$","",title)}))
        item["source_title"]=title
        item["audio_path"]=sources[title]
        item["audio_url"]="audio/"+item["song_id"]+".mp3" if title in by_title else None
        manifest.append(item)
    cache=ROOT/"output/analysis/song_layers_cache"
    cache.mkdir(parents=True,exist_ok=True)
    records = []
    for index,item in enumerate(manifest,1):
        notes=sorted(events.get(item["source_title"],[]))
        audio_path=item["audio_path"]
        stat=audio_path.stat()
        duration=float(item.get("duration") or librosa.get_duration(path=audio_path))
        cached=cache/f"{item['song_id']}.npz"
        features=None
        if cached.exists():
            with np.load(cached) as saved:
                if saved["size"].item()==stat.st_size and saved["mtime"].item()==stat.st_mtime_ns:
                    features=saved["features"]
        if features is None:
            features=audio_features(audio_path,duration)
            np.savez_compressed(cached,features=features,size=stat.st_size,mtime=stat.st_mtime_ns)
        boundaries_by_layer=hierarchical_boundaries(features,duration) if features is not None else []
        if not boundaries_by_layer:
            boundaries_by_layer=[[float(x) for x in range(0,int(duration),32)]+[duration]]
        layers=[]
        for depth,bounds in enumerate(boundaries_by_layer[:5],1):
            segments=[]
            for start,end in zip(bounds,bounds[1:]):
                label,count=describe(notes,start,end)
                segments.append({"start":round(start,2),"end":round(end,2),"label":label,"events":count,"change_strength":round(float(np.linalg.norm(features[min(int(end),len(features)-1)]-features[max(0,int(start))])) ,3) if features is not None else 0})
            # A repeated descriptor flags similar coarse timbre/event profiles,
            # not a verified note-for-note melodic repetition.
            profiles=[]
            for seg in segments:
                if features is None: profiles.append(None); continue
                a=max(0,int(seg["start"]));b=min(len(features),max(a+1,int(seg["end"])))
                profiles.append(features[a:b].mean(axis=0))
            for i,seg in enumerate(segments):
                seg["repeat"] = False
                for j,profile in enumerate(profiles):
                    if i==j or profile is None or abs((seg["end"]-seg["start"])-(segments[j]["end"]-segments[j]["start"]))>max(3,(seg["end"]-seg["start"])*.35): continue
                    den=np.linalg.norm(profiles[i])*np.linalg.norm(profile)
                    if den>1e-8 and float(np.dot(profiles[i],profile)/den)>.92:
                        seg["repeat"]=True;break
                seg["index"]=i+1
            layers.append({"depth":depth,"name":["Song arc","Sections","Phrase patterns","Motifs","Micro-motifs"][depth-1],"segments":segments})
        records.append({"song_id":item["song_id"],"title":item["title"],"duration":round(duration,2),"layers":layers,"event_count":len(notes),"audio_url":item["audio_url"],"motifs":find_motifs(notes)})
        if index%25==0: print(f"Analyzed {index}/{len(manifest)}",flush=True)
    (OUT/"songs").mkdir(parents=True,exist_ok=True)
    index=[]
    for record in records:
        (OUT/"songs"/f"{record['song_id']}.json").write_text(json.dumps(record,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
        index.append({"song_id":record["song_id"],"title":record["title"],"duration":record["duration"]})
    (OUT/"manifest.json").write_text(json.dumps(index,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    layer_counts=[len(s["layers"]) for s in records]
    sizes=[len(s["layers"][0]["segments"]) for s in records if s["layers"]]
    labels=Counter(seg["label"] for s in records for seg in s["layers"][min(2,len(s["layers"])-1)]["segments"])
    lines=["# Song pattern layers: corpus observations", "", f"Generated from the complete local corpus on 2026-11-03. **{len(records)} songs** were processed from {len(records)} local audio files and matching note-event exports.", "", "## How to read these results", "", "The map is an exploratory hierarchy with three to five levels. Boundaries are candidate change points from one-second audio summaries of chroma, MFCC timbre, loudness, spectral centroid, and onset strength. Each segment is described using the catalog's transcribed `other`-stem note-event density, adjacent-event pitch movement, and mean register. Similar audio-feature profiles at the same level are marked as recurrence candidates; this does not verify that the same notes or rhythm repeat. The boundaries can reflect arrangement, harmony, timbre, or production changes and need listening-based review before they are treated as musical section labels.", "", "Bass, drums, and vocals are not separately included in these web note-event exports. Full-mix audio features can suggest that the sound changed but cannot reliably name which instrument entered. The pattern catalog covers the complete local corpus of 1,128 songs. The existing listening catalog still has 75 published audio previews; other entries show their layer maps without publishing additional audio.", "", "## Corpus observations", "", f"- Average generated hierarchy depth (viewer design, not a measured natural number of musical layers): **{sum(layer_counts)/max(len(layer_counts),1):.2f} layers/song** (range {min(layer_counts)}–{max(layer_counts)}).", f"- Average number of broad arc windows: **{sum(sizes)/max(len(sizes),1):.1f} per song**.", f"- Total transcribed `other`-stem note events inspected: **{sum(s['event_count'] for s in records):,}**.", "- Frequently observed segment descriptors (these are coarse event-shape tags, not genre labels):", ""]
    for label,count in labels.most_common(8): lines.append(f"  - `{label}` — {count:,} segments at hierarchy level 3")
    lines += ["", f"- Songs with at least one conservative recurring 8-onset note shape: **{sum(bool(s.get('motifs')) for s in records):,} / {len(records):,}**.", f"- Saved within-song note-shape candidates: **{sum(len(s.get('motifs',[])) for s in records):,}** (up to four per song).", "", "## Method and evidence limits", "", "Audio is reduced to mono at 11,025 Hz. Features are summarized every second and standardized within each song. Novelty uses cosine distance between the four-second feature means on each side of a candidate boundary. The outer ring retains up to five strong, separated boundaries; inner rings recursively refine them. Thus the depth is a designed view capped at five, not a discovery that a song intrinsically has that many musical layers. Timing resolution is about one second.", "", "The note-shape pass reduces polyphony to the highest note near each onset (50 ms grouping). It searches eight-onset phrases with at least three different pitches and no inter-onset gap above 1.5 seconds. A match retains all seven semitone intervals plus inter-onset gaps quantized to quarters of the local median gap. Matches can be transposed and proportionally stretched in time; at least two nonoverlapping occurrences are required. The JSON retains pitches and relative onset times from the first instance and the times of every occurrence. This is symbolic evidence for a candidate top-line pattern, not a verified isolated melody. A rising event tag by itself is not proof of an ascending arpeggio.", "", "No human boundary-accuracy score has been measured for these maps. The specific Spotify instrumental in the prompt is not a separately ingested audio file here; the catalog's 1nonly music-video version has different source timing and should not inherit those timestamps without checking.", ""]
    lines += ["", "## Tidal, MIDI, and audio", "", "TidalCycles is a live pattern language and playback/control environment. It can express note/event patterns and send MIDI through SuperDirt/SuperCollider; a MIDI-to-Tidal converter translates symbolic note events to that representation. Tidal's default SuperDirt sampler can also trigger audio samples, including user-provided WAV samples, but this is sample playback rather than structural analysis of an arbitrary song. Tidal does not natively analyze or transcribe an arbitrary WAV/MP3 into musical sections. Use an audio-analysis pipeline (for example, [librosa's recurrence and segmentation tools](https://librosa.org/doc/main/segment.html) with spectral/chroma/onset features) to find candidate audio sections, then align those times with stem audio and MIDI. MIDI is useful for pitch/rhythm detail; the original audio remains necessary for timbre, layer-entry, and mix changes. See [Tidal's MIDI guide](https://tidalcycles.org/docs/configuration/MIDIOSC/midi/), [Tidal's sample library guide](https://tidalcycles.org/docs/configuration/AudioSamples/default_library/), and the [Tyler McLaughlin MIDI-to-Tidal converter](https://github.com/TylerMclaughlin/midi_to_tidalcycles).", "", "## Next accuracy step", "", "Listen through the candidate boundaries and annotate a diverse validation sample, beginning with the reference example's 0:00–0:55 outline and repeated arpeggio phrases. Add per-stem activity features so detected sound changes can be attributed to bass, drums, or other instrumentation. Only treat a repeated passage as a musical motif after its note/rhythm sequence is aligned and verified.", ""]
    lines += ["## Other pattern languages with audio support", "", "- [Strudel](https://strudel.cc/learn/samples/) provides Tidal-style patterns in JavaScript and can load WAV, MP3, and OGG samples. Its slice, chop, and arrangement functions are well suited to representing a song as reusable audio excerpts. It does not automatically discover a song's structure.", "- [Sonic Pi](https://sonic-pi.net/tutorial-03.html) can sequence and transform external audio samples; its current tutorial lists WAV, AIFF, OGG, FLAC, and MP3. Format support may depend on the installed build.", "- [SuperCollider buffers](https://doc.sccode.org/Classes/Buffer.html) support loading and processing audio, and its Patterns system can sequence sound events. It offers low-level audio analysis building blocks, rather than a ready-made hierarchical song labeler.", "", "For this phase, a neutral JSON hierarchy is more useful than choosing a playback language: it retains explicit segment times, descriptions, and recurrence evidence, and can later be exported to Tidal/Strudel or used for model features. No classifier or cross-song comparison was added.", ""]
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text("\n".join(lines),encoding="utf-8")
    print(f"wrote {OUT} ({len(records)} songs); {REPORT}")

if __name__ == "__main__": main()
