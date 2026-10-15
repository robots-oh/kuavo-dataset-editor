import assert from "node:assert/strict";
import {
  defaultTaskRange,
  nextTaskRange,
  snapBoundary,
  dragSelectionRange,
} from "../src/kuavo_dataset_editor/static/ranges.mjs";

assert.deepEqual(nextTaskRange(30, 29, 120), { start: 30, end: 31 });
assert.deepEqual(nextTaskRange(30, 59, 120), { start: 30, end: 60 });
assert.deepEqual(nextTaskRange(120, 119, 120), { start: 120, end: 120 });
assert.deepEqual(
  defaultTaskRange([{ end_frame: 30 }, { end_frame: 70 }], 0, 120, 30),
  { start: 70, end: 71 },
);
assert.deepEqual(defaultTaskRange([], 0, 120, 30), { start: 0, end: 30 });
console.log(
  "PASS: automatic next start, current playhead end, episode end, reopened clips, first clip",
);
assert.deepEqual(snapBoundary(104, [100, 200], 1), {
  frame: 100,
  snapped: true,
});
assert.deepEqual(snapBoundary(120, [100, 200], 1), {
  frame: 120,
  snapped: false,
});
assert.deepEqual(snapBoundary(104, [100, 200], 1, true), {
  frame: 104,
  snapped: false,
});
assert.deepEqual(snapBoundary(104, [100, 200], 4), {
  frame: 104,
  snapped: false,
});
assert.deepEqual(dragSelectionRange(30, 60, 120), {
  start: 30,
  end: 60,
  preview: 59,
});
assert.deepEqual(dragSelectionRange(60, 30, 120), {
  start: 30,
  end: 60,
  preview: 30,
});
assert.deepEqual(dragSelectionRange(120, 120, 120), {
  start: 119,
  end: 120,
  preview: 119,
});
console.log(
  "PASS: near/far snapping, Alt bypass, zoom sensitivity, live preview frame for both drag directions",
);
