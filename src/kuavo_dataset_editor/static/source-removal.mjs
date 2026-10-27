// Removal is project-local, reversible membership, never filesystem deletion.
export const isSourceRemoved=(project,id)=>project?.source_removed?.[id]===true;
export const activeSources=project=>(project?.sources||[]).filter(s=>!isSourceRemoved(project,s.source_id));
export const removedSources=project=>(project?.sources||[]).filter(s=>isSourceRemoved(project,s.source_id));

export function setupSourceRemovalConfirmation(doc=document) {
  const dialog=doc.getElementById('sourceRemovalDialog');
  const cancel=doc.getElementById('sourceRemovalCancel');
  let pending=null,approved=false;
  cancel.onclick=()=>{approved=false;dialog.close();};
  doc.getElementById('sourceRemovalApply').onclick=()=>{approved=true;dialog.close();};
  dialog.addEventListener('close',()=>{
    const done=pending;pending=null;done?.(approved);
  });
  dialog.addEventListener('cancel',()=>{approved=false;});
  return name=>{
    if(pending) throw Error('이미 원본 제거 확인창이 열려 있습니다.');
    doc.getElementById('sourceRemovalName').textContent=name;
    approved=false;
    return new Promise((resolve,reject)=>{
      pending=resolve;
      try {dialog.showModal();cancel.focus();}
      catch(e) {pending=null;reject(e);}
    });
  };
}
