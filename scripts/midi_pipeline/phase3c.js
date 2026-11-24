(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const status = $("audio-status");
  const original = $("original-audio");
  const midi = $("midi-audio");
  const seek = $("seek");
  const canvas = $("piano-roll");
  const ctx = canvas.getContext("2d");
  const palette = ["#0067a5", "#b45309", "#25804d", "#7847a6", "#ae3047", "#147b77", "#695b00"];
  const state = {data:null, view:[6,18], selected:null, sort:"onset", direction:1,
    active:"original", playing:false, tracks:[], visible:[], notesByKey:new Map(), midiReady:false, position:0};

  function message(text, isError=false) {
    status.textContent=text;
    status.classList.toggle("error", isError);
    status.classList.toggle("good-status", !isError && /Ready|Loaded|Playing/.test(text));
  }
  function esc(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }
  function pitchName(p) { return ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"][p%12]+(Math.floor(p/12)-1); }
  function fixed(v,n=3) { return Number(v).toFixed(n); }
  function models() { return state.data?.models || {}; }
  function allNotes() { return Object.entries(models()).flatMap(([key,m])=>(m.notes||[]).map(n=>({...n,model_key:key}))); }
  function allTracks() { return Object.entries(models()).flatMap(([key,m])=>(m.stats.programs_and_tracks||[]).map(t=>({...t,model_key:key}))); }
  function currentModel() { return $("reconstruction-source").value; }
  function activeAudio() { return state.active === "midi" ? midi : original; }
  function audioTime() { return activeAudio().readyState>=1 ? activeAudio().currentTime : state.position; }

  function buildSummary() {
    for(const key of ["yourmt3_mix","yourmt3_other"]) {
      const m=models()[key]; if(!m) continue;
      const target=document.querySelector(`[data-summary="${key}"] .summary-text`);
      if(target) target.textContent=summaryText(m);
    }
  }
  function summaryText(m) {
    const s=m.stats, range=s.focus_pitch_range_midi;
    const describeRange=range ? `${pitchName(range[0])} to ${pitchName(range[1])}` : "no pitched notes";
    const med=s.focus_duration_seconds.median;
    const tracks=s.programs_and_tracks.map(t=>`${t.name} (program ${t.midi_program}, ${t.note_count} notes)`).join(", ");
    return `YourMT3+ ${m.input.type.toLowerCase()} produced ${s.total_notes} note events across 0–30 seconds. ${s.notes_starting_in_focus} start in 6–18 seconds (${s.pitched_notes_starting_in_focus} pitched; ${s.percussion_event_count} percussion events in the full input). Pitched focus notes span ${describeRange}. Median duration for all focus events: ${med==null?"unavailable":`${(med*1000).toFixed(1)} ms`}; ${s.focus_duration_seconds.under_50ms_count} focus events are shorter than 50 ms. ${s.overlap_regions_focus.length} simultaneous-note regions occur in that window. Tracks: ${tracks}. These are unverified predictions.`;
  }

  function buildTableOptions() {
    const select=$("filter-track");
    const previous=select.value;
    const list=allTracks().map(t=>`<option value="${esc(t.model_key+":"+t.track)}">${esc(t.model_key.replace("yourmt3_","").toUpperCase())} · ${esc(t.track)}: ${esc(t.name)} (program ${t.midi_program})</option>`).join("");
    select.innerHTML='<option value="all">All tracks</option>'+list;
    if([...select.options].some(o=>o.value===previous)) select.value=previous;
  }
  function buildLegend() {
    $("roll-legend").innerHTML=allTracks().filter(t=>$("roll-source").value==="both"||$("roll-source").value===t.model_key)
      .map(t=>`<span><i style="background:${palette[t.track%palette.length]}"></i>${esc(t.model_key.replace("yourmt3_","").toUpperCase())} · ${esc(t.name)} (program ${t.midi_program})${t.is_percussion?" · percussion":""}</span>`).join("");
  }
  function selectedNotes() {
    const source=$("roll-source").value;
    return Object.entries(models()).filter(([key])=>source==="both"||key===source)
      .flatMap(([key,m])=>(m.notes||[]).map(n=>({...n,model_key:key})))
      .filter(n=>n.onset<state.view[1]&&n.offset>state.view[0]);
  }
  function autoPitchRange() {
    const notes=selectedNotes().filter(n=>!n.is_drum);
    const source=notes.length?notes:allNotes();
    const pitches=source.map(n=>n.pitch);
    const lo=pitches.length?Math.max(0,Math.min(...pitches)-3):36;
    const hi=pitches.length?Math.min(127,Math.max(...pitches)+3):84;
    $("pitch-min").value=lo; $("pitch-max").value=hi;
  }
  function chooseView(name) {
    if(name==="focus") state.view=[6,18];
    else if(name==="phrase-a") state.view=[6,12];
    else if(name==="phrase-b") state.view=[12,18];
    else if(name==="overview") state.view=[0,30];
    drawRoll();
  }
  function setCustom() { $("time-view").value="custom"; drawRoll(); }
  function drawRoll() {
    if(!state.data) return;
    const min=Math.max(0,Math.min(127,Number($("pitch-min").value)||0));
    const max=Math.max(min,Math.min(127,Number($("pitch-max").value)||127));
    const low=Math.min(min,max),high=Math.max(min,max),row=23,top=32,bottom=40;
    const width=Math.max(850,canvas.parentElement.clientWidth-2),height=top+(high-low+1)*row+bottom;
    canvas.width=width;canvas.height=height;
    const w=width,usable=w-76,plotLeft=62,plotRight=w-14,plotWidth=plotRight-plotLeft;
    const [a,b]=state.view,span=b-a;
    ctx.fillStyle="#fff";ctx.fillRect(0,0,w,height);
    if(a<18&&b>6){const sx=plotLeft+(Math.max(a,6)-a)/span*plotWidth,ex=plotLeft+(Math.min(b,18)-a)/span*plotWidth;ctx.fillStyle="#fff0d1";ctx.fillRect(sx,top,ex-sx,(high-low+1)*row);}
    ctx.fillStyle="#17212b";ctx.font="13px system-ui";
    const tick=span>22?5:span>8?1:.5;
    for(let t=Math.ceil(a/tick)*tick;t<=b+1e-6;t+=tick){
      const x=plotLeft+(t-a)/span*plotWidth;
      ctx.strokeStyle=(Math.abs(t%1)<1e-6)?"#becad3":"#e0e6eb";ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,height-bottom);ctx.stroke();
      ctx.fillStyle="#243442";ctx.fillText(`${Number(t.toFixed(3))} s`,Math.min(x+2,plotRight-40),height-12);
      ctx.fillText(`${Number(t.toFixed(3))} s`,Math.min(x+2,plotRight-40),21);
    }
    for(let p=low;p<=high;p++){
      const y=top+(high-p)*row;
      ctx.strokeStyle=p%12===0?"#99aab7":"#e0e5e9";ctx.beginPath();ctx.moveTo(plotLeft,y+row);ctx.lineTo(plotRight,y+row);ctx.stroke();
      ctx.fillStyle="#17212b";ctx.font=p%12===0?"bold 12px system-ui":"11px system-ui";ctx.fillText(pitchName(p),7,y+15);
    }
    const notes=selectedNotes();state.visible=[];
    notes.forEach((n,i)=>{
      if(n.pitch<low||n.pitch>high) return;
      const x1=plotLeft+(Math.max(a,n.onset)-a)/span*plotWidth;
      const x2=plotLeft+(Math.min(b,n.offset)-a)/span*plotWidth;
      const actual=(n.offset-n.onset)/span*plotWidth;
      const displayW=Math.max(4,x2-x1);
      const lane=n.model_key==="yourmt3_other"?10:2;
      const y=top+(high-n.pitch)*row+lane+(i%2)*1.5;
      const trackColor=palette[n.track%palette.length];
      ctx.fillStyle=trackColor;ctx.globalAlpha=n.is_drum ? 0.75 : 0.94;
      ctx.fillRect(x1,y,displayW,10);ctx.globalAlpha=1;
      ctx.strokeStyle=state.selected&&state.selected.key===n.model_key&&state.selected.id===n.id?"#b2182b":"#17212b";
      ctx.lineWidth=state.selected&&state.selected.key===n.model_key&&state.selected.id===n.id?3:1;ctx.strokeRect(x1,y,displayW,10);
      if(n.onset<a){ctx.fillStyle="#812425";ctx.beginPath();ctx.moveTo(plotLeft,y+5);ctx.lineTo(plotLeft+6,y);ctx.lineTo(plotLeft+6,y+10);ctx.fill();}
      if(n.offset>b){ctx.fillStyle="#812425";ctx.beginPath();ctx.moveTo(plotRight,y+5);ctx.lineTo(plotRight-6,y);ctx.lineTo(plotRight-6,y+10);ctx.fill();}
      state.visible.push({note:n,x:x1,y,w:displayW,actualW:actual});
    });
    const selected=state.selected&&state.visible.find(x=>x.note.model_key===state.selected.key&&x.note.id===state.selected.id);
    if(selected){ctx.strokeStyle="#b2182b";ctx.lineWidth=3;ctx.strokeRect(selected.x-1,selected.y-1,selected.w+2,12);}
  }

  function notesInTable() {
    const source=$("filter-source").value,track=$("filter-track").value;
    const min=Math.max(0,Number($("filter-pitch-min").value)||0),max=Math.min(127,Number($("filter-pitch-max").value)||127);
    return allNotes().filter(n=>6<=n.onset&&n.onset<18)
      .filter(n=>source==="all"||n.model_key===source)
      .filter(n=>track==="all"||`${n.model_key}:${n.track}`===track)
      .filter(n=>n.pitch>=min&&n.pitch<=max);
  }
  function flags(n) {
    const f=[];if(n.is_drum)f.push("percussion");if(n.duration<.05)f.push("<50 ms");
    if(n.onset<6)f.push("starts before focus");if(n.offset>18)f.push("ends after focus");
    return f.join("; ")||"none";
  }
  function renderTable() {
    if(!state.data)return;
    let rows=notesInTable();
    const key=state.sort,dir=state.direction;
    rows.sort((x,y)=>{const a=x[key],b=y[key];return (typeof a==="number"&&typeof b==="number"?a-b:String(a).localeCompare(String(b)))*dir || x.track-y.track || x.id-y.id;});
    $("notes-table").querySelector("tbody").innerHTML=rows.map(n=>`<tr data-model="${esc(n.model_key)}" data-id="${n.id}" class="${state.selected&&state.selected.key===n.model_key&&state.selected.id===n.id?"selected":""}"><td>${n.id}</td><td>${n.model_key==="yourmt3_mix"?"Mix":"Other"}</td><td>${n.track}</td><td>${esc(n.instrument)} / ${n.program}</td><td>${esc(pitchName(n.pitch))}</td><td>${n.pitch}</td><td class="num">${fixed(n.onset)}</td><td class="num">${fixed(n.offset)}</td><td class="num">${fixed(n.duration*1000,1)}</td><td>${n.velocity}</td><td>—</td><td>${esc(flags(n))}</td></tr>`).join("");
    $("row-count").textContent=`${rows.length} matching rows · ${allNotes().filter(n=>6<=n.onset&&n.onset<18).length} total focus events`;
    $("notes-table").querySelectorAll("tbody tr").forEach(tr=>tr.addEventListener("click",()=>selectNote(tr.dataset.model,Number(tr.dataset.id),true)));
  }
  function selectNote(key,id,seekAudio) {
    const note=(models()[key]?.notes||[]).find(n=>n.id===id);if(!note)return;
    state.selected={key,id};
    if(seekAudio) seekTo(note.onset);
    drawRoll();renderTable();
    const row=[...$("notes-table").querySelectorAll("tbody tr")].find(r=>r.dataset.model===key&&Number(r.dataset.id)===id);
    if(row)$("notes-table").parentElement.scrollTop=row.offsetTop-$("notes-table").querySelector("thead").offsetHeight;
    const tip=$("roll-tooltip");tip.textContent=`${key} · note ${id} · ${note.instrument}, program ${note.program}${note.is_drum?" (percussion)":""} · ${pitchName(note.pitch)} (MIDI ${note.pitch}) · onset ${fixed(note.onset)} s · offset ${fixed(note.offset)} s · duration ${(note.duration*1000).toFixed(2)} ms · velocity ${note.velocity} · confidence unavailable · raw note ID ${id}`;
    tip.hidden=false;
    $("review-note-id").value=id;
    $("review-source").value=key;
  }
  function seekTo(t) {
    const time=Math.max(0,Math.min(30,t));
    state.position=time;
    for(const audio of [original,midi])if(audio.readyState>=1){try{audio.currentTime=time;}catch(_){}}
    seek.value=time;updateClock();
  }
  function updateClock() {
    const t=audioTime();state.position=t;seek.value=t;
    const d=Number.isFinite(activeAudio().duration)?activeAudio().duration:30;
    $("time-display").textContent=`${fixed(t)} / ${fixed(d)} s · source recording clock`;
  }
  function applyAudioSettings() {
    const rate=Number($("play-speed").value),volume=Number($("volume").value);
    for(const audio of [original,midi]){audio.playbackRate=rate;audio.volume=volume;}
  }
  async function play() {
    const mode=$("play-mode").value;
    if($("loop-focus").checked&&(audioTime()<6||audioTime()>=18)) seekTo(6);
    const timestamp=audioTime();
    state.active=mode==="original"?"original":mode==="midi"?"midi":state.active;
    if(mode==="ab") $("ab-switch").hidden=false;else $("ab-switch").hidden=true;
    const chosen=activeAudio(),other=chosen===original?midi:original;
    other.pause();applyAudioSettings();
    try {chosen.currentTime=timestamp;await chosen.play();state.playing=true;$("play-toggle").textContent="Pause";message(`Playing ${chosen===original?"original reference WAV":`YourMT3+ ${currentModel()==="yourmt3_mix"?"Mix":"Other"} synthetic MIDI reconstruction`}.`);}
    catch(error){state.playing=false;$("play-toggle").textContent="Play";message(`Playback failed: ${error.message}. Check the local audio endpoint and browser media support.`,true);}
  }
  function pause() {original.pause();midi.pause();state.playing=false;$("play-toggle").textContent="Play";updateClock();}
  function switchAB() {
    if($("play-mode").value!=="ab")return;
    const time=audioTime(),wasPlaying=state.playing;
    original.pause();midi.pause();
    state.active=state.active==="original"?"midi":"original";
    seekTo(time);
    message(`A/B selected ${state.active==="original"?"original reference WAV":`YourMT3+ ${currentModel()==="yourmt3_mix"?"Mix":"Other"} MIDI reconstruction`} at ${fixed(time)} seconds.`);
    if(wasPlaying) play();
  }
  function handleTick() {
    if(state.playing) {
      const t=audioTime();
      if($("loop-focus").checked&&t>=18){seekTo(6);activeAudio().play().catch(e=>message(`Loop playback failed: ${e.message}`,true));}
      else {state.position=t;seek.value=t;const inactive=activeAudio()===original?midi:original;if(inactive.readyState>=1&&Math.abs(inactive.currentTime-t)>.15){try{inactive.currentTime=t;}catch(_){}}$("time-display").textContent=`${fixed(t)} / ${fixed(activeAudio().duration||30)} s · source recording clock`;}
    }
  }
  function buildComparison() {
    const c=state.data.mix_other_comparison;
    $("compare-summary").textContent=`Within 6–18 seconds, a one-to-one comparison found ${c.matched_candidates.length} same-pitch note-onset candidates within ${(c.note_onset_tolerance_seconds*1000).toFixed(0)} ms; ${c.mix_unique_note_ids.length} Mix events and ${c.other_unique_note_ids.length} Other events have no pair under this rule. This is cross-input agreement only, not transcription accuracy.`;
    $("compare-details").innerHTML=["yourmt3_mix","yourmt3_other"].map(key=>{
      const m=models()[key],s=m.stats;
      return `<div class="compare-model"><h3>${key==="yourmt3_mix"?"Mix":"Other"}</h3><ul><li>${s.total_notes} total notes · ${s.notes_starting_in_focus} start in focus</li><li>Pitch range: ${s.focus_pitch_range_midi?`${pitchName(s.focus_pitch_range_midi[0])}–${pitchName(s.focus_pitch_range_midi[1])}`:"none"}</li><li>Focus durations: min ${(s.focus_duration_seconds.minimum*1000).toFixed(1)} ms · median ${(s.focus_duration_seconds.median*1000).toFixed(1)} ms · max ${(s.focus_duration_seconds.maximum*1000).toFixed(1)} ms</li><li>${s.overlap_regions_focus.length} simultaneous-note regions; ${s.percussion_event_count} percussion events total</li><li>Tracks: ${s.programs_and_tracks.map(t=>`${esc(t.name)} (program ${t.midi_program})`).join(", ")}</li><li>${m.reconstruction.rendered_pitched_notes} pitched notes synthesized · ${m.reconstruction.percussion_notes_omitted} percussion events intentionally omitted from the tone render</li></ul></div>`;
    }).join("");
    const observations=[];
    for(const key of ["yourmt3_mix","yourmt3_other"]){
      const model=models()[key],desc=model.sequence_descriptions,label=key==="yourmt3_mix"?"Mix":"Other";
      for(const item of desc.ascending_triples.slice(0,8))observations.push(`${label}: ascending pitch triple ${item.pitches.map(p=>pitchName(p)).join(" → ")} on track ${item.track}; note IDs ${item.note_ids.join(", ")} at ${item.time_seconds.join(", ")} s.`);
      for(const item of desc.descending_triples.slice(0,8))observations.push(`${label}: descending pitch triple ${item.pitches.map(p=>pitchName(p)).join(" → ")} on track ${item.track}; note IDs ${item.note_ids.join(", ")} at ${item.time_seconds.join(", ")} s.`);
      for(const item of desc.repeated_pitch_triples.slice(0,6))observations.push(`${label}: same pitch triple ${item.pitches.map(p=>pitchName(p)).join("–")} recurs on track ${item.track}; ${item.occurrences.map(o=>`IDs ${o.note_ids.join("/")} at ${o.time_seconds.join("–")} s`).join("; ")}. This is an exact pitch-sequence statistic, not proof of a repeated musical phrase.`);
      if(desc.shorter_than_50ms.length)observations.push(`${label}: ${desc.shorter_than_50ms.length} pitched focus events are under 50 ms; first examples: ${desc.shorter_than_50ms.slice(0,5).map(n=>`ID ${n.note_id} ${pitchName(n.pitch)} @ ${fixed(n.onset)} s (${(n.duration*1000).toFixed(1)} ms)`).join(", ")}.`);
      for(const item of desc.at_least_500ms.slice(0,5))observations.push(`${label}: sustained candidate ID ${item.note_id}, ${pitchName(item.pitch)}, ${fixed(item.onset)}–${fixed(item.offset)} s (${(item.duration*1000).toFixed(0)} ms).`);
      for(const item of desc.within_track_gaps_over_500ms.slice(0,5))observations.push(`${label}: no note was predicted on track ${item.track} between ID ${item.after_note_id} and ID ${item.before_note_id} (${fixed(item.start)}–${fixed(item.end)} s). This is not evidence that the audio is silent.`);
      for(const item of desc.same_track_pitch_overlap_candidates.slice(0,5))observations.push(`${label}: possible duplicate/articulation pair IDs ${item.note_ids.join(" and ")} at ${item.onsets.map(t=>fixed(t)).join(" and ")} s, ${pitchName(item.pitch)}; review against audio.`);
      for(const item of model.stats.overlap_regions_focus.slice(0,5))observations.push(`${label}: simultaneous notes ${item.note_ids.join(", ")} in ${fixed(item.start)}–${fixed(item.end)} s.`);
    }
    $("sequence-summary").innerHTML=observations.length?`<ul class="sequence-list">${observations.map(x=>`<li>${esc(x)}</li>`).join("")}</ul>`:"<p>No candidates under these simple descriptive rules. This does not mean the audio has no pattern.</p>";
    $("candidate-details").textContent=JSON.stringify(c,null,2);
  }

  let review=null;
  let dirty=false;
  const screenshotURLs=[];
  function reviewStatus(text) { $("review-status").textContent=text; }
  function drawReview() {
    $("review-observations").innerHTML=review.observations.map(o=>`<li>${esc(o.kind)} · ${esc(o.source)} · ${esc(o.evidence)} · ${esc(o.measure_reference||"no measure reference")} · ${esc(o.comment)} · pitch ${o.pitch??"uncertain"} · ${o.onset_seconds??"unknown"}–${o.offset_seconds??"unknown"} s</li>`).join("")||"<li>No observations recorded.</li>";
    $("review-verified-notes").innerHTML=review.verified_audio_notes.map(n=>`<li>Human-verified pitch ${n.pitch??"unspecified"} · ${n.onset_seconds??"unknown"}–${n.offset_seconds??"unknown"} s · ${esc(n.comment)}</li>`).join("")||"<li>No independently audio-verified notes recorded.</li>";
    $("review-anchors").innerHTML=review.alignment.anchors.map(a=>`<li>${esc(a.label)} → ${fixed(a.source_time_seconds)} source seconds · manually confirmed</li>`).join("")||"<li>No confirmed screenshot/audio anchors. Screenshot alignment remains unknown.</li>";
  }
  function showScreenshots(files, local=false) {
    for(const url of screenshotURLs)URL.revokeObjectURL(url);
    screenshotURLs.length=0;
    const gallery=$("screenshot-gallery");gallery.replaceChildren();
    for(const item of files) {
      const figure=document.createElement("figure"),img=document.createElement("img"),cap=document.createElement("figcaption");
      if(local){img.src=URL.createObjectURL(item);screenshotURLs.push(img.src);cap.textContent=item.name;}
      else {img.src="/"+item.split("/").map(encodeURIComponent).join("/");cap.textContent=item.split("/").pop();}
      img.alt="Songscription screenshot — visual model reference; source-time alignment unverified";
      figure.append(img,cap);gallery.append(figure);
    }
    if(!files.length)gallery.textContent="No screenshots loaded. Preview images here or add them to the project screenshot directory.";
  }
  function checkedReview(v) {
    if(v?.schema!=="phase3b-manual-comparison/1"||!Array.isArray(v.observations)||!Array.isArray(v.verified_audio_notes)||!Array.isArray(v.alignment?.anchors))throw Error("Unsupported manual review structure");
    if(v.reference_audio_sha256!==state.data.reference_audio.sha256)throw Error("Review belongs to a different reference audio file");
    return v;
  }
  function optionalNumber(id) {const value=$(id).value;return value===""?null:Number(value);}
  $("review-add").addEventListener("click",()=>{
    if(!review){reviewStatus("Review not loaded. Reload the page or import a matching saved review first.");return;}
    const onset=optionalNumber("review-onset"),offset=optionalNumber("review-offset"),pitch=optionalNumber("review-pitch"),id=optionalNumber("review-note-id");
    if([onset,offset].some(t=>t!==null&&(!Number.isFinite(t)||t<0||t>30))||(onset!==null&&offset!==null&&offset<=onset)||(pitch!==null&&(!Number.isInteger(pitch)||pitch<0||pitch>127))||(id!==null&&(!Number.isInteger(id)||id<0))){reviewStatus("Enter valid source times, a positive duration and a MIDI pitch from 0–127, or leave uncertain fields blank.");return;}
    const evidence=$("review-evidence").value,verified=$("review-verified").checked;
    if(verified&&(evidence!=="audio_reviewed"||$("review-kind").value!=="correctly_detected"||(pitch===null&&onset===null&&offset===null))){reviewStatus("Verified notes require a correct detection, personally checked audio evidence, and at least one confirmed pitch/time. Other findings stay qualitative.");return;}
    const row={id:"review-"+Date.now(),kind:$("review-kind").value,source:$("review-source").value,note_id:id,measure_reference:$("review-measure").value||null,onset_seconds:onset,offset_seconds:offset,pitch,evidence,comment:$("review-comment").value.trim(),ground_truth_role:"qualitative_observation"};
    review.observations.push(row);
    if(verified)review.verified_audio_notes.push({...row,ground_truth_role:"human_verified_against_original_audio"});
    dirty=true;drawReview();reviewStatus("Observation added in this tab. Download review JSON to preserve it.");
    $("review-verified").checked=false;$("review-comment").value="";
  });
  $("anchor-add").addEventListener("click",()=>{
    if(!review)return;
    const label=$("anchor-label").value.trim(),time=optionalNumber("anchor-time");
    if(!label||time===null||!Number.isFinite(time)||time<0||time>30){reviewStatus("Enter a label and an independently confirmed audio time from 0 to 30 seconds.");return;}
    review.alignment.anchors.push({label,source_time_seconds:time,provenance:"manually confirmed by reviewer against reference audio; not inferred from screenshot coordinates"});
    review.alignment.status="reviewer_confirmed_manual_anchors";dirty=true;drawReview();reviewStatus("Manual anchor added. No automatic screenshot alignment was applied.");
  });
  $("review-export").addEventListener("click",()=>{
    if(!review)return;
    const a=document.createElement("a"),url=URL.createObjectURL(new Blob([JSON.stringify(review,null,2)+"\n"],{type:"application/json"}));
    a.href=url;a.download="manual-comparison.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);dirty=false;reviewStatus("Review download requested. Keep the downloaded JSON for the next session.");
  });
  $("review-import").addEventListener("change",async e=>{
    if(!e.target.files.length)return;
    try{review=checkedReview(JSON.parse(await e.target.files[0].text()));drawReview();dirty=false;reviewStatus("Saved review imported; model predictions remain unchanged.");}
    catch(error){reviewStatus("Review import failed: "+error.message);}
  });
  $("screenshot-files").addEventListener("change",e=>showScreenshots([...e.target.files],true));
  window.addEventListener("beforeunload",e=>{if(dirty){e.preventDefault();e.returnValue="";}});

  function renderTableSort(sort) {if(state.sort===sort)state.direction*=-1;else{state.sort=sort;state.direction=1;}renderTable();}
  function exportCSV() {
    const headers=["note_id","source","track","instrument","program","pitch_name","midi_pitch","onset_seconds","offset_seconds","duration_ms","velocity","confidence","flags"];
    const rows=notesInTable().map(n=>[n.id,n.model_key==="yourmt3_mix"?"Mix":"Other",n.track,n.instrument,n.program,pitchName(n.pitch),n.pitch,n.onset,n.offset,n.duration*1000,n.velocity,"",flags(n)]);
    const csv=[headers,...rows].map(row=>row.map(value=>`"${String(value??"").replace(/"/g,'""')}"`).join(",")).join("\r\n");
    const url=URL.createObjectURL(new Blob([csv],{type:"text/csv;charset=utf-8"}));const a=document.createElement("a");a.href=url;a.download="yourmt3-notes-6-18s.csv";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }

  canvas.addEventListener("mousemove",e=>{
    const r=canvas.getBoundingClientRect(),x=(e.clientX-r.left)/r.width*canvas.width,y=(e.clientY-r.top)/r.height*canvas.height;
    const item=[...state.visible].reverse().find(n=>x>=n.x&&x<=n.x+n.w&&y>=n.y&&y<=n.y+10);
    if(item){const n=item.note;const tip=$("roll-tooltip");tip.textContent=`${n.model_key} · ID ${n.id} · ${n.instrument} / program ${n.program} · ${pitchName(n.pitch)} (MIDI ${n.pitch}) · onset ${fixed(n.onset)} s · offset ${fixed(n.offset)} s · ${(n.duration*1000).toFixed(2)} ms · velocity ${n.velocity} · confidence unavailable`;tip.hidden=false;}
  });
  canvas.addEventListener("click",e=>{
    const r=canvas.getBoundingClientRect(),x=(e.clientX-r.left)/r.width*canvas.width,y=(e.clientY-r.top)/r.height*canvas.height;
    const item=[...state.visible].reverse().find(n=>x>=n.x&&x<=n.x+n.w&&y>=n.y&&y<=n.y+10);
    if(item)selectNote(item.note.model_key,item.note.id,true);
    else if(x>=62&&x<=1426){const t=state.view[0]+(x-62)/(1426-62)*(state.view[1]-state.view[0]);seekTo(t);}
  });
  for(const event of ["loadedmetadata"]){
    original.addEventListener(event,()=>{if(original.readyState>=1){try{original.currentTime=state.position;}catch(_){}if(Math.abs(original.duration-30)<.1)message("Original reference WAV loaded · 30.000-second duration.");}});
    midi.addEventListener(event,()=>{if(midi.readyState>=1){state.midiReady=true;try{midi.currentTime=state.position;}catch(_){}message(`${currentModel()==="yourmt3_mix"?"Mix":"Other"} MIDI reconstruction loaded · ${midi.duration.toFixed(3)} seconds.`);}});
  }
  for(const [name,audio] of [["Original reference WAV",original],["MIDI reconstruction",midi]]){
    audio.addEventListener("error",()=>message(`${name} failed to load. Verify that the local HTTP server is running and the audio endpoint returns HTTP 200 with audio/wav. No alternate audio is substituted.`,true));
    audio.addEventListener("timeupdate",()=>{if(audio===activeAudio()){updateClock();handleTick();}});
    audio.addEventListener("ended",()=>{if(audio===activeAudio()){state.playing=false;$("play-toggle").textContent="Play";message(`${name} playback ended.`);}});
  }
  $("play-toggle").addEventListener("click",()=>state.playing?pause():play());
  $("play-mode").addEventListener("change",()=>{
    const wasPlaying=state.playing,time=audioTime(),mode=$("play-mode").value;pause();$("ab-switch").hidden=mode!=="ab";
    if(mode==="original")state.active="original";else if(mode==="midi")state.active="midi";
    seekTo(time);updateClock();message(`Selected ${state.active==="original"?"original reference WAV":`YourMT3+ ${currentModel()==="yourmt3_mix"?"Mix":"Other"} MIDI reconstruction`} · paused at ${fixed(time)} s.`);if(wasPlaying)play();
  });
  $("ab-switch").addEventListener("click",switchAB);
  $("seek").addEventListener("input",()=>seekTo(Number(seek.value)));
  $("volume").addEventListener("input",applyAudioSettings);$("play-speed").addEventListener("change",applyAudioSettings);
  $("loop-focus").addEventListener("change",()=>{if($("loop-focus").checked&&(audioTime()<6||audioTime()>=18))seekTo(6);});
  $("reconstruction-source").addEventListener("change",()=>{
    const wasPlaying=state.playing,t=audioTime();
    const resume=wasPlaying&&state.active==="midi";
    midi.pause();state.midiReady=false;message(`Loading ${currentModel()==="yourmt3_mix"?"Mix":"Other"} MIDI reconstruction…`);
    if(resume)midi.addEventListener("canplay",()=>{seekTo(t);play();},{once:true});
    midi.src=`/reconstruction/${currentModel()}.wav`;midi.load();seekTo(t);
    buildComparison();
  });
  $("time-view").addEventListener("change",()=>chooseView($("time-view").value));
  $("roll-source").addEventListener("change",()=>{buildLegend();autoPitchRange();drawRoll();});
  $("pitch-min").addEventListener("change",drawRoll);$("pitch-max").addEventListener("change",drawRoll);
  $("pitch-auto").addEventListener("click",()=>{autoPitchRange();drawRoll();});
  $("zoom-in").addEventListener("click",()=>{const [a,b]=state.view,center=(a+b)/2,span=Math.max(1,(b-a)/1.5);state.view=[Math.max(0,center-span/2),Math.min(30,center+span/2)];if(state.view[1]-state.view[0]<span)state.view[0]=Math.max(0,state.view[1]-span);setCustom();});
  $("zoom-out").addEventListener("click",()=>{const [a,b]=state.view,center=(a+b)/2,span=Math.min(30,(b-a)*1.5);state.view=[Math.max(0,center-span/2),Math.min(30,center+span/2)];setCustom();});
  $("pan-left").addEventListener("click",()=>{const span=state.view[1]-state.view[0],shift=span*.25;state.view=[Math.max(0,state.view[0]-shift),Math.max(span,Math.min(30,state.view[1]-shift))];setCustom();});
  $("pan-right").addEventListener("click",()=>{const span=state.view[1]-state.view[0],shift=span*.25;state.view=[Math.min(30-span,state.view[0]+shift),Math.min(30,state.view[1]+shift)];setCustom();});
  $("reset-view").addEventListener("click",()=>{state.view=[6,18];$("time-view").value="focus";drawRoll();});
  for(const id of ["filter-source","filter-track","filter-pitch-min","filter-pitch-max"])$(id).addEventListener("change",renderTable);
  $("notes-table").querySelectorAll("th button[data-sort]").forEach(b=>b.addEventListener("click",()=>renderTableSort(b.dataset.sort)));
  $("csv-export").addEventListener("click",exportCSV);

  fetch("/inspection.json",{cache:"no-store"}).then(r=>{if(!r.ok)throw Error(`HTTP ${r.status}`);return r.json();}).then(data=>{
    state.data=data;
    showScreenshots(data.screenshots||[]);
    fetch("/manual-comparison.json",{cache:"no-store"}).then(r=>{if(!r.ok)throw Error(`HTTP ${r.status}`);return r.json();}).then(v=>{review=checkedReview(v);drawReview();reviewStatus("Existing review loaded. New entries stay in this tab until exported.");}).catch(e=>reviewStatus("Existing review failed to load: "+e.message));
    const health=fetch("/api/health").then(r=>r.json()).catch(()=>null);
    return health.then(info=>{
      if(info?.ok){original.dataset.expectedDuration=info.duration_seconds;}
      buildSummary();buildTableOptions();buildLegend();buildComparison();autoPitchRange();drawRoll();renderTable();
      if(!info?.ok)message("Reference audio health check failed. Use the diagnostics endpoint and local server instructions.",true);
    });
  }).catch(error=>message(`The inspection data failed to load (${error.message}). Start the documented Phase 3C localhost server.`,true));
})();
