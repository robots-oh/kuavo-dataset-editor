import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {nextTaskRange,defaultTaskRange,shortRangeWarning,resizeClipRange,snapBoundary} from '../src/kuavo_dataset_editor/static/ranges.mjs';

// Run the shipped UI handlers with isolated inputs and an in-memory edit sink.
// No running server, real workspace, annotation revision, or video is touched.
const sourceText=fs.readFileSync(new URL('../src/kuavo_dataset_editor/static/app.js',import.meta.url),'utf8');
const between=(a,b)=>sourceText.slice(sourceText.indexOf(a),sourceText.indexOf(b));
function harness() {
  const elements=new Map();
  const element=id=>{
    if(!elements.has(id))elements.set(id,{value:'',style:{},checked:false,disabled:false,hidden:false,textContent:'',getBoundingClientRect:()=>({left:0,width:1000}),querySelector:()=>({style:{}})});
    return elements.get(id);
  };
  const saved=[],listeners=new Map(),notifications=[];
  const context=vm.createContext({
    $:element,document:{activeElement:null},performance:{now:()=>0},
    nextTaskRange,defaultTaskRange,shortRangeWarning,resizeClipRange,snapBoundary,
    episode:()=>({num_frames:16589}),source:()=>({fps:30}),clips:()=>[],
    range:()=>[Number(element('startFrame').value),Number(element('endFrame').value)],
    renderTimeline:()=>{},renderClips:()=>{},
    dataInspector:{cursorChanged:()=>{}},fetchFrame:()=>{},
    time:f=>(f/30).toFixed(2)+'s',confirm:()=>true,toast:s=>notifications.push(s),
    edit:async(op,payload)=>saved.push({op,...payload}),
    window:{addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name)},
    setAnnotationTrack:()=>true,dragBoundaries:()=>[0,8322,7999,16589],showSnap:()=>{},
  });
  vm.runInContext(`let frame=3605,playing=false,selected=null,autoRangeEnd=false,sourceId='bag',episodeId=0;
    let lastTick=0,playOrigin=0;`,context);
  for(const [a,b] of [
    ['function deselect() {','async function fetchFrame() {'],
    ['function seek(value) {','function togglePlay() {'],
    ['function currentRange() {','async function history(backward) {'],
    ['function pos(e) {','$("timeline").onpointerdown ='],
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
