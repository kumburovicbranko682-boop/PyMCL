# -*- coding: utf-8 -*-
"""精简 NBT 读取器（Java 版，只读）。

够用为准：解析 level.dat / servers.dat 这类小文件成 Python 原生结构。
支持 gzip / zlib / 未压缩三种载荷。
"""
from __future__ import annotations

import gzip
import struct
import zlib

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


class NBTError(Exception):
    pass


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise NBTError("NBT 数据被截断")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, self.take(size))[0]

    def string(self) -> str:
        length = self.unpack(">H")
        return self.take(length).decode("utf-8", "replace")

    def payload(self, tag: int):
        if tag == TAG_BYTE:
            return self.unpack(">b")
        if tag == TAG_SHORT:
            return self.unpack(">h")
        if tag == TAG_INT:
            return self.unpack(">i")
        if tag == TAG_LONG:
            return self.unpack(">q")
        if tag == TAG_FLOAT:
            return self.unpack(">f")
        if tag == TAG_DOUBLE:
            return self.unpack(">d")
        if tag == TAG_BYTE_ARRAY:
            n = self.unpack(">i")
            return list(self.take(n))
        if tag == TAG_STRING:
            return self.string()
        if tag == TAG_LIST:
            item_tag = self.u8()
            n = self.unpack(">i")
            return [self.payload(item_tag) for _ in range(max(0, n))]
        if tag == TAG_COMPOUND:
            out = {}
            while True:
                child = self.u8()
                if child == TAG_END:
                    return out
                name = self.string()
                out[name] = self.payload(child)
        if tag == TAG_INT_ARRAY:
            n = self.unpack(">i")
            return [self.unpack(">i") for _ in range(max(0, n))]
        if tag == TAG_LONG_ARRAY:
            n = self.unpack(">i")
            return [self.unpack(">q") for _ in range(max(0, n))]
        raise NBTError(f"未知 NBT tag: {tag}")


def _decompress(data: bytes) -> bytes:
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    if data[:1] == b"\x78":
        try:
            return zlib.decompress(data)
        except zlib.error:
            pass
    return data


def loads(data: bytes) -> dict:
    """解析 NBT 字节流，返回 {根名: 内容}。根必须是 Compound。"""
    raw = _decompress(bytes(data or b""))
    reader = _Reader(raw)
    tag = reader.u8()
    if tag != TAG_COMPOUND:
        raise NBTError("NBT 根不是 Compound")
    name = reader.string()
    return {name: reader.payload(TAG_COMPOUND)}


def load_file(path) -> dict:
    with open(path, "rb") as f:
        return loads(f.read())
