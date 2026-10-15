"""A 1.2 second ROS1 bag with embedded custom Kuavo message definitions."""
from copy import deepcopy
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image
from rosbags.rosbag1 import Writer
from rosbags.typesys import Stores, get_typestore, get_types_from_msg
from kuavo_dataset_editor.bag import import_bag, inspect_bag
from kuavo_dataset_editor.sources import CatalogSource, fingerprint
from kuavo_dataset_editor.ros1_reader import recorded_types


def test_legacy_lowercase_constants_are_not_wire_fields():
    parsed = recorded_types('int8 kError = -1\nint8 kMoving = 1\nint8[] state\n', 'kuavo_msgs/msg/test')
    constants, wire = parsed['kuavo_msgs/msg/test']
    assert constants == [('kError', 'int8', -1), ('kMoving', 'int8', 1)]
    assert [name for name, _ in wire] == ['state']


def test_ros1_custom_messages_camera_and_missing_tag(tmp_path):
    store = get_typestore(Stores.ROS1_NOETIC)
    store.register(get_types_from_msg("float64[] joint_q\n", "kuavo_msgs/msg/jointData"))
    store.register(get_types_from_msg("std_msgs/Header header\nkuavo_msgs/jointData joint_data\n", "kuavo_msgs/msg/sensorsData"))
    profile = json.loads((Path(__file__).parents[1] / "src/kuavo_dataset_editor/profiles/kuavo_5w.json").read_text())
    profile["fps"] = 10
    types = store.types
    bag = tmp_path / "fixture.bag"
    image_buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (80, 160, 200)).save(image_buffer, format="JPEG")
    with Writer(bag) as writer:
        connections = {}
        for topic in profile["cameras"].values():
            connections[topic] = writer.add_connection(topic, "sensor_msgs/msg/CompressedImage", typestore=store)
        connections["/sensors_data_raw"] = writer.add_connection("/sensors_data_raw", "kuavo_msgs/msg/sensorsData", typestore=store)
        connections["/kuavo_arm_traj"] = writer.add_connection("/kuavo_arm_traj", "sensor_msgs/msg/JointState", typestore=store)
        for frame in range(12):
            stamp = 1_700_000_000_000_000_000+frame*100_000_000
            ros_time = types["builtin_interfaces/msg/Time"](sec=stamp//1_000_000_000, nanosec=stamp%1_000_000_000)
            header = types["std_msgs/msg/Header"](seq=frame, stamp=ros_time, frame_id="camera")
            image = types["sensor_msgs/msg/CompressedImage"](header, "jpeg", np.frombuffer(image_buffer.getvalue(), dtype=np.uint8))
            sensor = types["kuavo_msgs/msg/sensorsData"](header, types["kuavo_msgs/msg/jointData"](np.arange(18, dtype=np.float64)))
            joint = types["sensor_msgs/msg/JointState"](header, [], np.full(14, 90., dtype=np.float64), np.array([], dtype=np.float64), np.array([], dtype=np.float64))
            for topic, connection in connections.items():
                msg = sensor if topic == "/sensors_data_raw" else joint if topic == "/kuavo_arm_traj" else image
                writer.write(connection, stamp, store.serialize_ros1(msg, connection.msgtype))
    before = fingerprint(bag)
    assert inspect_bag(bag)["duration_s"] > 1
    desc = import_bag(bag, tmp_path / "catalog", "bag-source", profile, lambda *_: None)
    source = CatalogSource(tmp_path / "catalog")
    assert desc["episodes"][0]["num_frames"] == 12
    row = source.row(0, 3)
    assert np.allclose(row["observation.state"], np.arange(4, 18))
    assert np.allclose(row["action"], np.pi/2)
    assert row["observation.images.head"].shape == (48, 64, 3)
    assert "observation.gripper" in desc["report"]["omitted_optional_features"]
    assert not any(desc["report"]["missing_samples"].values())
    assert fingerprint(bag) == before
