"""ROS1 reader handling legal legacy lowercase constants without disabling MD5.

Some Kuavo message definitions use `int8 kError = -1`. rosbags' generic parser
currently classifies that as a wire field, whereas ROS1 treats it as a constant.
Normalize names only while parsing, then restore their exact original spelling
before computing the recorded ROS1 digest. No bag bytes or field values change.
"""
import re
from pathlib import Path
from rosbags.rosbag1 import Reader
from rosbags.typesys import Stores, get_typestore, get_types_from_msg


def recorded_types(text, typename):
    aliases = {}
    pattern = re.compile(r"^([A-Za-z][\w/\[\]]*)\s+([A-Za-z_]\w*)\s*=\s*(.*)$")
    lines = []
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if match and not match[2].isupper():
            alias = f"KUAVO_READER_CONST_{len(aliases)}"
            while alias in text or alias in aliases:
                alias += "_X"
            aliases[alias] = match[2]
            line = f"{match[1]} {alias}={match[3]}"
        lines.append(line)
    result = get_types_from_msg("\n".join(lines)+"\n", typename)
    for name, (constants, wire_fields) in result.items():
        result[name] = ([(aliases.get(k, k), kind, value) for k, kind, value in constants], wire_fields)
    return result


class Ros1Reader:
    def __init__(self, path):
        self.reader = Reader(Path(path))
        self.typestore = get_typestore(Stores.EMPTY)

    def __enter__(self):
        self.reader.open()
        try:
            definitions = {}
            for connection in self.reader.connections:
                parsed = recorded_types(connection.msgdef.data, connection.msgtype)
                for name, definition in parsed.items():
                    if name in definitions and definitions[name] != definition:
                        raise ValueError(f"한 bag에 서로 다른 message definition이 있습니다: {name}")
                    definitions[name] = definition
            self.typestore.register(definitions)
            for connection in self.reader.connections:
                generated = self.typestore.generate_msgdef(connection.msgtype)[1]
                if generated != connection.digest:
                    raise ValueError(f"기록된 message definition/MD5 불일치: {connection.msgtype}: {generated} != {connection.digest}")
        except BaseException:
            self.reader.close()
            raise
        return self

    def __exit__(self, *_):
        self.reader.close()

    def __getattr__(self, name):
        return getattr(self.reader, name)

    def deserialize(self, raw, typename):
        return self.typestore.deserialize_ros1(raw, typename)
