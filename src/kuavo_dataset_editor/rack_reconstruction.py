"""Offline rack anchor selection from raw simultaneous tags, NOT online pose smoothing."""
import numpy as np
from .recorded_tf import stamp_ns


def compact_detections(message):
    """Do not retain every ROS pose covariance while scanning a long recording."""
    result = []
    for detection in message.detections:
        if len(detection.id)!=1:
            continue
        header = detection.pose.header if detection.pose.header.frame_id else message.header
        p = detection.pose.pose.pose.position
        result.append((stamp_ns(header.stamp),header.frame_id,int(detection.id[0]),(p.x,p.y,p.z)))
    return result


def fit_planar(local, observed):
    local, observed = np.asarray(local), np.asarray(observed)
    a,b = local.mean(axis=0), observed.mean(axis=0)
    cross = (local-a).T @ (observed-b)
    yaw = np.arctan2(cross[0,1]-cross[1,0], cross[0,0]+cross[1,1])
    c,s = np.cos(yaw), np.sin(yaw)
    rot = np.array([[c,-s],[s,c]])
    translation = b-rot@a
    rms = float(np.sqrt(np.mean(np.sum((local@rot.T+translation-observed)**2,axis=1))))
    return np.array([*translation,yaw]), rms


def candidates(detections, tags, tf, min_baseline_m=0.2, max_rms_m=0.05):
    result, skipped = [], {'insufficient_tags':0,'short_baseline':0,'missing_tf':0,'large_rms':0}
    for receipt, msg in detections:
        grouped = {}
        for timestamp,frame,tid,xyz in msg:
            if tid not in tags:
                continue
            if timestamp <= 0:
                continue
            try:
                point = tf.lookup('odom',frame,timestamp) @ np.array([*xyz,1])
            except (KeyError, ValueError):
                skipped['missing_tf'] += 1
                continue
            if np.isfinite(point).all():
                grouped.setdefault(timestamp,{})[tid] = point[:3]
        if not grouped:
            skipped['insufficient_tags'] += 1
        for timestamp, points in grouped.items():
            ids = sorted(points)
            if len(ids) < 2:
                skipped['insufficient_tags'] += 1
                continue
            local = np.asarray([[tags[i]['x'],tags[i]['y']] for i in ids],dtype=float)
            baseline = float(np.linalg.norm(local[:,None,:]-local[None,:,:],axis=2).max())
            if baseline < min_baseline_m:
                skipped['short_baseline'] += 1
                continue
            observed = np.asarray([points[i] for i in ids])
            anchor,rms = fit_planar(local, observed[:,:2])
            if not np.isfinite(anchor).all() or not np.isfinite(rms) or rms > max_rms_m:
                skipped['large_rms'] += 1
                continue
            result.append({'time_ns':int(timestamp),'receipt_ns':int(receipt),'ids':ids,
                           'anchor':anchor.tolist(),'rms_m':rms,'baseline_m':baseline})
    return result, skipped


def anchor_knots(observations, window_s=1.0, min_frames=3):
    """Robust windows; retain multiple time anchors to compensate slow odom drift."""
    bins = {}
    for item in observations:
        bins.setdefault(item['time_ns']//int(window_s*1e9),[]).append(item)
    knots = []
    for entries in bins.values():
        if len(entries) < min_frames:
            continue
        poses = np.asarray([entry['anchor'] for entry in entries])
        center_yaw = np.arctan2(np.sin(poses[:,2]).mean(),np.cos(poses[:,2]).mean())
        yaws = np.arctan2(np.sin(poses[:,2]-center_yaw),np.cos(poses[:,2]-center_yaw))
        center = np.median(poses[:,:2],axis=0)
        distances = np.linalg.norm(poses[:,:2]-center,axis=1)
        # Filter at the reconstruction level only; never alter/drop raw messages.
        pos_limit = max(0.03,float(np.median(distances))*3)
        yaw_limit = max(np.deg2rad(5),float(np.median(abs(yaws)))*3)
        keep = (distances <= pos_limit) & (abs(yaws) <= yaw_limit)
        chosen = [entry for entry, good in zip(entries,keep) if good]
        if len(chosen) < min_frames:
            continue
        accepted = poses[keep]
        xy = np.median(accepted[:,:2],axis=0)
        yaw = np.arctan2(np.sin(accepted[:,2]).mean(),np.cos(accepted[:,2]).mean())
        knots.append({'time_ns':int(np.median([e['time_ns'] for e in chosen])),
                      'anchor':[*xy.tolist(),float(yaw)],'frames':len(chosen),
                      'ids':sorted({i for e in chosen for i in e['ids']}),
                      'rms_m':float(np.median([e['rms_m'] for e in chosen]))})
    return sorted(knots,key=lambda knot:knot['time_ns'])


def reconstruct(tf, query_times, knots):
    values = np.full((len(query_times),4),np.nan,dtype=np.float32)
    sources = np.zeros(len(query_times),dtype=np.uint8)
    if not knots:
        return values,sources
    times = np.asarray([k['time_ns'] for k in knots],dtype=np.int64)
    poses = np.asarray([k['anchor'] for k in knots])
    anchors = np.column_stack([np.interp(query_times,times,poses[:,i]) for i in (0,1)])
    yaws = np.interp(query_times,times,np.unwrap(poses[:,2]))
    for index,timestamp in enumerate(query_times):
        try:
            base = tf.lookup('odom','base_link',int(timestamp))
            yaw = yaws[index]
            c,s = np.cos(yaw),np.sin(yaw)
            delta = base[:2,3]-anchors[index]
            xy = np.array([[c,s],[-s,c]])@delta
            heading = np.arctan2(base[1,0],base[0,0])-yaw
            values[index] = [*xy,np.sin(heading),np.cos(heading)]
            sources[index] = 1 if timestamp < times[0] else 3 if timestamp > times[-1] else 2
        except (KeyError, ValueError):
            pass
    return values,sources
