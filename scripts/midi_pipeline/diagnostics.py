"""Inspection aids only: these plots/audio never create or modify predicted events."""
import html
import json
from pathlib import Path

import numpy as np


def create(output, document, analysis):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import soundfile as sf
    from matplotlib.collections import LineCollection

    output = Path(output)
    waveform, rate = sf.read(output / "input.wav", always_2d=True)
    waveform = waveform.mean(axis=1)
    start, end = document["source_window"]
    notes = [n for n in document["notes"] if n["in_source_window"]]
    colors = plt.get_cmap("tab10")
    def roll(axis):
        for track in document["tracks"]:
            selected = [n for n in notes if n["track"] == track["track"]]
            segments = [[(n["onset"], n["pitch"]), (n["offset"], n["pitch"])] for n in selected]
            axis.add_collection(LineCollection(segments, colors=[colors(track["track"] % 10)], linewidths=3,
                                              label=f"track {track['track']} program {track['program']}" +
                                              (" drums" if track["is_drum"] else "")))
        axis.set_xlim(start, end)
        axis.set_ylim(min([n["pitch"] for n in notes] or [48]) - 2, max([n["pitch"] for n in notes] or [84]) + 2)
        axis.set_ylabel("MIDI pitch / drum key")
        if notes:
            axis.legend(loc="upper right", fontsize=7)
        axis.grid(alpha=.2)
    fig, axis = plt.subplots(figsize=(14, 5))
    roll(axis)
    axis.set_xlabel("Source recording seconds (native, unquantized timings)")
    fig.tight_layout()
    fig.savefig(output / "piano-roll.png", dpi=140)
    plt.close(fig)
    fig, axes = plt.subplots(3, 1, figsize=(14, 8), sharex=True)
    stride = max(1, len(waveform) // 50000)
    axes[0].plot(start + np.arange(0, len(waveform), stride) / rate, waveform[::stride], linewidth=.4)
    axes[0].set_ylabel("Source waveform")
    roll(axes[1])
    for n in notes:
        axes[2].plot([n["onset"], n["onset"]], [n["track"] - .3, n["track"] + .3],
                     color=colors(n["track"] % 10), linewidth=.8)
    axes[2].set_ylabel("Predicted attack / track")
    axes[2].set_xlabel("Source recording seconds")
    axes[2].set_xlim(start, end)
    fig.tight_layout()
    fig.savefig(output / "audio-aligned.png", dpi=140)
    plt.close(fig)
    fig, axis = plt.subplots(figsize=(14, 3))
    for track in analysis["tracks"]:
        axis.eventplot([n["onset"] for n in notes if n["track"] == track["track"]],
                       lineoffsets=track["track"], colors=[colors(track["track"] % 10)])
    axis.set(xlim=(start, end), xlabel="Source recording seconds", ylabel="Track", title="Predicted MIDI onsets")
    fig.tight_layout()
    fig.savefig(output / "rhythm-onsets.png", dpi=140)
    plt.close(fig)

    # Neutral sine rendering helps compare attacks/pitches. It is not reconstructed audio.
    preview_rate = 16000
    preview = np.zeros(int((end - start) * preview_rate), dtype=np.float32)
    for n in notes:
        if n["is_drum"]:
            continue
        first = max(0, int((n["onset"] - start) * preview_rate))
        stop = min(len(preview), int((n["offset"] - start) * preview_rate))
        if stop <= first:
            continue
        t = np.arange(stop - first) / preview_rate
        envelope = np.minimum(t / .005, 1) * np.minimum((len(t) / preview_rate - t) / .01, 1)
        preview[first:stop] += .08 * envelope * np.sin(2 * np.pi * 440 * 2 ** ((n["pitch"] - 69) / 12) * t)
    if np.max(np.abs(preview), initial=0) > .9:
        preview *= .9 / np.max(np.abs(preview))
    sf.write(output / "prediction-sine-preview.wav", preview, preview_rate)

    data = json.dumps({"start": start, "end": end, "notes": notes}).replace("<", "\\u003c")
    page = """<!doctype html><meta charset="utf-8"><title>MIDI prediction review</title>
<style>body{font:16px system-ui;max-width:1300px;margin:24px auto;background:#fafafa}audio{width:100%}
canvas,img{width:100%;background:white}pre{white-space:pre-wrap}label{display:block;margin-top:16px}</style>
<h1>Audio-aligned MIDI prediction review</h1>
<p>Unverified model output. Programs are predicted; velocity is constant. No listening labels are used.</p>
<label>Source audio — shared recording clock below</label><audio id="audio" controls src="input.wav"></audio>
<label>Neutral sine rendering of pitched MIDI (no percussion, no instrument timbre)</label>
<audio controls src="prediction-sine-preview.wav"></audio>
<p id="clock"></p><canvas id="roll" width="1300" height="430"></canvas>
<p>Click the piano roll to seek the source audio. Raw MIDI starts at crop-relative zero; JSON uses original recording seconds.</p>
<p><a href="raw.mid">Native MIDI</a> · <a href="notes.json">Canonical notes</a> · <a href="analysis.json">Candidates and uncertainties</a></p>
<img src="audio-aligned.png" alt="Source waveform aligned with MIDI notes and onsets">
<img src="rhythm-onsets.png" alt="Predicted MIDI attack times">
<h2>Unresolved claims</h2><pre id="unresolved"></pre>
<script>const data=DATA; const unresolved=UNRESOLVED;
const a=document.getElementById('audio'),c=document.getElementById('roll'),x=c.getContext('2d');
const px=t=>(t-data.start)/(data.end-data.start)*c.width;
function draw(){x.clearRect(0,0,c.width,c.height);x.fillStyle='#eee';
for(let p=24;p<=108;p+=12){let y=(108-p)/84*400;x.fillRect(0,y,c.width,1);x.fillStyle='#888';x.fillText(''+p,2,y);x.fillStyle='#eee'}
for(const n of data.notes){x.fillStyle=`hsl(${n.track*67%360} 65% 40%)`;x.fillRect(px(n.onset),(108-n.pitch)/84*400,Math.max(2,px(n.offset)-px(n.onset)),4)}
x.fillStyle='#d22';x.fillRect(px(data.start+a.currentTime),0,2,c.height);
document.getElementById('clock').textContent=(data.start+a.currentTime).toFixed(3)+' source seconds';requestAnimationFrame(draw)}
c.onclick=e=>{a.currentTime=(e.clientX-c.getBoundingClientRect().left)/c.getBoundingClientRect().width*(data.end-data.start)};
document.getElementById('unresolved').textContent=unresolved.join('\n');draw();</script>"""
    page = page.replace("DATA", data).replace("UNRESOLVED", json.dumps(analysis["unresolved"]).replace("<", "\\u003c"))
    (output / "review.html").write_text(page, encoding="utf-8")
