import test from 'node:test';
import assert from 'node:assert/strict';
import { summarizeSections } from './summaries.mjs';
const row=(bar,instrument,events,extra={})=>({bar,instrument,active:true,events,steps_per_bar:16,onset_count:new Set(events.map(e=>e[0])).size,...extra});
const section=(id,start,end)=>({rhythmic_section_id:id,start_bar:start,end_bar:end,start_s:start*4,end_s:end*4});
test('profiles merge layers, filter voice/silence, classify hits and skip irregular neighbours',()=>{
 const bars=[0,1,2,3,4].map(i=>({bar:i,start_s:i*4,end_s:(i+1)*4,beats_per_bar:i===2?3:4}));
 const rows=[row(0,'piano__chord',[[0,60],[4,64]]),row(1,'piano__chord',[[0,62],[4,66]]),row(3,'piano__chord',[[0,64],[4,68]]),row(4,'piano__chord',[[0,65],[4,69]]),row(0,'voice__line',[[0,60]]),row(0,'piano__irregular',[[0,55]]),row(0,'violin__line',[],{active:false}),row(3,'drums__line',[[0,36],[4,38],[8,42],[12,49]])];
 const song={beats:{bars},patterns:{bars:rows},layers:{pattern_notes:[]},report:{rhythmic_sections:[section('a',0,2),section('odd',2,3),section('b',3,5)]},rings:[{key:'vocals',segments:[{start:0,end:9,label:'Vocal segment 1'},{start:9,end:20,label:'Chorus'}]}]};
 const s=summarizeSections(song).sections;
 assert.equal(s[0].label,'Vocal segment 1'); assert.equal(s[2].label,'Chorus');
 assert.equal(s[0].instruments.size,1); assert.equal(s[0].instruments.get('piano').text,'sparse rising chords on the beat');
 assert.equal(s[0].instruments.get('piano').stability,1);
 assert.equal(s[1].bars[0].text,'Irregular bar (3 beats), not compared');
 assert.deepEqual(s[2].changes,['drums enters']); assert.equal(s[2].boundary,'clear');
 assert.match(s[2].instruments.get('drums').text,/kick, snare, hi-hat, crash/);
});
test('single attacks use pattern notes, and missing evidence blocks comparison',()=>{
 const bars=[0,1].map(i=>({bar:i,start_s:i*4,end_s:i*4+4,beats_per_bar:4}));
 const song={beats:{bars},patterns:{bars:[{bar:0,instrument:'bass__line',active:true,onset_count:1,steps_per_bar:16},{bar:1,instrument:'bass__line',active:true,onset_count:1,steps_per_bar:16}]},layers:{pattern_notes:[{bar:0,instrument:'bass__line',step:2,pitch:40}]},report:{rhythmic_sections:[section('a',0,1),section('b',1,2)]},rings:[]};
 const s=summarizeSections(song).sections;
 assert.equal(s[0].instruments.get('bass').text,'sparse repeated-note line off the beat');
 assert.match(s[1].instruments.get('bass').text,/unavailable/); assert.equal(s[1].changes,null); assert.equal(s[1].boundary,undefined);
});
