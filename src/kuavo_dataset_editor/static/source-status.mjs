// Completion is a human review decision, not a percentage of labeled frames.
export function sourceLabelingStatus(project, sourceId) {
  if (project?.source_not_required?.[sourceId] === true)
    return { key:'not_required', text:'라벨링 불필요' };
  if (project?.source_completed?.[sourceId] === true)
    return { key:'completed', text:'라벨링 완료' };
  const started=['clips','goals','exclusions'].some(key=>
    (project?.[key]||[]).some(item=>item.source_id===sourceId));
  return started ? { key:'in_progress', text:'라벨링 중' }
    : { key:'not_started', text:'미라벨' };
}

export function matchesSourceFilter(project, sourceId, filter) {
  const {key}=sourceLabelingStatus(project,sourceId);
  return filter==='all' || filter===key || (filter==='incomplete' && ['in_progress','not_started'].includes(key));
}
