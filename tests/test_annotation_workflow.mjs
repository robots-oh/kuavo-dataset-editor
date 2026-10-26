import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {nextTaskRange,defaultTaskRange,shortRangeWarning,resizeClipRange,snapBoundary,dragSelectionRange} from '../src/kuavo_dataset_editor/static/ranges.mjs';

// Run the shipped UI handlers with isolated inputs and an in-memory edit sink.
// No running server, real workspace, annotation revision, or video is touched.
const sourceText=fs.readFileSync(new URL('../src/kuavo_dataset_editor/static/app.js',import.meta.url),'utf8');
const between=(a,b)=>sourceText.slice(sourceText.indexOf(a),sourceText.indexOf(b));
function harness() {
  const elements=new Map();
  const element=id=>{
    if(!elements.has(id))elements.set(id,{value:'',style:{},checked:false,disabled:false,hidden:false,textContent:'',replaceChildren:()=>{},getBoundingClientRect:()=>({left:0,width:1000}),querySelector:()=>({style:{}})});
    return elements.get(id);
  };
  const saved=[],listeners=new Map(),notifications=[];
  const context=vm.createContext({
    $:element,document:{activeElement:null},performance:{now:()=>0},requestAnimationFrame:()=>{},
    nextTaskRange,defaultTaskRange,shortRangeWarning,resizeClipRange,snapBoundary,dragSelectionRange,
    episode:()=>({num_frames:16589}),source:()=>({fps:30}),clips:()=>[],
    range:()=>[Number(element('startFrame').value),Number(element('endFrame').value)],
    renderTimeline:()=>{},renderClips:()=>{},renderSourceList:()=>{},
    dataInspector:{cursorChanged:()=>{}},fetchFrame:()=>{},
    time:f=>(f/30).toFixed(2)+'s',confirm:()=>true,toast:s=>notifications.push(s),action:fn=>fn,
    edit:async(op,payload)=>saved.push({op,...payload}),
    window:{addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name)},
    setAnnotationTrack:()=>true,dragBoundaries:()=>[0,8322,7999,16589],showSnap:()=>{},
  });
  vm.runInContext(`let frame=3605,playing=false,selected=null,autoRangeEnd=false,sourceId='bag',episodeId=0,cursorBoundary=null,seekDragging=false,seekAlt=false;
    let lastTick=0,playOrigin=0;`,context);
  for(const [a,b] of [
    ['function deselect() {','async function fetchFrame() {'],
    ['function seek(value,','function togglePlay() {'],
    ['function togglePlay() {','async function edit(operation, extra) {'],
    ['function currentRange() {','async function history(backward) {'],
    ['function pos(e) {','$("timeline").onpointerdown ='],
    ['$("timeline").onpointerdown =','function dragPoint(event,'],
    ['function dragPoint(event,','function dragBoundaries('],
    ['function clipPointer(e, c, el,layer) {','async function startImport(demo) {'],
  ]) vm.runInContext(between(a,b),context);
  const run=code=>vm.runInContext(code,context);
  return {element,context,run,saved,listeners,notifications};
}

{
  const h=harness(),e=h.element;
  e('startFrame').value=3262;e('endFrame').value=3606;e('task').value='carry';
  await h.run('applyLabel()');
  assert.equal(Number(e('startFrame').value),3606);
  assert.equal(Number(e('endFrame').value),3606); // pending, not a phantom frame
  await assert.rejects(h.run('applyLabel()'),/아직 선택된 프레임/);
  h.run('seek(3742)');
  assert.equal(Number(e('endFrame').value),3743);
  e('task').value='place';await h.run('applyLabel()');
  assert.equal(h.saved[1].start_frame,3606);assert.equal(h.saved[1].end_frame,3743);
  assert.equal(h.saved.length,2);
  console.log('PASS: sequential apply follows the live playhead; pending next ranges never save phantom frames');
}
{
  const h=harness(),e=h.element;
  e('startFrame').value=3606;e('endFrame').value=3700;e('task').value='manual';
  h.run('autoRangeEnd=false;seek(3742)');
  await h.run('applyLabel()');assert.equal(h.saved[0].end_frame,3700);
  h.run("selected='existing';autoRangeEnd=false");
  e('startFrame').value=8322;e('endFrame').value=8438;
  h.run('seek(9000)');assert.equal(Number(e('endFrame').value),8438);
  e('endFrame').value=8323;await h.run('updateClip()');
  assert.equal(h.saved[1].end_frame,8323);assert.match(h.notifications.at(-1),/1프레임/);
  console.log('PASS: explicit ranges and existing clips stay fixed; intentional one-frame edits warn but still save');
}
{
  const h=harness(),e=h.element;
  e('startFrame').value=3606;e('endFrame').value=3700;
  h.context.document.activeElement=e('endSeconds');e('endSeconds').value='123.';
  h.run('renderSelection()');assert.equal(e('endSeconds').value,'123.');
  console.log('PASS: live range rendering does not replace a seconds field while it is being typed');
}
{
  const h=harness(),e=h.element;
  h.context.selectClip=()=>{h.run("selected='short';autoRangeEnd=false");e('startFrame').value=8322;e('endFrame').value=8438;};
  const x=8438/16589*1000;
  h.context.down={clientX:x,target:{dataset:{edge:'end'}},stopPropagation:()=>{},preventDefault:()=>{}};
  h.run("clipPointer(down,{id:'short',start_frame:8322,end_frame:8438},null,'step')");
  h.listeners.get('pointermove')({clientX:x+1,altKey:false});
  await h.listeners.get('pointerup')({type:'pointerup'});
  assert.equal(h.saved.length,0);assert.equal(Number(e('endFrame').value),8438);
  console.log('PASS: a handle click or one-pixel jitter does not save a resize');
}
{
  const h=harness();
  h.context.pointer={clientX:8438/16589*1000,altKey:false};
  const point=h.run('dragPoint(pointer,null,8322)');
  assert.equal(point.frame,8438);
  console.log('PASS: dragging a new selection away from its anchor does not snap back into a one-frame range');
}
{
  const h=harness(),e=h.element;
  h.context.copy=x=>JSON.parse(JSON.stringify(x));h.context.taskOptions=()=>[];
  h.run("let project={project_id:'p',revision:0,name:'merge',tasks:{carry:'carry'}},annotationReady=true,annotationTrack='step',undo=[],redo=[];selected='right';");
  const merged={project_id:'p',revision:1,name:'merge',tasks:{carry:'carry'},
    clips:[{id:'left',task_id:'carry',start_frame:7998,end_frame:8400,merged_clip_ids:['right']}]};
  h.context.api=async()=>merged;h.context.clips=()=>merged.clips;
  vm.runInContext(between('async function edit(operation, extra) {','function currentRange() {'),h.context);
  e('startFrame').value=8322;e('endFrame').value=8400;
  await h.run("edit('update_clip',{})");
  assert.equal(h.run('selected'),'left');
  assert.equal(Number(e('startFrame').value),7998);assert.equal(Number(e('endFrame').value),8400);
  console.log('PASS: merge retargets the selected ID and inspector bounds, so a second edit cannot trim the earlier segment');
}
{
  const h=harness(),requests=[];
  h.context.copy=x=>JSON.parse(JSON.stringify(x));h.context.render=()=>{};
  h.run("let project={project_id:'p',revision:2,exclusions:[{id:'excluded'}]},undo=[{project_id:'p',revision:1,exclusions:[]}],redo=[],revisionHistoryRestore=true;");
  h.context.api=async(path,body,method)=>{
    requests.push({path,body:JSON.parse(JSON.stringify(body)),method});
    return {project_id:'p',revision:h.run('project.revision')+1,
      exclusions:body.restore_revision===1?[]:[{id:'excluded'}]};
  };
  vm.runInContext(between('async function history(backward) {','function pos(e) {'),h.context);
  await h.run('history(true)');
  assert.deepEqual(requests[0],{path:'/api/projects/p',body:{restore_revision:1,expected_revision:2},method:'PUT'});
  assert.equal(h.run('project.exclusions.length'),0);
  await h.run('history(false)');
  assert.equal(requests[1].body.restore_revision,2);
  assert.equal(h.run('project.exclusions.length'),1);
  h.context.api=async()=>{throw Error('revision conflict');};
  const stackBefore=h.run('JSON.stringify([undo,redo,project])');
  await assert.rejects(h.run('history(true)'),/revision conflict/);
  assert.equal(h.run('JSON.stringify([undo,redo,project])'),stackBefore);
  console.log('PASS: undo/redo sends only server revision IDs and retains history after a failed restore');
}
for (const operation of ['source_completion','source_not_required','remove_clip']) {
  const h=harness(),e=h.element;
  h.context.copy=x=>JSON.parse(JSON.stringify(x));h.context.taskOptions=()=>[];
  h.run("let project={project_id:'p',revision:0,name:'review',tasks:{pick:'saved instruction'}},annotationReady=true,annotationTrack='step',undo=[],redo=[];selected='clip';");
  const clip={id:'clip',task_id:'pick',start_frame:10,end_frame:50};
  h.context.api=async()=>({project_id:'p',revision:1,name:'review',tasks:{pick:'saved instruction'},source_completed:{bag:true}});
  h.context.clips=()=>[clip];
  vm.runInContext(between('async function edit(operation, extra) {','function currentRange() {'),h.context);
  e('task').value='unsaved draft';e('startFrame').value=12;e('endFrame').value=45;
  h.context.operation=operation;
  await h.run("edit(operation,{source_id:'bag',completed:true,track:operation==='remove_clip'?'goal':'step'})");
  assert.equal(e('task').value,'unsaved draft');
  assert.equal(Number(e('startFrame').value),12);assert.equal(Number(e('endFrame').value),45);
  console.log(`PASS: ${operation} does not replace another open annotation draft or its bounds`);
}
{
  const h=harness();
  h.context.rightClick={button:2,target:{dataset:{edge:'end'}},
    stopPropagation:()=>{throw Error('right click entered the drag handler');},preventDefault:()=>{}};
  h.run("clipPointer(rightClick,{id:'clip',start_frame:100,end_frame:200},null,'goal')");
  assert.equal(h.listeners.size,0);assert.equal(h.saved.length,0);assert.equal(h.run('frame'),3605);
  console.log('PASS: right-clicking a trim handle never starts a resize or seeks the video');
}
{
  const h=harness(),e=h.element;
  h.context.clips=()=>[{id:'existing',start_frame:600,end_frame:900}];
  h.context.confirm=()=>{throw Error('A boundary-aligned range must not ask to replace its neighbor.');};
  e('startFrame').value=300;e('task').value='align';
  h.run('autoRangeEnd=true;seek(600,600)');
  assert.equal(Number(e('endFrame').value),600);
  h.run('renderClock()');assert.equal(Number(e('endFrame').value),600);
  assert.equal(e('playhead').style.left,600/16589*100+'%');
  await h.run('applyLabel()');
  assert.equal(h.saved[0].start_frame,300);assert.equal(h.saved[0].end_frame,600);
  assert.equal(Number(e('startFrame').value),600);assert.equal(Number(e('endFrame').value),600);
  console.log('PASS: snapped cursor saves the exact exclusive boundary, without a one-frame overlap or phantom next range');
}
for(const altKey of [false,true]) {
  const h=harness(),e=h.element;
  h.context.dragBoundaries=()=>[0,600,900,16589];
  e('startFrame').value=300;
  h.context.down={clientX:300/16589*1000,altKey:false,button:0,
    target:{closest:()=>null},preventDefault:()=>{}};
  h.run('$("timeline").onpointerdown(down)');
  h.listeners.get('pointermove')({clientX:595/16589*1000,altKey});
  h.listeners.get('pointerup')({type:'pointerup'});
  assert.equal(Number(e('startFrame').value),300);
  assert.equal(Number(e('endFrame').value),altKey?595:600);
  assert.equal(h.run('frame'),altKey?594:599);
  assert.equal(h.run('cursorBoundary'),altKey?null:600);
  assert.equal(h.saved.length,0);
  console.log(`PASS: blue selection ${altKey?'uses Alt for free positioning':'and yellow cursor align exactly to a neighboring clip'} without saving labels`);
}
{
  const h=harness(),e=h.element;
  vm.runInContext(between('$("prev").onclick =','$("speed").onchange ='),h.context);
  vm.runInContext(between('$("markIn").onclick =','$("startFrame").oninput ='),h.context);
  h.context.dragBoundaries=()=>[0,600,900,16589];
  e('seek').onpointerdown({altKey:false});e('seek').value=595;e('seek').oninput();
  assert.equal(h.run('cursorBoundary'),600);e('markOut').onclick();
  assert.equal(Number(e('endFrame').value),600);
  e('prev').onclick();assert.equal(h.run('frame'),599);assert.equal(h.run('cursorBoundary'),null);
  h.listeners.get('pointerup')();e('seek').value=598;e('seek').oninput();
  assert.equal(h.run('frame'),598);assert.equal(h.run('cursorBoundary'),null);
  e('seek').onpointerdown({altKey:true});e('seek').value=595;e('seek').oninput();
  assert.equal(h.run('frame'),595);assert.equal(h.run('cursorBoundary'),null);
  h.run('seek(16588,16589)');e('markOut').onclick();
  assert.equal(Number(e('endFrame').value),16589);
  e('prev').onclick();assert.equal(h.run('frame'),16588);
  console.log('PASS: pointer scrubbing snaps, O keeps the exact boundary, while keyboard steps and Alt remain precise even at episode end');
}
{
  const h=harness();
  h.context.clips=()=>[{id:'existing',start_frame:300,end_frame:900}];
  vm.runInContext(between('$("split").onclick =','$("undo").onclick ='),h.context);
  h.run('seek(900,900)');
  await assert.rejects(h.element('split').onclick(),/clip 내부/);
  assert.equal(h.saved.length,0);
  h.run('seek(899)');await h.element('split').onclick();
  assert.equal(h.saved[0].op,'split');assert.equal(h.saved[0].frame,899);
  console.log('PASS: S still splits at an intentional interior frame, and a snapped endpoint cannot create an accidental one-frame fragment');
}
{
  const h=harness(),e=h.element;
  h.run(`let project={exclusions:[
    {id:'excluded',source_id:'bag',start_frame:3000,end_frame:6000},
    {id:'other-bag',source_id:'other',episode_id:0,start_frame:2000,end_frame:4000},
    {id:'other-episode',source_id:'bag',episode_id:1,start_frame:5000,end_frame:7000}
  ]};`);
  vm.runInContext(between('function dragBoundaries(','function showSnap('),h.context);
  const before=h.run('JSON.stringify(project)');
  assert.deepEqual(Array.from(h.run('dragBoundaries()')),[0,16589,3000,6000]);
  for(const edge of [3000,6000]) {
    h.context.pointer={clientX:(edge-5)/16589*1000,altKey:false};
    assert.equal(h.run('dragPoint(pointer).frame'),edge);
    h.context.pointer.altKey=true;
    assert.equal(h.run('dragPoint(pointer).frame'),edge-5);
  }
  vm.runInContext(between('$("prev").onclick =','$("speed").onchange ='),h.context);
  e('seek').onpointerdown({altKey:false});e('seek').value=5995;e('seek').oninput();
  assert.equal(h.run('frame'),6000);assert.equal(h.run('cursorBoundary'),6000);
  h.listeners.get('pointerup')();
  for(const [anchor,moving,start,end,preview] of [[1000,2995,1000,3000,2999],[9000,6005,6000,9000,6000]]) {
    h.context.down={clientX:anchor/16589*1000,altKey:false,button:0,
      target:{closest:()=>null},preventDefault:()=>{}};
    h.run('$("timeline").onpointerdown(down)');
    h.listeners.get('pointermove')({clientX:moving/16589*1000,altKey:false});
    h.listeners.get('pointerup')({type:'pointerup'});
    assert.equal(Number(e('startFrame').value),start);assert.equal(Number(e('endFrame').value),end);
    assert.equal(h.run('frame'),preview);
    assert.equal(h.run('cursorBoundary'),moving<anchor?start:end);
  }
  assert.equal(h.run('JSON.stringify(project)'),before);assert.equal(h.saved.length,0);
  console.log('PASS: exclusion boundaries snap the cursor, scrubber and both drag directions; other bags/episodes and Alt remain independent, without changing labels');
}
