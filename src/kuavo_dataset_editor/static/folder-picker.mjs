/* Directory-only SERVER picker. Never grants new roots or opens client files. */
export function setupFolderPicker({ api }) {
  const $ = id => document.getElementById(id);
  const dialog = $('downloadFolderPicker');
  let generation = 0, session = 0, listing = null, rows = [], folder = null, resolveChoice = null, choice = null;
  function render() {
    $('downloadFolderUse').disabled = !folder;
    $('downloadFolderUp').disabled = !listing?.parent;
    $('downloadFolderMore').hidden = listing?.next_offset == null;
    $('downloadFolderEntries').replaceChildren(...rows.map(entry => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = '📁 ' + entry.name;
      button.title = entry.path;
      button.onclick = () => load(entry.path, { resetSearch:true });
      return button;
    }));
  }
  async function load(path, {more=false,resetSearch=false}={}) {
    const token = ++generation;
    const offset = more ? listing?.next_offset : 0;
    if (resetSearch) $('downloadFolderSearch').value = '';
    folder = null;
    $('downloadFolderUse').disabled = true;
    $('downloadFolderStatus').textContent = '에디터 실행 PC의 폴더 조회 중…';
    try {
      const result = await api(`/api/folders?${new URLSearchParams({path,query:$('downloadFolderSearch').value.trim(),offset})}`);
      if (token !== generation || !dialog.open) return false;
      listing = result; folder = result.path;
      rows = more ? [...rows,...result.entries] : result.entries;
      $('downloadFolderPath').value = folder;
      $('downloadFolderStatus').textContent = `${result.total}개 하위 폴더 · 현재 폴더를 저장 위치로 선택할 수 있습니다.`;
      render();
      return true;
    } catch (error) {
      if (token === generation && dialog.open) {
        $('downloadFolderStatus').textContent = error.message;
        // Do not allow selecting a stale folder after a failed navigation.
        $('downloadFolderUse').disabled = true;
      }
      return false;
    }
  }
  dialog.addEventListener('close', () => {
    generation++;
    session++;
    const finish = resolveChoice;
    resolveChoice = null;
    finish?.(choice);
  });
  $('downloadFolderCancel').onclick = () => { choice=null; dialog.close(); };
  $('downloadFolderUse').onclick = () => { if (folder) { choice=folder; dialog.close(); } };
  $('downloadFolderRoot').onchange = () => load($('downloadFolderRoot').value,{resetSearch:true});
  $('downloadFolderUp').onclick = () => listing?.parent && load(listing.parent,{resetSearch:true});
  $('downloadFolderGo').onclick = () => load($('downloadFolderPath').value);
  $('downloadFolderFind').onclick = () => load(listing?.path || $('downloadFolderPath').value);
  $('downloadFolderMore').onclick = () => load(listing.path,{more:true});
  for (const id of ['downloadFolderPath','downloadFolderSearch']) {
    $(id).onkeydown = event => {
      if (event.key === 'Enter') {
        event.preventDefault();
        load(id === 'downloadFolderPath' ? $(id).value : listing?.path || $('downloadFolderPath').value);
      }
    };
  }
  return {
    async open(currentPath='') {
      if (dialog.open) dialog.close();
      generation++;
      const opened=++session;
      choice=null; folder=null; listing=null; rows=[];
      $('downloadFolderSearch').value='';
      $('downloadFolderPath').value=currentPath;
      $('downloadFolderRoot').replaceChildren();
      const outcome = new Promise(resolve => { resolveChoice=resolve; });
      render(); dialog.showModal();
      try {
        const response=await api('/api/folders');
        if (opened !== session || !dialog.open) return outcome;
        $('downloadFolderRoot').replaceChildren(...response.roots.map(path => new Option(path,path)));
        const root=[...response.roots].sort((a,b)=>b.length-a.length)
          .find(path => currentPath===path || currentPath.startsWith(path.replace(/\/$/,'')+'/')) || response.roots[0];
        if (!root) throw Error('허용된 폴더가 없습니다. 서버의 --allow-root 설정을 확인하세요.');
        $('downloadFolderRoot').value=root;
        const initial=currentPath===root || currentPath.startsWith(root.replace(/\/$/,'')+'/') ? currentPath : root;
        if (!(await load(initial,{resetSearch:true})) && opened===session && dialog.open && initial!==root) {
          await load(root,{resetSearch:true});
        }
      } catch(error) {
        if (opened===session && dialog.open) $('downloadFolderStatus').textContent=error.message;
      }
      return outcome;
    },
  };
}
