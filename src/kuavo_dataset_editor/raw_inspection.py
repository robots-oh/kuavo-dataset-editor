"""Bounded, read-only ROS1 inspection. No ROS master, playback or publishers."""
from dataclasses import fields, is_dataclass
import math
import re

import numpy as np

from .core import EditError
from .ros1_reader import Ros1Reader


def bounded(value,depth=0):
    if depth>9: return {'_truncated':'maximum depth'}
    if isinstance(value,np.ndarray):
        array=value.reshape(-1)
        limit=32 if value.dtype==np.uint8 else 128
        preview=[bounded(v,depth+1) for v in array[:limit]]
        if array.size<=limit: return preview
        return {'_truncated':True,'dtype':str(value.dtype),'shape':list(value.shape),
                'length':int(array.size),'preview':preview}
    if is_dataclass(value):
        return {field.name:bounded(getattr(value,field.name),depth+1)
                for field in fields(value) if not field.name.startswith('__')}
    if isinstance(value,(list,tuple)):
        items=[bounded(item,depth+1) for item in value[:128]]
        return items if len(value)<=128 else {'_truncated':True,'length':len(value),'preview':items}
    if isinstance(value,np.generic): value=value.item()
    if isinstance(value,float) and not math.isfinite(value): return str(value)
    if isinstance(value,str): return value[:2048] if len(value)<=2048 else value[:2048]+' [truncated]'
    if isinstance(value,(bool,int,float)) or value is None: return value
    return str(value)[:256]


def numeric_field(message,path):
    if not isinstance(path,str) or len(path)>256 or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\[\d{1,6}\])?(?:\.[A-Za-z][A-Za-z0-9_]*(?:\[\d{1,6}\])?)*',path):
        raise EditError('숫자 필드는 joint_data.joint_q[0] 또는 position[3]처럼 입력하세요.')
    value=message
    for part in path.split('.'):
        name,index=re.fullmatch(r'([A-Za-z][A-Za-z0-9_]*)(?:\[(\d+)\])?',part).groups()
        names={field.name for field in fields(value)} if is_dataclass(value) else set()
        if name not in names: raise EditError('메시지에 없는 필드입니다: '+name)
        value=getattr(value,name)
        if index is not None:
            if not isinstance(value,(list,tuple,np.ndarray)) or int(index)>=len(value):
                raise EditError('배열 인덱스 범위 밖입니다: '+part)
            value=value[int(index)]
    if not isinstance(value,(int,float,bool,np.number)):
        raise EditError('스칼라 숫자 필드를 고르세요. 배열이면 [인덱스]를 붙이세요.')
    result=float(value)
    return result if math.isfinite(result) else None


def inventory(path):
    with Ros1Reader(path) as reader:
        duration=(reader.end_time-reader.start_time)/1e9
        topics={}
        for connection in reader.connections:
            item=topics.setdefault(connection.topic,{'topic':connection.topic,'types':[], 'messages':0})
            item['messages']+=connection.msgcount
            if connection.msgtype not in item['types']: item['types'].append(connection.msgtype)
        for item in topics.values():
            item['approx_hz']=item['messages']/duration if duration>0 else None
        return {'path':str(path),'start_ns':str(reader.start_time),'duration_s':duration,
                'topics':sorted(topics.values(),key=lambda item:item['topic']),
                'read_only':True,'frequency_note':'전체 녹화 길이로 나눈 평균이며 실제 순간 주파수가 아닙니다.'}


def samples(path,topic,time_s=0,limit=3,field='',window_s=2):
    if not math.isfinite(time_s) or time_s<0 or not 1<=limit<=5:
        raise EditError('원본 조회 시각은 0 이상, 메시지 개수는 1-5개로 지정하세요.')
    if not math.isfinite(window_s) or not .01<=window_s<=10:
        raise EditError('그래프 조회 범위는 0.01-10초로 지정하세요.')
    with Ros1Reader(path) as reader:
        connections=[c for c in reader.connections if c.topic==topic]
        if not connections: raise EditError('원본 bag에 없는 토픽입니다: '+topic)
        origin=reader.start_time
        target=origin+int(time_s*1e9)
        messages=[]
        for connection,stamp,raw in reader.messages(connections=connections,start=target):
            msg=reader.deserialize(raw,connection.msgtype)
            header=getattr(msg,'header',None)
            ros_stamp=getattr(header,'stamp',None)
            header_ns=(int(ros_stamp.sec)*10**9+int(ros_stamp.nanosec)) if ros_stamp else None
            messages.append({'bag_time_ns':str(int(stamp)),'source_time_s':(int(stamp)-origin)/1e9,
                             'header_time_ns':str(header_ns) if header_ns is not None else None,'message':bounded(msg)})
            if len(messages)>=limit: break
        points=[]; scanned=0; truncated=False
        if field:
            start=max(origin,target-int(window_s*1e9)); stop=min(reader.end_time,target+int(window_s*1e9)+1)
            if start<stop:
                for connection,stamp,raw in reader.messages(connections=connections,start=start,stop=stop):
                    if scanned>=10000:
                        truncated=True; break
                    msg=reader.deserialize(raw,connection.msgtype)
                    points.append({'time_s':(int(stamp)-origin)/1e9,'value':numeric_field(msg,field)})
                    scanned+=1
            if len(points)>400:
                indices=np.linspace(0,len(points)-1,400,dtype=int)
                points=[points[index] for index in indices]
        return {'topic':topic,'requested_time_s':time_s,'messages':messages,'points':points,
                'field':field,'scanned_messages':scanned,'scan_truncated':truncated,
                'selection':'지정 시각 이후의 첫 메시지 (bag 수신 시각 기준)',
                'graph_note':'원본 메시지 시각 그대로, 보간·단위 변환 없음. 그래프는 최대 400점 샘플.',
                'payload_note':'큰 배열/이미지 바이트는 preview로 줄여 표시하며 원본 파일은 변경하지 않습니다.'}
