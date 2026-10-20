"""Small ROS-free recorded TF reader. Never substitutes latest TF for image time."""
from collections import defaultdict
import numpy as np


def stamp_ns(stamp):
    return int(stamp.sec)*1_000_000_000 + int(stamp.nanosec)


def rotation(q):
    q = np.asarray(q, dtype=np.float64)
    norm = np.linalg.norm(q)
    if not np.isfinite(q).all() or norm < 1e-10:
        raise ValueError('invalid TF quaternion')
    x,y,z,w = q/norm
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


def inverse(matrix):
    result = np.eye(4)
    result[:3,:3] = matrix[:3,:3].T
    result[:3,3] = -result[:3,:3] @ matrix[:3,3]
    return result


def matrix(xyz, q):
    result = np.eye(4)
    result[:3,:3], result[:3,3] = rotation(q), xyz
    if not np.isfinite(result).all():
        raise ValueError('non-finite TF')
    return result


class RecordedTF:
    def __init__(self, max_gap_s=0.25):
        self.max_gap_ns = int(max_gap_s*1e9)
        self.samples, self.parents, self.static = defaultdict(list), {}, {}
        self.errors = {}

    def add(self, message, static=False):
        for transform in message.transforms:
            parent = transform.header.frame_id.lstrip('/')
            child = transform.child_frame_id.lstrip('/')
            if child in self.parents and self.parents[child] != parent:
                self.errors[child] = 'TF parent changed; ambiguous recorded frame'
                continue
            self.parents[child] = parent
            t,q = transform.transform.translation, transform.transform.rotation
            xyz, quat = [t.x,t.y,t.z], [q.x,q.y,q.z,q.w]
            if static:
                try:
                    value = matrix(xyz, quat)
                except ValueError:
                    self.errors[child] = 'invalid static TF'
                    continue
                if child in self.static and not np.allclose(self.static[child], value, atol=1e-7):
                    self.errors[child] = 'static TF changed; ambiguous recorded frame'
                    continue
                self.static[child] = value
            else:
                self.samples[child].append((stamp_ns(transform.header.stamp), xyz, quat))

    def finalize(self):
        self.dynamic = {}
        for child, samples in self.samples.items():
            unique = {t:(xyz,q) for t,xyz,q in samples if t > 0}
            times = np.asarray(sorted(unique), dtype=np.int64)
            if len(times):
                self.dynamic[child] = (times, np.asarray([unique[t][0] for t in times]),
                                       np.asarray([unique[t][1] for t in times]))
        self.samples.clear()

    def edge(self, child, timestamp):
        if child in self.errors:
            raise ValueError(self.errors[child])
        if child in self.static:
            return self.static[child]
        times, xyz, quats = self.dynamic[child]
        right = int(np.searchsorted(times, timestamp))
        if right < len(times) and times[right] == timestamp:
            return matrix(xyz[right], quats[right])
        if right == 0 or right == len(times):
            raise ValueError('TF time outside recorded range')
        left = right-1
        if times[right]-times[left] > self.max_gap_ns:
            raise ValueError('TF interpolation gap too long')
        alpha = (timestamp-times[left])/(times[right]-times[left])
        qa, qb = quats[left]/np.linalg.norm(quats[left]), quats[right]/np.linalg.norm(quats[right])
        dot = np.dot(qa,qb)
        if dot < 0:
            qb,dot = -qb,-dot
        if dot > 0.9995:
            q = qa*(1-alpha)+qb*alpha
        else:
            theta = np.arccos(np.clip(dot,-1,1))
            q = (np.sin((1-alpha)*theta)*qa+np.sin(alpha*theta)*qb)/np.sin(theta)
        return matrix(xyz[left]*(1-alpha)+xyz[right]*alpha, q)

    def lookup(self, target, source, timestamp):
        target, source = target.lstrip('/'), source.lstrip('/')
        source_path, cursor, value = {source:np.eye(4)}, source, np.eye(4)
        for _ in range(100):
            if cursor == target:
                return value
            if cursor not in self.parents:
                break
            value = self.edge(cursor,timestamp) @ value
            cursor = self.parents[cursor]
            if cursor in source_path:
                raise ValueError('TF cycle')
            source_path[cursor] = value
        cursor, value = target, np.eye(4)
        for _ in range(100):
            if cursor in source_path:
                return inverse(value) @ source_path[cursor]
            if cursor not in self.parents:
                break
            value = self.edge(cursor,timestamp) @ value
            cursor = self.parents[cursor]
        raise ValueError(f'no recorded TF path {target} <- {source}')
