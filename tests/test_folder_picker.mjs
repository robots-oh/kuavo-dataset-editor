import assert from 'node:assert/strict';
import { setupFolderPicker } from '../src/kuavo_dataset_editor/static/folder-picker.mjs';
class Element {
  constructor() { this.value=''; this.children=[]; this.listeners={}; this.open=false; }
  replaceChildren(...children) { this.children=children; if (children[0]?.value!==undefined) this.value=children[0].value; }
  addEventListener(name,callback) { this.listeners[name]=callback; }
  showModal() { this.open=true; }
  close() { this.open=false; this.listeners.close?.(); }
}
const elements=new Map();
const el=id=>{ if (!elements.has(id)) elements.set(id,new Element()); return elements.get(id); };
globalThis.document={getElementById:el,createElement:()=>new Element()};
globalThis.Option=class extends Element { constructor(label,value) { super(); this.textContent=label; this.value=value; } };
const calls=[];
let pending=null;
const api=async path=>{
  calls.push(path);
  if (path==='/api/folders') return {roots:['/workspace','/ssd']};
  const url=new URL(path,'http://editor');
  const folder=url.searchParams.get('path');
  if (folder==='/ssd/missing') throw Error('missing folder');
  if (folder==='/ssd/slow') return new Promise(resolve=>{pending=resolve;});
  return {path:folder,parent:folder==='/ssd' ? null : '/ssd',total:1,next_offset:null,
          entries:[{name:'bags <not markup>',path:'/ssd/bags',kind:'directory'}]};
};
const picker=setupFolderPicker({api});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
let result=picker.open('/ssd'); await tick();
assert.equal(el('downloadFolderUse').disabled,false);
assert.equal(el('downloadFolderRoot').value,'/ssd');
assert.equal(el('downloadFolderEntries').children[0].textContent,'📁 bags <not markup>');
await el('downloadFolderEntries').children[0].onclick();
el('downloadFolderUse').onclick();
assert.equal(await result,'/ssd/bags');
result=picker.open('/ssd/missing'); await tick();
assert.equal(el('downloadFolderPath').value,'/ssd');
el('downloadFolderCancel').onclick(); assert.equal(await result,null);
// Failed navigation must not leave the old directory enabled for selection.
result=picker.open('/ssd'); await tick();
el('downloadFolderPath').value='/ssd/missing'; await el('downloadFolderGo').onclick();
assert.equal(el('downloadFolderUse').disabled,true);
el('downloadFolderCancel').onclick(); assert.equal(await result,null);
// A response arriving after close cannot overwrite the next dialog session.
const old=picker.open('/ssd/slow'); await tick();
el('downloadFolderCancel').onclick();
const next=picker.open('/workspace'); await tick();
pending({path:'/ssd/slow',entries:[],total:0}); await tick();
assert.equal(await old,null);
assert.equal(el('downloadFolderPath').value,'/workspace');
el('downloadFolderUse').onclick(); assert.equal(await next,'/workspace');
assert.ok(calls.every(path=>path.startsWith('/api/folders')));
console.log('PASS: folder-only selection, cancellation, stale requests and error selection');
