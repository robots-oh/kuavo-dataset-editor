import { TASK_PRESETS, BOX_PICK_PRESETS } from "./tasks.mjs";

// Only the draft is changed. Saving/relabeling remains an explicit editor action.
export function setupTaskPresets({ input, presets, locationPanel, locations }) {
  const createButton = (preset, pick) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = preset.label;
    button.title = preset.instruction;
    button.dataset.preset = preset.id;
    button.setAttribute("aria-pressed", "false");
    button.onclick = pick;
    return button;
  };
  function sync() {
    const instruction = input.value.trim();
    const location = BOX_PICK_PRESETS.find((p) => p.instruction === instruction);
    const isPick = !!location || instruction === TASK_PRESETS.find((p) => p.id === "pick_box").instruction;
    locationPanel.hidden = !isPick;
    for (const button of presets.children) {
      const preset = TASK_PRESETS.find((p) => p.id === button.dataset.preset);
      button.setAttribute("aria-pressed", String(preset.id === "pick_box" ? isPick : preset.instruction === instruction));
      if (preset.id === "pick_box") button.setAttribute("aria-expanded", String(!locationPanel.hidden));
    }
    for (const button of locations.children) {
      button.setAttribute("aria-pressed", String(button.dataset.preset === location?.id));
    }
  }
  presets.replaceChildren(...TASK_PRESETS.map((preset) => {
    const button = createButton(preset, () => {
      if (preset.id === "pick_box") {
        // Opening choices must not erase a manually written draft or relabel a clip.
        locationPanel.hidden = false;
        button.setAttribute("aria-expanded", "true");
      } else {
        input.value = preset.instruction;
        sync();
      }
    });
    if (preset.id === "pick_box") {
      button.title = "상단/중간 선반의 왼쪽·오른쪽 상자를 선택하세요.";
      button.setAttribute("aria-controls", locationPanel.id);
      button.setAttribute("aria-expanded", "false");
    }
    return button;
  }));
  locations.replaceChildren(...BOX_PICK_PRESETS.map((preset) => createButton(preset, () => {
    input.value = preset.instruction;
    sync();
  })));
  sync();
  return { sync };
}
