import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../src/kuavo_dataset_editor/static/app.js',import.meta.url),'utf8');
const html=fs.readFileSync(new URL('../src/kuavo_dataset_editor/static/index.html',import.meta.url),'utf8');
class Element {
  children=[];
  textContent='';
  hidden=false;
  style={};
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.children=children;}
}
const groups=new Map([...html.matchAll(/data-job-group="([^"]+)"/g)].map(([,key])=>{
  const current=new Element(),history=new Element(),summary=new Element(),list=new Element(),hubResults=new Element();
  history.open=key==='export';
  history.querySelector=selector=>selector==='summary'?summary:list;
  const hubHistories=new Map(['plans','uploads','cancelled'].map(kind=>{
    const history=new Element(),summary=new Element(),list=new Element();
    history.open=kind!=='cancelled';
    history.querySelector=selector=>selector==='summary'?summary:list;
    return [`.job-hub-${kind}`,{history,summary,list}];
  }));
  const group=new Element();group.dataset={jobGroup:key};
  group.querySelector=selector=>selector==='.job-current'?current:selector==='.job-hub-results'?hubResults:hubHistories.get(selector)?.history || history;
  return [key,{group,current,history,summary,list,hubHistories,hubResults}];
}));
assert(groups.has('preview'),'the page needs a separate preview section');
const hubHtml=html.slice(html.indexOf('<section data-job-group="hub"'),html.indexOf('<section data-job-group="other"'));
for (const kind of ['plans','uploads'])
  assert.match(hubHtml,new RegExp(`job-hub-${kind}" open hidden`),'completed HF previews and uploads start expanded');
assert.match(hubHtml,/job-hub-cancelled" hidden/,'cancelled HF jobs start collapsed');
const elements=new Map([
  ['jobList',{querySelectorAll:()=>[...groups.values()].map(g=>g.group)}],
  ['projectSelect',{options:[{value:'a',textContent:'October 17'},{value:'b',textContent:'October 22'}]}],
  ['hubPath',new Element()],
]);
elements.get('hubPath').focus=()=>{};
const published=Date.parse('2026-10-24T15:02:37Z')/1000;
const hubPlan={repo_id:'team/dataset',private:true,exists:true,url:'https://huggingface.co/datasets/team/dataset',
  snapshot:{files:[],bytes:0,frames:22009,omitted:[]},delete:[],preserved_remote:[]};
const jobs=[
  {id:'export-b',kind:'export',status:'completed',project_id:'b',created:published-100,
    result:{path:'/exports/b',published_at:published,source_names:['trial_b.bag'],mode:'append',
      episodes_added:3,frames_added:2500,episodes:15,frames:22000}},
  {id:'export-a',kind:'export',status:'completed',project_id:'a',created:published-300,
    result:{path:'/exports/a',published_at:published-200}},
  {id:'export-old-b',kind:'export',status:'completed',project_id:'b',created:published+100,
    result:{path:'/exports/b',published_at:published-150,source_names:['old_b.bag']}},
  {id:'preview-a',kind:'export-preview',status:'completed',project_id:'a'},
  {id:'preview-b',kind:'export-preview',status:'completed',project_id:'b'},
  {id:'running-a',kind:'export',status:'running',project_id:'a'},
  {id:'failed-b',kind:'export',status:'failed',project_id:'b',error:'failed export'},
  {id:'import-b',kind:'import',status:'completed',project_id:'b'},
  {id:'import-ready',kind:'import-batch',status:'running',project_id:'a',result:{sources:[
    {source_id:'ready-a',path:'/cache/a',preparation:{events:'on_export'},import_cache:{reused:true}},
  ]}},
  {id:'plan-old',kind:'hub-plan',status:'completed',created:published-400,result:hubPlan},
  {id:'upload-old',kind:'hub-upload',status:'completed',created:published-500,result:{repo_id:'team/old',url:'https://huggingface.co/datasets/team/old',commit:'old'}},
  {id:'cancelled-old',kind:'hub-plan',status:'cancelled',created:published-600,message:'old repository preview'},
  {id:'plan-new',kind:'hub-plan',status:'completed',created:published+200,result:hubPlan},
  {id:'upload-new',kind:'hub-upload',status:'completed',created:published+250,result:{repo_id:'team/dataset',url:'https://huggingface.co/datasets/team/dataset',commit:'new'}},
  {id:'hub-running',kind:'hub-plan',status:'running',created:published+300},
  {id:'hub-failed',kind:'hub-upload',status:'failed',created:published+150,error:'upload failed'},
  {id:'cancelled-new',kind:'hub-upload',status:'cancelled',created:published-100},
];
const before=JSON.stringify(jobs),requests=[],observed=[];
let hideHub=false;
const projectA={project_id:'a',sources:[]};
const context=vm.createContext({
  incrementalImportReady:true,
  project:projectA,$:id=>elements.get(id),
  document:{createElement:()=>new Element()},
  api:async(path,body)=>{requests.push([path,body]);return structuredClone(hideHub?jobs.filter(j=>!j.kind.startsWith('hub-')):jobs);},
  action:fn=>fn,console,
  dataInspector:{observeJobs:jobs=>observed.push(jobs),loadPreview:()=>{}},
});
vm.runInContext(source.slice(source.indexOf('function jobDate(seconds) {'),source.indexOf('const reviewQuality = setupQualityReview();')),context);
const poll=()=>vm.runInContext('pollJobs()',context);
await poll();
const actual=groups.get('export'),preview=groups.get('preview');
assert.equal(actual.list.children.length,3,'completed exports from both projects are visible');
assert.equal(actual.summary.textContent,'실제 Export 완료 · 전체 프로젝트 3개');
assert.equal(preview.summary.textContent,'미리보기 완료 · 현재 프로젝트 1개');
assert.equal(preview.list.children.length,1);
assert.equal(actual.current.children.length,1,'another project’s failed export stays out of this project');
const otherExport=actual.list.children[0];
assert(otherExport.children.some(el=>el.textContent==='프로젝트: October 22'));
assert(otherExport.children.some(el=>el.textContent==='/exports/b'));
assert.match(otherExport.children[0].textContent,/가장 최근 Export.*현재 폴더 결과/);
assert(otherExport.children.some(el=>el.textContent==='원본: trial_b.bag'));
assert(otherExport.children.some(el=>el.textContent.includes('게시 2026-10-25 00:02:37 KST')));
assert(otherExport.children.some(el=>el.textContent.includes('이번 추가: 3 episodes · 2,500프레임 → 완료 후 합계: 15 episodes · 22,000프레임')));
const historical=actual.list.children[1];
assert.match(historical.children[0].textContent,/이전 Export 기록/,'publication time determines order even after an older-started job is resumed');
assert(historical.children.some(el=>el.textContent.includes('공통 경로는 이후 Export로 갱신')));
assert(historical.children.some(el=>el.textContent==='이 경로의 최신 결과를 Hub 업로드 경로로 사용'));
otherExport.children.find(el=>el.textContent==='이 결과를 Hub 업로드 경로로 사용').onclick();
assert.equal(elements.get('hubPath').value,'/exports/b','each result retains its own upload path');
assert.equal(context.project,projectA,'using a result does not switch the labeling project');
assert(!observed.at(-1).some(j=>j.id==='preview-b'));
const hub=groups.get('hub');
const hubPlans=hub.hubHistories.get('.job-hub-plans');
const hubUploads=hub.hubHistories.get('.job-hub-uploads');
const hubCancelled=hub.hubHistories.get('.job-hub-cancelled');
assert.equal(hub.group.hidden,false);
assert.equal(hubPlans.summary.textContent,'업로드 미리보기 완료 · 전송 안 함 2개');
assert.equal(hubUploads.summary.textContent,'실제 Hub 업로드 완료 2개');
assert.equal(hubCancelled.summary.textContent,'취소된 이전 작업 2개');
assert.deepEqual(hub.hubResults.children,[hubUploads.history,hubPlans.history],
  'the completed category with the newest job comes first');
const logPath=card=>card.children.at(-1).href;
assert.deepEqual(hubPlans.list.children.map(logPath),['/api/jobs/plan-new/log','/api/jobs/plan-old/log']);
assert.deepEqual(hubUploads.list.children.map(logPath),['/api/jobs/upload-new/log','/api/jobs/upload-old/log']);
assert.deepEqual(hubCancelled.list.children.map(logPath),['/api/jobs/cancelled-new/log','/api/jobs/cancelled-old/log']);
assert.match(hubPlans.list.children[0].children[0].textContent,/가장 최근 · 업로드 미리보기 · 완료 · team\/dataset/);
assert.match(hubUploads.list.children[0].children[0].textContent,/가장 최근 · 실제 Hub 업로드 · 완료 · team\/dataset/);
assert(hubPlans.list.children.every(card=>card.children.some(el=>el.textContent==='대상·공개 범위 확인 후 실제 업로드')));
assert(hubUploads.list.children.every(card=>!card.children.some(el=>el.textContent==='대상·공개 범위 확인 후 실제 업로드')));
assert.deepEqual(hub.current.children.map(logPath),['/api/jobs/hub-running/log','/api/jobs/hub-failed/log'],
  'cancelled history stays out of current jobs, while failures stay visible');
assert.equal(hubCancelled.history.open,false);
hubPlans.history.open=false;
hubCancelled.history.open=true;
actual.history.open=false;
context.project={project_id:'b',sources:[]};
await poll();
assert.equal(actual.list.children.length,3,'switching projects keeps all completed exports');
assert.equal(actual.history.open,false,'polling preserves the history toggle');
assert.equal(preview.list.children.length,1);
assert(preview.list.children[0].children.some(el=>el.textContent==='프로젝트: October 22'));
assert.equal(actual.current.children.length,1);
assert.equal(groups.get('import').list.children.length,1);
assert.equal(hubPlans.history.open,false,'polling preserves preview history being collapsed');
assert.equal(hubCancelled.history.open,true,'polling preserves cancelled history being expanded');
assert.equal(hubUploads.list.children.length,2,'HF history remains available while switching labeling projects');
hideHub=true;
await poll();
assert.equal(hub.group.hidden,true,'empty HF history is hidden');
assert.equal(hubPlans.history.hidden,true);
assert.equal(hubUploads.history.hidden,true);
assert.equal(hubCancelled.history.hidden,true);
assert.equal(JSON.stringify(jobs),before,'displaying history must not modify job records');
assert(requests.every(([path,body])=>path==='/api/jobs' && body===undefined),'history only reads the API');
console.log('PASS: export and HF histories stay separate, newest first, preserve toggles, and never submit an upload while displaying history');

let finishPoll, readCalls=0;
context.api=()=>{readCalls++;return new Promise(resolve=>{finishPoll=resolve;});};
const inFlight=poll();
await poll();
assert.equal(readCalls,1,'a slow poll must not overlap with the next timer tick');
finishPoll([]);await inFlight;
assert.equal(vm.runInContext('jobPollPending',context),false);
context.api=async()=>{throw Error('offline');};
context.console={warn:()=>{}};
await poll();
assert.equal(vm.runInContext('jobPollPending',context),false,'errors also release the polling guard');
console.log('PASS: job polling stays single-flight and recovers after errors');
