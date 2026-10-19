/* Explicit gap review; rendering uses textContent, never filenames as HTML. */
export const METHODS = {
  linear: "상태값 선형 보간",
  hold: "이전 실측·명령값 유지",
  hold_image: "이전 실제 영상 프레임 재사용",
  exclude: "이 구간을 Export에서 제외",
};

export function setupQualityReview() {
  const $ = (id) => document.getElementById(id);
  const dialog = $("qualityReview");
  let pending = null, entries = [], report = null;
  const finish = (value) => {
    const resolve = pending;
    pending = null;
    resolve?.(value);
  };
  function count() {
    const remaining = entries.filter(({ select }) => !select.value).length;
    const excluded = entries.filter(({ select }) => select.value === "exclude").length;
    $("qualityStatus").textContent = `미선택 ${remaining}개 · 제외 ${excluded}개 구간 · 보정 ${entries.length - remaining - excluded}개 구간`;
    $("qualityApply").disabled = remaining > 0;
  }
  $("qualityCancel").onclick = () => dialog.close();
  dialog.addEventListener("close", () => finish(null));
  $("qualityRepairAll").onclick = () => {
    for (const { issue, select } of entries) select.value = issue.method || "";
    count();
  };
  $("qualityExcludeAll").onclick = () => {
    for (const { select } of entries) select.value = "exclude";
    count();
  };
  $("qualityApply").onclick = () => {
    if (entries.some(({ select }) => !select.value)) return;
    const decisions = Object.fromEntries(entries.map(({ issue, select }) => [issue.id, select.value]));
    finish({ token: report.token, decisions });
    dialog.close();
  };
  return (nextReport) => {
    if (pending) throw Error("이미 누락 점검 창이 열려 있습니다.");
    report = nextReport;
    $("qualitySummary").textContent = `선택 ${report.checked_frames.toLocaleString()}프레임 중 ${report.invalid_frames.toLocaleString()}프레임에 필수 상태값 또는 카메라 누락이 있습니다. ${report.issues.length}개 센서별 구간을 확인하세요. 겹친 센서 누락은 전체 프레임 수에서 한 번만 셉니다.`;
    entries = report.issues.map((issue) => {
      const row = document.createElement("tr");
      const texts = [
        `${issue.source_name}\nEpisode ${issue.episode_id} · ${issue.feature}`,
        `${issue.missing_frames}프레임 (${issue.duration_s.toFixed(3)}초)\n[${issue.start_frame}, ${issue.end_frame}) · ${issue.start_time_s.toFixed(3)}–${issue.end_time_s.toFixed(3)}초`,
        issue.sample_gap_s == null ? "앞뒤 실측값 부족" : `앞뒤 메시지 간격 ${issue.sample_gap_s.toFixed(3)}초`,
      ];
      if (issue.max_hold_age_s != null)
        texts[2] += `\n이전 값 사용 시 최대 ${issue.max_hold_age_s.toFixed(3)}초 경과`;
      if (issue.max_endpoint_delta != null) {
        const angle = issue.unit === "rad";
        texts[2] += `\n앞뒤 최대 차이 ${(issue.max_endpoint_delta * (angle ? 180 / Math.PI : 1)).toPrecision(4)} ${angle ? "도" : issue.unit || ""}`;
      }
      for (const text of texts) {
        const cell = document.createElement("td");
        cell.textContent = text;
        row.append(cell);
      }
      const cell = document.createElement("td");
      const select = document.createElement("select");
      select.setAttribute("aria-label", `${issue.source_name} ${issue.feature} ${issue.start_frame} 처리 방법`);
      select.replaceChildren(new Option("처리 방법 선택…", ""),
        ...(issue.method ? [new Option(METHODS[issue.method], issue.method)] : []),
        new Option(METHODS.exclude, "exclude"));
      select.onchange = count;
      cell.append(select);
      row.append(cell);
      return { issue, select, row };
    });
    $("qualityRows").replaceChildren(...entries.map(({ row }) => row));
    count();
    dialog.showModal();
    return new Promise((resolve) => { pending = resolve; });
  };
}
