# -*- coding: utf-8 -*-
"""极简 NBT（Java 版，大端）读写，够 servers.dat 用，支持全部标准 tag。

值的内存表示（保证 round-trip 无损）：
- 数值 tag (1-6)：(type, number)
- ByteArray (7)：(7, bytes)
- String (8)：(8, str)
- List (9)：(9, (elem_type, [raw_value, ...]))
- Compound (10)：(10, {name: (type, value), ...})
- IntArray (11) / LongArray (12)：(type, [int, ...])
"""
from __future__ import annotations

import gzip
import io
import struct
from pathlib import Path

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12

_NUM_FMT = {
    TAG_BYTE: ">b", TAG_SHORT: ">h", TAG_INT: ">i", TAG_LONG: ">q",
    TAG_FLOAT: ">f", TAG_DOUBLE: ">d",
}


class NBTError(Exception):
    pass


# ---------------------------------------------------------------- 读

def _read_string(f) -> str:
    (n,) = struct.unpack(">H", f.read(2))
    raw = f.read(n)
    if len(raw) != n:
        raise NBTError("NBT 字符串长度不足")
    return raw.decode("utf-8", errors="replace")


def _read_payload(f, tag_type: int):
    if tag_type in _NUM_FMT:
        fmt = _NUM_FMT[tag_type]
        size = struct.calcsize(fmt)
        data = f.read(size)
        if len(data) != size:
            raise NBTError("NBT 数据不完整")
        return struct.unpack(fmt, data)[0]
    if tag_type == TAG_BYTE_ARRAY:
        (n,) = struct.unpack(">i", f.read(4))
        return f.read(n)
    if tag_type == TAG_STRING:
        return _read_string(f)
    if tag_type == TAG_LIST:
        (elem_type,) = struct.unpack(">b", f.read(1))
        (n,) = struct.unpack(">i", f.read(4))
        items = [_read_payload(f, elem_type) for _ in range(max(0, n))]
        return (elem_type, items)
    if tag_type == TAG_COMPOUND:
        out = {}
        while True:
            head = f.read(1)
            if not head:
                raise NBTError("NBT compound 未正常结束")
            (child_type,) = struct.unpack(">b", head)
            if child_type == TAG_END:
                return out
            name = _read_string(f)
            out[name] = (child_type, _read_payload(f, child_type))
    if tag_type == TAG_INT_ARRAY:
        (n,) = struct.unpack(">i", f.read(4))
        return list(struct.unpack(f">{max(0, n)}i", f.read(4 * max(0, n))))
    if tag_type == TAG_LONG_ARRAY:
        (n,) = struct.unpack(">i", f.read(4))
        return list(struct.unpack(f">{max(0, n)}q", f.read(8 * max(0, n))))
    raise NBTError(f"未知 NBT tag 类型: {tag_type}")


def loads(data: bytes) -> tuple[str, dict]:
    """解析 NBT 字节（自动识别 gzip）。返回 (根名称, 根 compound)。"""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    f = io.BytesIO(data)
    (root_type,) = struct.unpack(">b", f.read(1))
    if root_type != TAG_COMPOUND:
        raise NBTError(f"NBT 根必须是 compound，实际 {root_type}")
    name = _read_string(f)
    return name, _read_payload(f, TAG_COMPOUND)


def load(path) -> tuple[str, dict]:
    return loads(Path(path).read_bytes())


# ---------------------------------------------------------------- 写

def _write_string(f, s: str):
    raw = str(s).encode("utf-8")
    f.write(struct.pack(">H", len(raw)))
    f.write(raw)


def _write_payload(f, tag_type: int, value):
    if tag_type in _NUM_FMT:
        f.write(struct.pack(_NUM_FMT[tag_type], value))
        return
    if tag_type == TAG_BYTE_ARRAY:
        f.write(struct.pack(">i", len(value)))
        f.write(bytes(value))
        return
    if tag_type == TAG_STRING:
        _write_string(f, value)
        return
    if tag_type == TAG_LIST:
        elem_type, items = value
        f.write(struct.pack(">b", elem_type))
        f.write(struct.pack(">i", len(items)))
        for item in items:
            _write_payload(f, elem_type, item)
        return
    if tag_type == TAG_COMPOUND:
        for name, (child_type, child_value) in value.items():
            f.write(struct.pack(">b", child_type))
            _write_string(f, name)
            _write_payload(f, child_type, child_value)
        f.write(struct.pack(">b", TAG_END))
        return
    if tag_type == TAG_INT_ARRAY:
        f.write(struct.pack(">i", len(value)))
        f.write(struct.pack(f">{len(value)}i", *value))
        return
    if tag_type == TAG_LONG_ARRAY:
        f.write(struct.pack(">i", len(value)))
        f.write(struct.pack(f">{len(value)}q", *value))
        return
    raise NBTError(f"未知 NBT tag 类型: {tag_type}")


def dumps(root: dict, name: str = "") -> bytes:
    """序列化根 compound 为未压缩 NBT 字节（servers.dat 就是未压缩的）。"""
    f = io.BytesIO()
    f.write(struct.pack(">b", TAG_COMPOUND))
    _write_string(f, name)
    _write_payload(f, TAG_COMPOUND, root)
    return f.getvalue()


def dump(path, root: dict, name: str = ""):
    Path(path).write_bytes(dumps(root, name))
