/* No build step, no third-party CDN, no robot commands. */
import { setupTaskPresets } from "./task-presets.mjs";
import { GOAL_PRESETS,trackClips,coveringGoal,goalDraft,contiguousSteps } from './goals.mjs';
import { setupFilePicker } from "./file-picker.mjs";
import { setupQualityReview } from "./quality-review.mjs";
import { setupDataInspector } from "./data-inspector.mjs";
import { setupActivity } from './activity.mjs';
import { restoreHubTarget } from './hub-target.mjs';
import { sourceLabelingStatus, matchesSourceFilter } from './source-status.mjs';
import { isSourceRemoved, activeSources, removedSources, setupSourceRemovalConfirmation } from './source-removal.mjs';
import {
  defaultTaskRange,
  nextTaskRange,
  snapBoundary,
  dragSelectionRange,
  resizeClipRange,
  shortRangeWarning,
} from "./ranges.mjs";
const $ = (id) => document.getElementById(id);
const confirmSourceRemoval=setupSourceRemovalConfirmation();
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
let pendingRequests = 0;
let initializing = true;
let clipMenuTarget = null;
let annotationTrack='step';
let annotationReady=false;
let sourceExportReady=false;
let revisionHistoryRestore=false;
let sourceCompletionReady=false;
let sourceNotRequiredReady=false;
let sourceRemovalReady=false;
let sharedPollBusy=false;
let autoRangeEnd=true;
let cursorBoundary=null,seekDragging=false,seekAlt=false;
const annotationDrafts={step:'',goal:''};
const colors = [
  "#377b74",
  "#735ca1",
  "#9b6c35",
  "#416f9e",
  "#8e5269",
  "#5c7b40",
];
const copy = (x) => JSON.parse(JSON.stringify(x));
const source = () => project?.sources.find((s) => s.source_id === sourceId && !isSourceRemoved(project,s.source_id));
const episode = () =>
  source()?.episodes.find((e) => e.episode_id === episodeId);
const clips = (track=annotationTrack) => trackClips(project,track,sourceId,episodeId);
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
const taskPresetPicker = setupTaskPresets({
  input: $("task"), presets: $("taskPresets"),
  locationPanel: $("boxPickOptions"), locations: $("boxPickPresets"),
});
function updatePresetSelection() {
  taskPresetPicker.sync();
  for(const b of $("goalPresets").children) b.setAttribute('aria-pressed',String(b.title===$("task").value.trim()));
}
function renderTrackControls() {
  const goal=annotationTrack==='goal';
  $('editStep').setAttribute('aria-pressed',String(!goal));$('editGoal').setAttribute('aria-pressed',String(goal));
  $('stepPresetPanel').hidden=goal;$('goalPresetPanel').hidden=!goal;$('newEpisodeLabel').hidden=goal;
  $('annotationTitle').textContent=goal?'Goal · 전체 작업 구간':'Step · 세부 동작 구간';
  $('clipListTitle').textContent=goal?'Goal 목록 · 포함된 Step':'Step 목록 · Goal 연결';
  $('apply').textContent=goal?'새 Goal에 적용':'새 Step에 task 적용';
  $('update').textContent=goal?'선택 Goal 수정':'선택 Step 수정';
  $('taskPresetHint').textContent=goal?'후보는 문장 입력만 합니다. 범위·완료 목표를 확인하고 적용/수정 버튼으로 저장하세요.':'‘상자 집기’는 위치를 고른 뒤 문장이 입력됩니다. 구간 저장은 아래 적용/수정 버튼을 누르세요.';
  $('track').classList.toggle('active-lane',!goal);$('goalTrack').classList.toggle('active-lane',goal);
}
function setAnnotationTrack(track) {
  if(!annotationReady) {toast('진행 중인 작업이 끝난 후 에디터 서버를 재시작하고 새로고침하세요.');return false;}
  if(annotationTrack!==track) {
    annotationDrafts[annotationTrack]=$('task').value;annotationTrack=track;selected=null;
    $('task').value=annotationDrafts[track];$('goalIncludeUnlabeled').checked=false;
    $('clipMode').textContent=track==='goal'?'새 Goal 구간':'새 Step 구간';
    beginAutomaticRange();
  }
  renderTrackControls();updatePresetSelection();renderTimeline();renderClips();
  return true;
}
function beginGoal(draft) {
  if(!draft) throw Error('현재 원본에 묶을 Step이 없습니다. 영상에서 범위를 먼저 선택하세요.');
  if(!setAnnotationTrack('goal'))return;
  selected=null;
  $('task').value=draft.instruction;$('startFrame').value=draft.start;$('endFrame').value=draft.end;
  autoRangeEnd=false;
  $('goalIncludeUnlabeled').checked=false;$('clipMode').textContent=`Goal 초안 · Step ${draft.steps}개 · 아직 저장되지 않음`;
  updatePresetSelection();renderTimeline();renderClips();
  toast('Goal 초안입니다. 범위·문장을 확인하고 새 Goal에 적용을 누르세요. Step은 변경되지 않았습니다.');
}
$('editStep').onclick=()=>setAnnotationTrack('step');$('editGoal').onclick=()=>setAnnotationTrack('goal');
$('goalFromRange').onclick=action(()=>{const r=currentRange();beginGoal(goalDraft(project,sourceId,episodeId,r.start_frame,r.end_frame));});
$('goalFromSteps').onclick=action(()=>beginGoal(contiguousSteps(project,sourceId,episodeId,frame)));
$('goalPresets').replaceChildren(...GOAL_PRESETS.map(p=>{
  const b=document.createElement('button');b.type='button';b.textContent=p.label;b.title=p.instruction;b.setAttribute('aria-pressed','false');
  b.onclick=()=>{$('task').value=p.instruction;updatePresetSelection();};return b;
}));
async function api(path, body, method = "POST") {
  if (body !== undefined) pendingRequests++;
  try {
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
    return await r.json();
  } finally {
    if (body !== undefined) pendingRequests--;
  }
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
const refreshFields = [
  'task','startFrame','endFrame','startSeconds','endSeconds','newEpisode',
  'goalIncludeUnlabeled','speed','zoom','keptOnly','sourceStatusFilter',
  'exportAnnotationTrack','exportSchema','exportMode','exportScope','repoName','exportPath',
  'confirmExisting','hubPath','hubRepo','hubPrivate','importPath','profile','profilePick',
];
function captureEditorState() {
  return {projectId:project?.project_id,sourceId,episodeId,frame,selected,cursorBoundary,
    annotationTrack,annotationDrafts,autoRangeEnd,selectedImportPaths,
    fields:Object.fromEntries(refreshFields.map(id=>[id,{value:$(id).value,checked:$(id).checked}]))};
}
function refreshEditor() {
  if (initializing || pendingRequests || exportBusy || document.querySelector('dialog[open]')) {
    toast('화면 불러오기·저장·설정 확인이 끝나거나 열린 창을 닫은 뒤 새로고침하세요.');
    return;
  }
  // This is a tab-local draft, never a saved annotation or an Export request.
  const state = captureEditorState();
  try {
    // A retry after a failed page load must not overwrite the earlier draft.
    if (project || !sessionStorage.getItem('editorRefresh'))
      sessionStorage.setItem('editorRefresh',JSON.stringify(state));
  } catch {
    toast('미적용 입력을 임시 보관할 수 없어 새로고침을 취소했습니다. 먼저 라벨을 적용하세요.');
    return;
  }
  window.location.reload();
}
function restoreRefreshState(state, notify=true) {
  if (!state || state.projectId!==project?.project_id) return;
  const restoredSource=project.sources.find(s=>s.source_id===state.sourceId && project.source_removed?.[s.source_id]!==true);
  const restoredEpisode=restoredSource?.episodes.find(e=>e.episode_id===state.episodeId);
  if (state.sourceId && !restoredEpisode) {
    sourceId=project.sources.find(s=>project.source_removed?.[s.source_id]!==true)?.source_id||null;
    episodeId=project.sources.find(s=>s.source_id===sourceId)?.episodes[0]?.episode_id||0;
    frame=0;selected=null;playing=false;render();
    if(notify) toast('이전에 선택한 원본이 제거되어 다른 원본을 열었습니다. 제거한 원본 목록에서 복원할 수 있습니다.');
    return;
  }
  if (restoredEpisode) {
    sourceId=state.sourceId;episodeId=state.episodeId;
    frame=Math.max(0,Math.min(restoredEpisode.num_frames-1,Number(state.frame)||0));
    cursorBoundary=Number.isInteger(state.cursorBoundary) && state.cursorBoundary>=0
      && state.cursorBoundary<=restoredEpisode.num_frames ? state.cursorBoundary : null;
  }
  annotationTrack=state.annotationTrack==='goal'?'goal':'step';
  Object.assign(annotationDrafts,state.annotationDrafts);
  selected=clips().some(c=>c.id===state.selected)?state.selected:null;
  render();
  for (const id of refreshFields) {
    const field=state.fields?.[id];
    if (!field) continue;
    $(id).value=field.value;
    if (typeof field.checked==='boolean') $(id).checked=field.checked;
  }
  autoRangeEnd=!!state.autoRangeEnd;
  selectedImportPaths=state.selectedImportPaths||[];
  renderImportPaths();
  updateExportMode();
  $('confirmExisting').checked=!!state.fields?.confirmExisting?.checked;
  $('timeline').style.width=100*Number($('zoom').value)+'%';
  renderSourceList();renderTimeline();renderClips();updatePresetSelection();
  // Keep even an unfinished seconds entry (e.g. "123.") until it is applied.
  for (const id of ['startSeconds','endSeconds'])
    if (state.fields?.[id]) $(id).value=state.fields[id].value;
  if(notify) toast('새로고침 완료 · 미적용 입력은 초안으로 유지했습니다.');
}
async function pollLinkedProject() {
  const blocked=()=>initializing || pendingRequests || exportBusy || playing
    || document.querySelector('dialog[open], :active');
  if(sharedPollBusy || !project?.annotation_group || blocked()) return;
  sharedPollBusy=true;
  const original=project;
  try {
    const status=await api(`/api/projects/${original.project_id}/revision`);
    if(project!==original || blocked() || status.revision===original.revision) return;
    const updated=await api(`/api/projects/${original.project_id}`);
    if(project!==original || blocked()) return;
    const state=captureEditorState();
    project=updated;undo=[];redo=[];
    restoreRefreshState(state,false);
    $('undo').disabled=true;$('redo').disabled=true;
    await fetchFrame();
    toast('연결된 프로젝트의 라벨·진행 상태를 반영했습니다. 미적용 입력은 초안으로 유지했습니다.');
  } catch {
    // Keep drafts and retry after a temporary server disconnect.
  } finally {sharedPollBusy=false;}
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
  $('sourceStatusFilter').value='all';
  sourceId = activeSources(project)[0]?.source_id || null;
  cursorBoundary=null;
  episodeId = source()?.episodes[0]?.episode_id || 0;
  frame = resumeFrame();
  selected = null;
  await projectList();
  render();
  await fetchFrame();
}
function renderSourceList() {
  const sources=activeSources(project),filter=$('sourceStatusFilter').value;
  const counts={completed:0,in_progress:0,not_started:0,not_required:0};
  for (const s of sources) counts[sourceLabelingStatus(project,s.source_id).key]++;
  $('sourceStatusSummary').textContent=`완료 ${counts.completed} · 라벨링 중 ${counts.in_progress} · 미라벨 ${counts.not_started} · 불필요 ${counts.not_required} / 전체 ${sources.length}개`;
  const visible=sources.filter(s=>matchesSourceFilter(project,s.source_id,filter));
  $("sourceList").replaceChildren(
    ...visible.map((s) => {
      const b = document.createElement("button");
      b.className = "source" + (s.source_id === sourceId ? " active" : "");
      const status=sourceLabelingStatus(project,s.source_id);
      b.dataset.status=status.key;
      b.textContent =
        s.original_path?.split("/").pop() ||
        s.path.split("/").pop() ||
        s.source_id.slice(0, 8);
      const sm = document.createElement("small");
      sm.textContent = `${s.fps} FPS · ${s.episodes.length} episode`;
      b.append(sm);
      const badge=document.createElement('span');badge.className='source-status source-status--'+status.key;
      badge.textContent=status.text;b.append(badge);
      b.onclick = action(async () => {
        sourceId = s.source_id;
        cursorBoundary=null;
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
  if (!visible.length) $('sourceList').textContent=sources.length?'해당 상태의 원본이 없습니다.':'원본을 불러오세요.';
  $('removeSource').disabled=!source() || !sourceRemovalReady;
  $('removeSource').title=sourceRemovalReady?'원본 bag·캐시·annotation은 보존합니다. 제거한 원본에서 복원할 수 있습니다.':'서버를 업데이트·재시작하면 원본 제거를 사용할 수 있습니다.';
  const removed=removedSources(project);
  $('removedSourcesPanel').hidden=!removed.length;
  $('removedSourcesSummary').textContent=`제거한 원본 ${removed.length}개 · 복원`;
  $('removedSourceList').replaceChildren(...removed.map(s=>{
    const row=document.createElement('div');row.className='removed-source';
    const name=document.createElement('span');name.textContent=(s.original_path||s.path).split('/').pop();
    const restore=document.createElement('button');restore.textContent='복원';restore.disabled=!sourceRemovalReady;
    restore.setAttribute('aria-label',`${name.textContent} 복원`);
    restore.onclick=action(()=>setSourceRemoved(s.source_id,false));
    row.append(name,restore);return row;
  }));
  $('sourceCompleted').checked=project?.source_completed?.[sourceId]===true;
  $('sourceCompleted').disabled=!source()||!sourceCompletionReady;
  $('sourceCompleted').title=sourceCompletionReady?'라벨을 수정하면 완료 표시가 자동 해제됩니다.':'추출 작업이 끝난 뒤 에디터를 다시 열면 완료 표시를 저장할 수 있습니다.';
  $('sourceNotRequired').checked=project?.source_not_required?.[sourceId]===true;
  $('sourceNotRequired').disabled=!source()||!sourceNotRequiredReady;
  $('sourceNotRequired').title=sourceNotRequiredReady?'라벨과 원본은 보존하며 Export에서 제외합니다. 해제하면 다시 Export할 수 있습니다.':'에디터를 다시 실행하면 라벨링 불필요 표시를 저장할 수 있습니다.';
}
function render() {
  ++drawToken;
  updatePresetSelection();
  renderTrackControls();
  $("saveState").textContent = project
    ? `${project.name} · 저장됨 r${project.revision}`
    : "프로젝트를 열어주세요";
  $("empty").hidden = !!source();
  $("editor").hidden = !source();
  renderSourceList();
  $("undo").disabled = !undo.length;
  $("redo").disabled = !redo.length;
  $("taskReuse").replaceChildren(...taskOptions());
  activityPanel.sourceChanged();
  renderExportSource();
  if (!source()) {
    for (const image of $('cameras').querySelectorAll('img'))
      if(image.dataset.url) URL.revokeObjectURL(image.dataset.url);
    $('cameras').replaceChildren();
    return;
  }
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
  for (const image of $("cameras").querySelectorAll("img")) {
    if (image.dataset.url) URL.revokeObjectURL(image.dataset.url);
  }
  $("cameras").replaceChildren(
    ...source().cameras.map((camera) => {
      const el = document.createElement("div");
      el.className = "camera";
      el.dataset.camera = camera;
      const im = document.createElement("img");
      im.alt = camera;
      const caption = document.createElement("figcaption");
      const rotationKey=`editorCameraRotation:${source().fingerprint?.value || source().path}:${camera}`;
      let rotation=localStorage.getItem(rotationKey);
      rotation=rotation===null?(camera==='observation.images.right_wrist'?180:0):Number(rotation)===180?180:0;
      const rotate=document.createElement('button');
      rotate.setAttribute('aria-label',camera+' 보기 180° 회전');
      rotate.title='라벨링 화면만 회전합니다. 원본과 Export 영상은 유지됩니다.';
      const applyRotation=()=>{
        im.style.transform=`rotate(${rotation}deg)`;
        rotate.textContent=rotation?'원본 보기':'180° 보기';
        rotate.setAttribute('aria-pressed',String(rotation===180));
        caption.textContent=camera+(rotation?' · 보기 180°':'');
      };
      rotate.onclick=()=>{rotation=rotation?0:180;localStorage.setItem(rotationKey,String(rotation));applyRotation();};
      applyRotation();
      const b = document.createElement("button");
      b.textContent = "확대";
      b.onclick = () => {
        if (document.fullscreenElement) document.exitFullscreen();
        else el.requestFullscreen();
      };
      const tools=document.createElement('div');tools.className='camera-tools';tools.append(rotate,b);
      el.append(im, caption, tools);
      return el;
    }),
  );
  if (!selected) {
    autoRangeEnd=true;
    const draft = defaultTaskRange(
      clips(),
      frame,
      episode().num_frames,
      source().fps,
      cursorBoundary,
    );
    $("startFrame").value = draft.start;
    $("endFrame").value = draft.end;
  }
  renderTimeline();
  renderClips();
  renderClock();
}
function renderTimeline() {
  closeClipMenu();
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
  for(const layer of ['goal','step']) $(layer==='goal'?'goalTrack':'track').replaceChildren(
    ...clips(layer).map((c) => {
      const el = document.createElement("div");
      el.className = "clip" + (annotationTrack===layer && selected === c.id ? " selected" : "");
      el.style.left = (c.start_frame / n) * 100 + "%";
      el.style.width = ((c.end_frame - c.start_frame) / n) * 100 + "%";
      el.style.background = layer==='goal'?'#735ca1':taskColor(c.task_id);
      el.title = project.tasks[c.task_id];
      el.textContent = project.tasks[c.task_id];
      el.dataset.id = c.id;
      for (const edge of ["start", "end"]) {
        const h = document.createElement("span");
        h.className = "handle " + edge;
        h.dataset.edge = edge;
        el.append(h);
      }
      el.onpointerdown = (e) => clipPointer(e, c, el,layer);
      el.oncontextmenu = (e) => openClipMenu(e,c,layer);
      return el;
    }),
  );
  renderSelection();
  renderClock();
  $('excludedRanges').replaceChildren(...(project.exclusions||[]).filter(x=>x.source_id===sourceId && (x.episode_id??0)===episodeId).map(x=>{
    const el=document.createElement('div');el.style.left=(x.start_frame/n)*100+'%';el.style.width=((x.end_frame-x.start_frame)/n)*100+'%';return el;
  }));
}
function renderClips() {
  if(!episode()) { $('clipList').replaceChildren();$('exclusionList').replaceChildren();$('annotationWarnings').hidden=true;return; }
  const list = clips();
  const short = list.filter(c=>shortRangeWarning(c.start_frame,c.end_frame,source().fps));
  $('annotationWarnings').hidden=!short.length;
  $('annotationWarnings').textContent=`주의: 0.5초 미만 annotation 구간 ${short.length}개 (1프레임 ${short.filter(c=>c.end_frame-c.start_frame===1).length}개). 목록의 ‘짧음’ 구간을 확인하세요. 자동 연장·Export 차단은 하지 않습니다. ${annotationTrack==='step'?'인접한 동일 task는 합친 길이 기준입니다. 명시적인 분할·episode 경계는 유지합니다.':'별도 Goal은 작업 1회의 경계로 유지합니다.'}`;
  $("clipList").replaceChildren(
    ...list.map((c) => {
      const row = document.createElement("div");
      row.className = "cliprow" + (selected === c.id ? " selected" : "");
      const sw = document.createElement("span");
      sw.className = "swatch";
      sw.style.background = annotationTrack==='goal'?'#735ca1':taskColor(c.task_id);
      const t = document.createElement("span");
      t.className = "time";
      t.textContent = `${time(c.start_frame)} – ${time(c.end_frame)}`;
      const text = document.createElement("span");
      text.className = "text";
      text.textContent =
        project.tasks[c.task_id] + (c.new_episode ? " · 새 episode" : "");
      const warning=shortRangeWarning(c.start_frame,c.end_frame,source().fps);
      if(warning) {text.textContent+=` · 짧음: ${c.end_frame-c.start_frame}f`;row.title=warning;row.classList.add('short-annotation');}
      row.append(sw, t, text);
      const linked=annotationTrack==='step'?coveringGoal(project,c):null;
      if(annotationTrack==='step') {
        const badge=document.createElement('span');badge.className='goal-badge'+(linked?'':' unassigned');
        badge.textContent=linked?'Goal ✓':'Goal 없음/경계 걸침';badge.title=linked?project.tasks[linked.task_id]:'Step Export는 가능합니다. Goal 범위를 추가하거나 조정하세요.';
        row.append(badge);
      }
      row.onclick = () => selectClip(c.id);
      row.oncontextmenu = (e) => openClipMenu(e,c,annotationTrack);
      if(annotationTrack==='goal') {
        const wrap=document.createElement('div'),children=document.createElement('div');children.className='goal-children';
        for(const step of clips('step').filter(s=>s.start_frame<c.end_frame && c.start_frame<s.end_frame)) {
          const b=document.createElement('button');b.textContent=`${time(step.start_frame)}–${time(step.end_frame)} ${project.tasks[step.task_id]}`;
          b.onclick=()=>{setAnnotationTrack('step');selectClip(step.id);};children.append(b);
        }
        wrap.append(row,children);return wrap;
      }
      return row;
    }),
  );
  const kept = list.reduce((n, c) => n + c.end_frame - c.start_frame, 0);
  $("coverage").textContent =
    `${annotationTrack==='goal'?'Goal 범위':'Step 라벨'} ${kept} / ${episode().num_frames} frames · ${time(kept)} · Goal 미연결 Step ${clips('step').filter(s=>!coveringGoal(project,s)).length}개`;
  $('exclusionList').replaceChildren(...(project.exclusions||[]).filter(x=>x.source_id===sourceId && (x.episode_id??0)===episodeId).map(x=>{
    const row=document.createElement('div'),b=document.createElement('button');
    row.textContent=`${time(x.start_frame)}–${time(x.end_frame)} `;b.textContent='제외 해제';
    b.onclick=action(async()=>{await edit('restore_exclusion',{exclusion_id:x.id});toast('데이터 제외를 해제했습니다. 제거된 Step 라벨은 자동 복원하지 않습니다.');});row.append(b);return row;
  }));
}
function closeClipMenu() {
  clipMenuTarget=null;
  $('clipContextMenu').hidden=true;
  document.querySelectorAll('.context-target').forEach(el=>el.classList.remove('context-target'));
}
function openClipMenu(event,clip,track) {
  event.preventDefault();event.stopPropagation();
  closeClipMenu();
  playing=false;renderClock();
  clipMenuTarget={projectId:project.project_id,sourceId,episodeId,track,clipId:clip.id};
  const menu=$('clipContextMenu');
  $('clipContextTitle').textContent=`${track==='goal'?'Goal':'Step'} · ${time(clip.start_frame)}–${time(clip.end_frame)}`;
  event.currentTarget.classList.add('context-target');
  menu.hidden=false;
  const box=menu.getBoundingClientRect();
  menu.style.left=Math.max(8,Math.min(event.clientX,window.innerWidth-box.width-8))+'px';
  menu.style.top=Math.max(8,Math.min(event.clientY,window.innerHeight-box.height-8))+'px';
  $('deleteContextClip').focus({preventScroll:true});
}
async function deleteContextClip() {
  const target=clipMenuTarget;
  if (!target) return;
  if (pendingRequests || exportBusy) throw Error('진행 중인 저장·설정 확인이 끝난 뒤 삭제하세요.');
  closeClipMenu();
  if (project?.project_id!==target.projectId || sourceId!==target.sourceId || episodeId!==target.episodeId)
    throw Error('원본이 바뀌었습니다. 삭제할 클립을 다시 우클릭하세요.');
  const wasSelected=annotationTrack===target.track && selected===target.clipId;
  await edit('remove_clip',{clip_id:target.clipId,track:target.track});
  if (wasSelected && selected===null && annotationTrack===target.track && sourceId===target.sourceId && episodeId===target.episodeId) {
    deselect();$('task').value='';beginAutomaticRange();renderSelection();updatePresetSelection();
  }
  $('undo').focus({preventScroll:true});
  toast(`${target.track==='goal'?'Goal':'Step'} 클립을 삭제했습니다. 되돌리기 또는 Ctrl+Z로 복구할 수 있습니다.`);
}
function selectClip(id) {
  const c = clips().find((x) => x.id === id);
  if (!c) return;
  selected = id;
  autoRangeEnd=false;
  $("startFrame").value = c.start_frame;
  $("endFrame").value = c.end_frame;
  $("task").value = project.tasks[c.task_id];
  updatePresetSelection();
  $("newEpisode").checked = !!c.new_episode;
  $('goalIncludeUnlabeled').checked=!!c.include_unlabeled;
  $("clipMode").textContent = `선택 ${annotationTrack} · ` + id.slice(0, 8);
  seek(c.start_frame);
  renderTimeline();
  renderClips();
}
function deselect() {
  selected = null;
  $("clipMode").textContent = "새 구간";
  $("newEpisode").checked = false;
  $('goalIncludeUnlabeled').checked=false;
  renderTimeline();
  renderClips();
}
function beginAutomaticRange(start = null) {
  if (!episode()) return;
  autoRangeEnd=true;
  const draft=start===null
    ? defaultTaskRange(clips(),frame,episode().num_frames,source().fps,cursorBoundary)
    : nextTaskRange(start,frame,episode().num_frames,cursorBoundary);
  $('startFrame').value=draft.start;$('endFrame').value=draft.end;
}
function syncAutomaticEnd() {
  if (autoRangeEnd && !selected && episode())
    $('endFrame').value=nextTaskRange(Number($('startFrame').value),frame,episode().num_frames,cursorBoundary).end;
}
function renderSelection() {
  if (!episode()) return;
  const [a, b] = range();
  $("selection").style.left = (a / episode().num_frames) * 100 + "%";
  $("selection").style.width =
    (Math.max(0, b - a) / episode().num_frames) * 100 + "%";
  $("selectionText").textContent = `[${a}, ${b}) · ${time(a)}–${time(b)}`;
  // Playback may update the automatic end; never replace text being typed.
  if(document.activeElement!==$('startSeconds')) $("startSeconds").value = (a / source().fps).toFixed(4);
  if(document.activeElement!==$('endSeconds')) $("endSeconds").value = (b / source().fps).toFixed(4);
  $('followCursorEnd').checked=autoRangeEnd && !selected;
  $('followCursorEnd').disabled=!!selected;
  const warning=shortRangeWarning(a,b,source().fps);
  $('shortRangeWarning').hidden=!warning;$('shortRangeWarning').textContent=warning;
  $('rangeMode').textContent=selected?'기존 구간 · 재생해도 범위는 바뀌지 않습니다.'
    : autoRangeEnd ? (b<=a?'끝점 자동 · 시작 이후로 재생/이동하면 범위가 늘어납니다.':'끝점 자동 · 현재 재생 프레임까지 선택합니다.')
    : '끝점 고정 · 직접 지정한 범위를 유지합니다.';
}
function renderClock() {
  if (!episode()) return;
  syncAutomaticEnd();
  renderSelection();
  $("seek").value = frame;
  $("clock").textContent =
    `${time(frame)} · frame ${frame}/${episode().num_frames - 1}`;
  $("playhead").style.left = ((cursorBoundary ?? frame) / episode().num_frames) * 100 + "%";
  showSnap(cursorBoundary===null ? null : {frame:cursorBoundary,snapped:true});
  $("play").textContent = playing ? "Ⅱ 정지" : "▶ 재생";
  dataInspector.cursorChanged();
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
          try {
            await im.decode();
          } finally {
            if (previous) URL.revokeObjectURL(previous);
          }
          im.dataset.frame=String(f);
          el.classList.remove("missing");
        } catch (err) {
          el.classList.add("missing");
          el.title = err.message;
        }
      }),
    );
    if(token===drawToken && sourceId===s && episodeId===e) {
      const context={project_id:pid,source:source(),episode_id:e};
      if([...$('cameras').children].some(el=>el.classList.contains('missing'))) activityPanel.unavailable(context);
      else activityPanel.presented(context,f);
    }
  } finally {
    previewBusy = false;
    if (
      source() &&
      (token !== drawToken || frame !== f || sourceId !== s || episodeId !== e)
    )
      fetchFrame();
  }
}
function seek(value, boundary=null) {
  if (!episode()) return;
  cursorBoundary=boundary;
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
  if(playing) cursorBoundary=null;
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
    cursorBoundary=null;
    frame = next;
    renderClock();
    fetchFrame();
  }
  if (playing) requestAnimationFrame(tick);
}
async function edit(operation, extra) {
  if (!project) throw Error("프로젝트를 먼저 여세요.");
  if(!annotationReady) throw Error('Goal/Step을 지원하는 에디터 서버로 재시작 후 새로고침하세요.');
  const before = copy(project);
  $("saveState").textContent = "저장 중…";
  try {
    project = await api(`/api/projects/${project.project_id}/edit`, {
      operation,
      track:annotationTrack,
      expected_revision: project.revision,
      ...extra,
    });
  } catch (e) {
    $("saveState").textContent = `저장되지 않음 · r${project.revision}`;
    throw e;
  }
  undo.push(before);
  redo = [];
  const selection=clips().find((c) => c.id === selected || c.merged_clip_ids?.includes(selected));
  selected=selection?.id || null;
  if(selection && !['source_completion','source_not_required','remove_clip'].includes(operation)) {
    // A neighboring same-task edit can merge both sides. Never leave the old
    // fragment bounds in the inspector: a second edit would otherwise trim it.
    $('startFrame').value=selection.start_frame;$('endFrame').value=selection.end_frame;
    $('task').value=project.tasks[selection.task_id];
    $('newEpisode').checked=!!selection.new_episode;
    $('goalIncludeUnlabeled').checked=!!selection.include_unlabeled;
  }
  renderTimeline();
  renderClips();
  renderSourceList();
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
    throw Error(autoRangeEnd && !selected && b<=a
      ? "다음 구간의 시작 이후로 영상을 재생/이동하세요. 아직 선택된 프레임이 없습니다."
      : "시작 < 끝 범위로 프레임을 선택하세요.");
  return {
    source_id: sourceId,
    episode_id: episodeId,
    start_frame: a,
    end_frame: b,
  };
}
async function applyLabel() {
  syncAutomaticEnd();
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
    include_unlabeled:$('goalIncludeUnlabeled').checked,
  });
  const warning=shortRangeWarning(r.start_frame,r.end_frame,source().fps);
  if(warning) toast('저장됨 · '+warning);
  deselect();
  beginAutomaticRange(r.end_frame);
  const nextStart=Number($('startFrame').value);
  $("clipMode").textContent =
    nextStart === episode().num_frames
      ? "마지막 프레임까지 지정했습니다. 필요하면 기존 구간을 수정하세요."
      : `다음 task · 직전 끝점 ${time(nextStart)}부터`;
  renderSelection();
}
async function updateClip() {
  if (!selected) throw Error("수정할 clip을 선택하세요.");
  await edit("update_clip", {
    ...currentRange(),
    clip_id: selected,
    task: $("task").value,
    new_episode: $("newEpisode").checked,
    include_unlabeled:$('goalIncludeUnlabeled').checked,
  });
  const [a,b]=range(),warning=shortRangeWarning(a,b,source().fps);
  if(warning) toast('수정됨 · '+warning);
}
async function history(backward) {
  const from = backward ? undo : redo,
    to = backward ? redo : undo;
  if (!from.length) return;
  if (!revisionHistoryRestore) throw Error('되돌리기 수정은 추출 작업이 끝난 뒤 에디터를 다시 열면 적용됩니다. 현재 제외 구간은 제외 목록의 ‘제외 해제’를 사용하세요.');
  const desired = copy(from[from.length - 1]),
    before = copy(project);
  project = await api(
    `/api/projects/${project.project_id}`,
    { restore_revision: desired.revision, expected_revision: project.revision },
    "PUT",
  );
  from.pop();
  to.push(before);
  if(!source()) {
    sourceId=activeSources(project)[0]?.source_id||null;
    episodeId=source()?.episodes[0]?.episode_id||0;frame=resumeFrame();
  }
  selected = null;
  render();
  await fetchFrame();
}
async function setSourceRemoved(sid,removed) {
  if(!sourceRemovalReady) throw Error('진행 중인 작업이 끝난 뒤 서버를 업데이트·재시작하고 새로고침하세요.');
  const current=project;
  const item=current?.sources.find(s=>s.source_id===sid);
  if(!item) throw Error('현재 프로젝트에 없는 원본입니다.');
  const name=(item.original_path||item.path).split('/').pop();
  if(removed && !await confirmSourceRemoval(name)) return;
  const before=copy(current);
  const updated=await api(`/api/projects/${current.project_id}/edit`,{
    operation:'source_removal',source_id:sid,removed,expected_revision:current.revision,
  });
  if(project!==current) {toast('원래 프로젝트에 원본 상태를 저장했습니다. 다시 열면 확인할 수 있습니다.');return;}
  project=updated;undo.push(before);redo=[];
  if(!source()) {
    playing=false;selected=null;cursorBoundary=null;
    sourceId=activeSources(project)[0]?.source_id||null;
    episodeId=source()?.episodes[0]?.episode_id||0;frame=resumeFrame();
    render();await fetchFrame();
  } else {
    renderSourceList();renderExportSource();
    $('saveState').textContent=`${project.name} · 저장됨 r${project.revision}`;
    $('undo').disabled=false;$('redo').disabled=true;
  }
  toast(removed?'프로젝트에서 제거했습니다. bag·캐시·annotation은 보존합니다.':'원본을 복원했습니다. 저장된 annotation을 그대로 사용할 수 있습니다.');
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
  if (e.button!==undefined && e.button!==0) return;
  if (!episode() || e.target.closest(".clip")) return;
  e.preventDefault();
  const layer=e.target.closest('[data-track]')?.dataset.track;
  if(layer) setAnnotationTrack(layer);
  const origin = dragPoint(e),start=origin.frame;
  playing = false;
  deselect();
  seek(start,origin.snapped?start:null);
  let moved=false;
  const move = (m) => {
    if(!moved && Math.abs(m.clientX-e.clientX)<3)return;
    moved=true;autoRangeEnd=false;
    const point = dragPoint(m, null, start);
    const draft = dragSelectionRange(start, point.frame, episode().num_frames);
    $("startFrame").value = draft.start;
    $("endFrame").value = draft.end;
    showSnap(point);
    renderSelection();
    seek(draft.preview,point.snapped?point.frame:null);
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    window.removeEventListener("pointercancel", up);
    showSnap(cursorBoundary===null?null:{frame:cursorBoundary,snapped:true});
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
  window.addEventListener("pointercancel", up);
};
function dragPoint(event, excludedClipId = null, excludedBoundary = null) {
  return snapBoundary(pos(event),dragBoundaries(excludedClipId).filter(f=>f!==excludedBoundary),
    $("timeline").getBoundingClientRect().width / episode().num_frames,event.altKey);
}
function dragBoundaries(excludedClipId = null) {
  const boundaries = [0, episode().num_frames];
  for (const clip of [...clips('step'),...clips('goal')]) {
    if (clip.id !== excludedClipId)
      boundaries.push(clip.start_frame, clip.end_frame);
  }
  for (const exclusion of project.exclusions || []) {
    if (exclusion.source_id===sourceId && (exclusion.episode_id??0)===episodeId)
      boundaries.push(exclusion.start_frame, exclusion.end_frame);
  }
  return boundaries;
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
function clipPointer(e, c, el,layer) {
  if (e.button!==undefined && e.button!==0) return;
  e.stopPropagation();
  e.preventDefault();
  playing = false;
  const edge = e.target.dataset.edge;
  if(!setAnnotationTrack(layer))return;
  selectClip(c.id);
  if (!edge) return;
  el = $(layer==='goal'?'goalTrack':'track').querySelector(`[data-id="${c.id}"]`);
  const old = [c.start_frame, c.end_frame];
  const anchorFrame=pos(e);
  let moved=false;
  const move = (m) => {
    if(!moved && Math.abs(m.clientX-e.clientX)<3)return;
    moved=true;
    const raw=(edge==='end'?old[1]:old[0])+pos(m)-anchorFrame;
    const point=resizeClipRange(old[0],old[1],edge,raw,dragBoundaries(c.id),
      $('timeline').getBoundingClientRect().width/episode().num_frames,episode().num_frames,m.altKey);
    $("startFrame").value=point.start;$("endFrame").value=point.end;
    const [a, b] = range();
    el.style.left = (a / episode().num_frames) * 100 + "%";
    el.style.width = ((b - a) / episode().num_frames) * 100 + "%";
    const actual = edge === "end" ? b : a;
    showSnap({
      frame: actual,
      snapped: point.snapped && point.frame === actual,
    });
    renderSelection();
    seek(edge === "end" ? b - 1 : a,point.snapped?actual:null);
  };
  const up = async (event) => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    window.removeEventListener("pointercancel", up);
    showSnap(cursorBoundary===null?null:{frame:cursorBoundary,snapped:true});
    if (event.type === "pointercancel") {
      selectClip(c.id);
      return;
    }
    try {
      if(!moved || (Number($('startFrame').value)===old[0] && Number($('endFrame').value)===old[1]))return;
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
function jobDate(seconds) {
  if (!Number.isFinite(Number(seconds)) || Number(seconds)<=0) return '시각 미기록';
  const parts=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',
    hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date(Number(seconds)*1000));
  const values=Object.fromEntries(parts.map(part=>[part.type,part.value]));
  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second} KST`;
}
function jobOrder(job) {return Number(job.result?.published_at)||Number(job.created)||0;}
async function pollJobs() {
  try {
    const jobs = (await api("/api/jobs")).filter(
      (j) => !j.project_id || j.project_id === project?.project_id
        || (j.kind === 'export' && j.status === 'completed'),
    ).sort((a,b)=>jobOrder(b)-jobOrder(a));
    const completedExports=jobs.filter(j=>j.kind==='export' && j.status==='completed' && j.result?.path);
    const latestHubPlan=jobs.find(j=>j.kind==='hub-plan' && j.status==='completed');
    const latestHubUpload=jobs.find(j=>j.kind==='hub-upload' && j.status==='completed');
    const latestByPath=new Map();
    for (const job of completedExports)
      if (!latestByPath.has(job.result.path)) latestByPath.set(job.result.path,job.id);
    const projectNames = new Map(Array.from($("projectSelect").options,
      (option) => [option.value, option.textContent]));
    const cards = jobs.map((j) => {
        const exported=j.kind==='export' && j.status==='completed';
        const currentResult=exported && latestByPath.get(j.result?.path)===j.id;
        const el = document.createElement("div");
        el.className = "job"+(exported?(currentResult?' current-export':' previous-export'):'');
        const title = document.createElement("div");
        title.className='job-title';
        if (exported) {
          title.textContent = `${j.id===completedExports[0]?.id?'가장 최근 Export · ':''}${currentResult?'현재 폴더 결과':'이전 Export 기록'} · ${j.result?.dataset_name || j.result?.path?.split('/').pop() || ''}`;
        } else if (['hub-plan','hub-upload'].includes(j.kind)) {
          const latest=j.kind==='hub-plan'?latestHubPlan:latestHubUpload;
          const kind=j.kind==='hub-plan'?'업로드 미리보기':'실제 Hub 업로드';
          const status={queued:'대기 중',running:'진행 중',completed:'완료',cancelled:'취소됨',failed:'실패'}[j.status] || j.status;
          title.textContent=`${j.id===latest?.id?'가장 최근 · ':''}${kind} · ${status}`+
            `${j.result?.repo_id?` · ${j.result.repo_id}`:''} · ${j.message || ''}`;
        } else {
          title.textContent=`${j.kind} · ${j.status} · ${j.message || ''}`;
        }
        const bar = document.createElement("progress");
        bar.max = 1;
        bar.value = j.progress || 0;
        el.append(title, bar);
        const dates=document.createElement('p');dates.className='muted';
        dates.textContent=`시작 ${jobDate(j.created)}`+(j.result?.published_at?` · 게시 ${jobDate(j.result.published_at)}`:'');
        el.append(dates);
        if (['export', 'export-preview'].includes(j.kind)) {
          const owner = document.createElement('p');
          owner.className = 'muted';
          owner.textContent = `프로젝트: ${j.result?.project_name || projectNames.get(j.project_id) || j.project_id || '정보 없음'}`;
          el.append(owner);
        }
        if (exported) {
          const names=document.createElement('p');names.className='job-sources';
          names.textContent=`원본: ${j.result?.source_names?.join(', ') || '이전 작업의 원본 정보 미기록'}`;
          el.append(names);
          const addedFrames=j.result?.frames_added ?? j.checkpoint?.completed_frames;
          const addedEpisodes=j.result?.episodes_added ?? j.checkpoint?.completed_episodes;
          const counts=document.createElement('p');counts.className='job-summary';
          counts.textContent=`이번 ${j.result?.mode==='new'?'생성':'추가'}: ${addedEpisodes??'?'} episodes · ${addedFrames?.toLocaleString()??'?'}프레임`+
            ` → 완료 후 합계: ${j.result?.episodes??'?'} episodes · ${j.result?.frames?.toLocaleString()??'?'}프레임`;
          el.append(counts);
          if (!currentResult) {
            const note=document.createElement('p');note.className='muted';
            note.textContent='과거 작업 기록입니다. 아래 공통 경로는 이후 Export로 갱신되어 현재 최신 결과를 담고 있습니다.';
            el.append(note);
          }
        }
        if (j.kind === 'export-preview' && j.status === 'completed') {
          const open=document.createElement('button');
          open.textContent='이 데이터 미리보기 열기';
          open.onclick=action(()=>dataInspector.loadPreview(j.id));
          el.append(open);
        }
        if (j.checkpoint && !exported) {
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
            cursorBoundary=null;
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
            if (j.result.mask_summary) {
              const detail = document.createElement('details');
              const title = document.createElement('summary');
              title.textContent = '관절별 유효 정답 / 누락 요약 (0은 미기록·학습 제외)';
              detail.append(title);
              for (const [key, stats] of Object.entries(j.result.mask_summary)) {
                const line = document.createElement('p');
                line.textContent = `${key}: ${stats.frames} frames · 미기록 값은 명령 0이 아닙니다.`;
                detail.append(line);
                stats.valid_counts.forEach((count, i) => {
                  const item = document.createElement('div');
                  item.textContent = `${stats.names?.[i] || i}: 유효 ${count}/${stats.frames} (${(100*count/stats.frames).toFixed(1)}%), 누락 ${stats.masked_counts[i]}`;
                  detail.append(item);
                });
              }
              el.append(detail);
            }
            const use = document.createElement("button");
            use.textContent = currentResult ? "이 결과를 Hub 업로드 경로로 사용" : "이 경로의 최신 결과를 Hub 업로드 경로로 사용";
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
        return {job: j, element: el};
      });
    for (const group of $("jobList").querySelectorAll('[data-job-group]')) {
      const key = group.dataset.jobGroup;
      const items = cards.filter(({job}) => {
        const kind = job.kind;
        const category = ['demo', 'import', 'import-batch', 'ssh-import'].includes(kind) ? 'import'
          : kind === 'export' ? 'export'
          : kind === 'export-preview' ? 'preview'
          : ['hub-plan', 'hub-upload'].includes(kind) ? 'hub' : 'other';
        return category === key;
      });
      const current = items.filter(({job}) => job.status !== 'completed' && (key!=='hub' || job.status!=='cancelled'));
      const completed = items.filter(({job}) => job.status === 'completed');
      group.hidden = ['hub', 'other'].includes(key) && !items.length;
      group.querySelector('.job-current').replaceChildren(
        ...(current.length ? current.map(({element}) => element) : ['진행 중인 작업 없음']),
      );
      if (key==='hub') {
        const completedSections=[];
        for (const [selector,label,entries] of [
          ['.job-hub-plans','업로드 미리보기 완료 · 전송 안 함',completed.filter(({job})=>job.kind==='hub-plan')],
          ['.job-hub-uploads','실제 Hub 업로드 완료',completed.filter(({job})=>job.kind==='hub-upload')],
          ['.job-hub-cancelled','취소된 이전 작업',items.filter(({job})=>job.status==='cancelled')],
        ]) {
          const history=group.querySelector(selector);
          history.hidden=!entries.length;
          history.querySelector('summary').textContent=`${label} ${entries.length}개`;
          history.querySelector('.job-history').replaceChildren(...entries.map(({element})=>element));
          if (selector!=='.job-hub-cancelled')
            completedSections.push({history,order:entries.length?jobOrder(entries[0].job):-1});
        }
        group.querySelector('.job-hub-results').replaceChildren(
          ...completedSections.sort((a,b)=>b.order-a.order).map(({history})=>history),
        );
        continue;
      }
      // Keep the details element itself so polling preserves the user's toggle.
      const history = group.querySelector('.job-completed');
      history.hidden = !completed.length;
      const label = key === 'export' ? '실제 Export 완료 · 전체 프로젝트'
        : key === 'preview' ? '미리보기 완료 · 현재 프로젝트' : '완료된 작업';
      history.querySelector('summary').textContent = `${label} ${completed.length}개`;
      history.querySelector('.job-history').replaceChildren(...completed.map(({element}) => element));
    }
    dataInspector.observeJobs(jobs);
  } catch (e) {
    console.warn(e);
  }
}
const reviewQuality = setupQualityReview();
const activityPanel=setupActivity({api,getContext:()=>({project_id:project?.project_id,source:source(),episode_id:episodeId})});
const dataInspector = setupDataInspector({
  api,
  getContext: () => ({project_id:project?.project_id,source:source(),episode:episodeId,frame,playing}),
  getProject: () => project,
  getSchema: () => $("exportSchema").value,
  getAnnotationTrack: () => $('exportAnnotationTrack').value,
  getExportSourceId: exportSourceId,
  notify: toast,
  preparePreview: async () => {
    if (!project) throw Error("프로젝트를 먼저 여세요.");
    const projectId=project.project_id,body=exportBody();
    let plan=await api(`/api/projects/${projectId}/preview-plan`,body);
    if (plan.quality?.issues.length && !plan.quality.ready) {
      const approval=await reviewQuality(plan.quality);
      if (!approval) return null;
      body.quality=approval;
      plan=await api(`/api/projects/${projectId}/preview-plan`,body);
    }
    if (project.project_id!==projectId || (body.source_id||null)!==exportSourceId())
      throw Error("Export 대상이 바뀌었습니다. 다시 계산하세요.");
    const job=await api(`/api/projects/${projectId}/previews`,{...body,expected_revision:plan.project_revision});
    await pollJobs();
    return job.id;
  },
});
$("previewExport").onclick=()=>dataInspector.calculate();
function exportSourceId() {
  return $('exportMode').value!=='replace' && $('exportScope').value==='source' ? sourceId : null;
}
function renderExportSource() {
  const mode=$('exportMode').value;
  $('exportSourceName').textContent=mode==='replace'
    ? '수정 대상: 현재 프로젝트의 라벨 전체 (선택한 원본만으로 교체하지 않습니다).'
    : $('exportScope').value==='project'
      ? 'Export 대상: 프로젝트 전체의 라벨 구간 (여러 원본 일괄 처리).'
      : source() ? `Export 대상: ${(source().original_path||source().path).split('/').pop()} · 이 원본의 모든 episode 라벨 구간`
        : '왼쪽에서 Export할 원본을 선택하세요.';
}
function exportBody() {
  if(!annotationReady) throw Error('에디터 서버를 재시작하고 새로고침한 후 Export하세요.');
  if(!sourceExportReady) throw Error('선택 원본 Export·중복 방지를 적용하려면 에디터를 껐다 켜세요. 저장된 라벨은 유지됩니다.');
  if($('exportMode').value!=='replace' && $('exportScope').value==='source' && !source())
    throw Error('왼쪽에서 Export할 원본을 선택하세요.');
  return {mode:$("exportMode").value,schema:$("exportSchema").value,
    source_id:exportSourceId()||undefined,
    annotation_track:$('exportAnnotationTrack').value,
    repo_name:$("repoName").value.trim(),target:$("exportPath").value.trim()||undefined,
    confirm_existing:$("confirmExisting").checked,...dataInspector.previewOptions()};
}
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
  const body = exportBody();
  if (mode === "new" && !body.repo_name)
    throw Error("새 dataset repo 이름을 입력하세요.");
  if (mode !== "new" && !body.confirm_existing)
    throw Error("기존 dataset 변경 확인에 체크하세요.");
  const projectId = project.project_id;
  toast("선택 구간의 누락 데이터를 확인 중…");
  let p = await api(`/api/projects/${projectId}/export-plan`, body);
  if (p.quality?.issues.length && !p.quality.ready) {
    const approval = await reviewQuality(p.quality);
    if (!approval) return;
    if (project.project_id !== projectId || (body.source_id||null)!==exportSourceId())
      throw Error("Export 대상이 바뀌었습니다. 다시 Export하세요.");
    body.quality = approval;
    p = await api(`/api/projects/${projectId}/export-plan`, body);
  }
  if (!p.kept_frames) throw Error("먼저 task가 있는 clip을 만드세요.");
  if (project.project_id !== projectId || (body.source_id||null)!==exportSourceId())
    throw Error("Export 대상이 바뀌었습니다. 다시 Export하세요.");
  const shortExportWarning=p.short_annotations?.length
    ? `주의: 0.5초 미만 annotation 구간 ${p.short_annotations.length}개 (1프레임 ${p.short_annotations.filter(c=>c.frames===1).length}개). 의도한 범위인지 확인하세요. 자동 연장·Export 차단은 하지 않습니다.\n` : '';
  if(shortExportWarning) toast(shortExportWarning);
  const confirmExport=message=>confirm(`Export 대상: ${p.source_names.join(', ')}\n이미 저장된 원본 프레임 ${p.skipped_duplicate_frames}개 건너뜀\n\n`+shortExportWarning+message);
  if (
    !confirmExport(
      `${mode === "new" ? "새 생성" : mode === "append" ? "기존에 추가" : "기존 수정"}: ${p.repo_id}\n${p.target}\n\nInstruction 트랙: ${p.annotation_track==='goal'?'Goal · 전체 작업':'Step · 세부 동작'}\n출력 구성: ${p.schema}\n${p.schema !== 'legacy' ? '미기록 목표·상태는 validity=0으로 저장합니다. 학습 mask 연결이 필요합니다.\n랙 보정/다중 태그/TF가 없으면 랙 상대 위치도 미기록 처리됩니다.\n' : ''}현재 선택 ${p.kept_frames} frames (${(p.kept_frames / p.fps).toFixed(2)}초)\n미지정·승인된 누락 제외 ${p.dropped_frames} frames\n최종 ${p.output_frames} frames / ${p.output_episodes} episodes\n누락 구간 처리 ${p.quality?.issues.length || 0}건 (선택 내역 기록)\n${mode === "append" ? `기존 ${p.existing_frames} frames는 유지합니다.\n` : ""}${mode !== "new" ? "기존 폴더는 복구본으로 보존합니다.\n" : ""}\n저장본 r${p.project_revision}으로 진행할까요?`,
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
  $('exportScopeLabel').hidden=mode==='replace';
  renderExportSource();
  $("exportPathLabel").textContent =
    mode === "new" ? "새 출력 폴더 (선택)" : "기존 LeRobot dataset 폴더 (필수)";
  $("exportHint").textContent =
    mode === "new"
      ? "위에서 선택한 대상의 라벨 구간만 새 dataset으로 저장합니다. Hub에는 업로드하지 않습니다."
      : mode === "append"
        ? "기존 episode를 유지하고 선택한 대상의 새 구간만 추가합니다. 이미 저장된 원본 프레임은 건너뛰고 복구본을 보존합니다."
        : "수정할 dataset을 원본으로 불러온 뒤 사용하세요. 현재 프로젝트의 라벨 구간만 남긴 결과로 교체하고, 기존 폴더는 복구본으로 보존합니다.";
}
$("exportMode").onchange = updateExportMode;
$('exportScope').onchange=renderExportSource;
$("exportPath").addEventListener("input", () => {
  $("confirmExisting").checked = false;
});
async function loadSignals() {
  signalData = await api(
    `/api/projects/${project.project_id}/signals/${sourceId}/${episodeId}`,
  );
  const keys = [...new Set(signalData.flatMap((p) => Object.keys(p.values)))];
  $("signalKey").replaceChildren(...keys.map((k) => new Option(k, k)));
  signalDimensions();
  drawSignals();
}
function signalDimensions() {
  const key = $('signalKey').value;
  const feature = source()?.features?.[key];
  const count = feature?.shape?.reduce((a,b) => a*b,1) || signalData.find(p => p.values[key])?.values[key].length || 1;
  $('signalDimension').replaceChildren(...Array.from({length:count},(_,i) => new Option(feature?.names?.[i] || `${key}[${i}]`, i)));
}
function drawSignals() {
  const cv = $("signals"),
    ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, cv.width, cv.height);
  const key = $("signalKey").value,
    dim = Number($('signalDimension').value || 0),
    values = signalData.map((p) => p.values[key]?.[dim]).filter(Number.isFinite);
  if (!values.length) {
    ctx.fillStyle = '#9aa9bc';
    ctx.fillText(`${key}[${dim}] 미기록 / 유효성 0 — 실제 0 명령이 아닙니다`,10,20);
    return;
  }
  const min = Math.min(...values),
    max = Math.max(...values);
  ctx.strokeStyle = "#61d6b4";
  ctx.lineWidth = 2;
  ctx.beginPath();
  let active = false;
  for (const p of signalData) {
    const v = p.values[key]?.[dim];
    if (!Number.isFinite(v)) {
      ctx.fillStyle = '#77777744';
      ctx.fillRect((p.frame / episode().num_frames) * cv.width,20,Math.max(2,cv.width/signalData.length),100);
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
  ctx.fillText(`${key}[${dim}]  ${min.toFixed(3)} .. ${max.toFixed(3)} · 회색/빈 구간: 미기록`, 10, 12);
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
  cursorBoundary=null;
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
$("prev").onclick = () => seek((cursorBoundary ?? frame) - 1);
$("next").onclick = () => seek((cursorBoundary ?? frame) + 1);
$("play").onclick = togglePlay;
$("seek").onpointerdown=e=>{seekDragging=true;seekAlt=e.altKey;};
$("seek").onpointermove=e=>{if(seekDragging) seekAlt=e.altKey;};
window.addEventListener('pointerup',()=>{seekDragging=false;});
window.addEventListener('pointercancel',()=>{seekDragging=false;});
$("seek").oninput = () => {
  const value=Number($("seek").value);
  if(!seekDragging) {seek(value);return;}
  const point=snapBoundary(value,dragBoundaries(),
    $('seek').getBoundingClientRect().width/episode().num_frames,seekAlt);
  seek(point.frame,point.snapped?point.frame:null);
};
$("speed").onchange = () => {
  lastTick = performance.now();
  playOrigin = frame;
};
$("zoom").oninput = () => {
  $("timeline").style.width = Number($("zoom").value) * 100 + "%";
};
$("markIn").onclick = () => {
  $("startFrame").value = cursorBoundary ?? frame;
  syncAutomaticEnd();
  renderSelection();
};
$("markOut").onclick = () => {
  autoRangeEnd=false;
  $("endFrame").value = cursorBoundary ?? frame + 1;
  renderSelection();
};
$("startFrame").oninput = () => {syncAutomaticEnd();renderSelection();};
$("endFrame").oninput = () => {autoRangeEnd=false;renderSelection();};
for (const [seconds, frames] of [
  ["startSeconds", "startFrame"],
  ["endSeconds", "endFrame"],
]) {
  if(frames==='endFrame') $(seconds).oninput=()=>{autoRangeEnd=false;renderSelection();};
  $(seconds).onchange = () => {
    if (!source()) return;
    if(frames==='endFrame') autoRangeEnd=false;
    $(frames).value = Math.ceil(Number($(seconds).value) * source().fps - 1e-9);
    syncAutomaticEnd();
    renderSelection();
  };
}
$("apply").onclick = action(applyLabel);
$("update").onclick = action(updateClip);
$("clearClip").onclick = () => {deselect();beginAutomaticRange();renderSelection();};
$('followCursorEnd').onchange = () => {
  if(selected)return;
  autoRangeEnd=$('followCursorEnd').checked;syncAutomaticEnd();renderSelection();
};
$("exclude").onclick = action(async () => {
  if(!confirm('선택 구간을 Step·Goal Export 모두에서 제외합니다. Goal 범위를 넓혀도 다시 포함되지 않습니다. 진행할까요?'))return;
  await edit("exclude", currentRange());
  deselect();
});
$('removeAnnotation').onclick=action(async()=>{await edit('remove_annotation',currentRange());deselect();});
$("split").onclick = action(async () => {
  const boundary=cursorBoundary ?? frame;
  const c =
    clips().find((c) => c.id === selected) ||
    clips().find((c) => c.start_frame < boundary && boundary < c.end_frame);
  if (!c || !(c.start_frame < boundary && boundary < c.end_frame))
    throw Error("분할할 clip 내부로 이동하세요. 경계 근처는 Alt를 눌러 자유롭게 이동할 수 있습니다.");
  await edit("split", { clip_id: c.id, frame:boundary });
});
$("undo").onclick = action(() => history(true));
$("redo").onclick = action(() => history(false));
$('sourceStatusFilter').onchange=renderSourceList;
$('removeSource').onclick=action(()=>setSourceRemoved(sourceId,true));
$('sourceCompleted').onchange=action(async()=>{
  try {await edit('source_completion',{source_id:sourceId,completed:$('sourceCompleted').checked});}
  finally {renderSourceList();}
});
$('sourceNotRequired').onchange=action(async()=>{
  try {await edit('source_not_required',{source_id:sourceId,not_required:$('sourceNotRequired').checked});}
  finally {renderSourceList();}
});
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
window.editorRefresh=refreshEditor;
$("refresh").onclick=refreshEditor;
$('deleteContextClip').onclick=action(deleteContextClip);
document.addEventListener('pointerdown',e=>{
  if (!$('clipContextMenu').hidden && !$('clipContextMenu').contains(e.target)) closeClipMenu();
});
document.addEventListener('scroll',closeClipMenu,true);
window.addEventListener('resize',closeClipMenu);
$("loadSignals").onclick = action(loadSignals);
$("signalKey").onchange = () => { signalDimensions(); drawSignals(); };
$("signalDimension").onchange = drawSignals;
document.addEventListener("keydown", (e) => {
  if (e.key==='F5' || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase()==='r')) {
    e.preventDefault();refreshEditor();return;
  }
  if (!$('clipContextMenu').hidden) {
    if (e.key==='Escape') {e.preventDefault();closeClipMenu();}
    return;
  }
  if ($("filePicker").open || $("qualityReview").open) return;
  const arrowDirection=e.key==='ArrowLeft' ? -1 : e.key==='ArrowRight' ? 1 : 0;
  if ((e.target.matches("input,textarea,select") && !(arrowDirection && e.target===$('seek'))) || !episode()) return;
  if (e.code === "Space") {
    e.preventDefault();
    togglePlay();
  } else if (arrowDirection) {
    e.preventDefault();
    const step=e.shiftKey ? 1 : Math.max(1,Math.round(source().fps*0.5));
    seek((cursorBoundary ?? frame) + arrowDirection*step);
  } else if (e.key.toLowerCase() === "i") $("markIn").click();
  else if (e.key.toLowerCase() === "o") $("markOut").click();
  else if (e.key.toLowerCase() === "s" && !e.ctrlKey && !e.metaKey)
    $("split").click();
  else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
    e.preventDefault();
    (e.shiftKey ? $("redo") : $("undo")).click();
  }
});
action(async () => {
  const cfg = await api("/api/config");
  revisionHistoryRestore=cfg.revision_history_restore===true;
  sourceCompletionReady=cfg.source_completion===true;
  sourceNotRequiredReady=cfg.source_not_required===true;
  sourceRemovalReady=cfg.source_removal===true;
  annotationReady=cfg.annotation_tracks?.includes('goal')===true;
  sourceExportReady=cfg.source_scoped_export===true;
  $('compatibilityNotice').hidden=annotationReady && sourceNotRequiredReady && sourceExportReady && sourceRemovalReady;
  if(!annotationReady) throw Error('화면과 서버 버전이 다릅니다. 진행 중인 작업이 끝나면 에디터 서버를 재시작하고 새로고침하세요. annotation은 그대로 유지됩니다.');
  $("profile").value = JSON.stringify(cfg.bag_profile, null, 2);
  for (const [stem, profile] of Object.entries(cfg.bag_profiles || {})) {
    const option = document.createElement("option");
    option.value = stem;
    option.textContent = `${stem} · ${profile.name}`;
    $("profilePick").append(option);
  }
  $("profilePick").onchange = () => {
    const stem = $("profilePick").value;
    $("profile").value = JSON.stringify(stem ? cfg.bag_profiles[stem] : cfg.bag_profile, null, 2);
  };
  let refreshState=null;
  try {
    refreshState=JSON.parse(sessionStorage.getItem('editorRefresh')||'null');
  } catch { sessionStorage.removeItem('editorRefresh'); }
  // Deep link ?project=<id>&source=<id>[&episode=<n>] (e.g. a dashboard row's "open in editor") — opens that recording.
  const deep=new URLSearchParams(location.search);
  if (deep.get('project')) {
    refreshState={projectId:deep.get('project'),sourceId:deep.get('source')||null,
                  episodeId:Number(deep.get('episode')||0),frame:0,annotationTrack:'step',annotationDrafts:{},fields:{}};
    history.replaceState(null,'',location.pathname);   // a later reload keeps the user's own place (sessionStorage), not the link's
  }
  const ps = await projectList();
  if (ps.length) {
    const pid = refreshState?.projectId || localStorage.getItem("editorProject");
    await loadProject(
      ps.some((p) => p.project_id === pid) ? pid : ps[0].project_id,
    );
    restoreRefreshState(refreshState);
    if (refreshState) await fetchFrame();
    sessionStorage.removeItem('editorRefresh');
  } else {
    const p = await api("/api/projects", { name: "첫 번째 편집 프로젝트" });
    await loadProject(p.project_id);
  }
  await restoreHubTarget(cfg.workspace,{
    repo:$('hubRepo'),path:$('hubPath'),private:$('hubPrivate'),
  });
  await pollJobs();
  jobTimer = setInterval(()=>{pollJobs();pollLinkedProject();}, 2000);
})().finally(()=>{initializing=false;$("refresh").disabled=false;});
