"""Independent Goal/Step intervals projected to the existing official writer path."""
from copy import deepcopy
from bisect import bisect_right

from .core import EditError,source_key,subtract


def track_name(value='step'):
    if value not in ('step','goal'):
        raise EditError('annotation 트랙은 step 또는 goal이어야 합니다.')
    return value


def track_key(track):
    return 'goals' if track_name(track)=='goal' else 'clips'


def validate_extra_tracks(project):
    from .core import compile_selection
    if project.get('goals'):
        compile_selection({**project,'clips':project['goals'],'goals':[],'exclusions':[]})
        if any(type(g.get('include_unlabeled',False)) is not bool for g in project['goals']):
            raise EditError('Goal의 미지정 Step 포함 여부는 true/false로 지정하세요.')
    for item in project.get('exclusions',[]):
        source=next((s for s in project['sources'] if s['source_id']==item['source_id']),None)
        ep=next((e for e in source['episodes'] if e['episode_id']==item.get('episode_id',0)),None) if source else None
        a,b=item['start_frame'],item['end_frame']
        if ep is None or type(a) is not int or type(b) is not int or not 0<=a<b<=ep['num_frames']:
            raise EditError('데이터 제외 구간이 원본 범위를 벗어났습니다.')


def project_annotations(project,track='step'):
    """Pure, idempotent selection. Never modify stored labels or revive exclusions."""
    track_name(track)
    if project.get('_annotation_track')==track:
        return deepcopy(project)
    if project.get('_annotation_track'):
        raise EditError('이미 투영된 annotation의 트랙을 바꿀 수 없습니다. 원본 프로젝트를 사용하세요.')
    result=deepcopy(project)
    original={key:deepcopy(project.get(key,{} if key=='tasks' else []))
              for key in ('tasks','clips','goals','exclusions')}
    if track=='goal':
        clips=[]
        for goal in project.get('goals',[]):
            if goal.get('include_unlabeled',False):
                portions=[(goal['start_frame'],goal['end_frame'])]
            else:
                portions=[]
                for step in sorted(project['clips'],key=lambda c:c['start_frame']):
                    if source_key(step)!=source_key(goal):continue
                    a=max(step['start_frame'],goal['start_frame']);b=min(step['end_frame'],goal['end_frame'])
                    if a>=b:continue
                    if portions and portions[-1][1]==a:portions[-1]=(portions[-1][0],b)
                    else:portions.append((a,b))
            cuts=sorted({s['start_frame'] for s in project['clips'] if source_key(s)==source_key(goal) and s.get('new_episode')})
            pieces=[]
            for a,b in portions:
                boundaries=[a]+[cut for cut in cuts if a<cut<b]+[b]
                pieces.extend(zip(boundaries,boundaries[1:]))
            for a,b in pieces:
                clips.append({**deepcopy(goal),'id':f"{goal['id']}:{a}:{b}",'goal_id':goal['id'],
                              'start_frame':a,'end_frame':b,'new_episode':True})
        result['clips']=clips
    for exclusion in project.get('exclusions',[]):
        result['clips']=subtract(result['clips'],exclusion['source_id'],exclusion.get('episode_id',0),
                                 exclusion['start_frame'],exclusion['end_frame'])
    result['_annotation_track']=track
    result['_annotation_original']=original
    return result


class AnnotationLookup:
    """Build once; O(log N) frame provenance, not a full annotation scan per frame."""
    def __init__(self,project):
        original=project.get('_annotation_original',project)
        self.tasks=original['tasks'];self.track=project.get('_annotation_track','step');self.groups={}
        for name,key in (('step','clips'),('goal','goals')):
            for item in original.get(key,[]):self.groups.setdefault((name,*source_key(item)),[]).append(item)
        self.groups={key:(sorted(items,key=lambda c:c['start_frame'])) for key,items in self.groups.items()}
        self.starts={key:[c['start_frame'] for c in items] for key,items in self.groups.items()}

    def at(self,sid,episode,frame):
        result={'annotation_track':self.track}
        for name in ('step','goal'):
            key=(name,sid,episode);items=self.groups.get(key,[])
            i=bisect_right(self.starts.get(key,[]),frame)-1
            item=items[i] if i>=0 and frame<items[i]['end_frame'] else None
            result[name+'_id']=item['id'] if item else None
            result[name+'_task']=self.tasks.get(item['task_id']) if item else None
        return result


def edit_annotation(project,body):
    from .core import label,split_clip
    p=deepcopy(project);track=track_name(body.get('track','step'));key=track_key(track)
    view={**deepcopy(p),'clips':deepcopy(p.get(key,[])),'goals':[],'exclusions':[]}
    op=body['operation']
    if op=='label':
        view=label(view,body['source_id'],int(body['episode_id']),body['start_frame'],body['end_frame'],
                   body['task'],body.get('replace',False),body.get('new_episode',False))
        if track=='goal':view['clips'][-1]['include_unlabeled']=body.get('include_unlabeled',False)
    elif op=='split':view=split_clip(view,body['clip_id'],body['frame'])
    elif op=='remove_annotation':
        view['clips']=subtract(view['clips'],body['source_id'],int(body['episode_id']),body['start_frame'],body['end_frame'])
    elif op in ('update_clip','remove_clip'):
        item=next((c for c in view['clips'] if c['id']==body['clip_id']),None)
        if item is None:raise EditError('선택한 트랙에서 annotation을 찾을 수 없습니다.')
        if op=='remove_clip':view['clips'].remove(item)
        else:
            text=body.get('task','').strip()
            if not text:raise EditError('annotation 문장을 입력하세요.')
            from uuid import uuid4
            tid=next((k for k,v in view['tasks'].items() if v==text),uuid4().hex)
            view['tasks'][tid]=text
            item.update(start_frame=body['start_frame'],end_frame=body['end_frame'],task_id=tid,
                        new_episode=bool(body.get('new_episode')))
            if track=='goal':item['include_unlabeled']=body.get('include_unlabeled',item.get('include_unlabeled',False))
    else:raise EditError('지원하지 않는 annotation 편집입니다.')
    p[key]=view['clips'];p['tasks']=view['tasks']
    return p


def restore_exported_annotations(project,descriptor):
    """Restore both levels when importing our own export; external datasets use task_index."""
    from pathlib import Path
    from uuid import uuid4
    import pyarrow.parquet as pq
    path=Path(descriptor['path'])/'editor/frame_mapping.parquet'
    required=['episode_index','frame_index','task','step_id','step_task','goal_id','goal_task']
    if not path.is_file() or not set(required)<=set(pq.read_schema(path).names):return None
    rows=pq.read_table(path,columns=required).to_pylist()
    for row in rows:
        if not row['step_task'] and not row['goal_task']:
            row['step_task']=row['task'];row['step_id']='legacy:'+str(row['task'])
    if len(rows)!=sum(e['num_frames'] for e in descriptor['episodes']):return None
    rows.sort(key=lambda r:(r['episode_index'],r['frame_index']))
    lengths={e['episode_id']:e['num_frames'] for e in descriptor['episodes']};counts={}
    for row in rows:
        ep=row['episode_index'];frame=row['frame_index']
        if ep not in lengths or frame!=counts.get(ep,0) or frame>=lengths[ep]:return None
        counts[ep]=frame+1
    if counts!=lengths:return None
    result=deepcopy(project);by_text={v:k for k,v in result['tasks'].items()}
    for name,key in (('step','clips'),('goal','goals')):
        previous=None;identity=None
        for row in rows:
            text=row[name+'_task']
            if not text:previous=None;continue
            tid=by_text.setdefault(text,uuid4().hex);result['tasks'][tid]=text
            current=(row['episode_index'],row[name+'_id'],text)
            if previous and current==identity and previous['end_frame']==row['frame_index']:
                previous['end_frame']+=1
            else:
                previous={'id':uuid4().hex,'source_id':descriptor['source_id'],'episode_id':row['episode_index'],
                          'start_frame':row['frame_index'],'end_frame':row['frame_index']+1,'task_id':tid,'new_episode':False}
                if name=='goal':previous['include_unlabeled']=True
                result.setdefault(key,[]).append(previous)
            identity=current
    return result
