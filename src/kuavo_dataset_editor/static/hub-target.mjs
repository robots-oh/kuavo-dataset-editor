/* Optional PC-local defaults; reading these never starts a Hub job. */
export async function restoreHubTarget(workspace, fields, request=fetch) {
  try {
    const response=await request(new URL('./hub-target.local.json',import.meta.url),{cache:'no-store'});
    if(!response.ok) return false;
    const settings=await response.json();
    if(settings.workspace!==workspace || !settings.repo_id || !settings.path) return false;
    const current=fields.repo.value.trim();
    const moved=settings.previous_repo_ids?.includes(current);
    // A deliberately chosen different dataset keeps its own destination.
    if(current && current!==settings.repo_id && !moved) return false;
    fields.repo.value=settings.repo_id;
    if(!current || moved || !fields.path.value.trim()) fields.path.value=settings.path;
    if(typeof settings.private==='boolean') fields.private.checked=settings.private;
    return true;
  } catch {
    // This file is optional and must not prevent offline labeling.
    return false;
  }
}
