"""Read-only bag vs export units/masks, with a bounded 15-frame ROS-free fixture."""
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path

import numpy as np
import pytest

from kuavo_dataset_editor.core import EditError
from kuavo_dataset_editor.data_preview import build_preview,preview_row,preview_series,preview_key
from kuavo_dataset_editor.raw_inspection import inventory,samples,bounded,numeric_field
from kuavo_dataset_editor.sources import fingerprint,open_source,write_json
from kuavo_dataset_editor.wholebody import FULL
from test_wholebody import recording


def test_raw_topics_original_units_and_exact_clocks(tmp_path):
    project=recording(tmp_path)
    path=project['sources'][0]['original_path']; before=fingerprint(path)
    meta=inventory(path)
    assert '/tf_static' in {item['topic'] for item in meta['topics']}
    assert meta['read_only'] and isinstance(meta['start_ns'],str)
    data=samples(path,'/kuavo_arm_traj',.11,field='position[0]')
    first=data['messages'][0]
    assert first['source_time_s']==pytest.approx(.2)
    assert first['message']['position'][0]==90
    assert int(first['bag_time_ns'])-int(first['header_time_ns'])==5_000_000_000
    assert all(point['value']==90 for point in data['points'])
    raw=samples(path,'/sensors_data_raw',0,field='joint_data.joint_q[17]')
    assert raw['messages'][0]['message']['joint_data']['joint_q']==list(range(18))
    assert all(point['value']==17 for point in raw['points'])
    assert before==fingerprint(path)
    with pytest.raises(EditError): samples(path,'/missing')
    with pytest.raises(EditError): samples(path,'/odom',float('nan'))


def test_bounded_binary_nonfinite_and_no_eval():
    @dataclass
    class Message:
        values: np.ndarray
    msg=Message(np.array([1,np.nan]))
    assert bounded(msg)=={'values':[1,'nan']}
    assert numeric_field(msg,'values[0]')==1 and numeric_field(msg,'values[1]') is None
    for path in ['__class__','values[-1]','values[0].real','values[999999]','values[0];print(1)','values']:
        with pytest.raises(EditError): numeric_field(msg,path)
    binary=bounded(np.zeros(500000,dtype=np.uint8))
    assert len(binary['preview'])==32 and binary['length']==500000
    assert len(json.dumps(binary))<1024


def test_preview_schema_masks_exact_rows_and_cache(tmp_path,monkeypatch):
    project=recording(tmp_path); before=deepcopy(project)
    fingerprints=[fingerprint(s['path']) for s in project['sources']]
    root=tmp_path/'preview'; target=tmp_path/'output'; opts={'schema':FULL}
    result=build_preview(project,target,opts,root,lambda *_:None)
    saved=json.loads((root/'preview.json').read_text()); public=saved['public']
    assert result['frames']==5 and public['no_video_encoding']
    features={ft['feature']:ft for ft in public['sources'][0]['features']}
    assert len(features['observation.state']['dimensions'])==24
    action=features['action']['dimensions']
    assert len(action)==23 and action[0]['masked_count']==5 and action[0]['reasons']=={'1':5}
    assert action[4]['minimum']==pytest.approx(np.pi/2) and action[4]['unit']=='rad'
    assert all(ft['valid_frames']==5 for ft in features.values() if ft.get('camera'))
    row=preview_row(saved,'a',0,0)
    torso=next(f for f in row['fields'] if f['feature']=='action' and f['index']==0)
    assert not torso['valid'] and torso['value']==0 and torso['reason']==1
    assert next(f for f in row['fields'] if f['feature']=='observation.state' and f['index']==20)['value']==pytest.approx(-1)
    assert row['kept'] and not preview_row(saved,'a',0,8)['kept']
    assert not any(point['value'] is not None for point in preview_series(saved,'a',0,'action',0)['points'])
    assert preview_series(saved,'a',0,'action',4)['points'][0]['value']==pytest.approx(np.pi/2)
    assert preview_series(saved,'a',0,'action',4)['points'][8]['value'] is None
    assert not target.exists() and project==before
    assert fingerprints==[fingerprint(s['path']) for s in project['sources']]
    key=preview_key(project,target,opts)
    assert key==preview_key(project,tmp_path/'different-output',{**opts,'repo_name':'different'})
    assert key!=preview_key(project,target,{**opts,'rack_calibration':{'rack_tags':[]}})
    changed=deepcopy(project); changed['revision']+=1
    assert key!=preview_key(changed,target,opts)
    from kuavo_dataset_editor import wholebody
    monkeypatch.setattr(wholebody,'read_recording',lambda *_:(_ for _ in ()).throw(AssertionError('reread')))
    prepared,_=wholebody.prepare_project(project,root,opts,lambda *_:None)
    assert np.allclose(open_source(prepared['sources'][0]).row(0,0,cameras=False)['action'][4:18],np.pi/2)


def test_approved_image_hold_survives_wholebody_preview(tmp_path):
    from kuavo_dataset_editor.quality import inspect_quality
    project=recording(tmp_path)
    path=Path(project['sources'][0]['path'])/'catalog.json'
    catalog=json.loads(path.read_text()); camera=catalog['cameras'][0]
    catalog['images'][camera][2]=None; write_json(path,catalog)
    report=inspect_quality(project)
    approval={'token':report['token'],'decisions':{i['id']:'hold_image' for i in report['issues']}}
    directory=tmp_path/'preview'
    build_preview(project,tmp_path/'out',{'schema':FULL,'quality':approval},directory,lambda *_:None)
    saved=json.loads((directory/'preview.json').read_text())
    feature=next(f for f in saved['public']['sources'][0]['features'] if f['feature']==camera)
    assert feature['valid_frames']==5
    adapter=open_source(saved['project']['sources'][0])
    assert adapter.manifest['images'][camera][2]==catalog['images'][camera][1]
    assert adapter.row(0,2,camera_key=camera)[camera].shape==(48,64,3)
    assert json.loads(path.read_text())['images'][camera][2] is None


def test_legacy_numeric_correction_and_excluded_frame_preview(tmp_path):
    from kuavo_dataset_editor.sources import demo_source,CatalogSource
    from kuavo_dataset_editor.core import new_project,label
    from kuavo_dataset_editor.quality import inspect_quality
    path=tmp_path/'catalog';demo_source(path,'a')
    state=np.load(path/'state.npy');state[5,0]=np.nan;np.save(path/'state.npy',state)
    project=new_project('legacy');project['sources']=[CatalogSource(path).descriptor('a')]
    project=label(project,'a',0,0,10,'pick')
    report=inspect_quality(project)
    approval={'token':report['token'],'decisions':{i['id']:i['method'] for i in report['issues']}}
    root=tmp_path/'preview'
    build_preview(project,tmp_path/'out',{'schema':'legacy','quality':approval},root,lambda *_:None)
    saved=json.loads((root/'preview.json').read_text())
    row=preview_row(saved,'a',0,5)
    value=next(f for f in row['fields'] if f['feature']=='observation.state' and f['index']==0)
    assert value['valid'] and value['value']==pytest.approx((state[4,0]+state[6,0])/2)
    approval['decisions']={i['id']:'exclude' for i in report['issues']}
    root=tmp_path/'excluded'
    build_preview(project,tmp_path/'out',{'schema':'legacy','quality':approval},root,lambda *_:None)
    row=preview_row(json.loads((root/'preview.json').read_text()),'a',0,5)
    assert not row['kept']
    value=next(f for f in row['fields'] if f['feature']=='observation.state' and f['index']==0)
    assert not value['valid'] and value['value'] is None
    assert np.isnan(np.load(path/'state.npy')[5,0])


def test_api_ownership_allowlist_stale_preview_and_optional_use(tmp_path):
    from kuavo_dataset_editor.app import create_app
    project=recording(tmp_path)
    app=create_app(tmp_path/'workspace',[tmp_path]); store=app.state.store
    project=store.save(project,-1); pid=project['project_id']
    routes={r.name:r.endpoint for r in app.routes if hasattr(r,'endpoint')}
    assert routes['inspect_raw'](pid,'a')['read_only']
    assert routes['raw_messages'](pid,'a','/kuavo_arm_traj',0,'position[0]')['points'][0]['value']==90
    jid='a'*32
    jobroot=app.state.jobs.root/jid; jobroot.mkdir()
    options={'schema':FULL,'mode':'new','repo_name':None,'confirm_existing':False}
    target=tmp_path/'workspace/.preview-plans'/pid
    build_preview(project,target,options,jobroot,lambda *_:None)
    request={'kind':'export-preview','payload':{'project':project,'target':str(target),'options':options}}
    write_json(jobroot/'request.json',request)
    store.put_job({'id':jid,'kind':'export-preview','status':'completed'})
    write_json(jobroot/'status.json',{'id':jid,'kind':'export-preview','status':'completed'})
    assert routes['get_preview'](pid,jid)['frames']==5
    assert routes['get_preview_row'](pid,jid,'a',0,0)['kept']
    other=routes['create']({'name':'other'})['project_id']
    with pytest.raises(EditError,match='다른 프로젝트'): routes['get_preview'](other,jid)
    starts=[];app.state.jobs.start=lambda kind,payload: starts.append((kind,payload)) or {'id':'fake'}
    body={'schema':FULL,'repo_name':'output','preview_id':jid}
    routes['start_export'](pid,body)
    assert starts[-1][1]['options']['_preview_cache_root']==str(jobroot)
    changed=deepcopy(project);changed['revision']+=1
    store.save(changed,project['revision'])
    with pytest.raises(EditError,match='달라졌'): routes['start_export'](pid,body)
    routes['start_export'](pid,{'schema':FULL,'repo_name':'independent'})
    assert '_preview_cache_root' not in starts[-1][1]['options']
    assert not (tmp_path/'workspace/exports/output').exists()
    # The linked bag must be within this server's explicitly allowed roots.
    narrow=create_app(tmp_path/'narrow',[Path(project['sources'][0]['path'])])
    narrow.state.store.save(project,-1)
    endpoint=next(r.endpoint for r in narrow.routes if r.name=='inspect_raw')
    with pytest.raises(EditError,match='허용'): endpoint(pid,'a')


@pytest.mark.skipif(os.environ.get('EDITOR_TEST_EXPORT')!='1',reason='5-frame official writer smoke')
def test_preview_matches_official_export_and_reuses_calculation(tmp_path,monkeypatch):
    from kuavo_dataset_editor.exporter import export_dataset
    from kuavo_dataset_editor.sources import LeRobotSource
    from kuavo_dataset_editor import wholebody
    project=recording(tmp_path);root=tmp_path/'preview';target=tmp_path/'dataset'
    build_preview(project,target,{'schema':FULL},root,lambda *_:None)
    saved=json.loads((root/'preview.json').read_text())
    monkeypatch.setattr(wholebody,'read_recording',lambda *_:(_ for _ in ()).throw(AssertionError('cache not reused')))
    export_dataset(project,target,tmp_path/'export',lambda *_:None,{'schema':FULL,'_preview_cache_root':str(root)})
    row=LeRobotSource(target).row(0,0,cameras=False)
    for dim in preview_row(saved,'a',0,0)['fields']:
        assert row[dim['feature']][dim['index']]==pytest.approx(dim['value'])
        if 'validity.'+dim['feature'] in row:
            assert bool(row['validity.'+dim['feature']][dim['index']])==dim['valid']
