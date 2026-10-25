import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../src/kuavo_dataset_editor/static/app.js',import.meta.url),'utf8');
const html=fs.readFileSync(new URL('../src/kuavo_dataset_editor/static/index.html',import.meta.url),'utf8');
assert.match(html,/<select id="exportScope">\s*<option value="source">현재 선택한 원본만/);
const fields=new Map(Object.entries({exportMode:'append',exportScope:'source',exportSchema:'legacy',
  exportAnnotationTrack:'step',repoName:'common',exportPath:'/exports/common',exportSourceName:'',confirmExisting:''})
  .map(([id,value])=>[id,{value,checked:true}]));
const context=vm.createContext({$:id=>fields.get(id),sourceId:'a',annotationReady:true,sourceExportReady:true,
  source:()=>({original_path:'/bags/140.bag'}),dataInspector:{previewOptions:()=>({})}});
vm.runInContext(source.slice(source.indexOf('function exportSourceId() {'),source.indexOf('let exportBusy = false;')),context);
const run=code=>vm.runInContext(code,context);
assert.equal(run('exportBody().source_id'),'a');
run("sourceId='b'");assert.equal(run('exportBody().source_id'),'b');
run('renderExportSource()');assert.match(fields.get('exportSourceName').textContent,/140.bag/);
fields.get('exportScope').value='project';assert.equal(run('exportBody().source_id'),undefined);
run('renderExportSource()');assert.match(fields.get('exportSourceName').textContent,/일괄 처리/);
fields.get('exportScope').value='source';fields.get('exportMode').value='replace';
assert.equal(run('exportBody().source_id'),undefined);
run('renderExportSource()');assert.match(fields.get('exportSourceName').textContent,/프로젝트의 라벨 전체/);
run('sourceExportReady=false');assert.throws(()=>run('exportBody()'),/껐다 켜세요/);
console.log('PASS: one-source default, project-wide opt-in, replace scope and old-server guard');
