import assert from "node:assert/strict";
import { TASK_PRESETS } from "../src/kuavo_dataset_editor/static/tasks.mjs";

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
console.log("PASS: four fixed English task instruction candidates");
