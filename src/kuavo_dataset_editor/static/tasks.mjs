// Clicking a preset fills the draft only; it never rewrites saved annotations.
// Left/right is from a person facing the front of the rack, not camera pixels.
export const BOX_PICK_PRESETS = Object.freeze([
  Object.freeze({
    id: "pick_box_top_left",
    label: "상단 선반 · 왼쪽",
    instruction: "Pick up the box from the left side of the top shelf of the rack.",
  }),
  Object.freeze({
    id: "pick_box_top_right",
    label: "상단 선반 · 오른쪽",
    instruction: "Pick up the box from the right side of the top shelf of the rack.",
  }),
  Object.freeze({
    id: "pick_box_middle_left",
    label: "중간 선반 · 왼쪽",
    instruction: "Pick up the box from the left side of the middle shelf of the rack.",
  }),
  Object.freeze({
    id: "pick_box_middle_right",
    label: "중간 선반 · 오른쪽",
    instruction: "Pick up the box from the right side of the middle shelf of the rack.",
  }),
]);

export const TASK_PRESETS = Object.freeze([
  Object.freeze({
    id: "align_rack",
    label: "랙 정렬",
    instruction: "Align the robot with the rack.",
  }),
  Object.freeze({
    id: "pick_box",
    label: "상자 집기",
    instruction: "Pick up the box from the rack.",
  }),
  Object.freeze({
    id: "carry_to_conveyor",
    label: "conveyor 앞으로 옮기기",
    instruction: "Carry the box to the front of the conveyor.",
  }),
  Object.freeze({
    id: "put_down_box",
    label: "상자 놓기",
    instruction: "Put down the box.",
  }),
]);
