import assert from "node:assert/strict";
import {
  defaultTaskRange,
  nextTaskRange,
  snapBoundary,
  dragSelectionRange,
  resizeClipRange,
  shortRangeWarning,
} from "../src/kuavo_dataset_editor/static/ranges.mjs";

assert.deepEqual(nextTaskRange(30, 29, 120), { start: 30, end: 30 });
assert.deepEqual(nextTaskRange(30, 10, 120), { start: 30, end: 30 });
assert.deepEqual(nextTaskRange(30, 30, 120), { start: 30, end: 31 });
assert.deepEqual(nextTaskRange(30, 59, 120), { start: 30, end: 60 });
assert.deepEqual(nextTaskRange(120, 119, 120), { start: 120, end: 120 });
assert.deepEqual(
  defaultTaskRange([{ end_frame: 30 }, { end_frame: 70 }], 0, 120, 30),
  { start: 70, end: 70 },
);
assert.deepEqual(defaultTaskRange([], 0, 120, 30), { start: 0, end: 1 });
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

// Real affected range: a 116-frame clip in a 16,589-frame bag. Even at its
// original end, the old 8px snap targeted the preceding task's boundary.
for(const width of [420,800,1000,1200,2000]) {
  const r=resizeClipRange(8322,8438,'end',8438,[8322,7999],width/16589,16589);
  assert.equal(r.end,8438);assert.equal(r.start,8322);
}
assert.equal(resizeClipRange(8322,8438,'end',8440,[8322,8441],1,16589).end,8441);
assert.equal(resizeClipRange(8322,8438,'start',8322,[8438],1000/16589,16589).start,8322);
assert.equal(resizeClipRange(8322,8438,'end',8440,[8441],1,16589,true).end,8440);
assert.equal(resizeClipRange(8322,8438,'end',8300,[8322],1,16589,true).end,8323);
assert.match(shortRangeWarning(30,31,30),/1프레임/);
assert.match(shortRangeWarning(30,44,30),/0.5초 미만/);
assert.equal(shortRangeWarning(30,45,30),'');
assert.equal(shortRangeWarning(30,30,30),'');
assert.equal(shortRangeWarning(30,31,1),'');
console.log('PASS: zoomed-out resize preserves short clips; explicit 1-frame ranges are allowed with warnings');
