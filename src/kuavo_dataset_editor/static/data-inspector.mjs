/* Separate original messages from export values; text-only rendering of bag content. */
export function setupDataInspector({api,getContext,getProject,getSchema,getAnnotationTrack=()=> 'step',getExportSourceId=()=>null,preparePreview,notify}) {
  const $=id=>document.getElementById(id);
  let meta=null,report=null,previewId=null,pendingId=null,mode='raw',lastSource=null,generation=0,timer=null,calculating=false;
  const status=text=>{$('inspectorStatus').textContent=text;};
  const number=value=>value==null?'미기록':Number(value).toLocaleString(undefined,{maximumFractionDigits:6});
  const current=()=>getContext();
  function tab(next) {
    mode=next;
    $('rawInspector').hidden=mode!=='raw'; $('exportInspector').hidden=mode!=='export';
    $('inspectorRawTab').setAttribute('aria-selected',String(mode==='raw'));
    $('inspectorExportTab').setAttribute('aria-selected',String(mode==='export'));
  }
  function table(target,headers,rows) {
    const head=document.createElement('tr');
    head.append(...headers.map(value=>{const cell=document.createElement('th');cell.textContent=value;return cell;}));
    const thead=document.createElement('thead');thead.append(head);
    const body=document.createElement('tbody');
    for (const row of rows) {
      const tr=document.createElement('tr');
      tr.append(...row.map(value=>{const td=document.createElement('td');td.textContent=String(value??'');return td;}));
      body.append(tr);
    }
    target.replaceChildren(thead,body);
  }
  function graph(canvas,points,label) {
    const ctx=canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);
    const finite=points.filter(p=>Number.isFinite(p.value));
    ctx.fillStyle='#9aa9bc';ctx.font='12px sans-serif';
    if (!finite.length) {ctx.fillText('표시할 유효한 숫자 값이 없습니다.',12,24);return;}
    const lo=Math.min(...finite.map(p=>p.value)),hi=Math.max(...finite.map(p=>p.value));
    const start=points[0].time_s,end=points.at(-1).time_s;
    ctx.strokeStyle='#61d6b4';ctx.beginPath();let active=false;
    for (const p of points) {
      if (!Number.isFinite(p.value)) {active=false;continue;}
      const x=12+(p.time_s-start)/(end-start||1)*(canvas.width-24),y=canvas.height-22-(p.value-lo)/(hi-lo||1)*(canvas.height-52);
      if (active) ctx.lineTo(x,y);else ctx.moveTo(x,y);active=true;
    }
    ctx.stroke();
    if (finite.length===1) {ctx.fillStyle='#61d6b4';ctx.beginPath();ctx.arc(12+(finite[0].time_s-start)/(end-start||1)*(canvas.width-24),canvas.height-22,3,0,2*Math.PI);ctx.fill();}
    ctx.fillText(`${label.slice(0,80)} | ${number(lo)} .. ${number(hi)} | ${start.toFixed(3)} .. ${end.toFixed(3)} s`,12,16);
  }
  function tree(value,label='',path='',depth=0) {
    if (value!==null && typeof value==='object') {
      const node=document.createElement('details');node.open=depth<2;
      const title=document.createElement('summary');title.textContent=`${label || 'message'} ${Array.isArray(value)?`[${value.length}]`:''}`;node.append(title);
      for (const [key,item] of Object.entries(value)) {
        const next=Array.isArray(value)?`${path}[${key}]`:(path?`${path}.${key}`:key);
        node.append(tree(item,key,next,depth+1));
      }
      return node;
    }
    const numeric=typeof value==='number' && !path.includes('preview') && !path.split('.').some(key=>key.startsWith('_'));
    const node=document.createElement(numeric?'button':'div');node.className='raw-leaf';
    node.textContent=`${label}: ${String(value)}`;
    if (numeric) {node.type='button';node.title='이 원본 숫자 필드를 그래프로 선택';node.onclick=()=>{$('rawField').value=path;};}
    return node;
  }
  function topics() {
    const old=$('rawTopic').value,q=$('rawTopicFilter').value.toLowerCase();
    const items=(meta?.topics||[]).filter(item=>item.topic.toLowerCase().includes(q));
    $('rawTopic').replaceChildren(...items.map(item=>new Option(`${item.topic} · ${item.messages}개 · ${item.types.join(', ')}`,item.topic)));
    if (items.some(item=>item.topic===old)) $('rawTopic').value=old;
    else if (items.some(item=>item.topic==='/sensors_data_raw')) $('rawTopic').value='/sensors_data_raw';
  }
  async function rawTopics() {
    const c=current();if (!c?.source) throw Error('bag 원본을 먼저 선택하세요.');
    const token=++generation;
    status('원본 bag 토픽 목록 읽기 중 (읽기 전용)…');
    const result=await api(`/api/projects/${c.project_id}/raw/${c.source.source_id}`);
    if (token!==generation) return;
    meta=result;topics();
    $('rawMeta').textContent=`${result.path} | ${result.duration_s.toFixed(3)}초 | ${result.topics.length}개 토픽. 빈도는 전체 녹화 기준 평균입니다.`;
    if ($('rawTopic').value==='/sensors_data_raw' && !$('rawField').value) $('rawField').value='joint_data.joint_q[0]';
    status('원본 토픽을 선택하고 메시지 조회를 누르세요.');
  }
  async function rawRow() {
    const c=current();if (!c?.source) return;
    if (!meta) await rawTopics();
    if (current()?.source?.source_id!==c.source.source_id || current()?.project_id!==c.project_id) return;
    const topic=$('rawTopic').value;if (!topic) throw Error('토픽을 고르세요.');
    const token=++generation;
    status('원본 메시지 조회 중…');
    const query=new URLSearchParams({topic,time_s:$('rawTime').value,field:$('rawField').value.trim()});
    const data=await api(`/api/projects/${c.project_id}/raw/${c.source.source_id}/messages?${query}`);
    if (token!==generation) return;
    const entries=data.messages.map(msg=>{
      const details=document.createElement('details');details.open=true;
      const title=document.createElement('summary');title.textContent=`bag ${msg.source_time_s.toFixed(6)} s | 수신 ns ${msg.bag_time_ns} | header ns ${msg.header_time_ns??'없음'}`;
      details.append(title,tree(msg.message));return details;
    });
    $('rawMessages').replaceChildren(...entries);
    graph($('rawGraph'),data.points,data.field || '숫자 필드 선택 전');
    $('rawQueryNote').textContent=`${data.selection}. ${data.payload_note} ${data.graph_note} ${data.scan_truncated?'조회 상한에 도달해 일부 메시지만 검사했습니다.':''}`;
    status(entries.length?'원본 메시지 조회 완료 (숫자 필드를 클릭하면 그래프 입력에 선택됩니다).':'해당 시각 이후 메시지가 없습니다. 시각·토픽을 확인하세요.');
  }
  function summary() {
    const rows=[];
    for (const source of report.sources) for (const ft of source.features) {
      if (ft.camera) rows.push([source.name,ft.feature,ft.shape.join(' × '),ft.valid_frames==null?'metadata만 확인':`${ft.valid_frames}/${ft.frames}`,'',ft.note]);
      else if (ft.text) rows.push([source.name,ft.feature,'string',ft.frames,'',ft.note]);
      else for (const dim of ft.dimensions) rows.push([source.name,`${ft.feature}[${dim.index}] ${dim.name}`,dim.unit,
        `${dim.valid_count}/${ft.frames} (${dim.valid_percent.toFixed(1)}%)`,`${number(dim.minimum)} .. ${number(dim.maximum)}`,
        `${dim.source} | ${Object.entries(dim.reasons).filter(([code])=>code!=='0').map(([code,count])=>`${report.reason_codes[code]||code}: ${count}`).join(', ') || '누락 없음'}`]);
    }
    table($('previewSummary'),['원본','항목·이름','단위/shape','유효','최소 .. 최대','원본·누락 사유'],rows);
    const label=`${report.annotation_track==='goal'?'Goal':'Step'} instruction · ${report.schema} · ${report.frames} frames / ${report.episodes} episodes · 저장본 r${report.project_revision}`;
    const corrected=report.approved_corrections||[];
    $('previewMeta').textContent=label+(report.stale_revision?' · 편집 후 이전 미리보기입니다. 다시 계산하세요.':' · 영상 인코딩·dataset 저장 전')+` · 승인한 보정 ${corrected.length}구간`;
    const notes=report.sources.map(s=>{const r=s.reconstruction;return r?`${s.name}: anchor ${r.anchor_knots?.length||0}개, torso 목표 ${r.torso_action_source||'미기록'}, rack 사유 ${r.rack_unavailable_reason?report.reason_codes[r.rack_unavailable_reason]:'정상 계산(연속 odom·고정 rack 전제)'}`:'';}).filter(Boolean);
    $('previewReconstruction').textContent=notes.join(' / ');
  }
  async function loadPreview(id) {
    const p=getProject();if (!p) return;
    const data=await api(`/api/projects/${p.project_id}/previews/${id}`);
    if (getProject()?.project_id!==p.project_id) return;
    report=data;previewId=id;pendingId=null;tab('export');summary();
    $('dataInspector').open=true;status('Export 미리보기 준비됨. 항목별 누락과 현재 프레임의 값을 확인하세요.');
    await exportRow();
  }
  async function exportRow() {
    const c=current();if (!report || !c?.source || report.project_id!==c.project_id) return;
    const token=++generation;
    const query=new URLSearchParams({sid:c.source.source_id,episode:c.episode,frame:c.frame});
    const data=await api(`/api/projects/${c.project_id}/previews/${previewId}/row?${query}`);
    if (token!==generation) return;
    const corrected=(report.approved_corrections||[]).filter(i=>i.source_id===c.source.source_id && i.episode_id===c.episode && i.start_frame<=c.frame && c.frame<i.end_frame);
    $('previewRowMeta').textContent=`영상 ${data.time_s.toFixed(3)} s / frame ${data.frame} · ${data.kept?'Export에 포함':'현재 선택에서는 Export 제외'} · ${data.task||'선택된 instruction 없음'} · 미기록 0은 실제 명령이 아닙니다.`+(corrected.length?` 승인한 보정: ${corrected.map(i=>`${i.feature} (${i.decision})`).join(', ')}`:'');
    table($('previewValues'),['항목','idx','이름','값','단위','validity','사유'],data.fields.map(dim=>[
      dim.feature,dim.index,dim.name,dim.valid?number(dim.value):`${number(dim.value)} (placeholder)`,dim.unit,dim.valid?1:0,dim.reason_name]).concat(Object.entries(data.text_fields||{}).map(([key,value])=>[key,'','',value,'string','',''])));
    const old=$('previewDimension').value;
    $('previewDimension').replaceChildren(...data.fields.map(dim=>new Option(`${dim.feature}[${dim.index}] ${dim.name}`,JSON.stringify([dim.feature,dim.index]))));
    if (data.fields.some(dim=>JSON.stringify([dim.feature,dim.index])===old)) $('previewDimension').value=old;
    await exportGraph();
  }
  async function exportGraph() {
    const c=current();if (!previewId || !$('previewDimension').value || !c?.source) return;
    const [feature,index]=JSON.parse($('previewDimension').value),token=++generation;
    const query=new URLSearchParams({sid:c.source.source_id,episode:c.episode,feature,index});
    const data=await api(`/api/projects/${c.project_id}/previews/${previewId}/series?${query}`);
    if (token!==generation) return;
    graph($('previewGraph'),data.points,`${feature}[${index}]`);
  }
  const safe=fn=>async()=>{try {await fn();} catch(error) {status(error.message);notify(error.message);} };
  $('inspectorRawTab').onclick=()=>tab('raw');$('inspectorExportTab').onclick=()=>tab('export');
  $('rawLoadTopics').onclick=safe(rawTopics);$('rawQuery').onclick=safe(rawRow);
  $('rawTopicFilter').oninput=topics;$('rawTopic').onchange=()=>{ $('rawField').value=$('rawTopic').value==='/sensors_data_raw'?'joint_data.joint_q[0]':''; };
  $('inspectorCurrent').onclick=safe(async()=>{
    const c=current();if (!c?.source) return;
    $('rawTime').value=(c.frame/c.source.fps).toFixed(6);
    if (mode==='raw') await rawRow();else await exportRow();
  });
  $('previewCalculate').onclick=safe(async()=>{
    if (calculating) return;
    calculating=true;$('previewCalculate').disabled=true;$('previewExport').disabled=true;
    try {
      const id=await preparePreview();if (!id) return;
      pendingId=id;report=null;previewId=null;tab('export');$('dataInspector').open=true;
      status('실제 Export와 같은 계산을 준비 중입니다. 작업 목록에서 진행·취소를 확인하세요.');
    } finally {calculating=false;$('previewCalculate').disabled=false;$('previewExport').disabled=false;}
  });
  $('previewDisconnect').onclick=()=>{generation++;report=null;previewId=null;pendingId=null;$('previewSummary').replaceChildren();$('previewValues').replaceChildren();$('previewDimension').replaceChildren();$('previewRowMeta').textContent='';$('previewReconstruction').textContent='';graph($('previewGraph'),[],'');$('previewMeta').textContent='미리보기 연결을 해제했습니다. 기존 Export는 그대로 사용할 수 있습니다.';};
  $('previewDimension').onchange=safe(exportGraph);
  return {
    loadPreview,
    observeJobs(jobs) {
      const job=jobs.find(j=>j.id===pendingId);
      if (job?.status==='completed') {pendingId=null;safe(()=>loadPreview(job.id))();}
      if (job && ['failed','cancelled'].includes(job.status)) {pendingId=null;status(job.error || '미리보기 계산 취소됨');}
    },
    cursorChanged() {
      const c=current();if (!c?.source) return;
      $('inspectorCursor').textContent=`선택 원본: ${c.source.original_path?.split('/').pop() || c.source.source_id} · 영상 ${(c.frame/c.source.fps).toFixed(3)} s / frame ${c.frame}`;
      const identity=`${c.project_id}:${c.source.source_id}`;
      if (lastSource!==identity) {generation++;meta=null;lastSource=identity;$('rawTopic').replaceChildren();$('rawMessages').replaceChildren();$('rawMeta').textContent='';}
      clearTimeout(timer);
      if ($('dataInspector').open && $('inspectorFollow').checked && !c.playing && mode==='export' && report) timer=setTimeout(safe(exportRow),250);
    },
    previewOptions() {
      const p=getProject();
      return report && report.project_id===p?.project_id && report.project_revision===p.revision && report.schema===getSchema() && (report.annotation_track||'step')===getAnnotationTrack()
        && (report.source_id||null)===getExportSourceId()
        ? {preview_id:previewId,quality:report.quality} : {};
    },
    calculate:()=> $('previewCalculate').onclick(),
  };
}
