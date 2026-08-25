# -*- coding: utf-8 -*-
"""最小 NBT 读写器（level.dat 等小文件用）。

大端 Java 版格式，支持 gzip / 未压缩两种存储。

两套 API：
- loads / read_file：只读，丢类型（复合→dict、数值→int/float），看数据方便；
- loads_typed / dumps_typed：带类型无损往返，编辑 level.dat 用。
  带类型表示：标签 = (tag_type, value)；复合 value 是 {名字: 标签}；
  列表 value 是 (元素类型, [元素负载, ...])。
"""
from __future__ import annotations

import gzip
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

# 防御异常文件：level.dat 正常也就几 KB
MAX_NBT_BYTES = 8 * 1024 * 1024
_MAX_ELEMS = 1_000_000


class NBTError(Exception):
    """NBT 数据损坏或不支持。"""


class _Reader:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise NBTError("NBT 数据不完整")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, self.take(size))[0]

    def string(self) -> str:
        length = self.unpack(">H")
        return self.take(length).decode("utf-8", errors="replace")


def _read_payload(r: _Reader, tag: int):
    if tag == TAG_BYTE:
        return r.unpack(">b")
    if tag == TAG_SHORT:
        return r.unpack(">h")
    if tag == TAG_INT:
        return r.unpack(">i")
    if tag == TAG_LONG:
        return r.unpack(">q")
    if tag == TAG_FLOAT:
        return r.unpack(">f")
    if tag == TAG_DOUBLE:
        return r.unpack(">d")
    if tag == TAG_STRING:
        return r.string()
    if tag == TAG_BYTE_ARRAY:
        n = r.unpack(">i")
        if n < 0 or n > _MAX_ELEMS:
            raise NBTError(f"数组长度异常: {n}")
        return list(r.take(n))
    if tag == TAG_INT_ARRAY:
        n = r.unpack(">i")
        if n < 0 or n > _MAX_ELEMS:
            raise NBTError(f"数组长度异常: {n}")
        return list(struct.unpack(f">{n}i", r.take(4 * n)))
    if tag == TAG_LONG_ARRAY:
        n = r.unpack(">i")
        if n < 0 or n > _MAX_ELEMS:
            raise NBTError(f"数组长度异常: {n}")
        return list(struct.unpack(f">{n}q", r.take(8 * n)))
    if tag == TAG_LIST:
        elem_tag = r.unpack(">B")
        n = r.unpack(">i")
        if n < 0 or n > _MAX_ELEMS:
            raise NBTError(f"列表长度异常: {n}")
        return [_read_payload(r, elem_tag) for _ in range(n)]
    if tag == TAG_COMPOUND:
        out = {}
        while True:
            child = r.unpack(">B")
            if child == TAG_END:
                return out
            name = r.string()
            out[name] = _read_payload(r, child)
    raise NBTError(f"未知标签类型: {tag}")


def loads(data: bytes) -> dict:
    """解析 NBT 字节串（自动识别 gzip）。返回根复合标签内容。"""
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except OSError as e:
            raise NBTError(f"gzip 解压失败: {e}") from e
    if len(data) > MAX_NBT_BYTES:
        raise NBTError("NBT 数据过大")
    if not data:
        raise NBTError("NBT 数据为空")
    r = _Reader(data)
    tag = r.unpack(">B")
    if tag != TAG_COMPOUND:
        raise NBTError(f"根标签不是 Compound: {tag}")
    r.string()  # 根名（level.dat 里是空串）
    return _read_payload(r, TAG_COMPOUND)


def read_file(path) -> dict:
    p = Path(path)
    if p.stat().st_size > MAX_NBT_BYTES:
        raise NBTError("NBT 文件过大")
    return loads(p.read_bytes())


# ================================================================ 带类型读写


def _read_typed_payload(r: _Reader, tag: int):
    if tag == TAG_LIST:
        elem_tag = r.unpack(">B")
        n = r.unpack(">i")
        if n < 0 or n > _MAX_ELEMS:
            raise NBTError(f"列表长度异常: {n}")
        return (elem_tag, [_read_typed_payload(r, elem_tag) for _ in range(n)])
    if tag == TAG_COMPOUND:
        out = {}
        while True:
            child = r.unpack(">B")
            if child == TAG_END:
                return out
            name = r.string()
            out[name] = (child, _read_typed_payload(r, child))
    return _read_payload(r, tag)


def loads_typed(data: bytes) -> tuple:
    """解析成带类型树。返回 (根名, (TAG_COMPOUND, {...}))。"""
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except OSError as e:
            raise NBTError(f"gzip 解压失败: {e}") from e
    if len(data) > MAX_NBT_BYTES:
        raise NBTError("NBT 数据过大")
    if not data:
        raise NBTError("NBT 数据为空")
    r = _Reader(data)
    tag = r.unpack(">B")
    if tag != TAG_COMPOUND:
        raise NBTError(f"根标签不是 Compound: {tag}")
    root_name = r.string()
    return root_name, (TAG_COMPOUND, _read_typed_payload(r, TAG_COMPOUND))


_SCALAR_FMT = {
    TAG_BYTE: ">b", TAG_SHORT: ">h", TAG_INT: ">i", TAG_LONG: ">q",
    TAG_FLOAT: ">f", TAG_DOUBLE: ">d",
}


def _write_string(out: bytearray, s: str):
    raw = str(s).encode("utf-8")
    if len(raw) > 0xFFFF:
        raise NBTError("字符串过长")
    out += struct.pack(">H", len(raw))
    out += raw


def _write_typed_payload(out: bytearray, tag: int, value):
    fmt = _SCALAR_FMT.get(tag)
    if fmt:
        out += struct.pack(fmt, value)
        return
    if tag == TAG_STRING:
        _write_string(out, value)
        return
    if tag == TAG_BYTE_ARRAY:
        out += struct.pack(">i", len(value))
        out += bytes((b & 0xFF) for b in value)
        return
    if tag == TAG_INT_ARRAY:
        out += struct.pack(f">i{len(value)}i", len(value), *value)
        return
    if tag == TAG_LONG_ARRAY:
        out += struct.pack(f">i{len(value)}q", len(value), *value)
        return
    if tag == TAG_LIST:
        elem_tag, items = value
        out += struct.pack(">Bi", elem_tag, len(items))
        for item in items:
            _write_typed_payload(out, elem_tag, item)
        return
    if tag == TAG_COMPOUND:
        for name, (child_tag, child_value) in value.items():
            out += struct.pack(">B", child_tag)
            _write_string(out, name)
            _write_typed_payload(out, child_tag, child_value)
        out += struct.pack(">B", TAG_END)
        return
    raise NBTError(f"未知标签类型: {tag}")


def dumps_typed(root_name: str, root: tuple, compress: bool = True) -> bytes:
    """把带类型树编码回 NBT 字节串（compress=True 时 gzip，level.dat 默认压缩）。"""
    tag, value = root
    if tag != TAG_COMPOUND:
        raise NBTError("根标签必须是 Compound")
    out = bytearray()
    out += struct.pack(">B", TAG_COMPOUND)
    _write_string(out, root_name or "")
    _write_typed_payload(out, TAG_COMPOUND, value)
    data = bytes(out)
    return gzip.compress(data) if compress else data
