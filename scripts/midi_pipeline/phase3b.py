"""View-only Songscription screenshot comparison and human review tools."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from .phase3 import BASE
from .store import save

PUBLIC_URL = "https://www.songscription.ai/t/tE2Sw3ABTIqOj_LUifRVKw?view=sheet&ref=cp"
SCHEMA = "phase3b-manual-comparison/1"


def initial_review(root=BASE):
    root = Path(root)
    comparison = json.loads((root / "comparison.json").read_text(encoding="utf-8"))
    return {
        "schema": SCHEMA,
        "track_id": "6rQCPgrLJMQavladOGpwZt",
        "focus_window_seconds": [6.0, 18.0],
        "reference_audio_sha256": comparison["reference_audio_sha256"],
        "songscription_reference": {
            "public_url": PUBLIC_URL,
            "view_mode": "public view-only score and piano roll",
            "export_status": "MIDI download unavailable behind paywall; no export attempted",
            "screenshot_alignment": "unknown unless a reviewer adds explicit anchors",
            "visible_observations": [
                {
                    "observation_id": "songscription-visible-01",
                    "source": "public score viewer, visually inspected",
                    "scope": "whole 30-second score; individual note times unknown",
                    "observation": "The view displays a notated grand staff across numbered measures, chord labels, repeated beamed note groups including triplet markings, and later stacked/chord-like noteheads.",
                    "evidence_class": "visual model output only; not verified against original audio",
                    "confidence": "qualitative; no note-level transcription read into labels",
                },
                {
                    "observation_id": "songscription-visible-02",
                    "source": "public score viewer playback header and score marking",
                    "scope": "whole excerpt",
                    "observation": "The viewer identifies the score as 30 seconds long and visibly shows a 4/4 marking and a quarter-note tempo marking of 75. These markings do not establish a beat-accurate mapping to the source recording or to YourMT3+ events.",
                    "evidence_class": "visible metadata only; clock correspondence unverified",
                    "confidence": "visible; musical interpretation not independently checked",
                },
                {
                    "observation_id": "songscription-visible-03",
                    "source": "public viewer controls",
                    "scope": "piano-roll display mode",
                    "observation": "The viewer also provides a piano roll. Its visible Notes played control had Raw performance selected and Score-derived unselected when inspected; this view is not a downloadable, timestamp-verified note-event export.",
                    "evidence_class": "viewer control state only; no event data extracted",
                    "confidence": "directly visible control state",
                },
            ],
        },
        "alignment": {
            "status": "unknown_manual_anchors_needed",
            "anchors": [],
            "rule": "Never infer exact note timestamps from screenshot x-coordinates. Enter audio-clock anchors only after manually confirming them against playback.",
        },
        "observations": [],
        "verified_audio_notes": [],
        "summary": {
            "yourmt3_findings": "Local mix run has 206 events over 0–30 s; 38 non-drum events start in 6–18 s, 31 shorter than 50 ms. Decoder diagnostics and duration concerns are documented in PHASE3_TRANSCRIPTION_VALIDATION.md. These predictions have no independent accuracy labels.",
            "songscription_findings": "View-only score visually shows repeating beamed patterns and chord-like vertical stacks; individual pitches, exact durations, voice attribution, and source-time alignment have not been verified.",
            "agreement_and_disagreement": "Qualitative resemblance of repeated figures is not event agreement. Current YourMT3+ and Songscription event-level agreement is unknown because no Songscription MIDI is available and screenshots are not timestamped note events.",
            "verified_against_original_audio": "None recorded yet. Use playback and enter an audio-verified note/observation only after listening to the same reference excerpt.",
            "uncertain_findings": "Whether the visible repeated figures correspond to the heard pitched phrases, how many independent layers are present, and any screenshot-to-audio time mapping.",
            "phase4_readiness": "Not ready. Neither export nor complete audio-reviewed note labels establish reliable note recovery; Phase 4 remains out of scope.",
        },
    }


def ensure_workspace(root=BASE):
    root = Path(root)
    shots = root / "external" / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    instructions = shots / "README.txt"
    if not instructions.exists():
        instructions.write_text(
            "Place screenshots captured through the normal Songscription viewer here.\n"
            "Supported: PNG, JPG/JPEG, WEBP. Use only screenshots you are authorized to save.\n"
            "Do not use hidden application data or try to bypass export restrictions.\n"
            "Screenshots are shown as visual evidence; their x-position is not converted to audio time.\n"
            "For a reference, add a manually confirmed measure label or audio-clock anchor in\n"
            "manual-comparison.json or through the HTML annotation form.\n", encoding="utf-8")
    path = root / "manual-comparison.json"
    if not path.exists():
        save(path, initial_review(root))
    return path


def report_markdown(review):
    ref = review["songscription_reference"]
    lines = [
        "# Manual comparison: Meaningful Love, 6–18 seconds", "",
        "This is a qualitative, screenshot-assisted comparison. It contains no fabricated accuracy score. Songscription is a separate model output; only the `verified_audio_notes` field can hold human-verified note-level evidence.", "",
        "## YourMT3+ findings", "", review["summary"]["yourmt3_findings"], "",
        "## Songscription visible observations", "", review["summary"]["songscription_findings"], "",
        f"Public view: {ref['public_url']}", "",
        "The view was read-only. MIDI export was unavailable behind the paywall, and no export path was attempted. Visible sheet imagery includes beamed repeated note groups and chord-like stacks. The viewer also showed a piano-roll panel with Raw performance selected and Score-derived unselected. The displayed 30-second length and visible 4/4 / quarter-note=75 markings do not by themselves prove the score clock is aligned with source audio.", "",
        "## Agreement and disagreement", "", review["summary"]["agreement_and_disagreement"], "",
        "## Verified against original audio", "", review["summary"]["verified_against_original_audio"], "",
        "No note-level claim is marked verified. A human review should play the 6–18-second region of `reference.wav`, compare the separate YourMT3+ roll with the Songscription screenshot, and add only what the audio supports.", "",
        "## Uncertain findings", "", review["summary"]["uncertain_findings"], "",
        "Screenshot noteheads, stems, staff placement, triplet figures, and visual horizontal distance are not exact onset/offset labels. Mark pitch/timing as uncertain when staff reading or image resolution is insufficient. Measure numbers can be recorded without assigning them audio timestamps.", "",
        "## Phase 4 readiness", "", review["summary"]["phase4_readiness"], "",
        "## Human annotation status", "",
        f"- Screenshot files registered: {len(ref.get('screenshots', []))}",
        f"- Qualitative annotations: {len(review.get('observations', []))}",
        f"- Audio-verified notes: {len(review.get('verified_audio_notes', []))}",
        f"- Confirmed audio-clock anchors: {len(review['alignment'].get('anchors', []))}",
        "",
        "Edit `manual-comparison.json` or use the annotation controls in `report.html`; export the form data and place it at `external/manual-comparison.json` before rebuilding this report. Keep screenshots under `external/screenshots/`. Then run `output/amt/env/bin/python -B -m scripts.midi_pipeline.phase3b update` in WSL.", "",
    ]
    return "\n".join(lines)


def _panel(review, screenshots, comparison):
    local = comparison.get("models", {}).get("yourmt3_mix", {})
    options = []
    for model in ("yourmt3_mix", "yourmt3_other"):
        entry = comparison.get("models", {}).get(model)
        if not entry:
            continue
        for track in entry.get("tracks", []):
            if not track.get("is_drum"):
                options.append(f'<option value="{html.escape(model)}:{track["track"]}">{html.escape(model)} — track {track["track"]}: {html.escape(track["name"])}</option>')
    if screenshots:
        gallery = "".join(f'<figure><a href="{html.escape(p)}" target="_blank"><img src="{html.escape(p)}" alt="Songscription screenshot {i+1}"></a><figcaption>{html.escape(Path(p).name)} · source-time alignment unknown</figcaption></figure>' for i, p in enumerate(screenshots))
    else:
        gallery = '<div class="p3b-empty">No Songscription screenshots loaded. Add PNG/JPG/WEBP files to <code>external/screenshots/</code> and run the update command. The public view-only reference is linked below.</div>'
    review_json = json.dumps(review, ensure_ascii=False).replace("</", "<\\/")
    models_json = json.dumps(comparison.get("models", {}), ensure_ascii=False).replace("</", "<\\/")
    return f'''
<section id="phase3b-review">
<h2>Phase 3B · view-only Songscription comparison</h2>
<p class="status">Songscription is a separate transcription, not ground truth. A screenshot supplies notation or visual ordering, not exact audio-clock timestamps. Only explicitly reviewed notes belong in the audio-verified section.</p>
<p><a href="{html.escape(PUBLIC_URL)}" target="_blank" rel="noreferrer">Open the public Songscription view-only score</a> · <span id="p3b-alignment-status">Screenshot/audio alignment: unknown</span></p>
<div class="p3b-split">
 <div class="p3b-pane"><h3>Original reference audio + YourMT3+ prediction</h3>
  <audio id="p3b-audio" controls src="reference.wav" aria-label="Original 0–30 second reference audio"></audio>
  <div class="p3b-controls"><label>Prediction / instrument <select id="p3b-track">{''.join(options)}</select></label></div>
  <canvas id="p3b-roll" width="960" height="420" aria-label="YourMT3+ piano roll, 0 to 30 seconds"></canvas>
  <p>Click a note to seek the reference audio and inspect its model note ID. The shaded interval is 6–18 seconds. The roll uses local model timestamps; it does not align Songscription images.</p><pre id="p3b-note-detail">Select a note for details.</pre>
 </div>
 <div class="p3b-pane"><h3>Songscription visual reference</h3>
  <div class="p3b-gallery">{gallery}</div>
  <label class="p3b-file">Preview screenshot files in this browser <input id="p3b-files" type="file" accept="image/png,image/jpeg,image/webp" multiple></label>
  <p>Preview files are not copied into the project. For a persistent comparison, place them in <code>external/screenshots/</code> and rebuild this view. No screenshot x-coordinate is treated as a timestamp.</p>
  <p>Visible evidence: sheet measures, chord labels, repeated beamed groups and later stacked noteheads. This is qualitative model output only.</p>
 </div>
</div>
<details class="p3b-annotation" open><summary>Manual comparison notes · 6–18 seconds</summary>
 <p>Use measure labels when timing cannot be established. Select “uncertain” instead of guessing a note or timestamp.</p>
 <div class="p3b-actions"><label>Manual audio-clock anchor label<input id="p3b-anchor-label" placeholder="e.g. bar 4 downbeat, confirmed by playback"></label><label>Confirmed source time (seconds)<input id="p3b-anchor-time" type="number" min="0" max="30" step="0.001" placeholder="leave blank if not confirmed"></label><button id="p3b-anchor-add">Add confirmed time anchor</button></div><ol id="p3b-anchors"></ol>
 <div class="p3b-formgrid">
  <label>Finding type<select id="p3b-kind"><option value="correctly_detected">Possible correct detection</option><option value="missing_note">Missing note</option><option value="false_positive">False positive</option><option value="incorrect_octave">Incorrect octave</option><option value="incorrect_onset_duration">Incorrect onset or duration</option><option value="missing_instrument_layer">Missing instrument layer</option><option value="repeated_arpeggio_quality">Repeated arpeggio quality</option><option value="uncertain">Uncertain pitch/timing/reading</option><option value="comment">General comment</option></select></label>
  <label>Model / source<select id="p3b-source"><option value="yourmt3_mix">YourMT3+ original mix</option><option value="yourmt3_other">YourMT3+ Other stem</option><option value="songscription_screenshot">Songscription screenshot</option><option value="audio_listening">Original audio listening</option></select></label>
  <label>YourMT3+ note ID (optional)<input id="p3b-note-id" type="number" min="0" placeholder="from clicked note detail"></label>
  <label>Measure / visual reference (optional)<input id="p3b-measure" placeholder="e.g. measures 4–5; no audio time assumed"></label>
  <label>Audio onset in original seconds (optional)<input id="p3b-onset" type="number" min="0" max="30" step="0.001" placeholder="only if confirmed by listening"></label>
  <label>Audio offset in original seconds (optional)<input id="p3b-offset" type="number" min="0" max="30" step="0.001" placeholder="only if confirmed by listening"></label>
  <label>MIDI pitch if readable/verified (optional)<input id="p3b-pitch" type="number" min="0" max="127"></label>
  <label>Evidence<select id="p3b-evidence"><option value="visual_only">Screenshot/score only</option><option value="audio_reviewed">Checked by listening to original reference audio</option><option value="uncertain">Uncertain / insufficient evidence</option></select></label>
  <label class="p3b-wide">Comment<textarea id="p3b-comment" rows="3" placeholder="Describe what matches, differs, or remains uncertain."></textarea></label>
 </div>
 <div class="p3b-actions"><button id="p3b-add">Add qualitative observation</button><button id="p3b-export">Download structured review JSON</button><label class="p3b-file">Load a previous review JSON <input id="p3b-import" type="file" accept="application/json,.json"></label></div>
 <h4>Qualitative observations (kept separate from verified notes)</h4><ol id="p3b-observations"></ol>
 <h4>Audio-verified note evidence</h4><p>None entered. A record is listed here only when Evidence is set to audio-reviewed and it contains a pitch or original-clock time entered by the reviewer.</p><ol id="p3b-verified"></ol>
</details>
<script id="p3b-initial" type="application/json">{review_json}</script>
</section>
<style>
#phase3b-review{{margin:24px 0;padding:18px;background:#f7f9fc;border:1px solid #ccd6e0;border-radius:9px}}#phase3b-review .p3b-split{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px;align-items:start}}#phase3b-review .p3b-pane{{min-width:0;padding:12px;background:white;border:1px solid #d4dbe2;border-radius:7px}}#phase3b-review canvas{{width:100%;height:auto;background:#fff;border:1px solid #bbb}}.p3b-gallery{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px;max-height:600px;overflow:auto}}.p3b-gallery figure{{margin:0;padding:6px;background:#f4f4f4}}.p3b-gallery img{{width:100%;height:auto;max-height:440px;object-fit:contain}}.p3b-gallery figcaption{{font-size:.85em;color:#555}}.p3b-empty{{padding:24px;background:#f4f4f4}}.p3b-annotation{{margin-top:18px;padding:12px;border:1px solid #ccc}}.p3b-formgrid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}}.p3b-formgrid label{{display:flex;flex-direction:column;gap:4px}}.p3b-formgrid input,.p3b-formgrid select,.p3b-formgrid textarea{{font:inherit;padding:6px;max-width:100%}}.p3b-wide{{grid-column:1/-1}}.p3b-actions{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 0}}.p3b-file{{display:inline-flex;gap:6px;align-items:center;flex-wrap:wrap}}.p3b-annotation button{{font:inherit;padding:7px 12px;cursor:pointer}}#p3b-observations li,#p3b-verified li{{margin:8px 0}}@media(max-width:850px){{#phase3b-review .p3b-split{{grid-template-columns:1fr}}.p3b-formgrid{{grid-template-columns:1fr}}.p3b-wide{{grid-column:auto}}}}
</style>
<script>
(()=>{{const data=JSON.parse(document.getElementById('p3b-initial').textContent);let review=data;const models={models_json};const c=document.getElementById('p3b-roll'),ctx=c.getContext('2d'),sel=document.getElementById('p3b-track'),audio=document.getElementById('audio'),audioSide=document.getElementById('p3b-audio');let drawn=[],syncing=false;const syncClock=(from,to)=>{{if(syncing)return;if(Math.abs(from.currentTime-to.currentTime)>.15){{syncing=true;to.currentTime=from.currentTime;setTimeout(()=>syncing=false,80)}}}};audio.addEventListener('timeupdate',()=>syncClock(audio,audioSide));audioSide.addEventListener('timeupdate',()=>syncClock(audioSide,audio));
function draw(){{let [model,idx]=sel.value.split(':');idx=Number(idx);const entry=models[model],ns=(entry?.notes||[]).filter(n=>n.track===idx&&!n.is_drum);drawn=ns;const W=c.width,H=c.height,lo=Math.max(24,Math.min(48,...ns.map(n=>n.pitch))-3),hi=Math.min(108,Math.max(84,...ns.map(n=>n.pitch))+3);ctx.clearRect(0,0,W,H);ctx.fillStyle='#fff';ctx.fillRect(0,0,W,H);ctx.fillStyle='rgba(230,167,63,.20)';ctx.fillRect(6/30*W,0,12/30*W,H);for(let t=0;t<=30;t+=3){{const x=t/30*W;ctx.strokeStyle=t%6===0?'#c7d0d8':'#e4e8eb';ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,H-24);ctx.stroke();ctx.fillStyle='#333';ctx.fillText(t+'s',x+3,H-7)}}for(const n of ns){{const x=n.onset/30*W,w=Math.max(3,(n.offset-n.onset)/30*W),y=(hi-n.pitch)/(hi-lo)*(H-34);ctx.fillStyle='#1769aa';ctx.fillRect(x,y,w,6)}}}}
function drawRows(){{document.getElementById('p3b-observations').innerHTML=review.observations.map((o,i)=>`<li><b>${{esc(o.kind)}}</b> · ${{esc(o.source)}} · ${{esc(o.evidence)}}${{o.measure_reference?' · '+esc(o.measure_reference):''}}${{o.note_id!==null?' · note '+o.note_id:''}} · ${{esc(o.comment||'No comment')}} <button data-remove="${{i}}">Remove</button></li>`).join('')||'<li>No observations entered.</li>';document.getElementById('p3b-verified').innerHTML=review.verified_audio_notes.map(n=>`<li>Pitch ${{n.pitch??'unknown'}} · ${{n.onset_seconds??'unknown'}}–${{n.offset_seconds??'unknown'}} s · ${{esc(n.comment||'')}}</li>`).join('')||'<li>No independently audio-verified note events.</li>';document.getElementById('p3b-anchors').innerHTML=review.alignment.anchors.map(a=>`<li>${{esc(a.label)}} → ${{a.source_time_seconds.toFixed(3)}} s · ${{esc(a.provenance)}}</li>`).join('')||'<li>No confirmed screenshot/audio anchors; screenshot timestamps remain unknown.</li>';document.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{{const row=review.observations.splice(Number(b.dataset.remove),1)[0];review.verified_audio_notes=review.verified_audio_notes.filter(n=>n.id!==row.id);drawRows()}})}}
function esc(s){{return String(s??'').replace(/[&<>"']/g,x=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[x]))}}
function add(){{const evidence=document.getElementById('p3b-evidence').value,kind=document.getElementById('p3b-kind').value;const onset=document.getElementById('p3b-onset').value,offset=document.getElementById('p3b-offset').value,pitch=document.getElementById('p3b-pitch').value,nid=document.getElementById('p3b-note-id').value;const row={{id:'review-'+Date.now(),kind,source:document.getElementById('p3b-source').value,note_id:nid===''?null:Number(nid),measure_reference:document.getElementById('p3b-measure').value||null,onset_seconds:onset===''?null:Number(onset),offset_seconds:offset===''?null:Number(offset),pitch:pitch===''?null:Number(pitch),evidence,comment:document.getElementById('p3b-comment').value.trim(),ground_truth_role:'qualitative_observation'}};review.observations.push(row);if(evidence==='audio_reviewed'&&(pitch!==''||onset!==''||offset!==''))review.verified_audio_notes.push({{...row,ground_truth_role:'human_verified_against_original_audio'}});drawRows();document.getElementById('p3b-comment').value=''}}
function exportReview(){{const blob=new Blob([JSON.stringify(review,null,2)+'\\n'],{{type:'application/json'}}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='manual-comparison.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}}
sel.addEventListener('change',draw);c.addEventListener('click',e=>{{const r=c.getBoundingClientRect(),sec=(e.clientX-r.left)/r.width*30;const n=drawn.filter(x=>Math.abs(x.onset-sec)<.3).sort((a,b)=>Math.abs(a.onset-sec)-Math.abs(b.onset-sec))[0];if(n){{audio.currentTime=n.onset;document.getElementById('p3b-note-detail').textContent=`${{n.model_key}} · note ${{n.id}} · ${{n.instrument}} · MIDI ${{n.pitch}} · ${{n.onset.toFixed(3)}}–${{n.offset.toFixed(3)}} s · duration ${{n.duration.toFixed(3)}} s`;document.getElementById('p3b-note-id').value=n.id}}else if(audio)audio.currentTime=sec}});document.getElementById('p3b-anchor-add').onclick=()=>{{const label=document.getElementById('p3b-anchor-label').value.trim(),time=document.getElementById('p3b-anchor-time').value,value=Number(time);if(!label||time===''||!Number.isFinite(value)||value<0||value>30){{alert('Enter a label and a confirmed source-audio time from 0 to 30 seconds.');return}}review.alignment.anchors.push({{label,source_time_seconds:value,provenance:'manually confirmed by reviewer against reference audio; not inferred from screenshot coordinates'}});review.alignment.status='reviewer_confirmed_manual_anchors';drawRows()}};document.getElementById('p3b-add').onclick=add;document.getElementById('p3b-export').onclick=exportReview;document.getElementById('p3b-import').onchange=async e=>{{try{{const v=JSON.parse(await e.target.files[0].text());if(v.schema!==data.schema)throw Error('Unexpected comparison schema');review=v;drawRows()}}catch(x){{alert('Could not load review JSON: '+x.message)}}}};document.getElementById('p3b-files').onchange=e=>{{const g=document.querySelector('.p3b-gallery');g.innerHTML='';for(const f of e.target.files){{const u=URL.createObjectURL(f),fig=document.createElement('figure'),img=document.createElement('img'),cap=document.createElement('figcaption');img.src=u;img.alt='Local screenshot preview';cap.textContent=f.name+' · source-time alignment unknown';fig.append(img,cap);g.append(fig)}}}};draw();drawRows()}})();
</script>'''


def decorate_report(root=BASE):
    root = Path(root)
    ensure_workspace(root)
    review_path = root / "external" / "manual-comparison.json"
    if review_path.exists():
        review = json.loads(review_path.read_text(encoding="utf-8"))
    else:
        review = json.loads((root / "manual-comparison.json").read_text(encoding="utf-8"))
    comparison = json.loads((root / "comparison.json").read_text(encoding="utf-8"))
    screenshots = sorted(p.relative_to(root).as_posix() for p in (root / "external" / "screenshots").iterdir()
                         if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
    review.setdefault("songscription_reference", {})["screenshots"] = screenshots
    page_path = root / "report.html"
    page = page_path.read_text(encoding="utf-8")
    marker = '<h2>Inputs and evaluation state</h2>'
    if marker not in page:
        raise ValueError("Existing Phase 3 HTML report does not contain the expected insertion point")
    start = page.find('<section id="phase3b-review">')
    if start >= 0:
        end = page.find(marker, start)
        if end >= 0:
            page = page[:start] + page[end:]
    page = page.replace(marker, _panel(review, screenshots, comparison) + "\n" + marker, 1)
    page_path.write_text(page, encoding="utf-8")
    (root / "MANUAL_COMPARISON_6-18S.md").write_text(report_markdown(review), encoding="utf-8")
    return {"report_html": str(page_path), "report_markdown": str(root / "MANUAL_COMPARISON_6-18S.md"),
            "screenshot_count": len(screenshots), "annotations": len(review.get("observations", [])),
            "verified_audio_notes": len(review.get("verified_audio_notes", [])),
            "alignment": review["alignment"]["status"]}


def update(root=BASE):
    root = Path(root)
    ensure_workspace(root)
    # Import the registered human review without changing any Phase 3 model files.
    external_review = root / "external" / "manual-comparison.json"
    if external_review.exists():
        value = json.loads(external_review.read_text(encoding="utf-8"))
        if value.get("schema") != SCHEMA:
            raise ValueError("Unsupported manual comparison schema")
        save(root / "manual-comparison.json", value)
    result = decorate_report(root)
    print(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("update", choices=("update",))
    parser.add_argument("--root", type=Path, default=BASE)
    args = parser.parse_args()
    update(args.root)


if __name__ == "__main__":
    main()
