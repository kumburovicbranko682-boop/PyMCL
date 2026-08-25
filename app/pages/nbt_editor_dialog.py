# -*- coding: utf-8 -*-
"""NBT 树编辑器（对标 HMCL 世界管理的「NBT 编辑」）。

双击编辑标量/数组值；右键菜单增删标签、重命名；保存前自动刷 .pymcl_bak 备份。
模型就是 backend.read_nbt_file 返回的 JSON 树，树控件条目直接持有节点引用，
编辑时同步改模型，保存时整棵交给 backend.write_nbt_file 校验写盘。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidgetItem, QWidget
from qfluentwidgets import (
    Action, CaptionLabel, LineEdit, MessageBox, MessageBoxBase, RoundMenu,
    SubtitleLabel, TreeWidget,
)

from mclauncher import nbt_edit
from mclauncher.i18n import tr
from mclauncher.nbt import (
    NBTError, TAG_BYTE_ARRAY, TAG_COMPOUND, TAG_END, TAG_INT_ARRAY, TAG_LIST,
    TAG_LONG_ARRAY, TAG_STRING,
)

_SCALARS = (1, 2, 3, 4, 5, 6, 8)  # Byte..Double + String
_ARRAYS = (TAG_BYTE_ARRAY, TAG_INT_ARRAY, TAG_LONG_ARRAY)


class _Item(QTreeWidgetItem):
    """树条目直接持有 JSON 节点引用。

    不能走 setData(Qt.UserRole)：PySide6 会把 dict 转成 QVariantMap 拷贝，
    编辑改的是副本，保存时模型里还是旧值。
    """

    def __init__(self, texts, node: dict):
        super().__init__(texts)
        self.node = node


class _ValueDialog(MessageBoxBase):
    """单个值输入框：validate 里按标签类型解析，解析失败不关窗。"""

    def __init__(self, title: str, tag: int, initial: str, parent=None,
                 is_array: bool = False, name_mode: bool = False,
                 taken: set | None = None):
        super().__init__(parent)
        self.tag = tag
        self.is_array = is_array
        self.name_mode = name_mode
        self.taken = taken or set()
        self.value = None
        self.viewLayout.addWidget(SubtitleLabel(title, self))
        if not name_mode:
            hint = tr("整数用逗号或空格分隔") if is_array else \
                f"{nbt_edit.TAG_LABELS.get(tag, '?')}"
            self.viewLayout.addWidget(CaptionLabel(hint, self))
        self.edit = LineEdit(self)
        self.edit.setText(initial)
        self.edit.setClearButtonEnabled(True)
        self.viewLayout.addWidget(self.edit)
        self.err = CaptionLabel("", self)
        self.err.setStyleSheet("color: #d13438;")
        self.err.hide()
        self.viewLayout.addWidget(self.err)
        self.yesButton.setText(tr("确定"))
        self.cancelButton.setText(tr("取消"))
        self.widget.setMinimumWidth(380)

    def validate(self) -> bool:
        text = self.edit.text()
        try:
            if self.name_mode:
                name = text.strip()
                if not name:
                    raise NBTError(tr("名字不能为空"))
                if name in self.taken:
                    raise NBTError(tr("同级已存在「{name}」").format(name=name))
                self.value = name
            elif self.is_array:
                self.value = nbt_edit.parse_array(self.tag, text)
            else:
                self.value = nbt_edit.parse_scalar(self.tag, text)
        except NBTError as e:
            self.err.setText(str(e))
            self.err.show()
            return False
        return True


class NbtEditorDialog(MessageBoxBase):
    def __init__(self, backend, path: str, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.path = path
        self.doc = backend.read_nbt_file(path)  # NBTError 由调用方兜
        self.backup_path = ""
        self.viewLayout.addWidget(SubtitleLabel(
            tr("NBT 编辑 · {name}").format(name=self.doc.get("name") or ""), self))
        cap = CaptionLabel(tr("双击编辑值；右键增删标签。保存前会自动备份为 .pymcl_bak"), self)
        self.viewLayout.addWidget(cap)
        self.tree = TreeWidget(self)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels([tr("名称"), tr("类型"), tr("值")])
        self.tree.setColumnWidth(0, 240)
        self.tree.setColumnWidth(1, 100)
        self.tree.setMinimumSize(620, 380)
        self.tree.setBorderVisible(True)
        self.tree.setBorderRadius(8)
        self.viewLayout.addWidget(self.tree)
        self.yesButton.setText(tr("保存"))
        self.cancelButton.setText(tr("取消"))
        self.widget.setMinimumWidth(700)
        self.tree.itemDoubleClicked.connect(self._edit_item)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self._populate()

    # ------------------------------------------------------------ 构树

    def _populate(self):
        self.tree.clear()
        root = self.doc["root"]
        name = self.doc.get("root_name") or self.doc.get("name") or "root"
        item = self._make_item(name, root)
        self.tree.addTopLevelItem(item)
        item.setExpanded(True)

    def _make_item(self, label: str, node: dict) -> QTreeWidgetItem:
        item = _Item([label, nbt_edit.TAG_LABELS.get(node.get("t"), "?"),
                      nbt_edit.summary(node)], node)
        tag = node.get("t")
        if tag == TAG_COMPOUND:
            for child_name, child in (node.get("v") or {}).items():
                item.addChild(self._make_item(str(child_name), child))
        elif tag == TAG_LIST:
            for i, child in enumerate((node.get("v") or {}).get("items") or []):
                item.addChild(self._make_item(f"[{i}]", child))
        return item

    @staticmethod
    def _node(item) -> dict:
        return getattr(item, "node", None) or {}

    def _refresh_row(self, item):
        node = self._node(item)
        item.setText(1, nbt_edit.TAG_LABELS.get(node.get("t"), "?"))
        item.setText(2, nbt_edit.summary(node))

    def _renumber_list(self, parent_item):
        for i in range(parent_item.childCount()):
            parent_item.child(i).setText(0, f"[{i}]")

    # ------------------------------------------------------------ 编辑

    def _edit_item(self, item, _col=0):
        node = self._node(item)
        tag = node.get("t")
        if tag in _SCALARS:
            cur = "" if node.get("v") is None else str(node.get("v"))
            dlg = _ValueDialog(tr("编辑 {name}").format(name=item.text(0)),
                               tag, cur, self.window())
            if dlg.exec():
                node["v"] = dlg.value
                self._refresh_row(item)
        elif tag in _ARRAYS:
            cur = ", ".join(str(x) for x in (node.get("v") or []))
            dlg = _ValueDialog(tr("编辑 {name}").format(name=item.text(0)),
                               tag, cur, self.window(), is_array=True)
            if dlg.exec():
                node["v"] = dlg.value
                self._refresh_row(item)

    def _rename_item(self, item):
        parent = item.parent()
        if parent is None or self._node(parent).get("t") != TAG_COMPOUND:
            return
        parent_v = self._node(parent).get("v") or {}
        old = item.text(0)
        dlg = _ValueDialog(tr("重命名 {name}").format(name=old), TAG_STRING, old,
                           self.window(), name_mode=True,
                           taken=set(parent_v) - {old})
        if not dlg.exec():
            return
        new = dlg.value
        if new == old:
            return
        # 原地换键并保持顺序：dict 重建
        self._node(parent)["v"] = {
            (new if k == old else k): v for k, v in parent_v.items()}
        item.setText(0, new)

    def _delete_item(self, item):
        parent = item.parent()
        if parent is None:
            return
        pnode = self._node(parent)
        if pnode.get("t") == TAG_COMPOUND:
            (pnode.get("v") or {}).pop(item.text(0), None)
        elif pnode.get("t") == TAG_LIST:
            idx = parent.indexOfChild(item)
            items = (pnode.get("v") or {}).get("items") or []
            if 0 <= idx < len(items):
                items.pop(idx)
        parent.removeChild(item)
        if pnode.get("t") == TAG_LIST:
            self._renumber_list(parent)
        self._refresh_row(parent)

    def _add_child(self, item, tag: int):
        node = self._node(item)
        if node.get("t") == TAG_COMPOUND:
            children = node.setdefault("v", {})
            dlg = _ValueDialog(tr("新标签名（{kind}）").format(
                kind=nbt_edit.TAG_LABELS.get(tag, "?")), TAG_STRING, "",
                self.window(), name_mode=True, taken=set(children))
            if not dlg.exec():
                return
            child = nbt_edit.empty_node(tag)
            children[dlg.value] = child
            child_item = self._make_item(dlg.value, child)
        elif node.get("t") == TAG_LIST:
            v = node.setdefault("v", {"et": TAG_END, "items": []})
            if not v.get("items") and v.get("et", TAG_END) == TAG_END:
                v["et"] = tag
            if tag != v.get("et"):
                MessageBox(tr("类型不符"),
                           tr("这个列表只能装 {kind} 元素").format(
                               kind=nbt_edit.TAG_LABELS.get(v.get("et"), "?")),
                           self.window()).exec()
                return
            child = nbt_edit.empty_node(tag)
            v.setdefault("items", []).append(child)
            child_item = self._make_item(f"[{len(v['items']) - 1}]", child)
        else:
            return
        item.addChild(child_item)
        item.setExpanded(True)
        self._refresh_row(item)
        # 标量顺手打开值编辑，省一次右键
        if tag in _SCALARS or tag in _ARRAYS:
            self._edit_item(child_item)

    # ------------------------------------------------------------ 菜单

    def _menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        node = self._node(item)
        tag = node.get("t")
        menu = RoundMenu(parent=self.tree)
        if tag in _SCALARS or tag in _ARRAYS:
            menu.addAction(Action(tr("编辑值"), triggered=lambda: self._edit_item(item)))
        if tag == TAG_COMPOUND:
            sub = RoundMenu(tr("添加子标签"), menu)
            for t in sorted(nbt_edit.TAG_LABELS):
                sub.addAction(Action(nbt_edit.TAG_LABELS[t],
                                     triggered=lambda _=False, t=t: self._add_child(item, t)))
            menu.addMenu(sub)
        elif tag == TAG_LIST:
            v = node.get("v") or {}
            et = v.get("et", TAG_END)
            if et != TAG_END or v.get("items"):
                menu.addAction(Action(
                    tr("添加元素（{kind}）").format(
                        kind=nbt_edit.TAG_LABELS.get(et, "?")),
                    triggered=lambda: self._add_child(item, et)))
            else:
                sub = RoundMenu(tr("添加元素"), menu)
                for t in sorted(nbt_edit.TAG_LABELS):
                    sub.addAction(Action(nbt_edit.TAG_LABELS[t],
                                         triggered=lambda _=False, t=t: self._add_child(item, t)))
                menu.addMenu(sub)
        parent = item.parent()
        if parent is not None:
            if self._node(parent).get("t") == TAG_COMPOUND:
                menu.addAction(Action(tr("重命名"), triggered=lambda: self._rename_item(item)))
            menu.addSeparator()
            menu.addAction(Action(tr("删除"), triggered=lambda: self._delete_item(item)))
        if menu.actions():
            menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------ 保存

    def validate(self) -> bool:
        try:
            self.backup_path = self.backend.write_nbt_file(self.path, self.doc)
        except Exception as e:
            MessageBox(tr("保存失败"), str(e), self.window()).exec()
            return False
        return True
