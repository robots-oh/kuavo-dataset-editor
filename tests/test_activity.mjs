import assert from 'node:assert/strict';
import {activityAt,DEFAULT_THRESHOLDS,gripperConvention,needsRawGripperFeedback} from '../src/kuavo_dataset_editor/static/activity.mjs';
const data={series:{
  left_gripper:[0,1,.4,null],left_gripper_change:[null,.5,-.2,null],left_gripper_command:[1,1,0,null],
  left_arm_recorded:[1,1,1,null],left_arm_command:[null,1,0,null],left_arm_motion:[null,0,2,null],
  torso_motion:[null,0,2,null],
  base_command_speed:[null,0,.03,0],base_command_yaw:[null,3,0,0],
  base_motion_speed:[null,0,null,0],base_motion_yaw:[null,0,5,null],
}};
assert.equal(activityAt(data,0).left_gripper.text,'닫힘');
assert.equal(activityAt(data,0).left_arm_command.text,'명령 있음 · 변화 미확인');
assert.equal(activityAt(data,1).left_gripper.change,'열리는 중');
assert.equal(activityAt(data,2).left_gripper.change,'닫히는 중');
assert.equal(activityAt(data,1).left_arm_command.kind,'command');
assert.equal(activityAt(data,1).left_arm_motion.text,'정지');
assert.equal(activityAt(data,2).left_arm_command.text,'목표 유지');
assert.equal(activityAt(data,2).left_arm_motion.text,'움직임');
assert.equal(activityAt(data,2,{...DEFAULT_THRESHOLDS,arm:3}).left_arm_motion.text,'정지');
assert.equal(activityAt(data,1).torso_motion.text,'정지');
assert.equal(activityAt(data,2).torso_motion.text,'움직임');
assert.equal(activityAt(data,2,{...DEFAULT_THRESHOLDS,arm:3}).torso_motion.text,'정지');
assert.equal(activityAt(data,3).torso_motion.text,'미기록');
assert.equal(activityAt(data,1).base_command.text,'이동 명령'); // Rotation alone is motion.
assert.equal(activityAt(data,2).base_motion.text,'이동·회전'); // One known active axis suffices.
assert.equal(activityAt(data,3).base_motion.text,'미기록'); // Unknown yaw cannot prove a stop.
assert.equal(activityAt(data,3).left_gripper.text,'미기록');
assert.equal(activityAt(data,100).left_arm_motion.text,'미기록');
const feedback=activityAt({series:{left_gripper:[-.007]}},0).left_gripper;
assert.equal(feedback.text,'닫힘');assert.equal(feedback.percent,0);
assert.ok(Math.abs(feedback.rawPercent+.7)<1e-10);
assert.equal(activityAt({window_frames:8,series:{}},0).left_arm_motion.text,'판정 전');
assert.equal(activityAt({window_frames:8,series:{}},9).left_arm_motion.text,'미기록');
assert.equal(activityAt({window_frames:8,series:{}},0).torso_motion.text,'판정 전');
const source={profile:{signals:[
  {key:'observation.gripper',topic:'/leju_claw_state',field:'data.position',scale:.01},
  {key:'action.gripper',topic:'/leju_claw_command',field:'data.position',scale:.01},
]}};
const recorded={gripper_convention:gripperConvention(source),series:{
  left_gripper:[0,1,.4,-.007,null],left_gripper_change:[null,1,-.6,0,null],
  left_gripper_command:[0,1,.25,0,null],
  right_gripper:[1,0],right_gripper_change:[null,-1],right_gripper_command:[1,0],
}};
assert.equal(activityAt(recorded,0).left_gripper.text,'열림');
assert.equal(activityAt(recorded,0).left_gripper.percent,100);
assert.equal(activityAt(recorded,0).left_gripper.target,1);
assert.equal(activityAt(recorded,1).left_gripper.text,'닫힘');
assert.equal(activityAt(recorded,1).left_gripper.change,'닫히는 중');
assert.equal(activityAt(recorded,1).left_gripper.target,0);
assert.equal(activityAt(recorded,2).left_gripper.change,'열리는 중');
assert.equal(activityAt(recorded,2).left_gripper.percent,60);
assert.equal(activityAt(recorded,2).left_gripper.target,.75);
assert.equal(activityAt(recorded,1).right_gripper.change,'열리는 중');
assert.equal(activityAt(recorded,3).left_gripper.percent,100);
assert.ok(Math.abs(activityAt(recorded,3).left_gripper.rawPercent+.7)<1e-10);
assert.equal(activityAt(recorded,4).left_gripper.text,'미기록');
assert.deepEqual(gripperConvention({}),{state:'open_fraction',command:'open_fraction'});
assert.equal(gripperConvention({profile:{signals:[{...source.profile.signals[0],scale:-.01}]}}).state,'open_fraction');
assert.equal(activityAt({series:recorded.series},0).left_gripper.text,'닫힘');
const rwhSource={profile:{signals:[
  {...source.profile.signals[0],topic:'/rwh/capture/claw_state'},
  {...source.profile.signals[1],topic:'/rwh/capture/claw_cmd_hold'},
]}};
assert.deepEqual(gripperConvention(rwhSource),{state:'closed_fraction',command:'closed_fraction'});
assert.equal(activityAt({...recorded,gripper_convention:gripperConvention(rwhSource)},0).left_gripper.text,'열림');
assert.equal(activityAt({...recorded,gripper_convention:gripperConvention(rwhSource)},1).left_gripper.text,'닫힘');
assert.equal(needsRawGripperFeedback(rwhSource),false);
assert.equal(needsRawGripperFeedback(source),true);
assert.deepEqual(gripperConvention({profile:{signals:rwhSource.profile.signals,display:{gripper:{
  state_convention:'open_fraction',command_convention:'open_fraction',
}}}}),{state:'open_fraction',command:'open_fraction'});
console.log('Activity cue semantics passed.');
