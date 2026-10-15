// Half-open intervals: the next clip starts exactly at the preceding end.
export function nextTaskRange(lastEnd, currentFrame, totalFrames) {
  const start = Math.max(0, Math.min(totalFrames, lastEnd));
  return {
    start,
    end: Math.min(totalFrames, Math.max(start + 1, currentFrame + 1)),
  };
}

export function defaultTaskRange(clips, currentFrame, totalFrames, fps) {
  if (clips.length) {
    return nextTaskRange(
      Math.max(...clips.map((clip) => clip.end_frame)),
      currentFrame,
      totalFrames,
    );
  }
  return {
    start: Math.min(currentFrame, totalFrames - 1),
    end: Math.min(totalFrames, Math.max(currentFrame + 1, Math.round(fps))),
  };
}

export function snapBoundary(
  rawFrame,
  boundaries,
  pixelsPerFrame,
  disabled = false,
  thresholdPx = 8,
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
