import assert from "node:assert/strict";
import { TASK_PRESETS, BOX_PICK_PRESETS } from "../src/kuavo_dataset_editor/static/tasks.mjs";
import { setupTaskPresets } from "../src/kuavo_dataset_editor/static/task-presets.mjs";

assert.equal(TASK_PRESETS.length, 4);
assert.equal(new Set(TASK_PRESETS.map((p) => p.id)).size, 4);
assert.deepEqual(
  TASK_PRESETS.map((p) => p.label),
  ["랙 정렬", "상자 집기", "conveyor 앞으로 옮기기", "상자 놓기"],
);
assert.deepEqual(
  TASK_PRESETS.map((p) => p.instruction),
  [
    "Align the robot with the rack.",
    "Pick up the box from the rack.",
    "Carry the box to the front of the conveyor.",
    "Put down the box.",
  ],
);
assert.equal(BOX_PICK_PRESETS.length,4);
assert.deepEqual(BOX_PICK_PRESETS.map(p=>p.instruction),[
  'Pick up the box from the left side of the middle shelf of the rack.',
  'Pick up the box from the right side of the middle shelf of the rack.',
  'Pick up the box from the left side of the top shelf of the rack.',
  'Pick up the box from the right side of the top shelf of the rack.',
]);
assert.equal(new Set([...TASK_PRESETS,...BOX_PICK_PRESETS].map(p=>p.id)).size,8);
class Element {
  constructor() {this.children=[];this.dataset={};this.attrs={};this.value='';this.hidden=false;}
  replaceChildren(...children) {this.children=children;}
  setAttribute(key,value) {this.attrs[key]=value;}
}
globalThis.document={createElement:()=>new Element()};
const input=new Element(),presets=new Element(),locationPanel=new Element(),locations=new Element();
locationPanel.id='boxPickOptions';
const picker=setupTaskPresets({input,presets,locationPanel,locations});
assert.equal(locationPanel.hidden,true);
input.value='A manually written draft';
presets.children[1].onclick();
assert.equal(locationPanel.hidden,false);
assert.equal(input.value,'A manually written draft','opening choices preserves draft');
assert.equal(presets.children[1].attrs['aria-expanded'],'true');
for (const [i,preset] of BOX_PICK_PRESETS.entries()) {
  locations.children[i].onclick();
  assert.equal(input.value,preset.instruction);
  assert.equal(presets.children[1].attrs['aria-pressed'],'true');
  assert.equal(locations.children[i].attrs['aria-pressed'],'true');
  assert.equal(locations.children.filter(b=>b.attrs['aria-pressed']==='true').length,1);
}
presets.children[0].onclick();
assert.equal(input.value,TASK_PRESETS[0].instruction);
assert.equal(locationPanel.hidden,true);
// Selecting/reusing a saved location instruction reopens its choices.
input.value=BOX_PICK_PRESETS[2].instruction;picker.sync();
assert.equal(locationPanel.hidden,false);
assert.equal(locations.children[2].attrs['aria-pressed'],'true');
// Old generic instructions and custom text stay byte-for-byte unchanged.
input.value=TASK_PRESETS[1].instruction;picker.sync();
assert.equal(input.value,'Pick up the box from the rack.');
assert.equal(locations.children.filter(b=>b.attrs['aria-pressed']==='true').length,0);
input.value='Custom task';picker.sync();assert.equal(locationPanel.hidden,true);
console.log("PASS: four main tasks, four location instructions, draft-only picker and saved/legacy label sync");
