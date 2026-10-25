// Half-open intervals: the next clip starts exactly at the preceding end.
export function nextTaskRange(lastEnd, currentFrame, totalFrames, boundary = null) {
  const start = Math.max(0, Math.min(totalFrames, lastEnd));
  return {
    start,
    // A snapped boundary is already exclusive; do not include one extra frame.
    // A cursor before the next start is a pending range, not a fabricated frame.
    end: Math.min(totalFrames, Math.max(start, boundary ?? currentFrame + 1)),
  };
}

export function defaultTaskRange(clips, currentFrame, totalFrames, fps, boundary = null) {
  if (clips.length) {
    return nextTaskRange(
      Math.max(...clips.map((clip) => clip.end_frame)),
      currentFrame,
      totalFrames,
      boundary,
    );
  }
  const start = Math.min(currentFrame, totalFrames - 1);
  return {start, end: Math.min(totalFrames, Math.max(start, boundary ?? currentFrame + 1))};
}

export function snapBoundary(
  rawFrame,
  boundaries,
  pixelsPerFrame,
  disabled = false,
  thresholdPx = 12,
) {
  if (disabled || pixelsPerFrame <= 0)
    return { frame: rawFrame, snapped: false };
  let best = rawFrame,
    distance = Infinity;
  for (const boundary of boundaries) {
    const delta = Math.abs(boundary - rawFrame) * pixelsPerFrame;
    if (
      delta <= thresholdPx &&
      (delta < distance || (delta === distance && boundary < best))
    ) {
      best = boundary;
      distance = delta;
    }
  }
  return { frame: best, snapped: distance !== Infinity };
}

export function dragSelectionRange(anchor, moving, totalFrames) {
  if (moving > anchor)
    return { start: anchor, end: moving, preview: moving - 1 };
  if (moving < anchor) return { start: moving, end: anchor, preview: moving };
  const start = Math.min(anchor, totalFrames - 1);
  return { start, end: start + 1, preview: start };
}

// The unchanged edge is an invalid snap target; keep the original moving edge
// as a candidate so zoomed-out short clips do not jump when a handle is grabbed.
export function resizeClipRange(start, end, edge, rawFrame, boundaries,
  pixelsPerFrame, totalFrames, disabled = false) {
  const original = edge === "end" ? end : start;
  const candidates = [...boundaries, original].filter((f) =>
    edge === "end" ? f > start && f <= totalFrames : f >= 0 && f < end);
  const point = snapBoundary(rawFrame, candidates, pixelsPerFrame, disabled);
  const actual = edge === "end"
    ? Math.max(start + 1, Math.min(totalFrames, point.frame))
    : Math.max(0, Math.min(end - 1, point.frame));
  return {start: edge === "start" ? actual : start,
    end: edge === "end" ? actual : end, frame: actual,
    snapped: point.snapped && point.frame === actual};
}

export function shortRangeWarning(start, end, fps) {
  const frames = end - start;
  if (!Number.isInteger(frames) || frames <= 0 || fps <= 0 || frames / fps >= 0.5)
    return "";
  return `짧은 구간: ${frames}프레임 / ${(frames / fps).toFixed(3)}초 (0.5초 미만). 의도한 범위인지 확인하세요. 저장·Export는 가능합니다.`;
}
