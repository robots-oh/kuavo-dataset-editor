/* Pure picker state test: no browser, filesystem access, SSH or real data. */
import assert from "node:assert/strict";
import { setupFilePicker } from "../src/kuavo_dataset_editor/static/file-picker.mjs";
class Element {
  constructor() {
    this.children = [];
    this.value = "";
    this.listeners = {};
    this.open = false;
  }
  append(...nodes) {
    this.children.push(...nodes);
  }
  replaceChildren(...nodes) {
    this.children = nodes;
    if (nodes[0]?.value !== undefined) this.value = nodes[0].value;
  }
  setAttribute() {}
  addEventListener(name, fn) {
    this.listeners[name] = fn;
  }
  showModal() {
    this.open = true;
  }
  close() {
    this.open = false;
    this.listeners.close?.();
  }
}
const ids = new Map();
const el = (id) => {
  if (!ids.has(id)) ids.set(id, new Element());
  return ids.get(id);
};
globalThis.document = {
  getElementById: el,
  createElement: () => new Element(),
};
globalThis.Option = class extends Element {
  constructor(label, value) {
    super();
    this.textContent = label;
    this.value = value;
  }
};
const calls = [],
  selected = [],
  remote = [];
const listing = {
  path: "/bags",
  parent: null,
  entries: [
    { name: "one.bag", path: "/bags/one.bag", kind: "bag", size: 10 },
    { name: "two.bag", path: "/bags/two.bag", kind: "bag", size: 20 },
  ],
  total: 2,
  next_offset: null,
};
const api = async (path, body) => {
  calls.push({ path, body });
  if (path === "/api/files") return { roots: ["/bags"] };
  if (path.startsWith("/api/files?")) return listing;
  if (path === "/api/ssh/hosts") return { hosts: ["fixture-host"] };
  if (path === "/api/ssh/browse") return listing;
  throw Error(path);
};
const picker = setupFilePicker({
  api,
  notify: () => {},
  onSelect: (paths) => selected.push(paths),
  onSSH: async (host, paths) => remote.push({ host, paths }),
});
await picker.open("local");
el("pickerAll").onclick();
assert.equal(el("pickerUse").disabled, false);
await el("pickerUse").onclick();
assert.deepEqual(selected, [["/bags/one.bag", "/bags/two.bag"]]);
assert.equal(el("filePicker").open, false);
assert.equal(remote.length, 0);
await picker.open("ssh");
el("pickerHost").value = "fixture-host";
el("pickerHost").onchange();
await el("pickerGo").onclick();
el("pickerAll").onclick();
await el("pickerUse").onclick();
assert.deepEqual(remote, [
  { host: "fixture-host", paths: ["/bags/one.bag", "/bags/two.bag"] },
]);
assert.equal(selected.length, 1);
assert.ok(
  calls.some(
    (c) => c.path === "/api/ssh/browse" && c.body.host === "fixture-host",
  ),
);
console.log(
  "PASS: local multi-selection stages paths; SSH selection starts explicit download/import callback",
);
