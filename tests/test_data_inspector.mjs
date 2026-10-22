import assert from 'node:assert/strict';
import { setupDataInspector } from '../src/kuavo_dataset_editor/static/data-inspector.mjs';
class Element {
  constructor() {this.value='';this.children=[];this.width=960;this.height=160;this.checked=false;}
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.children=children;if(children[0]?.value!==undefined)this.value=children[0].value;}
  setAttribute(key,value) {this[key]=value;}
  getContext() {return new Proxy({},{get:()=>()=>{}});}
}
const elements=new Map();
const el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
globalThis.document={getElementById:el,createElement:()=>new Element()};
globalThis.Option=class extends Element {constructor(text,value){super();this.textContent=text;this.value=value;}};
let project={project_id:'p',revision:1},schema='kuavo_wholebody_v1';
let context={project_id:'p',source:{source_id:'a',fps:10},frame:0,episode:0,playing:false};
let resolveRaw=null;
const calls=[],messages=[];
const api=async path=>{
  calls.push(path);
  if(path.includes('/raw/'))return new Promise(resolve=>{resolveRaw=resolve;});
  if(path.endsWith('/row?sid=a&episode=0&frame=0'))return {time_s:0,frame:0,kept:true,fields:[{feature:'action',index:0,name:'knee_joint',unit:'rad',value:0,valid:false,reason_name:'topic_absent'}]};
  if(path.includes('/series?'))return {points:[{time_s:0,value:null}]};
  return {project_id:'p',project_revision:1,schema,frames:5,episodes:1,sources:[],reason_codes:{},quality:null};
};
const inspector=setupDataInspector({api,getContext:()=>context,getProject:()=>project,getSchema:()=>schema,
  preparePreview:async()=> 'job',notify:text=>messages.push(text)});
inspector.cursorChanged();
const pending=el('rawLoadTopics').onclick();
context={...context,source:{source_id:'b',fps:10}};inspector.cursorChanged();
resolveRaw({path:'/old.bag',duration_s:1,topics:[{topic:'<script>',types:['String'],messages:1}]});
await pending;
assert.equal(el('rawTopic').children.length,0,'old source cannot overwrite new topic list');
context={...context,source:{source_id:'a',fps:10}};inspector.cursorChanged();
await inspector.calculate();
assert.equal(el('previewCalculate').disabled,false);
inspector.observeJobs([{id:'job',status:'completed'}]);
await new Promise(resolve=>setImmediate(resolve));
assert.deepEqual(inspector.previewOptions(),{preview_id:'job',quality:null});
const cells=el('previewValues').children[1].children[0].children;
assert.equal(cells[3].textContent,'0 (placeholder)');assert.equal(cells[5].textContent,'0');
assert.equal(cells[6].textContent,'topic_absent');
project={...project,revision:2};assert.deepEqual(inspector.previewOptions(),{});
project={...project,revision:1};schema='legacy';assert.deepEqual(inspector.previewOptions(),{});
schema='kuavo_wholebody_v1';el('previewDisconnect').onclick();assert.deepEqual(inspector.previewOptions(),{});
assert.equal(el('previewValues').children.length,0);
assert.equal(messages.length,0);
console.log('PASS: original-source race, preview job binding, placeholder masks, stale revisions/schema and disconnect');
