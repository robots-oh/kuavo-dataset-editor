import assert from "node:assert/strict";
import { setupQualityReview } from "../src/kuavo_dataset_editor/static/quality-review.mjs";

class Element {
  constructor() { this.children = []; this.listeners = {}; this.value = ""; this.open = false; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; if (nodes[0]?.value !== undefined) this.value = nodes[0].value; }
  setAttribute() {}
  addEventListener(name, fn) { this.listeners[name] = fn; }
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.close?.(); }
}
const elements = new Map();
const el = (id) => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
globalThis.document = { getElementById: el, createElement: () => new Element() };
globalThis.Option = class extends Element {
  constructor(label, value) { super(); this.textContent = label; this.value = value; }
};
const review = setupQualityReview();
const base = { source_name: "sample.bag", episode_id: 0, feature: "observation.state", missing_frames: 1,
  duration_s: .033, start_frame: 5, end_frame: 6, start_time_s: .167, end_time_s: .2, sample_gap_s: .235 };
const report = { token: "sample-token", checked_frames: 100, invalid_frames: 2,
  issues: [{ ...base, id: "middle", method: "linear" }, { ...base, id: "edge", method: null }] };
const result = review(report);
assert.equal(el("qualityApply").disabled, true);
el("qualityRepairAll").onclick();
assert.equal(el("qualityApply").disabled, true); // No extrapolation or silent exclusion.
const select = el("qualityRows").children[1].children[3].children[0];
select.value = "exclude"; select.onchange();
assert.equal(el("qualityApply").disabled, false);
el("qualityApply").onclick();
assert.deepEqual(await result, { token: "sample-token", decisions: { middle: "linear", edge: "exclude" } });
assert.equal(el("qualityReview").open, false);
const cancelled = review(report);
assert.equal(el("qualityApply").disabled, true); // Choices are not silently reused.
el("qualityCancel").onclick();
assert.equal(await cancelled, null);
console.log("PASS: no default repair, explicit choices, unrepairable gaps, cancel, and fresh review state");
