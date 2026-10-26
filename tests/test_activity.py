import json
from copy import deepcopy

import numpy as np
import pytest

from kuavo_dataset_editor.activity import (build_activity, torso_window, gripper_window,
    separate_gripper_feedback, window_change, window_excursion)
from kuavo_dataset_editor.core import EditError, new_project
from kuavo_dataset_editor.sources import CatalogSource, fingerprint, write_json
from kuavo_dataset_editor.wholebody import STATE_NAMES, ACTION_NAMES


def catalog(root, values, names=None, robot='kuavo_5w', fps=20):
    root.mkdir()
    features, arrays = {}, {}
    for i, (key, value) in enumerate(values.items()):
        value = np.asarray(value)
        np.save(root / f'{i}.npy', value)
        arrays[key] = f'{i}.npy'
        features[key] = {'dtype':str(value.dtype), 'shape':[value.shape[1]],
                         'names':(names or {}).get(key)}
    write_json(root / 'catalog.json', {'features':features, 'arrays':arrays, 'images':{}, 'cameras':[],
        'episodes':[{'episode_id':0, 'num_frames':len(value)}], 'fps':fps, 'robot_type':robot})
    return CatalogSource(root).descriptor('a')


def test_cues_preserve_missingness_command_vs_motion_and_source(tmp_path):
    n=16
    state=np.zeros((n,14)); state[:,0]=np.arange(n)*.02
    command=np.zeros((n,14)); command[6,7]=.5
    grip=np.tile([1.,-.007],(n,1)); grip[6:,0]=0
    grip_mask=np.ones((n,2),bool);grip_mask[5,0]=False
    base=np.zeros((n,6)); base[:,0]=np.arange(n)*.01;base[:,5]=1
    base_cmd=np.zeros((n,3));base_cmd[7:,0]=.1
    base_mask=np.ones((n,1),bool);base_mask[8:10]=False
    source=catalog(tmp_path/'source',{'observation.state':state,'action':command,
        'observation.gripper':grip,'validity.observation.gripper':grip_mask,
        'action.gripper':grip,'observation.base':base,'action.base':base_cmd,
        'validity.action.base':base_mask})
    before=fingerprint(source['path']);snapshot=deepcopy(source)
    data=build_activity(source,0);s=data['series']
    assert data['num_frames']==n and data['window_frames']==5 and data['base_source']=='odom'
    assert s['left_arm_motion'][0] is None
    assert s['left_arm_motion'][10] == pytest.approx(np.degrees(.1),abs=1e-6)
    assert s['left_arm_command'][10]==0
    assert s['right_arm_command'][10]==pytest.approx(np.degrees(.5),abs=1e-6)
    assert s['right_arm_motion'][10]==0 # A changed command does not prove execution.
    assert s['left_gripper'][5] is None and s['right_gripper'][5]==-.007
    assert s['left_gripper_change'][10] is None # Do not bridge a masked sample.
    assert s['left_gripper_change'][11]==0
    assert s['base_command_speed'][8] is None and s['base_command_speed'][10]==.1
    assert s['base_motion_speed'][8]==.2
    assert source==snapshot and fingerprint(source['path'])==before
    json.dumps(data,allow_nan=False)
    with pytest.raises(EditError): build_activity(source,99)


def test_named_schema_reordering_per_joint_masks_and_yaw_wrap(tmp_path):
    n=12
    names=list(reversed(STATE_NAMES)); action_names=list(reversed(ACTION_NAMES))
    state=np.zeros((n,24));action=np.zeros((n,23));mask=np.ones((n,24),bool)
    state[:,names.index('gripper.left_claw.open_fraction')]=.8
    state[:,names.index('gripper.right_claw.open_fraction')]=.1
    yaw=np.radians(np.linspace(179,181,n))
    state[:,names.index('base.rack.sin_yaw')]=np.sin(yaw)
    state[:,names.index('base.rack.cos_yaw')]=np.cos(yaw)
    action[:,action_names.index('base.velocity.yaw_rate')]=.2
    mask[5,names.index('joint.zarm_l3_joint.position')]=False
    source=catalog(tmp_path/'source',{'observation.state':state,'action':action,
        'validity.observation.state':mask}, {'observation.state':names,'action':action_names})
    data=build_activity(source,0);s=data['series']
    assert data['base_source']=='rack'
    assert s['left_gripper'][5]==.8 and s['right_gripper'][5]==.1
    assert s['left_arm_motion'][8] is None and s['right_arm_motion'][8]==0
    assert s['base_motion_yaw'][8]==pytest.approx(40/11,abs=1e-6)
    assert s['base_command_yaw'][8]==pytest.approx(np.degrees(.2),abs=1e-6)
    assert s['base_motion_speed'][8]==0


def test_unknown_schema_and_incomplete_windows_do_not_become_idle(tmp_path):
    source=catalog(tmp_path/'source',{'observation.state':np.zeros((8,14)),
                                    'action':np.zeros((8,14))},robot='unrelated_robot')
    s=build_activity(source,0)['series']
    assert all(v is None for v in s['left_arm_recorded'])
    assert all(v is None for v in s['left_arm_motion'])
    assert all(v is None for v in s['torso_motion'])
    assert all(v is None for v in s['base_command_speed'])
    values=np.array([[0.],[1.],[0.],[np.nan],[0.]])
    assert window_excursion(values,2)[2,0]==1
    assert window_change(values,2)[2,0]==0
    assert np.isnan(window_change(values,2)[4,0])


def test_torso_uses_measured_named_joints_and_masks(tmp_path):
    names=list(reversed(STATE_NAMES))
    n=16
    state=np.zeros((n,24));mask=np.ones((n,24),bool)
    state[:,names.index('joint.waist_yaw_joint.position')]=np.arange(n)*.01
    mask[6,names.index('joint.knee_joint.position')]=False
    source=catalog(tmp_path/'source',{'observation.state':state,
        'validity.observation.state':mask}, {'observation.state':names})
    data=build_activity(source,0)
    assert data['torso_source']=='schema'
    motion=data['series']['torso_motion']
    assert motion[0] is None
    assert motion[5]==pytest.approx(np.degrees(.05),abs=1e-6)
    assert motion[6] is None and motion[11] is None
    assert motion[12]==pytest.approx(np.degrees(.05),abs=1e-6)


def test_torso_reads_only_current_bag_segment_and_leaves_import_intact(tmp_path,monkeypatch):
    from rosbags.rosbag1 import Writer
    from rosbags.typesys import Stores,get_typestore,get_types_from_msg
    from kuavo_dataset_editor import ros1_reader

    store=get_typestore(Stores.ROS1_NOETIC)
    store.register(get_types_from_msg('float64[] joint_q\n','kuavo_msgs/msg/jointData'))
    store.register(get_types_from_msg('kuavo_msgs/jointData joint_data\n','kuavo_msgs/msg/sensorsData'))
    bag=tmp_path/'robot.bag'
    origin=1_700_000_000_000_000_000
    with Writer(bag) as writer:
        connection=writer.add_connection('/sensors_data_raw','kuavo_msgs/msg/sensorsData',typestore=store)
        for i in range(340):
            if 205<=i<=211: continue
            joints=np.zeros(18);joints[0]=i*.01
            message=store.types['kuavo_msgs/msg/sensorsData'](store.types['kuavo_msgs/msg/jointData'](joints))
            writer.write(connection,origin+i*50_000_000,store.serialize_ros1(message,connection.msgtype))
    source=catalog(tmp_path/'source',{'observation.state':np.zeros((340,14))})
    manifest_path=tmp_path/'source/catalog.json'
    manifest=json.loads(manifest_path.read_text())
    manifest.update(original_path=str(bag),fingerprint=fingerprint(bag),origin_bag_time_ns=origin,
                    profile={'clock':'bag_receipt_time','state_tolerance_s':.1})
    write_json(manifest_path,manifest)
    source=CatalogSource(tmp_path/'source').descriptor('a')
    before=(fingerprint(bag),fingerprint(source['path']))
    calls=[]
    class TrackedReader(ros1_reader.Ros1Reader):
        def messages(self,**kwargs):
            calls.append(kwargs)
            return self.reader.messages(**kwargs)
    monkeypatch.setattr(ros1_reader,'Ros1Reader',TrackedReader)

    assert build_activity(source,0)['torso_source']=='bag'
    segment=torso_window(source,0,210,bag)
    assert segment['start_frame']==200 and len(segment['motion'])==100
    assert segment['motion'][10] is None # Do not bridge missing raw measurements.
    assert segment['motion'][20]==pytest.approx(np.degrees(.05),abs=1e-5)
    assert len(calls)==1 and calls[0]['stop']-calls[0]['start']<6_000_000_000
    assert calls[0]['start']>origin+9_000_000_000
    assert torso_window(source,0,220,bag)==segment and len(calls)==1
    assert (fingerprint(bag),fingerprint(source['path']))==before
    with pytest.raises(EditError,match='프레임'):torso_window(source,0,340,bag)

    manifest['fingerprint']['value']='changed-since-import'
    write_json(manifest_path,manifest)
    with pytest.raises(EditError,match='변경'):torso_window(source,0,210,bag)


def test_activity_api_enforces_project_ownership_and_allowlist(tmp_path):
    from kuavo_dataset_editor.app import create_app
    source=catalog(tmp_path/'source',{'action':np.zeros((8,14))})
    p=new_project('test');p['sources']=[source]
    app=create_app(tmp_path/'workspace',[tmp_path/'source']);app.state.store.save(p,-1)
    endpoint=next(r.endpoint for r in app.routes if r.name=='activity')
    assert endpoint(p['project_id'],'a',0)['num_frames']==8
    with pytest.raises(KeyError): endpoint(p['project_id'],'other-source',0)
    narrow=create_app(tmp_path/'narrow');narrow.state.store.save(p,-1)
    with pytest.raises(EditError,match='허용'): next(r.endpoint for r in narrow.routes if r.name=='activity')(p['project_id'],'a',0)
    p['sources'][0]['original_path']=str(tmp_path/'outside.bag')
    app.state.store.save(p,0)
    torso_endpoint=next(r.endpoint for r in app.routes if r.name=='torso')
    with pytest.raises(EditError,match='허용'):torso_endpoint(p['project_id'],'a',0,0)
    with pytest.raises(KeyError):torso_endpoint(p['project_id'],'other-source',0,0)
    gripper_endpoint=next(r.endpoint for r in app.routes if r.name=='gripper')
    with pytest.raises(EditError,match='허용'):gripper_endpoint(p['project_id'],'a',0,0)
    with pytest.raises(KeyError):gripper_endpoint(p['project_id'],'other-source',0,0)


def test_gripper_separation_requires_sustained_nearby_conflicting_feedback():
    records=[]
    for i in range(20):
        records.extend([(i*2_000_000,np.array([92.,94.]),False),
                        (i*2_000_000+1_000_000,np.zeros(2),True)])
    kept,removed=separate_gripper_feedback(records)
    assert removed==20 and len(kept)==20
    assert all(np.array_equal(r[1],[92,94]) for r in kept)
    # Reached/zero feedback in an ordinary single stream remains a valid zero.
    zeros=[r for r in records if r[2]]
    assert separate_gripper_feedback(zeros)==(zeros,0)
    # A real transition to zero must not be removed as an echo.
    transition=[(i*50_000_000,np.array([92.,94.]) if i<10 else np.zeros(2),i>=10)
                for i in range(20)]
    assert separate_gripper_feedback(transition)==(transition,0)


def test_gripper_raw_window_separates_echo_keeps_gaps_and_preserves_cache(tmp_path,monkeypatch):
    from rosbags.rosbag1 import Writer
    from rosbags.typesys import Stores,get_typestore,get_types_from_msg
    from kuavo_dataset_editor import ros1_reader
    store=get_typestore(Stores.EMPTY)
    store.register(get_types_from_msg('string[] name\nfloat64[] position\nfloat64[] velocity\nfloat64[] effort\n',
        'kuavo_msgs/msg/endEffectorData'))
    store.register(get_types_from_msg('int8[] state\nkuavo_msgs/endEffectorData data\n','kuavo_msgs/msg/lejuClawState'))
    bag=tmp_path/'gripper.bag';origin=1_700_000_000_000_000_000
    data_type=store.types['kuavo_msgs/msg/endEffectorData'];state_type=store.types['kuavo_msgs/msg/lejuClawState']
    with Writer(bag) as writer:
        connection=writer.add_connection('/leju_claw_state','kuavo_msgs/msg/lejuClawState',typestore=store)
        for i in range(8500):
            # A real hardware channel may itself report Reached; do not drop that state.
            if not 5125<=i<=5325:
                data=data_type(['right_claw','left_claw'],np.array([94.,92.]),np.zeros(2),np.array([.7,.8]))
                msg=state_type(np.array([2,3],dtype=np.int8),data)
                writer.write(connection,origin+i*2_000_000,store.serialize_ros1(msg,connection.msgtype))
            data=data_type(['left_claw','right_claw'],np.zeros(2),np.zeros(2),np.zeros(2))
            msg=state_type(np.array([2,2],dtype=np.int8),data)
            writer.write(connection,origin+i*2_000_000+1_000_000,store.serialize_ros1(msg,connection.msgtype))
    source=catalog(tmp_path/'source',{'observation.gripper':np.zeros((340,2))})
    path=tmp_path/'source/catalog.json';manifest=json.loads(path.read_text())
    manifest.update(original_path=str(bag),fingerprint=fingerprint(bag),origin_bag_time_ns=origin,
        profile={'clock':'bag_receipt_time','state_tolerance_s':.1,'signals':[
            {'key':'observation.gripper','topic':'/leju_claw_state','field':'data.position',
             'size':2,'order':['left_claw','right_claw'],'names_field':'data.name','scale':.01,
             'unit':'open_fraction_0_closed_1_open','method':'nearest'}]})
    write_json(path,manifest);source=CatalogSource(tmp_path/'source').descriptor('a')
    before=(fingerprint(bag),fingerprint(source['path']))
    calls=[]
    class TrackedReader(ros1_reader.Ros1Reader):
        def messages(self,**kwargs):
            calls.append(kwargs);return self.reader.messages(**kwargs)
    monkeypatch.setattr(ros1_reader,'Ros1Reader',TrackedReader)
    assert build_activity(source,0)['gripper_source']=='bag'
    result=gripper_window(source,0,210,bag)
    assert result['start_frame']==200 and result['separated_messages']>1000
    assert len(result['series']['left_gripper'])==100
    assert result['series']['left_gripper'][10] is None # No filling a missing hardware sample with an echo.
    assert result['series']['left_gripper'][30]==pytest.approx(.92)
    assert result['series']['right_gripper'][30]==pytest.approx(.94)
    assert result['series']['left_gripper_change'][15] is None # Do not bridge the gap for motion.
    assert result['series']['left_gripper_change'][30]==0
    assert len(calls)==1 and calls[0]['stop']-calls[0]['start']<6_000_000_000
    assert calls[0]['start']>origin+9_000_000_000
    assert gripper_window(source,0,230,bag)==result and len(calls)==1
    assert (fingerprint(bag),fingerprint(source['path']))==before
    with pytest.raises(EditError,match='프레임'):gripper_window(source,0,340,bag)
    manifest['fingerprint']['value']='changed';write_json(path,manifest)
    with pytest.raises(EditError,match='변경'):gripper_window(source,0,210,bag)
