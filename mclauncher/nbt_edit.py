# -*- coding: utf-8 -*-
"""通用 NBT 树编辑（HMCL 世界管理「NBT 编辑」同款后端）。

在 nbt.py 无损类型读写之上提供 JSON 安全的树表示，Qt / RPC 前端都能直接吃：

  node := {"t": <标签号 1..12>, "v": <值>}
  - Byte/Short/Int/Long:  v = int
  - Float/Double:         v = float
  - String:               v = str
  - ByteArray/IntArray/LongArray: v = [int, ...]
  - List:     v = {"et": <元素标签号>, "items": [node, ...]}（空列表 et 可为 0）
  - Compound: v = {名字: node}

save_file 写盘前把原文件刷成 .pymcl_bak 备份（和 saves.edit_world 同一约定），
并按原文件的 gzip/未压缩格式原样写回。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import nbt
from .nbt import NBTError

# 只碰玩家数据类小文件；region/*.mca 是分块格式，不归这个编辑器管
ALLOWED_SUFFIXES = (".dat", ".dat_old", ".nbt")
BACKUP_SUFFIX = ".pymcl_bak"

TAG_LABELS = {
    nbt.TAG_BYTE: "Byte", nbt.TAG_SHORT: "Short", nbt.TAG_INT: "Int",
    nbt.TAG_LONG: "Long", nbt.TAG_FLOAT: "Float", nbt.TAG_DOUBLE: "Double",
    nbt.TAG_BYTE_ARRAY: "ByteArray", nbt.TAG_STRING: "String",
    nbt.TAG_LIST: "List", nbt.TAG_COMPOUND: "Compound",
    nbt.TAG_INT_ARRAY: "IntArray", nbt.TAG_LONG_ARRAY: "LongArray",
}

_INT_RANGES = {
    nbt.TAG_SHORT: (-(1 << 15), (1 << 15) - 1),
    nbt.TAG_INT: (-(1 << 31), (1 << 31) - 1),
    nbt.TAG_LONG: (-(1 << 63), (1 << 63) - 1),
}
_INT_TAGS = (nbt.TAG_BYTE, nbt.TAG_SHORT, nbt.TAG_INT, nbt.TAG_LONG)
_FLOAT_TAGS = (nbt.TAG_FLOAT, nbt.TAG_DOUBLE)
_ARRAY_TAGS = (nbt.TAG_BYTE_ARRAY, nbt.TAG_INT_ARRAY, nbt.TAG_LONG_ARRAY)
# 数组元素按对应标量校验（写入时 ByteArray 会 & 0xFF，所以放宽到 -128..255）
_ARRAY_ELEM = {
    nbt.TAG_BYTE_ARRAY: (-128, 255),
    nbt.TAG_INT_ARRAY: _INT_RANGES[nbt.TAG_INT],
    nbt.TAG_LONG_ARRAY: _INT_RANGES[nbt.TAG_LONG],
}


def _check_int(tag: int, value) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        raise NBTError(f"{TAG_LABELS.get(tag, tag)} 需要整数，收到 {value!r}")
    lo, hi = _INT_RANGES.get(tag, (-128, 127))
    if not (lo <= out <= hi):
        raise NBTError(f"{TAG_LABELS.get(tag, tag)} 取值范围 {lo}..{hi}，收到 {out}")
    return out


# ---------------------------------------------------------------- 树转换


def _to_json(tag: int, payload) -> dict:
    if tag == nbt.TAG_COMPOUND:
        return {"t": tag, "v": {name: _to_json(t, p) for name, (t, p) in payload.items()}}
    if tag == nbt.TAG_LIST:
        elem_tag, items = payload
        return {"t": tag, "v": {"et": elem_tag,
                                "items": [_to_json(elem_tag, p) for p in items]}}
    if tag in _ARRAY_TAGS:
        return {"t": tag, "v": [int(x) for x in payload]}
    return {"t": tag, "v": payload}


def _from_json(node) -> tuple:
    """JSON 节点 → (tag, typed payload)。带类型/范围校验，坏数据抛 NBTError。"""
    if not isinstance(node, dict) or "t" not in node:
        raise NBTError(f"节点格式不对: {node!r}")
    tag = node.get("t")
    value = node.get("v")
    if tag not in TAG_LABELS:
        raise NBTError(f"未知标签类型: {tag}")
    if tag in _INT_TAGS:
        return tag, _check_int(tag, value)
    if tag in _FLOAT_TAGS:
        try:
            return tag, float(value)
        except (TypeError, ValueError):
            raise NBTError(f"{TAG_LABELS[tag]} 需要数值，收到 {value!r}")
    if tag == nbt.TAG_STRING:
        return tag, str(value if value is not None else "")
    if tag in _ARRAY_TAGS:
        if not isinstance(value, (list, tuple)):
            raise NBTError(f"{TAG_LABELS[tag]} 需要整数列表")
        lo, hi = _ARRAY_ELEM[tag]
        out = []
        for x in value:
            try:
                n = int(x)
            except (TypeError, ValueError):
                raise NBTError(f"{TAG_LABELS[tag]} 元素需要整数，收到 {x!r}")
            if not (lo <= n <= hi):
                raise NBTError(f"{TAG_LABELS[tag]} 元素范围 {lo}..{hi}，收到 {n}")
            out.append(n)
        return tag, out
    if tag == nbt.TAG_LIST:
        if not isinstance(value, dict):
            raise NBTError("List 节点需要 {et, items}")
        items_in = value.get("items") or []
        elem_tag = value.get("et", nbt.TAG_END)
        if items_in and elem_tag == nbt.TAG_END:
            raise NBTError("非空 List 的元素类型不能是 End")
        if elem_tag != nbt.TAG_END and elem_tag not in TAG_LABELS:
            raise NBTError(f"List 元素类型未知: {elem_tag}")
        payloads = []
        for item in items_in:
            t, p = _from_json(item)
            if t != elem_tag:
                raise NBTError(
                    f"List 元素类型不一致: 期望 {TAG_LABELS.get(elem_tag, elem_tag)}"
                    f"，收到 {TAG_LABELS.get(t, t)}")
            payloads.append(p)
        return tag, (elem_tag, payloads)
    # Compound
    if not isinstance(value, dict):
        raise NBTError("Compound 节点需要 {名字: 节点}")
    out = {}
    for name, child in value.items():
        out[str(name)] = _from_json(child)
    return tag, out


def empty_node(tag: int, elem_tag: int = nbt.TAG_END) -> dict:
    """给「添加标签」用的该类型默认空节点。"""
    if tag in _INT_TAGS:
        return {"t": tag, "v": 0}
    if tag in _FLOAT_TAGS:
        return {"t": tag, "v": 0.0}
    if tag == nbt.TAG_STRING:
        return {"t": tag, "v": ""}
    if tag in _ARRAY_TAGS:
        return {"t": tag, "v": []}
    if tag == nbt.TAG_LIST:
        return {"t": tag, "v": {"et": elem_tag, "items": []}}
    if tag == nbt.TAG_COMPOUND:
        return {"t": tag, "v": {}}
    raise NBTError(f"未知标签类型: {tag}")


def parse_scalar(tag: int, text: str):
    """把用户输入的字符串按标签类型解析（编辑值对话框用）。"""
    text = str(text if text is not None else "")
    if tag in _INT_TAGS:
        return _check_int(tag, text.strip())
    if tag in _FLOAT_TAGS:
        try:
            return float(text.strip())
        except ValueError:
            raise NBTError(f"{TAG_LABELS[tag]} 需要数值，收到 {text!r}")
    if tag == nbt.TAG_STRING:
        return text
    raise NBTError(f"{TAG_LABELS.get(tag, tag)} 不是标量，不能直接输入")


def parse_array(tag: int, text: str) -> list[int]:
    """逗号/空格分隔的整数串 → 数组值（带范围校验）。"""
    if tag not in _ARRAY_TAGS:
        raise NBTError(f"{TAG_LABELS.get(tag, tag)} 不是数组")
    parts = [p for chunk in str(text or "").split(",") for p in chunk.split()]
    node = {"t": tag, "v": parts}
    return _from_json(node)[1]


def summary(node: dict) -> str:
    """节点值的单行摘要（树控件「值」列）。"""
    tag = node.get("t")
    value = node.get("v")
    if tag == nbt.TAG_COMPOUND:
        return f"{{{len(value or {})}}}"
    if tag == nbt.TAG_LIST:
        items = (value or {}).get("items") or []
        et = (value or {}).get("et", nbt.TAG_END)
        label = TAG_LABELS.get(et, "?") if et != nbt.TAG_END else ""
        return f"[{len(items)}] {label}".rstrip()
    if tag in _ARRAY_TAGS:
        n = len(value or [])
        head = ", ".join(str(x) for x in (value or [])[:8])
        return f"[{n}] {head}" + ("…" if n > 8 else "")
    if tag == nbt.TAG_STRING:
        return str(value)
    return str(value)


# ---------------------------------------------------------------- 文件读写


def _check_path(path) -> Path:
    p = Path(path)
    if p.suffix.lower() not in ALLOWED_SUFFIXES:
        raise NBTError(f"只支持 {'/'.join(ALLOWED_SUFFIXES)} 文件: {p.name}")
    return p


def load_file(path) -> dict:
    """读 NBT 文件 → {"path", "name", "root_name", "compressed", "root"}。"""
    p = _check_path(path)
    if not p.is_file():
        raise NBTError(f"文件不存在: {p}")
    if p.stat().st_size > nbt.MAX_NBT_BYTES:
        raise NBTError("NBT 文件过大")
    raw = p.read_bytes()
    compressed = raw[:2] == b"\x1f\x8b"
    root_name, (tag, payload) = nbt.loads_typed(raw)
    return {
        "path": str(p),
        "name": p.name,
        "root_name": root_name,
        "compressed": compressed,
        "root": _to_json(tag, payload),
    }


def save_file(path, tree: dict) -> str:
    """校验 JSON 树并写回文件。返回备份文件路径（原文件不存在则空串）。

    先在内存里整棵编码，全部通过才动盘上文件；写前刷新 .pymcl_bak。
    """
    p = _check_path(path)
    tree = tree or {}
    root = tree.get("root")
    if not isinstance(root, dict):
        raise NBTError("缺少 root 节点")
    tag, payload = _from_json(root)
    if tag != nbt.TAG_COMPOUND:
        raise NBTError("根标签必须是 Compound")
    data = nbt.dumps_typed(str(tree.get("root_name") or ""), (tag, payload),
                           compress=bool(tree.get("compressed", True)))
    backup = ""
    if p.is_file():
        bak = p.with_name(p.name + BACKUP_SUFFIX)
        shutil.copy2(p, bak)
        backup = str(bak)
    p.write_bytes(data)
    return backup
