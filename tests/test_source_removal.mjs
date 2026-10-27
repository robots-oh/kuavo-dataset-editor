import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {activeSources,removedSources,isSourceRemoved,setupSourceRemovalConfirmation} from '../src/kuavo_dataset_editor/static/source-removal.mjs';

const ui=fs.readFileSync(new URL('../src/kuavo_dataset_editor/static/app.js',import.meta.url),'utf8');
const handler=ui.slice(ui.indexOf('async function setSourceRemoved('),ui.indexOf('function pos(e) {'));
function fixture() {
  const nodes=new Map(),requests=[],notices=[];
  let allowed=true;
  const context=vm.createContext({activeSources,removedSources,isSourceRemoved,
    $:id=>{if(!nodes.has(id))nodes.set(id,{});return nodes.get(id);},
    copy:structuredClone,confirmSourceRemoval:async()=>allowed,toast:t=>notices.push(t),
    render:()=>{},renderSourceList:()=>{},renderExportSource:()=>{},fetchFrame:async()=>{},});
  vm.runInContext(`let project={project_id:'p',revision:2,name:'demo',sources:['a','b'].map(source_id=>({source_id,path:'/cache/'+source_id,episodes:[{episode_id:0}]})),clips:[{source_id:'a',task_id:'pick'}]},
    sourceId='a',episodeId=0,frame=22,selected='clip',cursorBoundary=null,playing=true,
    sourceRemovalReady=true,undo=[],redo=[];
    const source=()=>project?.sources.find(s=>s.source_id===sourceId&&!isSourceRemoved(project,s.source_id));
    const resumeFrame=()=>0;`,context);
  context.api=async(path,body)=>{
    requests.push([path,body]);
    const p=vm.runInContext('project',context);
    return {...structuredClone(p),revision:p.revision+1,source_removed:{...p.source_removed,[body.source_id]:body.removed}};
  };
  vm.runInContext(handler,context);
  return {run:code=>vm.runInContext(code,context),context,requests,notices,setConfirm:v=>allowed=v};
}

const h=fixture();
await h.run("setSourceRemoved('a',true)");
assert.equal(h.run('sourceId'),'b');assert.equal(h.run('playing'),false);
assert.equal(h.run('project.clips.length'),1);
assert.equal(h.run('removedSources(project).length'),1);
assert.equal(h.run('undo.length'),1);
assert.match(h.notices.at(-1),/캐시.*보존/);
await h.run("setSourceRemoved('a',false)");
assert.equal(h.run('sourceId'),'b','restoration does not discard the current bag draft');
assert.equal(h.run('activeSources(project).length'),2);
await h.run("setSourceRemoved('b',true)");
assert.equal(h.run('sourceId'),'a');
await h.run("setSourceRemoved('a',true)");
assert.equal(h.run('sourceId'),null);
assert.equal(h.run('activeSources(project).length'),0);
await h.run("setSourceRemoved('a',false)");
assert.equal(h.run('sourceId'),'a','last removed bag can be restored without reimport');
console.log('PASS: remove/restore switches only when needed, handles an empty project, and keeps annotation data');

const cancelled=fixture();cancelled.setConfirm(false);
await cancelled.run("setSourceRemoved('a',true)");
assert.equal(cancelled.requests.length,0);assert.equal(cancelled.run('undo.length'),0);
const stale=fixture();stale.context.api=async()=>{throw Error('stale revision');};
await assert.rejects(stale.run("setSourceRemoved('a',true)"),/stale/);
assert.equal(stale.run('project.revision'),2);assert.equal(stale.run('sourceId'),'a');
assert.equal(stale.run('undo.length'),0);
stale.run('sourceRemovalReady=false');
await assert.rejects(stale.run("setSourceRemoved('a',true)"),/재시작/);
console.log('PASS: confirmation cancellation, old servers and stale edits do not change project state');

const switching=fixture();let complete;
switching.context.api=()=>new Promise(resolve=>{complete=resolve;});
const pending=switching.run("setSourceRemoved('a',true)");
await new Promise(setImmediate);
switching.run("project={project_id:'another',sources:[]}");
complete({project_id:'p',source_removed:{a:true}});await pending;
assert.equal(switching.run('project.project_id'),'another');
assert.equal(switching.run('undo.length'),0);
console.log('PASS: a delayed removal response never replaces a newly selected project');

const listeners={},elements=new Map();
for(const id of ['sourceRemovalDialog','sourceRemovalCancel','sourceRemovalApply','sourceRemovalName'])
  elements.set(id,{addEventListener:(name,fn)=>listeners[name]=fn,focus:()=>{}});
const dialog=elements.get('sourceRemovalDialog');
dialog.showModal=()=>{dialog.open=true;};dialog.close=()=>{dialog.open=false;listeners.close();};
const ask=setupSourceRemovalConfirmation({getElementById:id=>elements.get(id)});
const rejected=ask('<bag name>');
assert.equal(elements.get('sourceRemovalName').textContent,'<bag name>');
elements.get('sourceRemovalCancel').onclick();assert.equal(await rejected,false);
const accepted=ask('demo.bag');elements.get('sourceRemovalApply').onclick();assert.equal(await accepted,true);
const escaped=ask('demo.bag');listeners.cancel();dialog.close();assert.equal(await escaped,false);
console.log('PASS: removal requires explicit confirmation; cancel/Escape never approve it');
