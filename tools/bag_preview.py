#!/usr/bin/env python3
"""Read RGB topics into an H.264 preview. Never publishes ROS messages.

Runs locally with rosbags or on an existing ROS1 PC using rosbag. No package
installation or ROS master is necessary when the appropriate reader is present.
"""
import argparse
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

import av
import cv2
import numpy as np

TOPICS = ["/head_camera/color/image_raw/compressed",
          "/left_wrist_camera/color/image_raw/compressed",
          "/right_wrist_camera/color/image_raw/compressed"]
NAMES = ["HEAD", "LEFT WRIST", "RIGHT WRIST"]


def messages(path, topics):
    try:
        import rosbag
    except ImportError:
        from kuavo_dataset_editor.ros1_reader import Ros1Reader
        with Ros1Reader(path) as reader:
            yield (None, reader.start_time/1e9, (reader.end_time-1)/1e9)
            connections = [c for c in reader.connections if c.topic in topics]
            for connection, stamp, raw in reader.messages(connections=connections):
                yield connection.topic, stamp/1e9, reader.deserialize(raw, connection.msgtype)
    else:
        with rosbag.Bag(str(path), "r") as bag:
            yield (None, bag.get_start_time(), bag.get_end_time())
            for topic, msg, stamp in bag.read_messages(topics=topics):
                yield topic, stamp.to_sec(), msg


def draw_view(canvas, image, rect, label, age):
    x, y, width, height = rect
    if image is not None and age <= 0.25:
        h, w = image.shape[:2]
        scale = min(width/w, height/h)
        w, h = int(w*scale), int(h*scale)
        resized = cv2.resize(image, (w, h))
        left, top = x+(width-w)//2, y+(height-h)//2
        canvas[top:top+h, left:left+w] = resized
    else:
        cv2.putText(canvas, "NO FRAME", (x+15, y+height//2), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (130, 140, 160), 1)
    cv2.rectangle(canvas, (x, y), (x+width, y+25), (20, 25, 30), -1)
    cv2.putText(canvas, label, (x+10, y+18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (220, 230, 240), 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bag")
    parser.add_argument("output")
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--head-only", action="store_true")
    args = parser.parse_args()
    target = Path(args.output).resolve()
    if target.exists():
        raise SystemExit("Existing preview is not overwritten. Choose a new output path.")
    target.parent.mkdir(parents=True, exist_ok=True)
    selected = TOPICS[:1] if args.head_only else TOPICS
    stream = messages(args.bag, selected)
    _, origin, bag_end = next(stream)
    begin = origin+args.start
    end = min(bag_end, begin+args.duration) if args.duration is not None else bag_end
    if not origin <= begin < end or args.fps <= 0:
        raise SystemExit("Invalid preview interval or FPS")
    width, height = (640, 396) if args.head_only else (960, 396)
    boxes = [(0, 0, 640, 360)] if args.head_only else [(0, 0, 640, 360), (640, 0, 320, 180), (640, 180, 320, 180)]
    latest = {t: (None, -1e30) for t in selected}
    frames, next_time, last_report = 0, begin, -1
    partial = target.with_name(target.stem+".partial.mp4")
    if partial.exists():
        raise SystemExit("Partial preview exists; choose a new output path.")
    with av.open(str(partial), "w") as output:
        video = output.add_stream("libx264", rate=args.fps)
        video.width, video.height = width, height
        video.pix_fmt = "yuv420p"
        video.options = {"crf": "23", "preset": "veryfast"}

        def emit(t):
            nonlocal frames, last_report
            canvas = np.zeros((height, width, 3), dtype=np.uint8)
            for i, topic in enumerate(selected):
                image, stamp = latest[topic]
                draw_view(canvas, image, boxes[i], NAMES[i], t-stamp)
            kst = datetime.fromtimestamp(t, timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S KST")
            cv2.putText(canvas, "%s | source +%.2fs" % (kst, t-origin), (12, 384), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (180, 220, 210), 1)
            frame = av.VideoFrame.from_ndarray(canvas, format="bgr24")
            frame.pts, frame.time_base = frames, Fraction(1, args.fps)
            for packet in video.encode(frame):
                output.mux(packet)
            frames += 1
            percent = int((t-begin)/(end-begin)*100)
            if percent >= last_report+10:
                print("Preview %d%% (%d frames)" % (percent, frames), flush=True)
                last_report = percent

        for topic, stamp, msg in stream:
            if stamp < begin:
                continue
            while next_time < min(stamp, end):
                emit(next_time)
                next_time = begin+frames/args.fps
            if stamp >= end:
                break
            image = cv2.imdecode(np.frombuffer(msg.data, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is not None:
                latest[topic] = (image, stamp)
        while next_time < end:
            emit(next_time)
            next_time = begin+frames/args.fps
        for packet in video.encode():
            output.mux(packet)
    partial.rename(target)
    print("COMPLETE %s | %d frames | %.2fs" % (target, frames, frames/args.fps), flush=True)


if __name__ == "__main__":
    main()
