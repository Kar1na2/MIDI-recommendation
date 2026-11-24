import fs from 'node:fs'; import path from 'node:path'; import {fileURLToPath} from 'node:url'; import assert from 'node:assert/strict';
import {buildSong} from './model.mjs'; import {summarizeSections} from './summaries.mjs';
const here=path.dirname(fileURLToPath(import.meta.url)), root=path.dirname(here), out=path.resolve(process.env.BEPOP_SITE_EXPORT_DIR || path.join(here,'site-export'));
const {catalog}=JSON.parse(fs.readFileSync(path.join(out,'app-data/catalog.json'),'utf8'));
const original=new Set((await import('./model.mjs')).parseCSV(fs.readFileSync(path.join(root,'mvp/sample.csv'),'utf8')).map(r=>r.id));
const read=(dir,n)=>{try{return JSON.parse(fs.readFileSync(path.join(dir,n+'.json'),'utf8'));}catch(e){if(e.code==='ENOENT') return {error:`${n}.json: source file is missing.`};throw e;}};
const names=['report','beats','lyrics','patterns','layers']; let count=0;
for(const item of catalog){
 const sourceDir=path.join(root,original.has(item.id)?'outputs':'mvp_325/songs',item.id), exportDir=path.join(out,'app-data/songs',item.id);
 const input=Object.fromEntries(names.map(n=>[n,read(sourceDir,n)])),output=Object.fromEntries(names.map(n=>[n,read(exportDir,n)]));
 const source=buildSong(item,input), result=buildSong(item,output);
 const signature=s=>s.rings.map(r=>({key:r.key,error:r.error,segments:r.segments.map(v=>[v.label,v.start,v.end,v.unassigned])}));
 assert.deepEqual(signature(result),signature(source),`ring mismatch: ${item.id}`);
 const simplify=s=>{const x=summarizeSections(s);return JSON.stringify({error:x.error,sections:x.sections.map(({bars,regularCount,instruments,label,changes,nextChanges,boundary})=>({bars,regularCount,instruments,label,changes,nextChanges,boundary}))},(k,v)=>v instanceof Map ? [...v.entries()] : v);};
 assert.equal(simplify(result),simplify(source),`summary mismatch: ${item.id}`); count++;
}
console.log(`${count} static exports match source ring timings and summaries.`);
