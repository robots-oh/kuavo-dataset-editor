"""Export-equivalent numeric previews. No dataset writer, videos, or source writes."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import numpy as np

from .core import EditError,compile_selection
from .sources import CatalogSource,open_source,fingerprint,write_json
from .quality import resolve_quality,repair_index,corrected_row
from .repositories import selected_project
from .wholebody import prepare_project,schema_name,contract
from .masking import mask_mapping,REASONS


def preview_key(project,target,options):
    selection=selected_project(project,target,options.get('mode','new'),options.get('annotation_track','step'),options.get('source_ids'))
    used={clip['source_id'] for clip in selection['clips']}
    sources=[]
    for source in selection['sources']:
        if source['source_id'] not in used: continue
        root=Path(source['path']); original=Path(source.get('original_path',root))
        if not root.exists() or not original.exists(): raise EditError('미리보기 원본 파일을 찾을 수 없습니다.')
        sources.append([source['source_id'],fingerprint(root),fingerprint(original) if original!=root else None])
    code=[Path(__file__).with_name(name).read_bytes() for name in
          ('data_preview.py','annotations.py','wholebody.py','recorded_tf.py','rack_reconstruction.py','masking.py','quality.py','sources.py')]
    value={'project':selection,'sources':sources,'schema':schema_name(options),
           'mode':options.get('mode','new'),'quality':options.get('quality'),
           'rack_calibration':options.get('rack_calibration')}
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()+b''.join(code)).hexdigest()


def dimensions(adapter,source,feature):
    ft=adapter.features[feature]; size=int(np.prod(ft['shape']))
    names=ft.get('names') or [f'{feature}[{i}]' for i in range(size)]
    result=[{'index':i,'name':names[i] if i<len(names) else f'{feature}[{i}]','unit':'', 'source':''} for i in range(size)]
    if source.get('export_derivation') or (adapter.root/'meta/kuavo_schema.json').is_file():
        schema=source.get('export_derivation',{}).get('schema')
        metadata=contract(schema) if schema else json.loads((adapter.root/'meta/kuavo_schema.json').read_text())
        groups=metadata.get('groups',{}).get(feature)
        if isinstance(groups,dict):
            for group,item in groups.items():
                units=item['unit'].split(',')
                for i in range(item['start'],min(item['end'],size)):
                    unit=units[i-item['start']] if len(units)>1 else units[0]
                    if unit in ('sin_yaw','cos_yaw'): unit='dimensionless'
                    if feature=='observation.state':
                        origin='/sensors_data_raw.joint_data.joint_q' if i<18 else '/leju_claw_state' if i<20 else 'rack tags + recorded TF/odom (offline)'
                    else:
                        origin='/joint_cmd or /lb_leg_traj' if i<4 else '/kuavo_arm_traj' if i<18 else '/leju_claw_command' if i<20 else '/cmd_vel'
                    result[i].update(unit=unit,source=origin,group=group)
    else:
        specs=getattr(adapter,'manifest',{}).get('profile',{}).get('signals',[])
        item=next((item for item in specs if item['key']==feature),{})
        for dim in result: dim.update(unit=item.get('unit',''),source=item.get('topic',''))
    return result


def _times(adapter):
    path=getattr(adapter,'manifest',{}).get('time_mapping')
    if path:
        with np.load(adapter.root/path,allow_pickle=False) as mapping:
            return {'bag_time_ns':mapping['bag_time_ns']}
    return None


def _feature_values(adapter,feature,episode,start,end,repairs,source_id,times):
    if isinstance(adapter,CatalogSource):
        values=np.asarray(adapter.arrays[feature][start:end]).copy()
    else:
        rows=adapter.rows(episode)
        values=np.asarray([row[feature] for row in rows[start:end]],dtype=adapter.features[feature]['dtype'])
    for (sid,ep,frame),fixes in repairs.items():
        if sid==source_id and ep==episode and start<=frame<end and any(fix['feature']==feature for fix in fixes):
            values[frame-start]=corrected_row(adapter,episode,frame,fixes,times,cameras=False)[feature]
    return values.reshape(end-start,-1)


def build_preview(project,target,options,directory,progress):
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
    key=preview_key(project,target,options)
    initial=selected_project(project,target,options.get('mode','new'),options.get('annotation_track','step'),options.get('source_ids'))
    selection,approved=resolve_quality(initial,options.get('quality'))
    if not approved['ready']: raise EditError('누락 구간 보정/제외를 선택한 뒤 미리보기를 계산하세요.')
    schema=schema_name(options)
    if schema!='legacy':
        selection,_=prepare_project(project,directory,{**options,'_target':str(target)},progress)
        active_repairs={}
    else: active_repairs=repair_index(approved)
    plan=compile_selection(selection)
    if not plan['runs']: raise EditError('미리볼 라벨 구간이 없습니다.')
    sources={source['source_id']:source for source in selection['sources']}
    reports=[]
    for sid in sorted({run['source_id'] for run in plan['runs']}):
        source=sources[sid]; adapter=open_source(source); times=_times(adapter)
        runs=[run for run in plan['runs'] if run['source_id']==sid]
        count=sum(run['end_frame']-run['start_frame'] for run in runs)
        features=[]; mapping=mask_mapping(adapter.features,getattr(adapter,'manifest',{}).get('validity_masks'))
        for feature,ft in adapter.features.items():
            if ft['dtype'] in ('image','video'):
                known=isinstance(adapter,CatalogSource)
                from .sources import _safe_child
                paths={value for run in runs for value in adapter.manifest['images'][feature][run['start_frame']:run['end_frame']]} if known else set()
                present={value:value is not None and _safe_child(adapter.root,value).is_file() for value in paths}
                available=sum(sum(present[value] for value in adapter.manifest['images'][feature][run['start_frame']:run['end_frame']]) for run in runs) if known else None
                features.append({'feature':feature,'dtype':ft['dtype'],'shape':list(ft['shape']),
                                 'frames':count,'valid_frames':available,'camera':True,
                                 'note':'이미지 파일 존재/승인된 hold 기준; 전체 픽셀 디코딩은 생략' if known else 'LeRobot 영상 metadata 확인; 전체 영상 디코딩은 생략'})
                continue
            if feature.startswith(('validity.','missing_reason.','provenance.')): continue
            if ft['dtype']=='string':
                features.append({'feature':feature,'dtype':'string','shape':list(ft['shape']),
                                 'frames':count,'text':True,'note':'문자열 항목: 현재 프레임에서 확인'})
                continue
            values=[]; masks=[]; reasons=[]
            for run in runs:
                args=(run['episode_id'],run['start_frame'],run['end_frame'],active_repairs,sid,times)
                value=_feature_values(adapter,feature,*args)
                mask_key=mapping.get(feature)
                mask=_feature_values(adapter,mask_key,*args).astype(bool) if mask_key else np.isfinite(value)
                mask=np.broadcast_to(mask,value.shape)&np.isfinite(value)
                reason_key='missing_reason.'+feature
                reason=_feature_values(adapter,reason_key,*args).astype(np.uint8) if reason_key in adapter.features else np.where(mask,0,2).astype(np.uint8)
                values.append(value); masks.append(mask); reasons.append(reason)
            values=np.concatenate(values); masks=np.concatenate(masks); reasons=np.concatenate(reasons)
            dims=dimensions(adapter,source,feature)
            for i,dim in enumerate(dims):
                valid=values[masks[:,i],i]
                dim.update(valid_count=int(masks[:,i].sum()),masked_count=int((~masks[:,i]).sum()),
                           valid_percent=100*float(masks[:,i].mean()),
                           minimum=float(valid.min()) if len(valid) else None,maximum=float(valid.max()) if len(valid) else None,
                           reasons={str(int(code)):int((reasons[:,i]==code).sum()) for code in np.unique(reasons[:,i])})
            features.append({'feature':feature,'dtype':ft['dtype'],'shape':list(ft['shape']),
                             'frames':count,'dimensions':dims,'camera':False})
        report_path=getattr(adapter,'manifest',{}).get('wholebody_report')
        reconstruction=json.loads(Path(report_path).read_text()) if report_path else None
        reports.append({'source_id':sid,'name':Path(source.get('original_path',source['path'])).name,
                        'frames':count,'features':features,'reconstruction':reconstruction})
        progress(.85,'항목별 유효 비율·단위·원본 매핑 계산 중 (영상 인코딩 없음)')
    if preview_key(project,target,options)!=key:
        raise EditError('미리보기 도중 원본이 변경되었습니다. 다시 계산하세요.')
    public={'project_id':project['project_id'],'project_revision':project['revision'],'schema':schema,
            'frames':plan['kept_frames'],'episodes':plan['num_episodes'],'sources':reports,
            'annotation_track':options.get('annotation_track','step'),
            'reason_codes':REASONS,'read_only':True,'no_video_encoding':True,
            'approved_corrections':[issue for issue in approved['issues'] if issue.get('decision')!='exclude'],
            'note':'누락은 경고로 표시합니다. 입력 mask와 action loss mask는 학습에서 별도로 연결해야 합니다.'}
    write_json(directory/'preview.json',{'public':public,'key':key,'project':selection,
                                       'repairs':approved if schema=='legacy' else {'issues':[]},'plan':plan})
    return {'preview_id':directory.name,'schema':schema,'frames':plan['kept_frames'],
            'masked_dimensions':sum(dim['masked_count']>0 for report in reports for ft in report['features'] for dim in ft.get('dimensions',[]))}


def preview_row(saved,sid,episode,frame):
    source=next((s for s in saved['project']['sources'] if s['source_id']==sid),None)
    if source is None: raise EditError('미리보기에서 선택되지 않은 원본입니다.')
    ep=next((ep for ep in source['episodes'] if ep['episode_id']==episode),None)
    if ep is None or not 0<=frame<ep['num_frames']: raise EditError('프레임 범위 밖입니다.')
    adapter=open_source(source)
    repairs=repair_index(saved['repairs']).get((sid,episode,frame),[])
    row=corrected_row(adapter,episode,frame,repairs,_times(adapter) if repairs else None,cameras=False,validate=False)
    mapping=mask_mapping(adapter.features,getattr(adapter,'manifest',{}).get('validity_masks'))
    fields=[];text_fields={}
    for feature,ft in adapter.features.items():
        if feature not in row or feature.startswith(('validity.','missing_reason.')): continue
        if ft['dtype']=='string':
            text_fields[feature]=str(row[feature])[:2048]
            continue
        values=np.asarray(row[feature]).reshape(-1)
        mask=np.broadcast_to(np.asarray(row.get(mapping.get(feature),[True]),dtype=bool).reshape(-1),values.shape)
        reasons=np.asarray(row.get('missing_reason.'+feature,np.where(mask,0,2)),dtype=np.uint8).reshape(-1)
        for i,dim in enumerate(dimensions(adapter,source,feature)):
            valid=bool(mask[i] and np.isfinite(values[i]))
            code=int(reasons[i])
            fields.append({**dim,'feature':feature,'value':float(values[i]) if np.isfinite(values[i]) else None,
                           'valid':valid,'reason':code,'reason_name':REASONS.get(code,'unknown')})
    kept=any(run['source_id']==sid and run['episode_id']==episode and run['start_frame']<=frame<run['end_frame'] for run in saved['plan']['runs'])
    selected_clip=next((c for run in saved['plan']['runs'] if run['source_id']==sid and run['episode_id']==episode
                        for c in run['clips'] if c['start_frame']<=frame<c['end_frame']),None)
    return {'source_id':sid,'episode_id':episode,'frame':frame,'time_s':frame/source['fps'],
            'kept':kept,'fields':fields,'text_fields':text_fields,'task':selected_clip['task'] if selected_clip else None}


def preview_series(saved,sid,episode,feature,index):
    source=next((s for s in saved['project']['sources'] if s['source_id']==sid),None)
    if source is None: raise EditError('미리보기에서 선택되지 않은 원본입니다.')
    ep=next((ep for ep in source['episodes'] if ep['episode_id']==episode),None)
    adapter=open_source(source)
    ft=adapter.features.get(feature)
    if ep is None or not ft or ft['dtype'] in ('image','video','string') or not 0<=index<int(np.prod(ft['shape'])):
        raise EditError('존재하는 숫자 feature/차원을 고르세요.')
    n=ep['num_frames']; repairs=repair_index(saved['repairs'])
    args=(episode,0,n,repairs,sid,_times(adapter) if repairs else None)
    values=_feature_values(adapter,feature,*args)
    mapping=mask_mapping(adapter.features,getattr(adapter,'manifest',{}).get('validity_masks'))
    valid=_feature_values(adapter,mapping[feature],*args).astype(bool) if feature in mapping else np.isfinite(values)
    valid=np.broadcast_to(valid,values.shape)
    kept=np.zeros(n,dtype=bool)
    for run in saved['plan']['runs']:
        if run['source_id']==sid and run['episode_id']==episode: kept[run['start_frame']:run['end_frame']]=True
    indices=np.unique(np.linspace(0,n-1,min(400,n),dtype=int))
    good=valid[:,index]&kept&np.isfinite(values[:,index])
    breaks=np.cumsum(~good)
    points=[];previous=-1
    for f in indices:
        # Do not draw a line across a short gap hidden by graph downsampling.
        continuous=(breaks[f]-(breaks[previous] if previous>=0 else 0))==0
        points.append({'time_s':int(f)/source['fps'],
                       'value':float(values[f,index]) if good[f] and continuous else None,'kept':bool(kept[f])})
        previous=int(f)
    return {'feature':feature,'index':index,'points':points,
            'note':'최대 400점 샘플. 미기록·Export 제외 구간은 연결하지 않습니다.'}
