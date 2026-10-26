import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../src/kuavo_dataset_editor/static/app.js',import.meta.url),'utf8');
const refreshCode=source.slice(source.indexOf('const refreshFields = ['),source.indexOf('async function projectList() {'));
function harness() {
  const elements=new Map(),storage=new Map(),notifications=[];
  let reloads=0,dialog=false;
  const element=id=>{
    if(!elements.has(id))elements.set(id,{value:'',checked:false,style:{}});
    return elements.get(id);
  };
  const context=vm.createContext({
    $:element,document:{querySelector:()=>dialog},toast:text=>notifications.push(text),
    sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},
    window:{location:{reload:()=>reloads++}},
    render:()=>{element('task').value='server';element('startFrame').value=0;},
    renderImportPaths:()=>{},renderSourceList:()=>{},renderTimeline:()=>{},renderClips:()=>{},updatePresetSelection:()=>{},
    fetchFrame:async()=>{},
    updateExportMode:()=>{element('confirmExisting').checked=false;},
  });
  vm.runInContext(`let project={project_id:'p',revision:3,sources:[
    {source_id:'first',episodes:[{episode_id:0,num_frames:50}]},
    {source_id:'second',episodes:[{episode_id:2,num_frames:300}]}]},
    sourceId='second',episodeId=2,frame=125,selected='step',annotationTrack='goal',
    annotationDrafts={step:'another draft',goal:''},autoRangeEnd=false,cursorBoundary=null,
    selectedImportPaths=['/ssd/trial.bag'],pendingRequests=0,exportBusy=false,initializing=false;
    const clips=()=>[{id:'step'}];`,context);
  vm.runInContext(refreshCode,context);
  const run=code=>vm.runInContext(code,context);
  return {element,storage,notifications,context,run,reloads:()=>reloads,setDialog:value=>dialog=value};
}

{
  const h=harness(),e=h.element;
  e('task').value='unsaved instruction';e('startFrame').value='110';e('endFrame').value='140';
  e('startSeconds').value='3.';e('zoom').value='2';
  e('exportMode').value='append';e('confirmExisting').checked=true;e('hubRepo').value='team/example';
  h.run('refreshEditor()');
  assert.equal(h.reloads(),1);
  const saved=JSON.parse(h.storage.get('editorRefresh'));
  assert.equal(saved.sourceId,'second');assert.equal(saved.frame,125);
  h.run("sourceId='first';episodeId=0;frame=0;selected=null;annotationTrack='step'");
  h.context.snapshot=saved;h.run('restoreRefreshState(snapshot)');
  assert.equal(h.run('sourceId'),'second');assert.equal(h.run('episodeId'),2);assert.equal(h.run('frame'),125);
  assert.equal(h.run('annotationTrack'),'goal');assert.equal(h.run('selected'),'step');
  assert.equal(e('task').value,'unsaved instruction');assert.equal(e('startFrame').value,'110');
  assert.equal(e('endFrame').value,'140');assert.equal(e('startSeconds').value,'3.');
  assert.equal(e('hubRepo').value,'team/example');assert.equal(e('confirmExisting').checked,true);
  assert.equal(e('timeline').style.width,'200%');assert.equal(h.run('project.revision'),3);
  console.log('PASS: refresh restores the selected bag, episode, frame, draft bounds/text and output settings without saving labels');
}
{
  const h=harness();
  h.run('initializing=true;refreshEditor()');assert.equal(h.reloads(),0);
  h.run('initializing=false');
  h.run('pendingRequests=1;refreshEditor()');assert.equal(h.reloads(),0);
  h.run('pendingRequests=0;exportBusy=true;refreshEditor()');assert.equal(h.reloads(),0);
  h.run('exportBusy=false');h.setDialog(true);h.run('refreshEditor()');assert.equal(h.reloads(),0);
  assert.equal(h.storage.size,0);
  console.log('PASS: refresh waits for in-flight saves and export confirmation dialogs');
}
{
  const h=harness();
  h.context.sessionStorage.setItem=()=>{throw Error('storage unavailable');};
  h.run('refreshEditor()');assert.equal(h.reloads(),0);assert.match(h.notifications[0],/취소/);
  console.log('PASS: failed draft storage prevents refresh and input loss');
}
{
  const h=harness();h.storage.set('editorRefresh','earlier draft');
  h.run('project=null;refreshEditor()');
  assert.equal(h.storage.get('editorRefresh'),'earlier draft');assert.equal(h.reloads(),1);
  console.log('PASS: retrying a failed page load retains the earlier draft');
}
{
  const h=harness(),e=h.element;
  h.run("project.sources=[];sourceId=null;selected=null");e('importPath').value='/ssd/not-yet-imported.bag';
  h.run('refreshEditor()');h.context.snapshot=JSON.parse(h.storage.get('editorRefresh'));
  e('importPath').value='';h.run('restoreRefreshState(snapshot)');
  assert.equal(e('importPath').value,'/ssd/not-yet-imported.bag');
  console.log('PASS: an empty project also retains its pending import selection');
}
{
  let finishBody;
  const context=vm.createContext({pendingRequests:0,
    fetch:async()=>({ok:true,json:()=>new Promise(resolve=>{finishBody=resolve;})})});
  vm.runInContext(source.slice(source.indexOf('async function api('),source.indexOf('function action(')),context);
  const request=vm.runInContext("api('/edit',{task:'save'})",context);
  await new Promise(setImmediate);
  assert.equal(context.pendingRequests,1);
  finishBody({revision:4});await request;assert.equal(context.pendingRequests,0);
  context.fetch=async()=>{throw Error('network failure');};
  await assert.rejects(vm.runInContext("api('/edit',{})",context),/network failure/);
  assert.equal(context.pendingRequests,0);
  console.log('PASS: an edit blocks refresh until its response is read and clears the guard on failure');
}
{
  const h=harness(),e=h.element;
  h.run("let sharedPollBusy=false,playing=false,undo=[1],redo=[2];project.annotation_group='dates'");
  e('task').value='unfinished instruction';e('startSeconds').value='3.';e('startFrame').value='110';
  const updated=JSON.parse(h.run('JSON.stringify(project)'));updated.revision=4;updated.source_completed={second:true};
  const calls=[];
  let framesLoaded=0;
  h.context.fetchFrame=async()=>{framesLoaded++;};
  h.context.api=async path=>{calls.push(path);return path.endsWith('/revision')?{revision:4}:updated;};
  await h.run('pollLinkedProject()');
  assert.equal(h.run('project.revision'),4);assert.equal(h.run('project.source_completed.second'),true);
  assert.equal(h.run('sourceId'),'second');assert.equal(h.run('frame'),125);
  assert.equal(e('task').value,'unfinished instruction');assert.equal(e('startSeconds').value,'3.');
  assert.equal(e('startFrame').value,'110');assert.equal(h.run('undo.length+redo.length'),0);
  assert.deepEqual(calls,['/api/projects/p/revision','/api/projects/p']);
  assert.equal(framesLoaded,1,'rebuilding the camera elements must reload the preserved frame');
  console.log('PASS: linked project updates retain draft text, bounds, bag and frame, and clear stale undo history');
}
{
  const h=harness();
  h.run('frame=299;cursorBoundary=300;refreshEditor()');
  const state=JSON.parse(h.storage.get('editorRefresh'));
  assert.equal(state.cursorBoundary,300);
  h.run('cursorBoundary=null;frame=0');h.context.snapshot=state;h.run('restoreRefreshState(snapshot)');
  assert.equal(h.run('frame'),299);assert.equal(h.run('cursorBoundary'),300);
  console.log('PASS: refresh preserves a snapped exclusive endpoint separately from its displayed video frame');
}
{
  const h=harness();let calls=0;
  h.run("let sharedPollBusy=false,playing=false,undo=[],redo=[];project.annotation_group='dates'");
  h.context.api=async()=>{calls++;return {revision:4};};
  for(const guard of ['pendingRequests=1','playing=true','exportBusy=true']) {
    h.run(guard);await h.run('pollLinkedProject()');
    h.run('pendingRequests=0;playing=false;exportBusy=false');
  }
  h.setDialog(true);await h.run('pollLinkedProject()');assert.equal(calls,0);h.setDialog(false);
  let resolve;
  h.context.api=()=>new Promise(done=>{resolve=done;});
  const pending=h.run('pollLinkedProject()');h.run("project={project_id:'other',revision:10}");
  resolve({revision:4});await pending;
  assert.equal(h.run('project.project_id'),'other');assert.equal(h.run('project.revision'),10);
  console.log('PASS: linked updates wait for local interaction and discard responses after a project switch');
}
