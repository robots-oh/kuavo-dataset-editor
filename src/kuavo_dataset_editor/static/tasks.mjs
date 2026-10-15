// Clicking a preset fills the draft only; it never rewrites saved annotations.
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
