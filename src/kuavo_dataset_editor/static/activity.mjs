/* Numeric cues are indexed by the displayed video frame, never by wall time. */
export const DEFAULT_THRESHOLDS={arm:.5,gripper:.02,linear:.02,angular:2};
const unknown={text:'미기록',kind:'unknown'};
const finite=value=>Number.isFinite(value);
export function gripperConvention(source) {
  const signals=source?.profile?.signals||[];
  const closing=(key,topic)=>signals.some(s=>s.key===key && s.topic===topic
    && s.field==='data.position' && s.scale===.01);
  return {state:closing('observation.gripper','/leju_claw_state')?'closed_fraction':'open_fraction',
    command:closing('action.gripper','/leju_claw_command')?'closed_fraction':'open_fraction'};
}
function moving(linear,angular,t) {
  if ((finite(linear)&&linear>t.linear)||(finite(angular)&&angular>t.angular)) return true;
  return finite(linear)&&finite(angular)?false:null;
}
export function activityAt(data,frame,t=DEFAULT_THRESHOLDS) {
  const at=key=>data?.series[key]?.[frame]??null;
  const noWindow=frame<data?.window_frames?{text:'판정 전',kind:'unknown'}:unknown;
  const result={};
  for (const side of ['left','right']) {
    const raw=at(side+'_gripper'),rawChange=at(side+'_gripper_change'),rawTarget=at(side+'_gripper_command');
    const closure=data?.gripper_convention?.state==='closed_fraction';
    const value=finite(raw)&&closure?1-raw:raw;
    const change=finite(rawChange)&&closure?-rawChange:rawChange;
    const target=finite(rawTarget)&&data?.gripper_convention?.command==='closed_fraction'?1-rawTarget:rawTarget;
    result[side+'_gripper']=!finite(value)?{...unknown,target}: {
      text:value<=.1?'닫힘':value>=.9?'열림':'중간',kind:value<=.1?'closed':value>=.9?'active':'partial',
      percent:Math.round(Math.min(1,Math.max(0,value))*100),target,rawPercent:raw*100,
      change:!finite(change)?'변화 미확인':change>t.gripper?'열리는 중':change<-t.gripper?'닫히는 중':'유지',
    };
    const cmd=at(side+'_arm_command'),motion=at(side+'_arm_motion');
    result[side+'_arm_command']=at(side+'_arm_recorded')!==1?unknown:
      !finite(cmd)?{text:'명령 있음 · 변화 미확인',kind:'unknown'}:
      {text:cmd>t.arm?'목표 변경':'목표 유지',kind:cmd>t.arm?'command':'idle'};
    result[side+'_arm_motion']=!finite(motion)?noWindow:
      {text:motion>t.arm?'움직임':'정지',kind:motion>t.arm?'active':'idle'};
  }
  const torso=at('torso_motion');
  result.torso_motion=!finite(torso)?noWindow:
    {text:torso>t.arm?'움직임':'정지',kind:torso>t.arm?'active':'idle'};
  for (const type of ['command','motion']) {
    const value=moving(at('base_'+type+'_speed'),at('base_'+type+'_yaw'),t);
    result['base_'+type]=value===null?(type==='motion'?noWindow:unknown):{
      text:type==='command'?(value?'이동 명령':'정지 명령'):(value?'이동·회전':'정지'),
      kind:value?(type==='command'?'command':'active'):'idle',
    };
  }
  return result;
}

export function setupActivity({api,getContext}) {
  const $=id=>document.getElementById(id);
  let key=null,data=null,displayed=null,generation=0,torsoPending=false,torsoError='';
  const torsoLoaded=new Set();
  let gripperPending=false,gripperError='';
  const gripperLoaded=new Map();
  const thresholds={...DEFAULT_THRESHOLDS};
  const contextKey=c=>c?.source?`${c.project_id}/${c.source.source_id}/${c.episode_id}`:null;
  function thresholdInputs() {
    for (const [name,id,scale] of [['arm','activityArm',1],['gripper','activityGripper',100],['linear','activityLinear',100],['angular','activityAngular',1]]) {
      $(id).value=thresholds[name]*scale;
      $(id).onchange=()=>{
        const value=Number($(id).value)/scale;
        if (!finite(value)||value<=0) {$(id).value=thresholds[name]*scale;return;}
        thresholds[name]=value;show();
      };
    }
  }
  function statusLine(target,label,status) {
    const line=document.createElement('div'),tag=document.createElement('span');
    tag.className='activity-tag '+status.kind;tag.textContent=status.text;
    line.append(document.createTextNode(label+' '),tag);target.append(line);
  }
  function show() {
    $('activityCards').replaceChildren();
    if (!data||displayed===null) return;
    $('activityPanel').dataset.frame=String(displayed);
    const status=activityAt(data,displayed,thresholds);
    $('activityClock').textContent=`표시 영상 ${(displayed/data.fps).toFixed(2)}s · frame ${displayed}`;
    $('activityNote').textContent=`가져온 신호 · 직전 ${data.window_s.toFixed(2)}초 변화 · ${data.base_source==='odom'?'base 실측은 odom 위치 변화':data.base_source==='rack'?'base는 재구성된 랙 상대 위치 변화':'base 위치 미기록'}. 판정은 라벨을 자동 변경하지 않습니다.`;
    if (data.gripper_convention?.state==='closed_fraction' || data.gripper_convention?.command==='closed_fraction')
      $('activityNote').textContent+=' Leju 원값은 0=열림·100=닫힘이며 화면은 개방도로 환산합니다.';
    if (data.torso_source==='bag') $('activityNote').textContent+=' Torso는 현재 구간의 원본 실측을 조회합니다.';
    if (torsoError) $('activityNote').textContent+=' Torso 조회: '+torsoError;
    if (data.gripper_source==='bag') {
      const separated=gripperLoaded.get(Math.floor(displayed/data.gripper_chunk_frames));
      $('activityNote').textContent+=' 그리퍼는 현재 구간의 원본 실측을 조회합니다.';
      if (separated>0) $('activityNote').textContent+=' 그리퍼의 상충하는 중복 기록을 분리했습니다.';
    }
    if (data.gripper_source==='unverified') $('activityNote').textContent+=' 그리퍼 원본 조회 기능을 적용하려면 에디터를 다시 실행하세요.';
    if (gripperError) $('activityNote').textContent+=' 그리퍼 조회: '+gripperError;
    for (const side of ['left','right']) {
      const card=document.createElement('div'),title=document.createElement('strong');
      const loading=data.gripper_source==='bag'&&!gripperLoaded.has(Math.floor(displayed/data.gripper_chunk_frames));
      const s=loading?{...unknown,text:'불러오는 중',target:status[side+'_gripper'].target}:status[side+'_gripper'];
      card.className='activity-card';title.textContent=(side==='left'?'왼':'오른')+' 그리퍼';card.append(title);
      statusLine(card,'개폐',s);
      if (s.percent!==undefined) {
        const meter=document.createElement('meter');meter.min=0;meter.max=100;meter.value=s.percent;meter.setAttribute('aria-label',title.textContent+' 개방도');card.append(meter);
        const detail=document.createElement('small');detail.textContent=`개방 ${s.percent}% · ${s.change}`+(s.rawPercent<0||s.rawPercent>100?` (센서 ${s.rawPercent.toFixed(1)}%)`:'');card.append(detail);
      }
      const command=document.createElement('small');command.textContent=finite(s.target)?`목표 개방 ${Math.round(s.target*100)}%`:'목표 미기록';card.append(command);$('activityCards').append(card);
    }
    for (const side of ['left','right']) {
      const card=document.createElement('div'),title=document.createElement('strong');card.className='activity-card';
      title.textContent=(side==='left'?'왼':'오른')+'팔';card.append(title);
      statusLine(card,'명령',status[side+'_arm_command']);statusLine(card,'실측',status[side+'_arm_motion']);$('activityCards').append(card);
    }
    const torsoCard=document.createElement('div'),torsoTitle=document.createElement('strong');
    torsoCard.className='activity-card';torsoTitle.textContent='Torso';torsoCard.append(torsoTitle);
    const loading=data.torso_source==='bag'&&!torsoLoaded.has(Math.floor(displayed/data.torso_chunk_frames));
    statusLine(torsoCard,'실측',loading?{text:'불러오는 중',kind:'unknown'}:status.torso_motion);$('activityCards').append(torsoCard);
    const card=document.createElement('div'),title=document.createElement('strong');card.className='activity-card';title.textContent='Base';card.append(title);
    statusLine(card,'명령',status.base_command);statusLine(card,'위치',status.base_motion);$('activityCards').append(card);
  }
  async function loadTorso() {
    if (!data||data.torso_source!=='bag'||displayed===null||torsoPending) return;
    const chunk=Math.floor(displayed/data.torso_chunk_frames);
    if (torsoLoaded.has(chunk)) return;
    const c=getContext(),token=generation,frame=displayed;
    torsoPending=true;
    try {
      const result=await api(`/api/projects/${c.project_id}/torso/${c.source.source_id}/${c.episode_id}/${frame}`);
      if (token!==generation) return;
      result.motion.forEach((value,i)=>{data.series.torso_motion[result.start_frame+i]=value;});
      torsoError='';
    } catch(error) {if (token===generation) torsoError=error.message;}
    finally {
      if (token===generation) {
        torsoLoaded.add(chunk);torsoPending=false;show();loadTorso();
      }
    }
  }
  async function loadGrippers() {
    if (!data||data.gripper_source!=='bag'||displayed===null||gripperPending) return;
    const chunk=Math.floor(displayed/data.gripper_chunk_frames);
    if (gripperLoaded.has(chunk)) return;
    const c=getContext(),token=generation,frame=displayed;
    gripperPending=true;
    try {
      const result=await api(`/api/projects/${c.project_id}/gripper/${c.source.source_id}/${c.episode_id}/${frame}`);
      if (token!==generation) return;
      for (const [name,values] of Object.entries(result.series))
        values.forEach((value,i)=>{data.series[name][result.start_frame+i]=value;});
      gripperLoaded.set(chunk,result.separated_messages);gripperError='';
    } catch(error) {
      if (token===generation) {gripperError=error.message;gripperLoaded.set(chunk,0);}
    } finally {
      if (token===generation) {gripperPending=false;show();loadGrippers();}
    }
  }
  async function sourceChanged() {
    const c=getContext(),next=contextKey(c);
    if (next===key) return;
    key=next;data=null;displayed=null;const token=++generation;
    torsoLoaded.clear();torsoPending=false;torsoError='';
    gripperLoaded.clear();gripperPending=false;gripperError='';
    $('activityCards').replaceChildren();$('activityPanel').hidden=!key;
    $('activityClock').textContent=key?'동작 신호 불러오는 중…':'';$('activityNote').textContent='';
    if (!key) return;
    try {
      const result=await api(`/api/projects/${c.project_id}/activity/${c.source.source_id}/${c.episode_id}`);
      if (token!==generation) return;
      data={...result,gripper_convention:gripperConvention(c.source)};
      if (data.gripper_convention.state==='closed_fraction') {
        // The old nearest-sample cache can mix angular echoes with percent feedback.
        // Do not show that cache as physical feedback while the raw query loads/fails.
        if (data.gripper_source!=='bag') data.gripper_source='unverified';
        for (const side of ['left','right']) for (const suffix of ['_gripper','_gripper_change'])
          data.series[side+suffix]=new Array(data.num_frames).fill(null);
      }
      show();loadTorso();loadGrippers();
    } catch(error) {if (token===generation) {$('activityClock').textContent='동작 요약 불러오기 실패';$('activityNote').textContent=error.message;}}
  }
  function presented(c,frame) {
    if (contextKey(c)!==key) return;
    displayed=frame;show();loadTorso();loadGrippers();
  }
  function unavailable(c) {
    if (contextKey(c)!==key) return;
    displayed=null;$('activityCards').replaceChildren();delete $('activityPanel').dataset.frame;
    $('activityClock').textContent='영상 표시 대기';$('activityNote').textContent='카메라 프레임을 표시하지 못해 동작 요약을 보류했습니다.';
  }
  thresholdInputs();
  return {sourceChanged,presented,unavailable};
}
