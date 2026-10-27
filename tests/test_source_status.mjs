import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {sourceLabelingStatus,matchesSourceFilter} from '../src/kuavo_dataset_editor/static/source-status.mjs';
import {activeSources,removedSources} from '../src/kuavo_dataset_editor/static/source-removal.mjs';

assert.equal(sourceLabelingStatus(null,'a').key,'not_started');
assert.equal(sourceLabelingStatus({tasks:{unused:'pick'}},'a').key,'not_started');
for (const key of ['clips','goals','exclusions']) {
  assert.equal(sourceLabelingStatus({[key]:[{source_id:'a'}]},'a').key,'in_progress');
  assert.equal(sourceLabelingStatus({[key]:[{source_id:'b'}]},'a').key,'not_started');
}
assert.equal(sourceLabelingStatus({source_completed:{a:true}},'a').key,'completed');
assert.equal(sourceLabelingStatus({source_completed:{a:false},clips:[{source_id:'a'}]},'a').key,'in_progress');
assert.equal(sourceLabelingStatus({source_completed:{a:'true'}},'a').key,'not_started');
assert.equal(sourceLabelingStatus({source_not_required:{a:true},clips:[{source_id:'a'}]},'a').key,'not_required');
assert.equal(sourceLabelingStatus({source_not_required:{a:false},clips:[{source_id:'a'}]},'a').key,'in_progress');
assert.equal(sourceLabelingStatus({source_not_required:{a:'true'}},'a').key,'not_started');
assert.equal(sourceLabelingStatus(null,'a').text,'미라벨');
console.log('PASS: source review status is explicit, source-scoped, and compatible with old projects');

const project={sources:['untouched','step','goal','excluded','done','unused'].map(source_id=>({source_id,path:`/ssd/${source_id}.bag`,fps:30,episodes:[{episode_id:0}]})),
  clips:[{source_id:'step'},{source_id:'done'},{source_id:'unused'}],goals:[{source_id:'goal'}],
  exclusions:[{source_id:'excluded'}],source_completed:{done:true},source_not_required:{unused:true}};
const before=JSON.stringify(project);
const visible=filter=>project.sources.filter(s=>matchesSourceFilter(project,s.source_id,filter)).map(s=>s.source_id);
assert.deepEqual(visible('all'),['untouched','step','goal','excluded','done','unused']);
assert.deepEqual(visible('completed'),['done']);
assert.deepEqual(visible('in_progress'),['step','goal','excluded']);
assert.deepEqual(visible('not_started'),['untouched']);
assert.deepEqual(visible('incomplete'),['untouched','step','goal','excluded']);
assert.deepEqual(visible('not_required'),['unused']);
assert.equal(JSON.stringify(project),before);
project.source_completed.step=true;
assert.deepEqual(visible('in_progress'),['goal','excluded']);
assert.deepEqual(visible('completed'),['step','done']);
console.log('PASS: each category filters only its own sources, retains the combined incomplete view, and never edits annotations');

const elements=new Map();
const $=id=>{
  if(!elements.has(id))elements.set(id,{value:'',textContent:'',children:[],
    replaceChildren(...items){this.children=items;}});
  return elements.get(id);
};
const context=vm.createContext({$,project,sourceLabelingStatus,matchesSourceFilter,activeSources,removedSources,sourceRemovalReady:true,
  sourceId:'step',frame:125,sourceCompletionReady:true,sourceNotRequiredReady:true,source:()=>project.sources[1],action:fn=>fn,
  document:{createElement:()=>({dataset:{},append:()=>{},setAttribute:()=>{}})}});
const ui=fs.readFileSync(new URL('../src/kuavo_dataset_editor/static/app.js',import.meta.url),'utf8');
vm.runInContext(ui.slice(ui.indexOf('function renderSourceList() {'),ui.indexOf('function render() {')),context);
for (const filter of ['all','completed','incomplete','in_progress','not_started','not_required']) {
  $('sourceStatusFilter').value=filter;
  vm.runInContext('renderSourceList()',context);
  assert.deepEqual($('sourceList').children.map(row=>row.textContent),visible(filter).map(id=>`${id}.bag`));
  assert.equal(context.sourceId,'step');assert.equal(context.frame,125);
}
assert.match($('sourceStatusSummary').textContent,/완료 2 · 라벨링 중 2 · 미라벨 1 · 불필요 1 \/ 전체 6개/);
context.sourceId='unused';vm.runInContext('renderSourceList()',context);
assert.equal($('sourceNotRequired').checked,true);
context.sourceNotRequiredReady=false;vm.runInContext('renderSourceList()',context);
assert.equal($('sourceNotRequired').disabled,true);
console.log('PASS: the shipped source list renders each filter and counts without changing the selected bag or playback position');
project.source_removed={step:true};
$('sourceStatusFilter').value='all';vm.runInContext('renderSourceList()',context);
assert.equal($('sourceList').children.length,5);
assert.equal($('removedSourceList').children.length,1);
assert.equal($('removedSourcesPanel').hidden,false);
assert.match($('removedSourcesSummary').textContent,/1개/);
assert.equal(project.clips.some(c=>c.source_id==='step'),true);
console.log('PASS: removed sources move to the recoverable list without deleting labels');
