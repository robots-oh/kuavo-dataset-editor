"""Versioned export-only Kuavo schema; raw bags/catalogs/annotations stay read-only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import numpy as np

from .core import EditError, compile_selection
from .sources import CatalogSource, fingerprint, write_json
from .recorded_tf import RecordedTF, stamp_ns
from .rack_reconstruction import candidates, anchor_knots, reconstruct, compact_detections
from .masking import REASONS, encode_missing, mask_mapping

FULL = 'kuavo_wholebody_v1'
STATE_ONLY = 'kuavo_state24_v1'
SCHEMAS = ('legacy', FULL, STATE_ONLY)
JOINTS = ['knee_joint','leg_joint','waist_pitch_joint','waist_yaw_joint'] + [
    f'zarm_{side}{i}_joint' for side in ('l','r') for i in range(1,8)]
STATE_NAMES = [f'joint.{name}.position' for name in JOINTS] + [
    'gripper.left_claw.open_fraction','gripper.right_claw.open_fraction',
    'base.rack.x','base.rack.y','base.rack.sin_yaw','base.rack.cos_yaw']
ACTION_NAMES = [f'joint.{name}.target_position' for name in JOINTS] + [
    'gripper.left_claw.target_open_fraction','gripper.right_claw.target_open_fraction',
    'base.velocity.vx','base.velocity.vy','base.velocity.yaw_rate']


def schema_name(options):
    name = (options or {}).get('schema','legacy')
    if name not in SCHEMAS:
        raise EditError('지원하지 않는 export schema: '+str(name))
    return name


def schema_features(features, schema):
    if schema == 'legacy':
        return deepcopy(features)
    result = {k:deepcopy(v) for k,v in features.items() if v['dtype'] in ('video','image')}
    result['observation.state'] = {'dtype':'float32','shape':[24],'names':STATE_NAMES}
    result['validity.observation.state'] = {'dtype':'bool','shape':[24],'names':STATE_NAMES}
    result['missing_reason.observation.state'] = {'dtype':'uint8','shape':[24],'names':STATE_NAMES}
    result['provenance.base_pose_source'] = {'dtype':'uint8','shape':[1],'names':['reconstruction_source_code']}
    if schema == FULL:
        result['action'] = {'dtype':'float32','shape':[23],'names':ACTION_NAMES}
        result['validity.action'] = {'dtype':'bool','shape':[23],'names':ACTION_NAMES}
        result['missing_reason.action'] = {'dtype':'uint8','shape':[23],'names':ACTION_NAMES}
    else:
        result.update({k:deepcopy(v) for k,v in features.items()
                       if k=='action' or k.startswith(('action.','validity.action','missing_reason.action'))})
    return result


def contract(schema):
    state = [('torso',0,4,'rad'),('left_arm',4,11,'rad'),('right_arm',11,18,'rad'),
             ('grippers',18,20,'open_fraction'),('base_in_rack',20,24,'m,m,sin_yaw,cos_yaw')]
    action = [('torso',0,4,'rad'),('left_arm',4,11,'rad'),('right_arm',11,18,'rad'),
              ('grippers',18,20,'open_fraction'),('base_velocity',20,23,'m/s,m/s,rad/s')]
    return {'schema':schema,'state_names':STATE_NAMES,'action_names':ACTION_NAMES if schema==FULL else None,
            'groups':{'observation.state':{name:{'start':a,'end':b,'unit':unit,'kind':'measured_or_reconstructed'}
                                           for name,a,b,unit in state},
                      'action':{name:{'start':a,'end':b,'unit':unit,'kind':'command'}
                                for name,a,b,unit in action} if schema==FULL else 'legacy_unchanged'},
            'rack_frame':{'origin':'front_face_center_on_floor','x':'into_rack','y':'left','z':'up',
                          'robot_frame':'base_link','model':'planar_xy_yaw'},
            'action_representation':'absolute_joint_targets_and_gripper_targets; base_velocity_is_not_a_position_delta',
            'validity':{'dtype':'bool','0':'unknown/excluded','1':'valid',
                        'shape':'identical to value feature; names/order identical',
                        'placeholder':0,'placeholder_is_command':False},
            'missing_reason':{'dtype':'uint8','codes':REASONS},
            'base_pose_source_codes':{'0':'unavailable','1':'backfilled_before_first_anchor',
                                      '2':'between_good_anchor_windows','3':'propagated_after_last_anchor'},
            'sources':{'torso_state':'/sensors_data_raw.joint_data.joint_q[0:4] (rad)',
                       'arms_state':'/sensors_data_raw.joint_data.joint_q[4:18] (rad)',
                       'torso_action':'/joint_cmd.joint_q[0:4] (rad), else /lb_leg_traj.position (deg to rad)',
                       'arms_action':'/kuavo_arm_traj.position (deg to rad)',
                       'gripper':'/leju_claw_state or /leju_claw_command .data.position, names reordered, /100',
                       'base_action':'/cmd_vel.linear.x, linear.y, angular.z',
                       'rack_state':'recorded TF odom<-base_link and robust multi-tag anchors; not /odom pose assumed base_link'},
            'training_note':'Extra validity columns are NOT automatically consumed by GR00T. '
                            'Map joint/gripper groups explicitly; do not subtract base pose from velocity. '
                            'State24-only actions do not align with state prefix (arms=state[4:18]).'}


def validate_source(source, schema):
    if schema == 'legacy':
        return
    if source['kind']=='lerobot':
        expected = schema_features(source['features'],schema)
        if source['features'] != expected:
            raise EditError('새 Kuavo schema 변환에는 원본 rosbag이 필요합니다. 기존 LeRobot의 누락 torso/랙 정보를 만들지 않습니다.')
    elif Path(source.get('original_path','')).suffix.lower() != '.bag':
        raise EditError('새 Kuavo schema는 원본 .bag이 연결된 catalog에서만 계산합니다.')


def _numeric(msg, key):
    from .bag import signal_value
    specs = {
        'joints':dict(field='joint_data.joint_q',slice=[0,18],size=18),
        'arms':dict(field='position',size=14,scale=np.pi/180),
        'torso':dict(field='joint_q',slice=[0,4],size=4),
        'torso_trajectory':dict(field='position',size=4,scale=np.pi/180),
        'grip':dict(field='data.position',names_field='data.name',order=['left_claw','right_claw'],size=2,scale=0.01),
        'velocity':dict(fields=['linear.x','linear.y','angular.z'],size=3),
    }
    return signal_value(msg,{'topic':key,**specs[key]})


def align(entries, targets, size, method, tolerance_s):
    from .bag import matched_indices
    result = np.full((len(targets),size),np.nan,dtype=np.float32)
    if entries:
        entries = sorted(entries,key=lambda e:e[0])
        times = np.asarray([e[0] for e in entries],dtype=np.int64)
        indices = matched_indices(times,targets,method,int(tolerance_s*1e9))
        good = indices >= 0
        result[good] = np.asarray([entries[i][1] for i in indices[good]])
    return result


def read_recording(path, schema, progress, calibration=None):
    from .ros1_reader import Ros1Reader
    tf = RecordedTF()
    samples = {key:[] for key in ('joints','arms','torso','grip_state','grip_action','velocity','odom_clock')}
    detections, calibrations, report = [], [], {'malformed_messages':{},'source_bag':str(path)}
    topics = {'/sensors_data_raw':('joints','joints'),'/kuavo_arm_traj':('arms','arms'),
              '/leju_claw_state':('grip_state','grip'),'/leju_claw_command':('grip_action','grip'),
              '/cmd_vel':('velocity','velocity')}
    with Ros1Reader(path) as reader:
        available = {c.topic for c in reader.connections}
        if '/joint_cmd' in available:
            torso_topic, torso_kind = '/joint_cmd','torso'
        elif '/lb_leg_traj' in available:
            torso_topic, torso_kind = '/lb_leg_traj','torso_trajectory'
        else:
            torso_topic = torso_kind = None
        report.update(torso_action_source=torso_topic,available_topics=sorted(available))
        if torso_topic:
            topics[torso_topic] = ('torso',torso_kind)
        tag_topic = '/rwh/tag_detections' if '/rwh/tag_detections' in available else '/tag_detections'
        wanted = set(topics) | {'/tf','/tf_static','/odom',tag_topic,'/dataset/base_pose_in_rack/status'}
        connections = [c for c in reader.connections if c.topic in wanted]
        total = max(1,sum(c.msgcount for c in connections))
        for index,(connection,timestamp,raw) in enumerate(reader.messages(connections=connections)):
            if index % 1000==0:
                progress(index/total*0.65,'원본 bag 전체: torso·마커·TF 읽기 (영상 재추출 없음)')
            msg = reader.deserialize(raw,connection.msgtype)
            topic = connection.topic
            if topic in ('/tf','/tf_static'):
                tf.add(msg,static=topic=='/tf_static')
            elif topic==tag_topic:
                detections.append((int(timestamp),compact_detections(msg)))
            elif topic=='/dataset/base_pose_in_rack/status':
                try:
                    status = json.loads(msg.data)
                    if status.get('schema')=='rack_relative_recording_v1' and status.get('rack_tags'):
                        calibrations.append(status['rack_tags'])
                except (ValueError,TypeError):
                    pass
            elif topic=='/odom':
                samples['odom_clock'].append((int(timestamp),[stamp_ns(msg.header.stamp)]))
            else:
                key,kind = topics[topic]
                try:
                    samples[key].append((int(timestamp),_numeric(msg,kind)))
                except (ValueError,AttributeError,IndexError):
                    report['malformed_messages'][topic] = report['malformed_messages'].get(topic,0)+1
                    size = {'joints':18,'arms':14,'torso':4,'grip_state':2,'grip_action':2,'velocity':3}[key]
                    samples[key].append((int(timestamp),np.full(size,np.nan)))
    tf.finalize()
    report['tf_errors'] = tf.errors
    if calibration is not None:
        recorded = calibration.get('rack_tags',calibration.get('tags',[]))
        calibrations = [recorded]+calibrations
    if not calibrations:
        report.update(rack_unavailable_reason=4,rack_tags=[],anchor_knots=[],
                      note='No recorded rack layout; base pose masked, never guessed from current RWH configuration.')
        return samples,tf,[],report
    selected = json.dumps(calibrations[0],sort_keys=True)
    if any(json.dumps(value,sort_keys=True)!=selected for value in calibrations):
        raise EditError('bag 안에서 랙 보정 배치가 바뀌었습니다. 구간 분리 또는 보정 설정 확인이 필요합니다.')
    tags = {int(t['id']):t for t in calibrations[0] if t.get('parent','rack')=='rack'}
    if not tags or any(not all(np.isfinite(t.get(axis,np.nan)) for axis in ('x','y')) for t in tags.values()):
        raise EditError('rack calibration의 rack 태그 id/x/y 좌표를 확인하세요.')
    observations, skipped = candidates(detections,tags,tf)
    knots = anchor_knots(observations)
    report.update(tag_topic=tag_topic,rack_tags=list(tags.values()),candidate_frames=len(observations),
                  rejected_observations=skipped,anchor_knots=knots,
                  reconstruction='robust_multi_frame_windows_then_time_interpolated_odom_anchors',
                  assumptions=['rack stationary within recording','odom continuous; no resets'],
                  source_codes={'0':'unavailable','1':'backfilled_before_first_anchor',
                                '2':'between_good_anchor_windows','3':'propagated_after_last_anchor'})
    if not knots:
        report['rack_unavailable_reason'] = 5
    return samples,tf,knots,report


class WholeBodySource(CatalogSource):
    def __init__(self, descriptor):
        super().__init__(descriptor['path'])
        directory = Path(descriptor['export_derivation']['path'])
        saved = json.loads((directory/'manifest.json').read_text())
        self.features = saved['features']
        self.arrays = {key:np.load(directory/relative,mmap_mode='r',allow_pickle=False)
                       for key,relative in saved['arrays'].items()}
        # Image paths remain rooted in the ORIGINAL catalog; nothing is copied.
        self.manifest = {**self.manifest,'features':self.features,
                         'arrays':{key:str(directory/value) for key,value in saved['arrays'].items()},
                         'time_mapping':str(directory/'time_mapping.npz'),'wholebody_report':str(directory/'report.json'),
                         'validity_masks':mask_mapping(self.features)}
        if saved.get('image_overrides'):
            self.manifest['images']=deepcopy(self.manifest['images'])
            for camera,overrides in saved['image_overrides'].items():
                for frame,path in overrides.items():
                    self.manifest['images'][camera][int(frame)]=path
        self.descriptor_value = descriptor


def prepare_project(project, job_root, options, progress):
    """Derive once per job; reuse on episode resume. Never change Store or source files."""
    schema = schema_name(options)
    if schema=='legacy':
        return project,options
    from .sources import open_source
    from .quality import resolve_quality, repair_index, corrected_row
    from .repositories import selected_project
    project = selected_project(project, options.get('_target',''), options.get('mode','new'))
    selection, approved = resolve_quality(project,options.get('quality'))
    repairs = repair_index(approved)
    result = deepcopy(selection)
    plan = compile_selection(result)
    used = {run['source_id'] for run in plan['runs']}
    for source in result['sources']:
        if source['source_id'] not in used:
            continue
        validate_source(source,schema)
        if source['kind']=='lerobot':
            continue
        original = open_source(source)
        path = Path(source['original_path'])
        if not path.is_file():
            raise EditError('원본 bag 경로가 없습니다: '+str(path))
        expected = source.get('fingerprint')
        actual = fingerprint(path)
        if expected and actual!=expected:
            raise EditError('원본 bag이 import 이후 바뀌었습니다: '+str(path))
        key = hashlib.sha256(json.dumps([actual,fingerprint(original.root),schema,
                                         options.get('rack_calibration'),approved],sort_keys=True).encode()
                             +Path(__file__).read_bytes()+Path(__file__).with_name('rack_reconstruction.py').read_bytes()
                             +Path(__file__).with_name('recorded_tf.py').read_bytes()
                             +Path(__file__).with_name('masking.py').read_bytes()).hexdigest()
        directory = Path(job_root).resolve()/'wholebody'/source['source_id']/key
        report_path = directory/'report.json'
        if not (directory/'manifest.json').exists():
            directory.mkdir(parents=True,exist_ok=True)
            samples,tf,knots,report = read_recording(path,schema,progress,options.get('rack_calibration'))
            n = original.manifest['episodes'][0]['num_frames']
            with np.load(original.root/original.manifest['time_mapping']) as mapping:
                times = {k:mapping[k] for k in mapping.files}
            targets = times['bag_time_ns']
            profile = original.manifest['profile']
            tolerance = profile.get('state_tolerance_s',0.1)
            command_age = profile.get('command_max_age_s',1.0)
            state = np.full((n,24),np.nan,dtype=np.float32)
            state[:,:18] = align(samples['joints'],targets,18,'nearest',tolerance)
            state[:,18:20] = align(samples['grip_state'],targets,2,'nearest',tolerance)
            # Map bag-receipt rows through odom header timestamps, preserving both clocks.
            # Keep int64 nanoseconds: float32 would lose seconds at Unix epoch scale.
            from .bag import matched_indices
            clock_entries = samples['odom_clock']
            query_times = targets.copy()
            if clock_entries:
                clock_t = np.asarray([e[0] for e in clock_entries],dtype=np.int64)
                indices = matched_indices(clock_t,targets,'nearest',int(tolerance*1e9))
                good = indices>=0
                query_times[good] = np.asarray([clock_entries[i][1][0] for i in indices[good]],dtype=np.int64)
                query_times[~good] = -1
            else:
                query_times[:] = -1
            pose,codes = reconstruct(tf,query_times,knots)
            state[:,20:24] = pose
            arrays = {'observation.state':state,'provenance.base_pose_source':codes[:,None]}
            if schema==FULL:
                action = np.column_stack([align(samples['torso'],targets,4,'previous',command_age),
                    align(samples['arms'],targets,14,'previous',command_age),
                    align(samples['grip_action'],targets,2,'previous',command_age),
                    align(samples['velocity'],targets,3,'previous',command_age)])
                arrays.update(action=action,**{'validity.action':np.isfinite(action)})
            else:
                arrays.update({k:np.asarray(v).copy() for k,v in original.arrays.items()
                               if k=='action' or k.startswith(('action.','validity.action','missing_reason.action'))})
            # Preserve explicitly approved old feature repairs, mapped by semantic group.
            image_overrides={}
            for (sid,episode,frame),fixes in repairs.items():
                if sid!=source['source_id']:
                    continue
                row = corrected_row(original,episode,frame,fixes,times)
                for fix in fixes:
                    feature = fix['feature']
                    if fix['decision']=='hold_image':
                        image_overrides.setdefault(feature,{})[str(frame)]=original.manifest['images'][feature][fix['left_frame']]
                    if feature=='observation.state':
                        state[frame,4:18] = row[feature]
                    elif schema==FULL and feature in ('action','action.gripper','action.base'):
                        start,end = {'action':(4,18),'action.gripper':(18,20),'action.base':(20,23)}[feature]
                        arrays['action'][frame,start:end] = row[feature]
            available = set(report['available_topics'])
            groups = [('observation.state',0,18,'joints','/sensors_data_raw','nearest',tolerance),
                      ('observation.state',18,20,'grip_state','/leju_claw_state','nearest',tolerance)]
            if schema==FULL:
                groups += [('action',0,4,'torso',report['torso_action_source'],'previous',command_age),
                           ('action',4,18,'arms','/kuavo_arm_traj','previous',command_age),
                           ('action',18,20,'grip_action','/leju_claw_command','previous',command_age),
                           ('action',20,23,'velocity','/cmd_vel','previous',command_age)]
            for feature in ('observation.state','action') if schema==FULL else ('observation.state',):
                raw = arrays[feature]
                filled,valid,reasons = encode_missing(raw)
                arrays[feature],arrays['validity.'+feature],arrays['missing_reason.'+feature] = filled,valid,reasons
            for feature,a,b,sample_key,topic,method,age in groups:
                reasons = arrays['missing_reason.'+feature][:,a:b]
                if topic not in available:
                    reasons[reasons!=0] = 1
                elif samples[sample_key]:
                    # Distinguish an invalid recorded value from no/stale sample.
                    entries = sorted(samples[sample_key],key=lambda e:e[0])
                    indices = matched_indices(np.asarray([e[0] for e in entries],dtype=np.int64),targets,
                                              method,int(age*1e9))
                    for frame in np.flatnonzero(indices>=0):
                        bad = ~np.isfinite(entries[indices[frame]][1])
                        reasons[frame,bad & (reasons[frame]!=0)] = 3
            base_reasons = arrays['missing_reason.observation.state'][:,20:24]
            base_reasons[base_reasons!=0] = report.get('rack_unavailable_reason',6)
            mapping = {}
            for index,(name,value) in enumerate(arrays.items()):
                filename = f'array_{index}.npy'
                np.save(directory/filename,value)
                mapping[name] = filename
            np.save(directory/'rack_source_codes.npy',codes)
            np.savez(directory/'time_mapping.npz',**times,**{'reconstructed_base/query_ros_ns':query_times})
            report.update(contract=contract(schema),original_fingerprint=actual,
                          invalid_state_frames=int((~arrays['validity.observation.state'].all(axis=1)).sum()))
            write_json(report_path,report)
            write_json(directory/'manifest.json',{'features':schema_features(original.features,schema),
                                                 'arrays':mapping,'image_overrides':image_overrides})
        source['export_derivation'] = {'schema':schema,'path':str(directory),'key':key}
        source['features'] = schema_features(original.features,schema)
        adapter = WholeBodySource(source)
        # Missing labels are explicit, not invented measured/command values.
        missing = []
        for run in plan['runs']:
            if run['source_id'] != source['source_id']:
                continue
            for feature in ('observation.state','action'):
                mask_key = 'validity.'+feature
                if mask_key not in adapter.arrays:
                    continue
                valid = adapter.arrays[mask_key][run['start_frame']:run['end_frame']]
                bad = np.flatnonzero(~valid.all(axis=1))
                if len(bad):
                    dimensions = np.flatnonzero(~valid.all(axis=0))
                    missing.append({'feature':feature,'frames':len(bad),
                                    'first_frame':int(bad[0])+run['start_frame'],
                                    'names':[adapter.features[feature]['names'][i] for i in dimensions]})
        if missing:
            write_json(directory/'missing_selected.json',missing)
            progress(0.0,'미기록 값은 유효성 0으로 저장: '+json.dumps(missing,ensure_ascii=False))
    new_options = {**options,'quality':None,'source_quality':approved}
    return result,new_options
