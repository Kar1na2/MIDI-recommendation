export const friendly = name => name.replaceAll('_', ' ');
const noteName = n => ['C','C♯','D','D♯','E','F','F♯','G','G♯','A','A♯','B'][((n % 12) + 12) % 12];
const drumName = n => ({35:'kick',36:'kick',38:'snare',40:'snare',39:'clap',42:'hi-hat',44:'hi-hat',46:'open hat',49:'crash',57:'crash',51:'ride',59:'ride'}[n] || (n >= 41 && n <= 50 ? 'toms' : null));
const useful = row => row.active === true && !row.instrument.startsWith('voice__') && !row.instrument.endsWith('__irregular');
const base = name => name.split('__')[0];
function describe(rows, notes) {
  const events = [], kinds = new Set(), missing = [];
  for (const row of rows) {
    const layer = row.instrument.split('__')[1];
    kinds.add(layer === 'chord' ? 'chords' : 'line');
    const found = Array.isArray(row.events) ? row.events : notes.filter(n => n.instrument === row.instrument).map(n => [n.step, n.pitch]);
    if (!found.length && row.onset_count > 0) missing.push(row.instrument);
    for (const [step, pitch] of found) if (Number.isFinite(step) && Number.isFinite(pitch)) events.push({step, pitch, grid: row.steps_per_bar});
  }
  if (missing.length) return {text:`pattern data unavailable (${missing.map(friendly).join(', ')})`, key:null};
  if (!events.length) {
    const held = notes.filter(n => rows.some(r => r.instrument === n.instrument) && Number.isFinite(n.pitch) && Number.isFinite(n.onset_s) && Number.isFinite(n.offset_s) && n.onset_s < n.barStart && n.offset_s > n.barStart);
    if (!held.length) return {text:'active; note events unavailable', key:null};
    return {text:`holds ${[...new Set(held.map(n => noteName(n.pitch)))].join(', ')}`, key:'holds'};
  }
  if (events.some(e => !Number.isFinite(e.grid) || e.grid <= 0)) return {text:'active; onset grid unavailable',key:null};
  const positions = [...new Set(events.map(e => e.step / e.grid))].sort((a,b)=>a-b);
  const density = positions.length <= 2 ? 'sparse' : positions.length <= 8 ? 'steady' : 'busy';
  const placement = positions.filter(p => Math.abs(p * 4 - Math.round(p * 4)) < 1e-9).length / positions.length >= .6 ? 'on the beat' : 'off the beat';
  if (base(rows[0].instrument) === 'drums') {
    const names = [...new Set(events.map(e => drumName(e.pitch)).filter(Boolean))];
    const unknown = [...new Set(events.map(e => e.pitch).filter(n => !drumName(n)))];
    const sound = [...names, ...unknown.map(n => `MIDI ${n}`)].join(', ');
    const text = `${density} ${sound} ${placement}`; return {text,key:text};
  }
  const byStep = positions.map(p => { const pitches=events.filter(e=>e.step/e.grid===p).map(e=>e.pitch).sort((a,b)=>a-b); const mid=Math.floor(pitches.length/2); return pitches.length % 2 ? pitches[mid] : (pitches[mid-1]+pitches[mid])/2; });
  let up = 0, down = 0; for (let i=1;i<byStep.length;i++) { if (byStep[i]>byStep[i-1]) up++; else if (byStep[i]<byStep[i-1]) down++; }
  const contour = up > down ? 'rising' : down > up ? 'falling' : 'repeated-note';
  const text = `${density} ${contour} ${[...kinds].sort().join(' and ')} ${placement}`;
  return {text,key:text};
}
export function summarizeSections(song) {
  const bars = song.beats?.bars, rows = song.patterns?.bars, notes = song.layers?.pattern_notes;
  if (!Array.isArray(bars) || !Array.isArray(rows) || !Array.isArray(notes)) return {error:'Section summaries require beats, per-bar patterns, and pattern-note data.', sections:[]};
  const barMap = new Map(bars.map(b=>[b.bar,b]));
  const rowsByBar = new Map(), notesByBar = new Map();
  for (const r of rows) if (useful(r)) { if (!rowsByBar.has(r.bar)) rowsByBar.set(r.bar,[]); rowsByBar.get(r.bar).push(r); }
  for (const n of notes) { if (!notesByBar.has(n.bar)) notesByBar.set(n.bar,[]); notesByBar.get(n.bar).push(n); }
  const detail = new Map();
  for (const bar of bars) {
    if (bar.beats_per_bar !== 4) { detail.set(bar.bar,{bar, irregular:true, text:`Irregular bar (${bar.beats_per_bar} beats), not compared`}); continue; }
    const grouped = new Map();
    for (const r of rowsByBar.get(bar.bar) || []) { const k=base(r.instrument); if (!grouped.has(k)) grouped.set(k,[]); grouped.get(k).push(r); }
    const instruments = new Map();
    for (const [instrument, group] of grouped) instruments.set(instrument, describe(group,(notesByBar.get(bar.bar)||[]).map(n=>({...n,barStart:bar.start_s}))));
    detail.set(bar.bar,{bar,instruments});
  }
  const sections = (song.report?.rhythmic_sections || []).map(section=> {
    const sectionBars = bars.filter(b=>b.bar>=section.start_bar && b.bar<section.end_bar);
    const regular = sectionBars.filter(b=>b.beats_per_bar===4);
    const instruments = new Map();
    const names = new Set(regular.flatMap(b=>[...detail.get(b.bar).instruments.keys()]));
    for (const name of names) {
      const values=regular.map(b=>detail.get(b.bar).instruments.get(name)).filter(Boolean);
      if (values.length / regular.length < .5) continue;
      const counts = new Map(); for(const val of values) { const key=val.key ?? `unavailable:${val.text}`; const entry=counts.get(key)||{count:0,val};entry.count++; counts.set(key,entry); }
      const best=[...counts.values()].sort((a,b)=>b.count-a.count || a.val.text.localeCompare(b.val.text))[0];
      instruments.set(name,{...best.val,stability:best.count / values.length});
    }
    const overlap=(song.rings.find(r=>r.key==='vocals')?.segments || []).filter(s=>!s.unassigned).map(s=>({s, duration:Math.max(0,Math.min(section.end_s,s.end)-Math.max(section.start_s,s.start))})).sort((a,b)=>b.duration-a.duration)[0];
    return {section, bars:sectionBars.map(b=>detail.get(b.bar)), regularCount:regular.length, instruments, label:overlap?.duration > 0 ? overlap.s.label : 'Vocal section unavailable'};
  });
  function diff(before,after) {
    const changes=[];
    for(const [name,val] of after.instruments) if(!before.instruments.has(name)) changes.push(`${friendly(name)} enters`);
    for(const [name] of before.instruments) if(!after.instruments.has(name)) changes.push(`${friendly(name)} drops out`);
    for(const [name,val] of after.instruments) { const old=before.instruments.get(name); if(old && old.key!==null && val.key!==null && old.key!==val.key) changes.push(`${friendly(name)} changes to ${val.text}`); }
    const unknown = [...before.instruments.values(), ...after.instruments.values()].some(v => v.key === null);
    return unknown ? null : changes;
  }
  for(let i=0;i<sections.length;i++) {
    const current=sections[i]; if (!current.regularCount) continue;
    const prev=sections.slice(0,i).reverse().find(s=>s.regularCount), next=sections.slice(i+1).find(s=>s.regularCount);
    current.changes=prev ? diff(prev,current) : null; current.nextChanges=next ? diff(current,next) : null;
    const values=[...current.instruments.values()];
    if(prev && current.changes && values.length && values.every(v=>v.key!==null)) { const mean=values.reduce((a,v)=>a+v.stability,0)/values.length; const count=current.changes.length; current.boundary=count<=2 && mean>=.6 ? 'clear' : count>=4 || mean<.4 ? 'weak' : 'moderate'; }
  }
  return {sections,detail};
}
