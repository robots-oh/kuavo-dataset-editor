/* No build step, no third-party CDN, no robot commands. */
import { TASK_PRESETS } from "./tasks.mjs";
import { setupFilePicker } from "./file-picker.mjs";
import { setupQualityReview } from "./quality-review.mjs";
import {
  defaultTaskRange,
  nextTaskRange,
  snapBoundary,
  dragSelectionRange,
} from "./ranges.mjs";
const $ = (id) => document.getElementById(id);
let project = null,
  sourceId = null,
  episodeId = 0,
  frame = 0,
  selected = null;
let undo = [],
  redo = [],
  playing = false,
  lastTick = 0,
  playOrigin = 0,
  drawToken = 0,
  previewBusy = false;
let signalData = [],
  jobTimer = null;
let selectedImportPaths = [];
const colors = [
  "#377b74",
  "#735ca1",
  "#9b6c35",
  "#416f9e",
  "#8e5269",
  "#5c7b40",
];
const copy = (x) => JSON.parse(JSON.stringify(x));
const source = () => project?.sources.find((s) => s.source_id === sourceId);
const episode = () =>
  source()?.episodes.find((e) => e.episode_id === episodeId);
const clips = () =>
  project?.clips
    .filter(
      (c) => c.source_id === sourceId && (c.episode_id ?? 0) === episodeId,
    )
    .sort((a, b) => a.start_frame - b.start_frame) || [];
const range = () => [
  Number($("startFrame").value),
  Number($("endFrame").value),
];
function resumeFrame() {
  if (!episode()) return 0;
  const draft = defaultTaskRange(
    clips(),
    0,
    episode().num_frames,
    source().fps,
  );
  return Math.min(episode().num_frames - 1, draft.start);
}
const time = (f) => (f / (source()?.fps || 1)).toFixed(2) + "s";
const taskColor = (tid) => {
  let n = 0;
  for (const c of tid) n = (n * 31 + c.charCodeAt(0)) >>> 0;
  return colors[n % colors.length];
};
function toast(text) {
  $("toast").textContent = text;
  $("toast").hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => ($("toast").hidden = true), 6000);
}
function taskOptions() {
  const seen = new Set();
  return [
    new Option("기존 task 선택…", ""),
    ...Object.entries(project?.tasks || {})
      .filter(([, t]) => {
        if (seen.has(t)) return false;
        seen.add(t);
        return true;
      })
      .map(([id, t]) => new Option(t, id)),
  ];
}
function renderTaskPresets() {
  $("taskPresets").replaceChildren(
    ...TASK_PRESETS.map((preset) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = preset.label;
      button.title = preset.instruction;
      button.dataset.preset = preset.id;
      button.setAttribute("aria-pressed", "false");
      button.onclick = () => {
        $("task").value = preset.instruction;
        updatePresetSelection();
      };
      return button;
    }),
  );
  updatePresetSelection();
}
function updatePresetSelection() {
  const instruction = $("task").value.trim();
  for (const button of $("taskPresets").children) {
    const preset = TASK_PRESETS.find((p) => p.id === button.dataset.preset);
    button.setAttribute(
      "aria-pressed",
      String(preset.instruction === instruction),
    );
  }
}
async function api(path, body, method = "POST") {
  const r = await fetch(
    path,
    body === undefined
      ? {}
      : {
          method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  if (!r.ok) {
    let e;
    try {
      e = await r.json();
    } catch {
      e = { detail: await r.text() };
    }
    throw Error(
      typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail),
    );
  }
  return r.json();
}
function action(fn) {
  return async (...a) => {
    try {
      await fn(...a);
    } catch (e) {
      toast(e.message);
    }
  };
}
async function projectList() {
  const ps = await api("/api/projects");
  $("projectSelect").replaceChildren(
    ...ps.map((p) => new Option(p.name, p.project_id)),
  );
  if (project) $("projectSelect").value = project.project_id;
  return ps;
}
async function loadProject(pid) {
  playing = false;
  project = await api("/api/projects/" + pid);
  localStorage.setItem("editorProject", pid);
  undo = [];
  redo = [];
  sourceId = project.sources[0]?.source_id || null;
  episodeId = source()?.episodes[0]?.episode_id || 0;
  frame = resumeFrame();
  selected = null;
  await projectList();
  render();
  await fetchFrame();
}
function render() {
  ++drawToken;
  updatePresetSelection();
  $("saveState").textContent = project
    ? `${project.name} · 저장됨 r${project.revision}`
    : "프로젝트를 열어주세요";
  $("empty").hidden = !!source();
  $("editor").hidden = !source();
  $("sourceList").replaceChildren(
    ...(project?.sources || []).map((s) => {
      const b = document.createElement("button");
      b.className = "source" + (s.source_id === sourceId ? " active" : "");
      b.textContent =
        s.original_path?.split("/").pop() ||
        s.path.split("/").pop() ||
        s.source_id.slice(0, 8);
      const sm = document.createElement("small");
      sm.textContent = `${s.fps} FPS · ${s.episodes.length} episode`;
      b.append(sm);
      b.onclick = action(async () => {
        sourceId = s.source_id;
        episodeId = s.episodes[0].episode_id;
        frame = resumeFrame();
        selected = null;
        playing = false;
        render();
        await fetchFrame();
      });
      return b;
    }),
  );
  $("undo").disabled = !undo.length;
  $("redo").disabled = !redo.length;
  $("taskReuse").replaceChildren(...taskOptions());
  if (!source()) return;
  $("sourceTitle").textContent = (source().original_path || source().path)
    .split("/")
    .pop();
  $("episodeSelect").replaceChildren(
    ...source().episodes.map(
      (e) =>
        new Option(
          `Episode ${e.episode_id} · ${e.num_frames} frames`,
          e.episode_id,
        ),
    ),
  );
  $("episodeSelect").value = episodeId;
  $("sourceSplit").value = project.source_splits?.[sourceId] || "unspecified";
  $("seek").max = episode().num_frames - 1;
  $("startFrame").max = episode().num_frames - 1;
  $("endFrame").max = episode().num_frames;
  $("timeline").style.width = 100 * Number($("zoom").value) + "%";
  $("cameras").replaceChildren(
    ...source().cameras.map((camera) => {
      const el = document.createElement("div");
      el.className = "camera";
      el.dataset.camera = camera;
      const im = document.createElement("img");
      im.alt = camera;
      const caption = document.createElement("figcaption");
      caption.textContent = camera;
      const b = document.createElement("button");
      b.textContent = "확대";
      b.onclick = () => {
        if (document.fullscreenElement) document.exitFullscreen();
        else el.requestFullscreen();
      };
      el.append(im, caption, b);
      return el;
    }),
  );
  if (!selected) {
    const draft = defaultTaskRange(
      clips(),
      frame,
      episode().num_frames,
      source().fps,
    );
    $("startFrame").value = draft.start;
    $("endFrame").value = draft.end;
  }
  renderTimeline();
  renderClips();
  renderClock();
}
function renderTimeline() {
  if (!episode()) return;
  const n = episode().num_frames;
  $("ticks").replaceChildren(
    ...Array.from({ length: 11 }, (_, i) => {
      const e = document.createElement("span");
      e.style.left = i * 10 + "%";
      e.textContent = time(Math.round((i * n) / 10));
      return e;
    }),
  );
  $("track").replaceChildren(
    ...clips().map((c) => {
      const el = document.createElement("div");
      el.className = "clip" + (selected === c.id ? " selected" : "");
      el.style.left = (c.start_frame / n) * 100 + "%";
      el.style.width = ((c.end_frame - c.start_frame) / n) * 100 + "%";
      el.style.background = taskColor(c.task_id);
      el.title = project.tasks[c.task_id];
      el.textContent = project.tasks[c.task_id];
      el.dataset.id = c.id;
      for (const edge of ["start", "end"]) {
        const h = document.createElement("span");
        h.className = "handle " + edge;
        h.dataset.edge = edge;
        el.append(h);
      }
      el.onpointerdown = (e) => clipPointer(e, c, el);
      return el;
    }),
  );
  renderSelection();
  renderClock();
}
function renderClips() {
  const list = clips();
  $("clipList").replaceChildren(
    ...list.map((c) => {
      const row = document.createElement("div");
      row.className = "cliprow" + (selected === c.id ? " selected" : "");
      const sw = document.createElement("span");
      sw.className = "swatch";
      sw.style.background = taskColor(c.task_id);
      const t = document.createElement("span");
      t.className = "time";
      t.textContent = `${time(c.start_frame)} – ${time(c.end_frame)}`;
      const text = document.createElement("span");
      text.className = "text";
      text.textContent =
        project.tasks[c.task_id] + (c.new_episode ? " · 새 episode" : "");
      row.append(sw, t, text);
      row.onclick = () => selectClip(c.id);
      return row;
    }),
  );
  const kept = list.reduce((n, c) => n + c.end_frame - c.start_frame, 0);
  $("coverage").textContent =
    `포함 ${kept} / ${episode().num_frames} frames · ${time(kept)}`;
}
function selectClip(id) {
  const c = project.clips.find((x) => x.id === id);
  if (!c) return;
  selected = id;
  $("startFrame").value = c.start_frame;
  $("endFrame").value = c.end_frame;
  $("task").value = project.tasks[c.task_id];
  updatePresetSelection();
  $("newEpisode").checked = !!c.new_episode;
  $("clipMode").textContent = "선택 clip · " + id.slice(0, 8);
  seek(c.start_frame);
  renderTimeline();
  renderClips();
}
function deselect() {
  selected = null;
  $("clipMode").textContent = "새 구간";
  $("newEpisode").checked = false;
  renderTimeline();
  renderClips();
}
function renderSelection() {
  if (!episode()) return;
  const [a, b] = range();
  $("selection").style.left = (a / episode().num_frames) * 100 + "%";
  $("selection").style.width =
    (Math.max(0, b - a) / episode().num_frames) * 100 + "%";
  $("selectionText").textContent = `[${a}, ${b}) · ${time(a)}–${time(b)}`;
  $("startSeconds").value = (a / source().fps).toFixed(4);
  $("endSeconds").value = (b / source().fps).toFixed(4);
}
function renderClock() {
  if (!episode()) return;
  $("seek").value = frame;
  $("clock").textContent =
    `${time(frame)} · frame ${frame}/${episode().num_frames - 1}`;
  $("playhead").style.left = (frame / episode().num_frames) * 100 + "%";
  $("play").textContent = playing ? "Ⅱ 정지" : "▶ 재생";
}
async function fetchFrame() {
  if (!source() || previewBusy) return;
  previewBusy = true;
  const token = ++drawToken,
    f = frame,
    s = sourceId,
    e = episodeId,
    pid = project.project_id;
  try {
    await Promise.all(
      [...$("cameras").children].map(async (el) => {
        const im = el.querySelector("img"),
          camera = el.dataset.camera;
        try {
          const r = await fetch(
            `/api/projects/${pid}/frame/${s}/${e}/${f}?camera=${encodeURIComponent(camera)}`,
          );
          if (!r.ok) throw Error(await r.text());
          const url = URL.createObjectURL(await r.blob());
          if (token !== drawToken || sourceId !== s || episodeId !== e) {
            URL.revokeObjectURL(url);
            return;
          }
          const previous = im.dataset.url;
          im.src = url;
          im.dataset.url = url;
          el.classList.remove("missing");
          if (previous) URL.revokeObjectURL(previous);
        } catch (err) {
          el.classList.add("missing");
          el.title = err.message;
        }
      }),
    );
  } finally {
    previewBusy = false;
    if (
      source() &&
      (token !== drawToken || frame !== f || sourceId !== s || episodeId !== e)
    )
      fetchFrame();
  }
}
function seek(value) {
  if (!episode()) return;
  frame = Math.max(0, Math.min(episode().num_frames - 1, Math.round(value)));
  if (playing) {
    lastTick = performance.now();
    playOrigin = frame;
  }
  renderClock();
  fetchFrame();
}
function togglePlay() {
  if (!episode()) return;
  playing = !playing;
  lastTick = performance.now();
  playOrigin = frame;
  renderClock();
  if (playing) requestAnimationFrame(tick);
}
function tick(now) {
  if (!playing) return;
  let next = Math.floor(
    playOrigin +
      ((now - lastTick) / 1000) * source().fps * Number($("speed").value),
  );
  if ($("keptOnly").checked) {
    const cs = clips();
    if (!cs.some((c) => c.start_frame <= next && next < c.end_frame)) {
      const c = cs.find((c) => c.start_frame > next);
      if (c) {
        toast(`미지정 구간 건너뜀 → ${time(c.start_frame)}`);
        next = c.start_frame;
        lastTick = now;
        playOrigin = next;
      } else {
        playing = false;
        renderClock();
        return;
      }
    }
  }
  if (next >= episode().num_frames) {
    playing = false;
    next = episode().num_frames - 1;
  }
  if (next !== frame) {
    frame = next;
    renderClock();
    fetchFrame();
  }
  if (playing) requestAnimationFrame(tick);
}
async function edit(operation, extra) {
  if (!project) throw Error("프로젝트를 먼저 여세요.");
  const before = copy(project);
  $("saveState").textContent = "저장 중…";
  try {
    project = await api(`/api/projects/${project.project_id}/edit`, {
      operation,
      expected_revision: project.revision,
      ...extra,
    });
  } catch (e) {
    $("saveState").textContent = `저장되지 않음 · r${project.revision}`;
    throw e;
  }
  undo.push(before);
  redo = [];
  selected = project.clips.some((c) => c.id === selected) ? selected : null;
  renderTimeline();
  renderClips();
  $("taskReuse").replaceChildren(...taskOptions());
  $("saveState").textContent = `${project.name} · 저장됨 r${project.revision}`;
  $("undo").disabled = false;
  $("redo").disabled = true;
}
function currentRange() {
  if (!source()) throw Error("원본을 먼저 여세요.");
  const [a, b] = range();
  if (
    !Number.isInteger(a) ||
    !Number.isInteger(b) ||
    a < 0 ||
    a >= b ||
    b > episode().num_frames
  )
    throw Error("시작 < 끝 범위로 프레임을 선택하세요.");
  return {
    source_id: sourceId,
    episode_id: episodeId,
    start_frame: a,
    end_frame: b,
  };
}
async function applyLabel() {
  const r = currentRange(),
    overlap = clips().some(
      (c) => c.start_frame < r.end_frame && c.end_frame > r.start_frame,
    );
  if (
    overlap &&
    !confirm("겹치는 기존 clip을 분할·교체할까요? 원본은 유지됩니다.")
  )
    return;
  await edit("label", {
    ...r,
    task: $("task").value,
    replace: overlap,
    new_episode: $("newEpisode").checked,
  });
  deselect();
  const draft = nextTaskRange(r.end_frame, frame, episode().num_frames);
  $("startFrame").value = draft.start;
  $("endFrame").value = draft.end;
  $("clipMode").textContent =
    draft.start === episode().num_frames
      ? "마지막 프레임까지 지정했습니다. 필요하면 기존 구간을 수정하세요."
      : `다음 task · 직전 끝점 ${time(draft.start)}부터`;
  renderSelection();
}
async function updateClip() {
  if (!selected) throw Error("수정할 clip을 선택하세요.");
  await edit("update_clip", {
    ...currentRange(),
    clip_id: selected,
    task: $("task").value,
    new_episode: $("newEpisode").checked,
  });
}
async function history(backward) {
  const from = backward ? undo : redo,
    to = backward ? redo : undo;
  if (!from.length) return;
  const desired = copy(from[from.length - 1]),
    before = copy(project);
  project = await api(
    `/api/projects/${project.project_id}`,
    { project: desired, expected_revision: project.revision },
    "PUT",
  );
  from.pop();
  to.push(before);
  selected = null;
  render();
  await fetchFrame();
}
function pos(e) {
  const r = $("timeline").getBoundingClientRect();
  return Math.max(
    0,
    Math.min(
      episode().num_frames,
      Math.round(((e.clientX - r.left) / r.width) * episode().num_frames),
    ),
  );
}
$("timeline").onpointerdown = (e) => {
  if (!episode() || e.target.closest(".clip")) return;
  e.preventDefault();
  const start = dragPoint(e).frame;
  playing = false;
  deselect();
  seek(start);
  const move = (m) => {
    const point = dragPoint(m);
    const draft = dragSelectionRange(start, point.frame, episode().num_frames);
    $("startFrame").value = draft.start;
    $("endFrame").value = draft.end;
    showSnap(point);
    renderSelection();
    seek(draft.preview);
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    window.removeEventListener("pointercancel", up);
    showSnap(null);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
  window.addEventListener("pointercancel", up);
};
function dragPoint(event, excludedClipId = null) {
  const boundaries = [0, episode().num_frames];
  for (const clip of clips()) {
    if (clip.id !== excludedClipId)
      boundaries.push(clip.start_frame, clip.end_frame);
  }
  return snapBoundary(
    pos(event),
    boundaries,
    $("timeline").getBoundingClientRect().width / episode().num_frames,
    event.altKey,
  );
}
function showSnap(point) {
  const active = !!point?.snapped;
  $("snapGuide").hidden = !active;
  $("snapHint").textContent = active
    ? `경계 맞춤 ${time(point.frame)} · Alt로 해제`
    : "";
  if (active)
    $("snapGuide").style.left =
      (point.frame / episode().num_frames) * 100 + "%";
}
function clipPointer(e, c, el) {
  e.stopPropagation();
  e.preventDefault();
  playing = false;
  const edge = e.target.dataset.edge;
  selectClip(c.id);
  if (!edge) return;
  el = $("track").querySelector(`[data-id="${c.id}"]`);
  const old = [c.start_frame, c.end_frame];
  const move = (m) => {
    const point = dragPoint(m, c.id),
      f = point.frame;
    $("startFrame").value = edge === "start" ? Math.min(f, old[1] - 1) : old[0];
    $("endFrame").value = edge === "end" ? Math.max(f, old[0] + 1) : old[1];
    const [a, b] = range();
    el.style.left = (a / episode().num_frames) * 100 + "%";
    el.style.width = ((b - a) / episode().num_frames) * 100 + "%";
    const actual = edge === "end" ? b : a;
    showSnap({
      frame: actual,
      snapped: point.snapped && point.frame === actual,
    });
    renderSelection();
    seek(edge === "end" ? b - 1 : a);
  };
  const up = async (event) => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    window.removeEventListener("pointercancel", up);
    showSnap(null);
    if (event.type === "pointercancel") {
      selectClip(c.id);
      return;
    }
    try {
      await updateClip();
    } catch (err) {
      toast(err.message);
      selectClip(c.id);
    }
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
  window.addEventListener("pointercancel", up);
}
async function startImport(demo) {
  if (!project) throw Error("프로젝트를 먼저 만드세요.");
  const body = demo
    ? { demo: true }
    : {
        paths: selectedImportPaths.length
          ? selectedImportPaths
          : [$("importPath").value.trim()],
        profile: JSON.parse($("profile").value),
      };
  await api(`/api/projects/${project.project_id}/imports`, body);
  toast("가져오기 시작. 아래 작업 목록에서 완료 후 프로젝트에 추가하세요.");
  await pollJobs();
}
async function pollJobs() {
  try {
    const jobs = (await api("/api/jobs")).filter(
      (j) => !j.project_id || j.project_id === project?.project_id,
    );
    $("jobList").replaceChildren(
      ...jobs.map((j) => {
        const el = document.createElement("div");
        el.className = "job";
        const title = document.createElement("div");
        title.textContent = `${j.kind} · ${j.status} · ${j.message || ""}`;
        const bar = document.createElement("progress");
        bar.max = 1;
        bar.value = j.progress || 0;
        el.append(title, bar);
        if (j.checkpoint) {
          const checkpoint = document.createElement('p');
          checkpoint.textContent = `재사용 가능한 완료 episode: ${j.checkpoint.completed_episodes}개 · ${j.checkpoint.completed_frames} frames`;
          el.append(checkpoint);
        }
        if (j.resumable) {
          const resume = document.createElement('button');
          resume.textContent = '완료 episode부터 이어가기';
          resume.onclick = action(async () => {
            if (!window.confirm('이 작업을 시작할 당시의 annotation과 누락 처리 선택으로 재개합니다. 완료 episode는 재사용하고 중단 episode부터 다시 처리할까요?')) return;
            await api(`/api/jobs/${j.id}/resume-export`, {});
            toast('원래 Export snapshot으로 재개합니다. 현재 편집 중인 annotation은 변경하지 않습니다.');
            await pollJobs();
          });
          el.append(resume);
        }
        if (j.error) {
          const err = document.createElement("p");
          err.className = "error";
          err.textContent = j.error;
          el.append(err);
        }
        if (j.retryable) {
          const retry = document.createElement("button");
          retry.textContent = "중단·실패한 파일 이어가기";
          retry.onclick = action(async () => {
            await api(`/api/jobs/${j.id}/retry-import`, {});
            toast(
              "완료된 원본은 건너뛰고 남은 파일을 다시 가져옵니다. SSH 임시 파일은 이어받습니다.",
            );
            await pollJobs();
          });
          el.append(retry);
        }
        const imported =
          j.result?.sources || (j.result?.source_id ? [j.result] : []);
        if (
          ["completed", "cancelled", "failed"].includes(j.status) &&
          imported.length &&
          project &&
          imported.some(
            (item) =>
              !project.sources.some((s) => s.source_id === item.source_id),
          )
        ) {
          const b = document.createElement("button");
          b.textContent =
            imported.length === 1
              ? "현재 프로젝트에 추가"
              : `${imported.length}개 원본을 현재 프로젝트에 추가`;
          b.onclick = action(async () => {
            project = await api(
              `/api/projects/${project.project_id}/attach/${j.id}`,
              { expected_revision: project.revision },
            );
            undo = [];
            redo = [];
            sourceId = imported[0].source_id;
            episodeId = source().episodes[0].episode_id;
            frame = 0;
            selected = null;
            render();
            await fetchFrame();
            await pollJobs();
          });
          el.append(b);
        }
        for (const failure of j.result?.failures || []) {
          const failed = document.createElement("p");
          failed.className = "error";
          failed.textContent = `${failure.path}: ${failure.error}`;
          el.append(failed);
        }
        for (const download of j.result?.downloads || []) {
          const info = document.createElement("p");
          info.textContent = `${download.reused ? "받아둔 파일 재사용" : download.resumed_bytes ? "이어받기 완료" : "다운로드 완료"}: ${download.path}`;
          el.append(info);
        }
        if (j.status === "completed" && j.result?.path) {
          const r = document.createElement("p");
          r.className = "result";
          r.textContent = j.result.path;
          el.append(r);
          if (j.kind === "export") {
            const use = document.createElement("button");
            use.textContent = "이 결과를 Hub 업로드 경로로 사용";
            use.onclick = () => {
              $("hubPath").value = j.result.path;
              $("hubPath").focus();
            };
            el.append(use);
          }
          if (j.result.backup) {
            const backup = document.createElement("p");
            backup.textContent = `이전 dataset 복구본: ${j.result.backup}`;
            el.append(backup);
            if (j.result.relocated_projects?.includes(project?.project_id)) {
              const note = document.createElement("p");
              note.textContent =
                "이 프로젝트는 편집 원본을 보존하기 위해 복구본을 참조합니다. 미적용 입력을 확인한 뒤 프로젝트를 다시 여세요. 수정된 dataset을 편집하려면 결과 폴더를 새로 불러오세요.";
              el.append(note);
            }
          }
        }
        if (j.status === "completed" && j.kind === "hub-plan") {
          const p = j.result;
          const summary = document.createElement("p");
          summary.textContent = `${p.repo_id} · ${p.private ? "비공개" : "공개"} · ${p.exists ? "기존 repo 전체 snapshot 동기화" : "새 repo 생성"}\n${p.snapshot.files.length} files · ${(p.snapshot.bytes / 1024 ** 3).toFixed(2)} GiB · ${p.snapshot.frames} frames\n원격의 오래된 dataset 파일 ${p.delete.length}개 삭제 · 로컬 제외 파일 ${p.snapshot.omitted.length}개`;
          el.append(summary);
          const details = document.createElement("details");
          const heading = document.createElement("summary");
          heading.textContent = "전송·삭제·제외 파일 목록";
          const list = document.createElement("pre");
          list.style.whiteSpace = "pre-wrap";
          list.textContent = `전송:\n${p.snapshot.files.map((f) => f.name).join("\n")}\n\n원격 삭제:\n${p.delete.join("\n") || "없음"}\n\n로컬 제외:\n${p.snapshot.omitted.join("\n") || "없음"}\n\n원격 보존:\n${p.preserved_remote.join("\n") || "없음"}`;
          details.append(heading, list);
          el.append(details);
          const upload = document.createElement("button");
          upload.textContent = "대상·공개 범위 확인 후 실제 업로드";
          upload.onclick = action(async () => {
            if (
              !confirm(
                `${p.url}\n${p.private ? "비공개" : "공개 — 누구나 데이터에 접근할 수 있습니다."}\n${p.snapshot.files.length} files / ${p.snapshot.frames} frames\n\n${p.exists ? `기존 dataset 파일을 로컬 결과와 동기화합니다. ${p.delete.length}개 원격 파일 삭제. 원격 append가 아닙니다.` : "새 dataset 저장소를 생성합니다."}\n원본 bag과 editor/ 이벤트·편집 기록은 제외됩니다.\n영상·데이터 공개 권한을 확인했으며 업로드할까요?`,
              )
            )
              return;
            upload.disabled = true;
            try {
              await api(`/api/hub/plans/${j.id}/upload`, {
                confirmed: true,
                sync_existing: p.exists,
                digest: p.digest,
              });
            } finally {
              upload.disabled = false;
            }
            await pollJobs();
          });
          el.append(upload);
        }
        if (j.status === "completed" && j.kind === "hub-upload") {
          const result = document.createElement("p");
          result.textContent = `${j.result.url}\ncommit: ${j.result.commit}\nLeRobot loader에는 revision="${j.result.loader_revision}"을 지정하세요.`;
          el.append(result);
        }
        if (
          ["queued", "running"].includes(j.status) &&
          j.kind !== "hub-upload"
        ) {
          const cancel = document.createElement("button");
          cancel.textContent = "작업 취소";
          cancel.onclick = action(async () => {
            await api(`/api/jobs/${j.id}/cancel`, {});
            toast("취소 요청됨. 다음 처리 단위에서 취소합니다.");
          });
          el.append(cancel);
        }
        const log = document.createElement("a");
        log.href = `/api/jobs/${j.id}/log`;
        log.target = "_blank";
        log.textContent = "로그";
        el.append(log);
        return el;
      }),
    );
    if (!jobs.length) $("jobList").textContent = "작업 없음";
  } catch (e) {
    console.warn(e);
  }
}
const reviewQuality = setupQualityReview();
let exportBusy = false;
async function exportNow() {
  if (exportBusy) return;
  exportBusy = true;
  $("export").disabled = true;
  try {
    await prepareExport();
  } finally {
    exportBusy = false;
    $("export").disabled = false;
  }
}
async function prepareExport() {
  if (!project) throw Error("프로젝트를 먼저 여세요.");
  const mode = $("exportMode").value;
  const body = {
    mode,
    repo_name: $("repoName").value.trim(),
    target: $("exportPath").value.trim() || undefined,
    confirm_existing: $("confirmExisting").checked,
  };
  if (mode === "new" && !body.repo_name)
    throw Error("새 dataset repo 이름을 입력하세요.");
  if (mode !== "new" && !body.confirm_existing)
    throw Error("기존 dataset 변경 확인에 체크하세요.");
  const projectId = project.project_id;
  toast("선택 구간의 누락 데이터를 확인 중…");
  let p = await api(`/api/projects/${projectId}/export-plan`, body);
  if (p.quality?.issues.length) {
    const approval = await reviewQuality(p.quality);
    if (!approval) return;
    if (project.project_id !== projectId) throw Error("프로젝트가 바뀌었습니다. 다시 Export하세요.");
    body.quality = approval;
    p = await api(`/api/projects/${projectId}/export-plan`, body);
  }
  if (!p.kept_frames) throw Error("먼저 task가 있는 clip을 만드세요.");
  if (project.project_id !== projectId) throw Error("프로젝트가 바뀌었습니다. 다시 Export하세요.");
  if (
    !confirm(
      `${mode === "new" ? "새 생성" : mode === "append" ? "기존에 추가" : "기존 수정"}: ${p.repo_id}\n${p.target}\n\n현재 선택 ${p.kept_frames} frames (${(p.kept_frames / p.fps).toFixed(2)}초)\n미지정·승인된 누락 제외 ${p.dropped_frames} frames\n최종 ${p.output_frames} frames / ${p.output_episodes} episodes\n누락 구간 처리 ${p.quality?.issues.length || 0}건 (선택 내역 기록)\n${mode === "append" ? `기존 ${p.existing_frames} frames는 유지합니다. 중복 구간은 자동 제거하지 않습니다.\n` : ""}${mode !== "new" ? "기존 폴더는 복구본으로 보존합니다.\n" : ""}\n저장본 r${p.project_revision}으로 진행할까요?`,
    )
  )
    return;
  await api(`/api/projects/${projectId}/exports`, {
    ...body,
    expected_revision: p.project_revision,
    expected_baseline: p.baseline,
  });
  await pollJobs();
}
function updateExportMode() {
  const mode = $("exportMode").value;
  $("repoNameLabel").hidden = mode !== "new";
  $("confirmExistingLabel").hidden = mode === "new";
  $("confirmExisting").checked = false;
  $("exportPathLabel").textContent =
    mode === "new" ? "새 출력 폴더 (선택)" : "기존 LeRobot dataset 폴더 (필수)";
  $("exportHint").textContent =
    mode === "new"
      ? "라벨 구간만 새 dataset으로 저장합니다. Hub에는 업로드하지 않습니다."
      : mode === "append"
        ? "기존 모든 episode를 유지하고 현재 라벨 구간을 추가합니다. 복구본을 보존하며, 중복을 자동 제거하지 않습니다."
        : "수정할 dataset을 원본으로 불러온 뒤 사용하세요. 현재 프로젝트의 라벨 구간만 남긴 결과로 교체하고, 기존 폴더는 복구본으로 보존합니다.";
}
$("exportMode").onchange = updateExportMode;
$("exportPath").addEventListener("input", () => {
  $("confirmExisting").checked = false;
});
async function loadSignals() {
  signalData = await api(
    `/api/projects/${project.project_id}/signals/${sourceId}/${episodeId}`,
  );
  const keys = [...new Set(signalData.flatMap((p) => Object.keys(p.values)))];
  $("signalKey").replaceChildren(...keys.map((k) => new Option(k, k)));
  drawSignals();
}
function drawSignals() {
  const cv = $("signals"),
    ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, cv.width, cv.height);
  const key = $("signalKey").value,
    values = signalData.map((p) => p.values[key]?.[0]).filter(Number.isFinite);
  if (!values.length) return;
  const min = Math.min(...values),
    max = Math.max(...values);
  ctx.strokeStyle = "#61d6b4";
  ctx.lineWidth = 2;
  ctx.beginPath();
  let active = false;
  for (const p of signalData) {
    const v = p.values[key]?.[0];
    if (!Number.isFinite(v)) {
      active = false;
      continue;
    }
    const x = (p.frame / episode().num_frames) * cv.width,
      y = 115 - ((v - min) / (max - min || 1)) * 100;
    if (active) ctx.lineTo(x, y);
    else ctx.moveTo(x, y);
    active = true;
  }
  ctx.stroke();
  ctx.fillStyle = "#9aa9bc";
  ctx.fillText(`${key}[0]  ${min.toFixed(3)} .. ${max.toFixed(3)}`, 10, 12);
}
$("newProject").onclick = action(async () => {
  const name = prompt("프로젝트 이름", "VR task 편집");
  if (!name) return;
  const p = await api("/api/projects", { name });
  await loadProject(p.project_id);
});
$("projectSelect").onchange = action(() =>
  loadProject($("projectSelect").value),
);
$("episodeSelect").onchange = action(async () => {
  episodeId = Number($("episodeSelect").value);
  frame = resumeFrame();
  selected = null;
  playing = false;
  render();
  await fetchFrame();
});
$("sourceSplit").onchange = action(() =>
  edit("split_assignment", {
    source_id: sourceId,
    split: $("sourceSplit").value,
  }),
);
$("import").onclick = action(() => startImport(false));
function selectImportPaths(paths) {
  if (!Array.isArray(paths)) return;
  selectedImportPaths = [
    ...new Set(paths.filter((p) => typeof p === "string" && p.trim())),
  ];
  $("importPath").value = selectedImportPaths[0] || "";
  renderImportPaths();
}
function renderImportPaths() {
  const panel = $("selectedImports");
  panel.hidden = !selectedImportPaths.length;
  panel.replaceChildren();
  if (!selectedImportPaths.length) return;
  const title = document.createElement("p");
  title.textContent = `${selectedImportPaths.length}개 선택 · 순차 가져오기`;
  panel.append(title);
  for (const path of selectedImportPaths) {
    const line = document.createElement("div");
    const name = document.createElement("span");
    name.textContent = path.split("/").pop();
    name.title = path;
    const remove = document.createElement("button");
    remove.textContent = "×";
    remove.setAttribute("aria-label", `${name.textContent} 선택 해제`);
    remove.onclick = () =>
      selectImportPaths(selectedImportPaths.filter((p) => p !== path));
    line.append(name, remove);
    panel.append(line);
  }
}
$("importPath").addEventListener("input", () => {
  selectedImportPaths = [];
  renderImportPaths();
});
window.addEventListener("editor-import-selection", (event) =>
  selectImportPaths(event.detail),
);
const picker = setupFilePicker({
  api,
  notify: toast,
  onSelect: selectImportPaths,
  onSSH: async (host, paths, options) => {
    if (!project) throw Error("프로젝트를 먼저 여세요.");
    await api(`/api/projects/${project.project_id}/ssh-imports`, {
      host,
      paths,
      ...options,
      profile: JSON.parse($("profile").value),
    });
    toast("SSH 전송·가져오기 시작. 다음 bag 미리받기와 처리 단계는 아래 작업 목록에서 확인하세요.");
    await pollJobs();
  },
});
$("browseLocal").onclick = action(() => picker.open("local"));
$("browseSSH").onclick = action(() => picker.open("ssh"));
$("demo").onclick = action(() => startImport(true));
$("prev").onclick = () => seek(frame - 1);
$("next").onclick = () => seek(frame + 1);
$("play").onclick = togglePlay;
$("seek").oninput = () => seek(Number($("seek").value));
$("speed").onchange = () => {
  lastTick = performance.now();
  playOrigin = frame;
};
$("zoom").oninput = () => {
  $("timeline").style.width = Number($("zoom").value) * 100 + "%";
};
$("markIn").onclick = () => {
  $("startFrame").value = frame;
  renderSelection();
};
$("markOut").onclick = () => {
  $("endFrame").value = frame + 1;
  renderSelection();
};
$("startFrame").oninput = renderSelection;
$("endFrame").oninput = renderSelection;
for (const [seconds, frames] of [
  ["startSeconds", "startFrame"],
  ["endSeconds", "endFrame"],
]) {
  $(seconds).onchange = () => {
    if (!source()) return;
    $(frames).value = Math.ceil(Number($(seconds).value) * source().fps - 1e-9);
    renderSelection();
  };
}
$("apply").onclick = action(applyLabel);
$("update").onclick = action(updateClip);
$("clearClip").onclick = deselect;
$("exclude").onclick = action(async () => {
  await edit("exclude", currentRange());
  deselect();
});
$("split").onclick = action(async () => {
  const c =
    project?.clips.find((c) => c.id === selected) ||
    clips().find((c) => c.start_frame < frame && frame < c.end_frame);
  if (!c) throw Error("분할할 clip 내부로 이동하세요.");
  await edit("split", { clip_id: c.id, frame });
});
$("undo").onclick = action(() => history(true));
$("redo").onclick = action(() => history(false));
$("export").onclick = action(exportNow);
$("hubPlan").onclick = action(async () => {
  if (!$("hubPath").value.trim() || !$("hubRepo").value.trim())
    throw Error("완료된 dataset 폴더와 owner/dataset-name을 입력하세요.");
  await api("/api/hub/plans", {
    path: $("hubPath").value.trim(),
    repo_id: $("hubRepo").value.trim(),
    private: $("hubPrivate").checked,
  });
  toast("Hub 미리보기 시작. 아래 작업 목록에서 확인 후 업로드하세요.");
  await pollJobs();
});
$("taskReuse").onchange = () => {
  if ($("taskReuse").value)
    $("task").value = project.tasks[$("taskReuse").value];
  updatePresetSelection();
};
$("task").addEventListener("input", updatePresetSelection);
$("snapshot").onclick = () => {
  if (project) window.location = `/api/projects/${project.project_id}/snapshot`;
};
$("loadSignals").onclick = action(loadSignals);
$("signalKey").onchange = drawSignals;
document.addEventListener("keydown", (e) => {
  if ($("filePicker").open || $("qualityReview").open) return;
  if (e.target.matches("input,textarea,select") || !episode()) return;
  if (e.code === "Space") {
    e.preventDefault();
    togglePlay();
  } else if (e.key === "ArrowLeft") {
    e.preventDefault();
    seek(frame - 1);
  } else if (e.key === "ArrowRight") {
    e.preventDefault();
    seek(frame + 1);
  } else if (e.key.toLowerCase() === "i") $("markIn").click();
  else if (e.key.toLowerCase() === "o") $("markOut").click();
  else if (e.key.toLowerCase() === "s" && !e.ctrlKey && !e.metaKey)
    $("split").click();
  else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
    e.preventDefault();
    (e.shiftKey ? $("redo") : $("undo")).click();
  }
});
renderTaskPresets();
action(async () => {
  const cfg = await api("/api/config");
  $("profile").value = JSON.stringify(cfg.bag_profile, null, 2);
  const ps = await projectList();
  if (ps.length) {
    const pid = localStorage.getItem("editorProject");
    await loadProject(
      ps.some((p) => p.project_id === pid) ? pid : ps[0].project_id,
    );
  } else {
    const p = await api("/api/projects", { name: "첫 번째 편집 프로젝트" });
    await loadProject(p.project_id);
  }
  await pollJobs();
  jobTimer = setInterval(pollJobs, 2000);
})();
