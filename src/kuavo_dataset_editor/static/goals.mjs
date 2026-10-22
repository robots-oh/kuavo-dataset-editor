import { BOX_PICK_PRESETS } from './tasks.mjs';

export const GOAL_PRESETS=Object.freeze(BOX_PICK_PRESETS.map(p=>Object.freeze({
  id:p.id.replace('pick_box','move_box'),label:p.label,
  instruction:p.instruction.replace('Pick up the box from ','Move the box from ').replace(/\.$/,' to the front of the conveyor.'),
})));
export const trackClips=(project,track,sid,episode)=>(project?.[track==='goal'?'goals':'clips']||[])
  .filter(c=>c.source_id===sid && (c.episode_id??0)===episode).sort((a,b)=>a.start_frame-b.start_frame);
export function coveringGoal(project,step) {
  return (project?.goals||[]).find(g=>g.source_id===step.source_id && (g.episode_id??0)===(step.episode_id??0)
    && g.start_frame<=step.start_frame && step.end_frame<=g.end_frame)||null;
}
export function goalDraft(project,sid,episode,start,end) {
  const steps=trackClips(project,'step',sid,episode).filter(c=>c.start_frame<end && start<c.end_frame);
  const picks=BOX_PICK_PRESETS.filter(p=>steps.some(c=>project.tasks[c.task_id]===p.instruction));
  return {start,end,steps:steps.length,instruction:picks.length===1?GOAL_PRESETS[BOX_PICK_PRESETS.indexOf(picks[0])].instruction:
    'Move the box from the rack to the front of the conveyor.'};
}
export function contiguousSteps(project,sid,episode,frame) {
  const steps=trackClips(project,'step',sid,episode);
  let i=steps.findIndex(c=>c.start_frame<=frame && frame<c.end_frame);
  if(i<0)i=steps.findIndex(c=>c.start_frame>=frame);
  if(i<0 && steps.length)i=steps.length-1;
  if(i<0)return null;
  let a=i,b=i;
  while(a>0 && steps[a-1].end_frame===steps[a].start_frame && !steps[a].new_episode)a--;
  while(b+1<steps.length && steps[b].end_frame===steps[b+1].start_frame && !steps[b+1].new_episode)b++;
  return goalDraft(project,sid,episode,steps[a].start_frame,steps[b].end_frame);
}
