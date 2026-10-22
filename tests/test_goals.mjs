import assert from 'node:assert/strict';
import {GOAL_PRESETS,trackClips,coveringGoal,goalDraft,contiguousSteps} from '../src/kuavo_dataset_editor/static/goals.mjs';
import {BOX_PICK_PRESETS} from '../src/kuavo_dataset_editor/static/tasks.mjs';
const p={tasks:{a:'Align',b:BOX_PICK_PRESETS[0].instruction,c:'Carry',g:GOAL_PRESETS[0].instruction},
  clips:[{id:'a',source_id:'s',episode_id:0,start_frame:0,end_frame:5,task_id:'a'},
    {id:'b',source_id:'s',episode_id:0,start_frame:5,end_frame:10,task_id:'b'},
    {id:'c',source_id:'s',episode_id:0,start_frame:10,end_frame:15,task_id:'c'},
    {id:'d',source_id:'s',episode_id:0,start_frame:20,end_frame:25,task_id:'c'}],
  goals:[{id:'g',source_id:'s',episode_id:0,start_frame:0,end_frame:15,task_id:'g'}]};
assert.equal(GOAL_PRESETS.length,4);
assert.equal(GOAL_PRESETS[0].instruction,'Move the box from the left side of the middle shelf of the rack to the front of the conveyor.');
assert.equal(trackClips(p,'goal','s',0).length,1);
assert.equal(coveringGoal(p,p.clips[1]).id,'g');assert.equal(coveringGoal(p,p.clips[3]),null);
const before=JSON.stringify(p),draft=contiguousSteps(p,'s',0,6);
assert.deepEqual([draft.start,draft.end,draft.steps],[0,15,3]);
assert.equal(draft.instruction,GOAL_PRESETS[0].instruction);
assert.equal(goalDraft(p,'s',0,20,25).instruction,'Move the box from the rack to the front of the conveyor.');
assert.equal(contiguousSteps(p,'s',0,25).start,20);
p.clips[2].new_episode=true;assert.equal(contiguousSteps(p,'s',0,6).end,10);delete p.clips[2].new_episode;
assert.equal(JSON.stringify(p),before);assert.equal(contiguousSteps(p,'s',1,0),null);
console.log('PASS: independent tracks, containment, location-aware Goal drafts and no bridging gaps/resets');
