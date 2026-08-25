# -*- coding: utf-8 -*-
"""最小 NBT 读取器（level.dat 等小文件用）。

只读不写。大端 Java 版格式，支持 gzip / 未压缩两种存储。
列表/数组按 Python list 返回，复合标签按 dict 返回。
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
