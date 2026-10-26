"""Bounded ROS-free geometry/mask tests and a tiny opt-in official export."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest
from PIL import Image
from rosbags.rosbag1 import Writer
from rosbags.typesys import Stores,get_typestore,get_types_from_msg

from kuavo_dataset_editor.bag import import_bag
from kuavo_dataset_editor.core import new_project,label
from kuavo_dataset_editor.masking import encode_missing,visible_values,masked_statistics,chunk_loss_mask
from kuavo_dataset_editor.recorded_tf import RecordedTF
from kuavo_dataset_editor.rack_reconstruction import fit_planar
from kuavo_dataset_editor.sources import open_source,fingerprint
from kuavo_dataset_editor.wholebody import FULL,STATE_NAMES,ACTION_NAMES,prepare_project


def test_mask_contract_and_loss_bridge():
    filled,valid,reasons = encode_missing([[0,np.nan],[2,np.nan]])
    assert filled.tolist()==[[0,0],[2,0]] and valid.tolist()==[[True,False],[True,False]]
    assert valid.dtype==np.bool_ and reasons.dtype==np.uint8
    assert reasons.tolist()==[[0,2],[0,2]]
    stats,counts = masked_statistics(filled,valid)
    assert counts==[2,0] and stats['mean']==[1,0] and stats['std']==[1,1]
    assert visible_values({'action':filled[0],'validity.action':valid[0]},
                          {'action':{},'validity.action':{}})['action']==[0,None]
    mask = chunk_loss_mask(valid[None],np.ones((1,2,4)))
    assert mask.tolist()==[[[True,False,False,False],[True,False,False,False]]]
    with pytest.raises(ValueError,match='horizon'):
        chunk_loss_mask(valid[0][None],np.ones((1,2,4)))


def test_recorded_tf_interpolates_without_latest_fallback():
    tf = RecordedTF()
    def message(t,x):
        return NS(transforms=[NS(header=NS(frame_id='odom',stamp=NS(sec=0,nanosec=t)),
                     child_frame_id='base_link',transform=NS(translation=NS(x=x,y=0,z=0),
                                                            rotation=NS(x=0,y=0,z=0,w=1)))])
    tf.add(message(100_000_000,0)); tf.add(message(200_000_000,1)); tf.finalize()
    assert tf.lookup('odom','base_link',150_000_000)[0,3]==pytest.approx(.5)
    assert tf.lookup('base_link','odom',150_000_000)[0,3]==pytest.approx(-.5)
    with pytest.raises(ValueError): tf.lookup('odom','base_link',300_000_000)
    pose,rms = fit_planar([[0,.4],[0,-.4]],[[2.6,1],[3.4,1]])
    assert np.allclose(pose,[3,1,np.pi/2]) and rms<1e-8


def recording(tmp_path,torso=False,calibration=True,rwh=False):
    store = get_typestore(Stores.ROS1_NOETIC)
    for definition,name in [
        ('float64[] joint_q\n','kuavo_msgs/msg/jointData'),
        ('std_msgs/Header header\nkuavo_msgs/jointData joint_data\n','kuavo_msgs/msg/sensorsData'),
        ('string[] name\nfloat64[] position\n','kuavo_msgs/msg/clawData'),
        ('kuavo_msgs/clawData data\n','kuavo_msgs/msg/clawState'),
        ('int32[] id\ngeometry_msgs/PoseWithCovarianceStamped pose\n','apriltag_ros/msg/AprilTagDetection'),
        ('std_msgs/Header header\napriltag_ros/AprilTagDetection[] detections\n','apriltag_ros/msg/AprilTagDetectionArray'),
        ('geometry_msgs/TransformStamped[] transforms\n','tf2_msgs/msg/TFMessage'),
        ('float64[] joint_q\n','kuavo_msgs/msg/jointCmd')]:
        store.register(get_types_from_msg(definition,name))
    t = store.types
    def msg(name,*args,**kw): return t[name](*args,**kw)
    def header(stamp,frame):
        return msg('std_msgs/msg/Header',0,msg('builtin_interfaces/msg/Time',stamp//10**9,stamp%10**9),frame)
    def transform(stamp,parent,child,xyz):
        return msg('geometry_msgs/msg/TransformStamped',header(stamp,parent),child,
                   msg('geometry_msgs/msg/Transform',msg('geometry_msgs/msg/Vector3',*xyz),
                       msg('geometry_msgs/msg/Quaternion',0.,0.,0.,1.)))
    def detection(stamp,tid,xyz):
        pose = msg('geometry_msgs/msg/Pose',msg('geometry_msgs/msg/Point',*xyz),msg('geometry_msgs/msg/Quaternion',0.,0.,0.,1.))
        covariance = msg('geometry_msgs/msg/PoseWithCovariance',pose,np.zeros(36))
        return msg('apriltag_ros/msg/AprilTagDetection',np.array([tid],dtype=np.int32),
                   msg('geometry_msgs/msg/PoseWithCovarianceStamped',header(stamp,'camera'),covariance))
    profile = json.loads((Path(__file__).parents[1]/'src/kuavo_dataset_editor/profiles'/
                          ('rwh_auto.json' if rwh else 'kuavo_5w.json')).read_text())
    profile['fps']=10
    topics = {topic:'sensor_msgs/msg/CompressedImage' for topic in profile['cameras'].values()}
    topics.update({'/sensors_data_raw':'kuavo_msgs/msg/sensorsData','/kuavo_arm_traj':'sensor_msgs/msg/JointState',
                   '/leju_claw_state':'kuavo_msgs/msg/clawState','/leju_claw_command':'kuavo_msgs/msg/clawState',
                   '/cmd_vel':'geometry_msgs/msg/Twist','/odom':'nav_msgs/msg/Odometry',
                   '/tf':'tf2_msgs/msg/TFMessage','/tf_static':'tf2_msgs/msg/TFMessage',
                   '/rwh/tag_detections':'apriltag_ros/msg/AprilTagDetectionArray'})
    if torso: topics['/joint_cmd']='kuavo_msgs/msg/jointCmd'
    if rwh:   # rwh_auto: team topics only while moving, continuous helper topics + recorded rack pose
        topics.update({'/rwh/capture/arm_cmd_hold':'sensor_msgs/msg/JointState','/rwh/capture/claw_state':'kuavo_msgs/msg/clawState',
                       '/rwh/capture/claw_cmd_hold':'kuavo_msgs/msg/clawState','/rwh/navigation_proxy/cmd_vel_plan':'geometry_msgs/msg/Twist',
                       '/rwh/capture/base_pose_in_rack':'geometry_msgs/msg/PoseStamped'})
    if calibration: topics['/dataset/base_pose_in_rack/status']='std_msgs/msg/String'
    buffer = io.BytesIO(); Image.new('RGB',(64,48),(90,140,180)).save(buffer,format='JPEG')
    bag = tmp_path/'robot.bag'
    with Writer(bag) as writer:
        connections = {topic:writer.add_connection(topic,kind,typestore=store) for topic,kind in topics.items()}
        def write(topic,stamp,value):
            connection = connections[topic]
            writer.write(connection,stamp,store.serialize_ros1(value,connection.msgtype))
        for i in range(15):
            stamp = 1_700_000_000_000_000_000+i*100_000_000
            receipt = stamp+5_000_000_000
            h = header(stamp,'base_link')
            image = msg('sensor_msgs/msg/CompressedImage',header(stamp,'camera'),'jpeg',np.frombuffer(buffer.getvalue(),dtype=np.uint8))
            for topic in profile['cameras'].values(): write(topic,receipt,image)
            write('/sensors_data_raw',receipt,msg('kuavo_msgs/msg/sensorsData',h,msg('kuavo_msgs/msg/jointData',np.arange(18,dtype=float))))
            claw = msg('kuavo_msgs/msg/clawState',msg('kuavo_msgs/msg/clawData',['right_claw','left_claw'],np.array([60.,50.])))
            if rwh:
                if i==0: write('/kuavo_arm_traj',receipt,msg('sensor_msgs/msg/JointState',h,[],np.full(14,90.),np.array([]),np.array([])))
                write('/leju_claw_state',receipt,claw)   # team topic mixes in fake messages; the filtered helper is used
                write('/rwh/capture/arm_cmd_hold',receipt,msg('sensor_msgs/msg/JointState',h,[],np.full(14,45.),np.array([]),np.array([])))
                write('/rwh/capture/claw_state',receipt,msg('kuavo_msgs/msg/clawState',msg('kuavo_msgs/msg/clawData',['left_claw','right_claw'],np.array([30.,40.]))))
                write('/rwh/capture/claw_cmd_hold',receipt,msg('kuavo_msgs/msg/clawState',msg('kuavo_msgs/msg/clawData',['left_claw','right_claw'],np.array([20.,10.]))))
                write('/rwh/navigation_proxy/cmd_vel_plan',receipt,msg('geometry_msgs/msg/Twist',msg('geometry_msgs/msg/Vector3',.05,0.,0.),msg('geometry_msgs/msg/Vector3',0.,0.,.1)))
                yaw = np.pi/2
                write('/rwh/capture/base_pose_in_rack',receipt,msg('geometry_msgs/msg/PoseStamped',header(stamp,'rack'),
                      msg('geometry_msgs/msg/Pose',msg('geometry_msgs/msg/Point',-1.1+i*.01,.02,0.),
                          msg('geometry_msgs/msg/Quaternion',0.,0.,np.sin(yaw/2),np.cos(yaw/2)))))
            else:
                write('/kuavo_arm_traj',receipt,msg('sensor_msgs/msg/JointState',h,[],np.full(14,90.),np.array([]),np.array([])))
                write('/leju_claw_state',receipt,claw); write('/leju_claw_command',receipt,claw)
                write('/cmd_vel',receipt,msg('geometry_msgs/msg/Twist',msg('geometry_msgs/msg/Vector3',.1,.2,0.),msg('geometry_msgs/msg/Vector3',0.,0.,.3)))
            pose = msg('geometry_msgs/msg/Pose',msg('geometry_msgs/msg/Point',i*.05,0.,0.),msg('geometry_msgs/msg/Quaternion',0.,0.,0.,1.))
            write('/odom',receipt,msg('nav_msgs/msg/Odometry',header(stamp,'odom'),'base_footprint',
                   msg('geometry_msgs/msg/PoseWithCovariance',pose,np.zeros(36)),
                   msg('geometry_msgs/msg/TwistWithCovariance',msg('geometry_msgs/msg/Twist',msg('geometry_msgs/msg/Vector3',0.,0.,0.),msg('geometry_msgs/msg/Vector3',0.,0.,0.)),np.zeros(36))))
            write('/tf',receipt,msg('tf2_msgs/msg/TFMessage',[transform(stamp,'odom','base_footprint',(i*.05,0.,0.))]))
            if i==0:
                write('/tf_static',receipt,msg('tf2_msgs/msg/TFMessage',[
                    transform(stamp,'base_footprint','base_link',(.2,0.,0.)),transform(stamp,'base_link','camera',(.1,0.,1.))]))
            if i in (10,11,12):
                ds = [detection(stamp,0,(2.6-i*.05-.3,1.,0.)),detection(stamp,1,(3.4-i*.05-.3,1.,0.)),
                      detection(stamp,7,(99.,99.,99.))] # box tag 7 must NEVER anchor rack
                write('/rwh/tag_detections',receipt,msg('apriltag_ros/msg/AprilTagDetectionArray',header(stamp,'camera'),ds))
            if torso: write('/joint_cmd',receipt,msg('kuavo_msgs/msg/jointCmd',np.arange(18,dtype=float)+.1))
            if calibration and i==14:
                status = {'schema':'rack_relative_recording_v1','rack_tags':[
                    dict(id=0,parent='rack',x=0,y=.4,z=1),dict(id=1,parent='rack',x=0,y=-.4,z=1)]}
                write('/dataset/base_pose_in_rack/status',receipt,msg('std_msgs/msg/String',json.dumps(status)))
    descriptor = import_bag(bag,tmp_path/'catalog','a',profile,lambda *_:None)
    project = new_project('wholebody fixture'); project['sources']=[descriptor]
    return label(project,'a',0,0,5,'align with rack')


@pytest.mark.parametrize('torso,calibration',[(False,True),(True,True),(False,False)])
def test_full_body_export_derivation(tmp_path,torso,calibration):
    original = recording(tmp_path,torso,calibration)
    before = deepcopy(original)
    stamps = [fingerprint(s['path']) for s in original['sources']]
    project,options = prepare_project(original,tmp_path/'job',{'schema':FULL},lambda *_:None)
    a = open_source(project['sources'][0]); row = a.row(0,0,cameras=False)
    assert row['observation.state'].shape==(24,) and row['action'].shape==(23,)
    assert a.features['observation.state']['names']==STATE_NAMES
    assert a.features['action']['names']==ACTION_NAMES
    assert np.allclose(row['observation.state'][:18],np.arange(18))
    assert np.allclose(row['observation.state'][18:20],[.5,.6])
    assert np.allclose(row['action'][4:18],np.pi/2)
    assert row['validity.action'][:4].all()==torso
    assert np.all(row['missing_reason.action'][:4]==(0 if torso else 1))
    if torso: assert np.allclose(row['action'][:4],np.arange(4)+.1)
    else: assert np.all(row['action'][:4]==0) # NOT measured torso copied as a target
    if calibration:
        assert np.allclose(row['observation.state'][20:],[-1,2.8,-1,0],atol=1e-5)
        assert row['provenance.base_pose_source'].tolist()==[1]
        report = json.loads(Path(a.manifest['wholebody_report']).read_text())
        assert report['anchor_knots'][0]['ids']==[0,1]
    else:
        assert not row['validity.observation.state'][20:].any()
        assert np.all(row['missing_reason.observation.state'][20:]==4)
    assert original==before and stamps==[fingerprint(s['path']) for s in original['sources']]
    # Cache reuse must not reread raw bag or change annotation.
    again,_ = prepare_project(original,tmp_path/'job',{'schema':FULL},lambda *_:None)
    assert again==project


def test_rwh_auto_profile_uses_helper_topics_and_recorded_rack_pose(tmp_path):
    original = recording(tmp_path,torso=True,calibration=False,rwh=True)
    catalog = open_source(original['sources'][0])
    assert catalog.manifest['report']['missing_samples']['action']==0
    assert 'action.base' not in catalog.manifest['report']['omitted_optional_features']
    project,_ = prepare_project(original,tmp_path/'job',{'schema':FULL},lambda *_:None)
    a = open_source(project['sources'][0])
    for frame in (0,4):
        row = a.row(0,frame,cameras=False)
        assert row['validity.action'].all() and row['validity.observation.state'].all()
        assert np.allclose(row['action'][4:18],np.pi/4)            # arm_cmd_hold (45 deg), not the stale /kuavo_arm_traj
        assert np.allclose(row['action'][18:20],[.2,.1])           # claw_cmd_hold, names reordered
        assert np.allclose(row['observation.state'][18:20],[.3,.4])
        assert np.allclose(row['action'][20:23],[.05,0.,.1])       # rwh wheel plan velocity
        assert np.allclose(row['observation.state'][20:],[-1.1+frame*.01,.02,1.,0.],atol=1e-5)
        assert row['provenance.base_pose_source'].tolist()==[4]
    report = json.loads(Path(a.manifest['wholebody_report']).read_text())
    assert report['topic_sources']['arms']=='/rwh/capture/arm_cmd_hold'
    assert report['base_pose_source']=='/rwh/capture/base_pose_in_rack'


def test_every_selected_recording_is_derived(tmp_path):
    (tmp_path/'a').mkdir(); (tmp_path/'b').mkdir()
    first = recording(tmp_path/'a',torso=True,calibration=False,rwh=True)
    second = recording(tmp_path/'b',torso=True,calibration=False,rwh=True)
    source = deepcopy(second['sources'][0]); source['source_id'] = 'b'
    first['sources'].append(source)
    first = label(first,'b',0,0,5,'align with rack')
    project,_ = prepare_project(first,tmp_path/'job',{'schema':FULL},lambda *_:None)
    assert all(s.get('export_derivation') for s in project['sources'])


def test_source_ids_export_only_the_chosen_recordings(tmp_path):
    from kuavo_dataset_editor.core import EditError
    from kuavo_dataset_editor.repositories import prepare_export, selected_project
    (tmp_path/'a').mkdir(); (tmp_path/'b').mkdir()
    project = recording(tmp_path/'a',torso=True,calibration=False,rwh=True)
    other = recording(tmp_path/'b',torso=True,calibration=False,rwh=True)
    source = deepcopy(other['sources'][0]); source['source_id'] = 'b'
    project['sources'].append(source)
    project = label(project,'b',0,2,9,'pick')
    picked = selected_project(project,tmp_path/'out','new','step',['b'])
    assert [s['source_id'] for s in picked['sources']]==['b'] and {c['source_id'] for c in picked['clips']}=={'b'}
    options = {'mode':'new','repo_name':'pick-only','schema':FULL}
    assert prepare_export(project,tmp_path/'out',options)['kept_frames']==5+7
    assert prepare_export(project,tmp_path/'out',{**options,'source_ids':['b']})['kept_frames']==7
    derived,_ = prepare_project(project,tmp_path/'job',{**options,'source_ids':['b']},lambda *_:None)
    assert [s['source_id'] for s in derived['sources']]==['b']
    with pytest.raises(EditError):
        selected_project(project,tmp_path/'out','new','step',['nope'])


@pytest.mark.skipif(os.environ.get('EDITOR_TEST_EXPORT')!='1',reason='5-frame official export smoke')
def test_wholebody_official_roundtrip(tmp_path):
    from kuavo_dataset_editor.exporter import export_dataset
    from kuavo_dataset_editor.sources import LeRobotSource
    project = recording(tmp_path)
    target = tmp_path/'output'
    result = export_dataset(project,target,tmp_path/'job',lambda *_:None,{'schema':FULL})
    assert result['frames']==5 and result['episodes']==1
    assert result['mask_summary']['action']['valid_counts'][:4]==[0]*4
    reader = LeRobotSource(target); row = reader.row(0,0,cameras=False)
    assert row['validity.action'].dtype==np.bool_ and row['missing_reason.action'].dtype==np.uint8
    assert not row['validity.action'][:4].any()
    assert json.loads((target/'meta/stats.json').read_text())['action']['std'][:4]==[1]*4
    assert (target/'meta/kuavo_schema.json').is_file()
    addition = deepcopy(project)
    addition['clips']=[]
    addition = label(addition,'a',0,6,9,'carry')
    appended = export_dataset(addition,target,tmp_path/'append-job',lambda *_:None,
                              {'schema':FULL,'mode':'append','confirm_existing':True})
    assert appended['frames']==8 and appended['episodes']==2
    assert appended['mask_summary']['action']['valid_counts'][:4]==[0]*4
    assert appended['mask_summary']['action']['valid_counts'][4:]==[8]*19
    reader = LeRobotSource(target)
    # Existing new-schema data can be re-edited without original bag or job cache.
    p = new_project(); p['sources']=[reader.descriptor('b')]; p=label(p,'b',0,1,4,'pick')
    second = export_dataset(p,tmp_path/'edited',tmp_path/'edit-job',lambda *_:None,{'schema':FULL})
    assert second['frames']==3 and second['mask_summary']['action']['valid_counts'][:4]==[0]*4
    assert list((tmp_path/'edited/editor/reconstruction').rglob('*.json'))
