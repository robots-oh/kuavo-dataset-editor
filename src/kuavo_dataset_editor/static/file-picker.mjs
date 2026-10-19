/* Shared local/SSH data picker. All filenames are rendered as text, never HTML. */
export function setupFilePicker({ api, onSelect, onSSH, notify }) {
  const $ = (id) => document.getElementById(id);
  const dialog = $("filePicker");
  let mode = "local",
    host = "",
    folder = null,
    listing = null,
    generation = 0;
  let selected = new Map(),
    rows = [];
  const size = (bytes) =>
    bytes == null
      ? "폴더"
      : bytes >= 1024 ** 3
        ? `${(bytes / 1024 ** 3).toFixed(2)} GiB`
        : `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
  const message = (text) => {
    $("pickerStatus").textContent = text;
  };
  function count() {
    $("pickerCount").textContent =
      `${selected.size}개 선택 · 폴더를 이동해도 선택은 유지됩니다.`;
    $("pickerUse").disabled = selected.size === 0;
    $("pickerUse").textContent =
      mode === "ssh"
        ? `선택한 ${selected.size}개 다운로드·가져오기`
        : `선택한 ${selected.size}개 사용`;
  }
  function choose(entry, checked) {
    if (checked && !selected.has(entry.path) && selected.size >= 100) {
      notify("한 번에 최대 100개까지 선택할 수 있습니다.");
    } else if (checked) selected.set(entry.path, entry);
    else selected.delete(entry.path);
    count();
  }
  function render() {
    $("pickerEntries").replaceChildren(
      ...rows.map((entry) => {
        const row = document.createElement("div");
        row.className = "file-entry";
        if (["bag", "dataset"].includes(entry.kind)) {
          const checkbox = document.createElement("input");
          checkbox.type = "checkbox";
          checkbox.checked = selected.has(entry.path);
          checkbox.setAttribute("aria-label", `${entry.name} 선택`);
          checkbox.onchange = () => {
            choose(entry, checkbox.checked);
            checkbox.checked = selected.has(entry.path);
          };
          row.append(checkbox);
        } else row.append(document.createElement("span"));
        const name = document.createElement(
          entry.kind === "bag" ? "label" : "button",
        );
        name.textContent = (entry.kind === "bag" ? "" : "📁 ") + entry.name;
        name.title = entry.path;
        if (entry.kind === "bag")
          name.onclick = () => {
            choose(entry, !selected.has(entry.path));
            render();
          };
        else name.onclick = () => load(entry.path, { resetSearch: true });
        const detail = document.createElement("span");
        detail.className = "muted";
        detail.textContent =
          entry.kind === "dataset" ? "LeRobot 폴더" : size(entry.size);
        row.append(name, detail);
        return row;
      }),
    );
    $("pickerMore").hidden = listing?.next_offset == null;
    $("pickerUp").disabled = !listing?.parent;
    $("pickerDataset").hidden = mode !== "local" || !listing?.dataset;
    count();
  }
  async function load(path, { more = false, resetSearch = false } = {}) {
    if (mode === "ssh" && !$("pickerHost").value) {
      message("먼저 SSH 호스트를 선택하세요.");
      return;
    }
    const token = ++generation;
    if (resetSearch) $("pickerSearch").value = "";
    const offset = more ? listing.next_offset : 0;
    const query = $("pickerSearch").value.trim();
    message(mode === "ssh" ? "SSH 연결·폴더 조회 중…" : "폴더 조회 중…");
    $("pickerUse").disabled = true;
    try {
      const response =
        mode === "ssh"
          ? await api("/api/ssh/browse", {
              host: $("pickerHost").value,
              path: path || "~",
              query,
              offset,
            })
          : await api(
              `/api/files?${new URLSearchParams({ path, query, offset })}`,
            );
      if (token !== generation || !dialog.open) return;
      folder = response.path;
      $("pickerPath").value = folder;
      listing = response;
      rows = more ? [...rows, ...response.entries] : response.entries;
      render();
      message(`${response.total}개 항목 · ${rows.length}개 표시`);
    } catch (error) {
      if (token === generation) {
        message(error.message);
        count();
      }
    }
  }
  async function open(nextMode) {
    generation++;
    mode = nextMode;
    host = "";
    folder = null;
    listing = null;
    selected = new Map();
    rows = [];
    $("pickerTitle").textContent =
      mode === "ssh" ? "SSH에서 bag 불러오기" : "로컬 파일·폴더에서 선택";
    $("pickerLocal").hidden = mode !== "local";
    $("pickerSSH").hidden = mode !== "ssh";
    $("pickerPath").value = mode === "ssh" ? "~" : "";
    $("pickerSearch").value = "";
    $("pickerHelp").textContent =
      mode === "ssh"
        ? "기존 ~/.ssh/config의 Host 별칭과 SSH 키/에이전트를 사용합니다. 원격 bag은 읽기만 하며, 로컬 다운로드 후 순차 import합니다."
        : "에디터 실행 PC의 허용된 폴더를 탐색합니다. 선택 후 왼쪽의 원본 불러오기를 누르세요. 다른 폴더는 --allow-root 또는 데스크톱 상단 Bag 선택으로 허용할 수 있습니다.";
    render();
    dialog.showModal();
    const token = generation;
    try {
      if (mode === "ssh") {
        const response = await api("/api/ssh/hosts");
        if (token !== generation) return;
        $("pickerHost").replaceChildren(
          new Option("SSH 호스트 선택…", ""),
          ...response.hosts.map((h) => new Option(h, h)),
        );
        message(
          response.hosts.length
            ? "호스트를 고른 뒤 폴더 열기를 누르세요."
            : "~/.ssh/config에 Host 별칭이 없습니다. 사용 가이드의 SSH 설정 예시를 참고하세요.",
        );
      } else {
        const response = await api("/api/files");
        if (token !== generation) return;
        $("pickerRoot").replaceChildren(
          ...response.roots.map((p) => new Option(p, p)),
        );
        await load(response.roots[0], { resetSearch: true });
      }
    } catch (error) {
      message(error.message);
    }
  }
  $("pickerRoot").onchange = () =>
    load($("pickerRoot").value, { resetSearch: true });
  $("pickerHost").onchange = () => {
    generation++;
    host = $("pickerHost").value;
    selected = new Map();
    rows = [];
    listing = null;
    folder = null;
    $("pickerPath").value = "~";
    render();
    message("폴더 열기를 눌러 연결하세요.");
  };
  $("pickerGo").onclick = () => load($("pickerPath").value);
  $("pickerFind").onclick = () => load(folder || $("pickerPath").value);
  $("pickerUp").onclick = () => load(listing.parent, { resetSearch: true });
  $("pickerMore").onclick = () => load(folder, { more: true });
  $("pickerAll").onclick = () => {
    rows
      .filter((e) => ["bag", "dataset"].includes(e.kind))
      .forEach((e) => choose(e, true));
    render();
  };
  $("pickerNone").onclick = () => {
    selected.clear();
    render();
  };
  $("pickerDataset").onclick = () => {
    choose(
      { path: folder, name: folder.split("/").pop(), kind: "dataset" },
      true,
    );
    render();
  };
  for (const id of ["pickerPath", "pickerSearch"])
    $(id).onkeydown = (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        load(
          id === "pickerPath" ? $(id).value : folder || $("pickerPath").value,
        );
      }
    };
  $("pickerCancel").onclick = () => dialog.close();
  dialog.addEventListener("close", () => generation++);
  $("pickerUse").onclick = async () => {
    if (!selected.size) return;
    $("pickerUse").disabled = true;
    try {
      const paths = [...selected.keys()];
      if (mode === "ssh") await onSSH(host, paths);
      else onSelect(paths);
      dialog.close();
    } catch (error) {
      message(error.message);
      count();
    }
  };
  return { open };
}
