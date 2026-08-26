# -*- coding: utf-8 -*-
"""目录项目版本选择：MC 版本 / 加载器 / 日期 / 文件名 / 下载量。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, ComboBox, FluentIcon as FIF, MessageBoxBase, PushButton,
    ScrollArea, SubtitleLabel, TransparentPushButton,
)

from ..pcl_chrome import Theme, ghost_btn_qss, row_qss
from ..ui_alive import guard
from ..widgets import fmt_downloads
from mclauncher.i18n import tr


class FilePickDialog(MessageBoxBase):
    PAGE = 80

    def __init__(self, backend, item: dict, kind: str, game_version: str = "", parent=None):
        super().__init__(parent)
        self.backend = backend
        self.item = dict(item or {})
        self.kind = kind
        self.chosen = None
        self._rows = []
        self._limit = self.PAGE
        self._dismissed = False
        self.viewLayout.addWidget(SubtitleLabel(self.item.get("name") or tr("选择版本"), self))
        hint = BodyLabel(tr("选择要安装的构建。可按游戏版本和加载器筛选。"), self)
        hint.setWordWrap(True)
        self.viewLayout.addWidget(hint)

        filt = QHBoxLayout()
        self.gv = ComboBox()
        self.gv.setFixedWidth(160)
        self.loader = ComboBox()
        self.loader.setFixedWidth(120)
        self.loader.addItems([tr("全部"), "Fabric", "Forge", "Quilt", "NeoForge"])
        filt.addWidget(BodyLabel("MC"))
        filt.addWidget(self.gv)
        filt.addWidget(BodyLabel(tr("加载器")))
        filt.addWidget(self.loader)
        filt.addStretch(1)
        host = QWidget(self)
        host.setLayout(filt)
        self.viewLayout.addWidget(host)

        # Mod 装进哪个实例 / 哪个版本（版本隔离时是各自的 mods 目录）
        self.target_box = None
        self.target_inst_box = None
        if kind == "mod":
            target = QHBoxLayout()
            target.addWidget(BodyLabel(tr("安装到")))
            self.target_inst_box = ComboBox()
            self.target_inst_box.setFixedWidth(140)
            for i in (backend.get_instances() or []):
                self.target_inst_box.addItem(i.get("name") or "?")
            cur_inst = self.item.get("instance") or ""
            if cur_inst:
                self.target_inst_box.setCurrentText(cur_inst)
            self.target_box = ComboBox()
            self.target_box.setFixedWidth(200)
            self._reload_targets()
            target.addWidget(self.target_inst_box)
            target.addWidget(self.target_box)
            target.addStretch(1)
            tip = BodyLabel(tr("开启版本隔离的版本会出现在这里"))
            tip.setStyleSheet("color: rgba(128,128,128,0.9); font-size: 11px;")
            target.addWidget(tip)
            thost = QWidget(self)
            thost.setLayout(target)
            self.viewLayout.addWidget(thost)
            self.target_inst_box.currentTextChanged.connect(lambda _t: self._reload_targets())

        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(320)
        scroll.setStyleSheet("ScrollArea { background: transparent; border: none; }")
        inner = QWidget()
        self.list_layout = QVBoxLayout(inner)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(0)
        scroll.setWidget(inner)
        self.viewLayout.addWidget(scroll)

        self.status = BodyLabel(tr("正在加载版本列表…"))
        self.viewLayout.addWidget(self.status)
        self.yesButton.setText(tr("安装所选"))
        self.cancelButton.setText(tr("取消"))
        latest = TransparentPushButton(FIF.DOWNLOAD, tr("安装最新"))
        latest.clicked.connect(self._latest)
        self.buttonLayout.insertWidget(0, latest, 1, Qt.AlignVCenter)
        self.widget.setMinimumWidth(640)

        extra = {
            "kind": kind,
            "source": self.item.get("source") or "",
            "slug": self.item.get("slug"),
            "id": self.item.get("id"),
            "name": self.item.get("name"),
            "game_version": "" if (not game_version or str(game_version).startswith(tr("全部"))) else game_version,
        }
        call_async = getattr(backend, "call_async", None)
        if callable(call_async):
            call_async(lambda e=extra: backend.list_catalog_files(e),
                       guard(self, self._on_ok), guard(self, self._on_err))
        else:
            try:
                self._on_ok(backend.list_catalog_files(extra))
            except Exception as exc:
                self._on_err(exc)

        self.gv.currentTextChanged.connect(self._on_filter)
        self.loader.currentTextChanged.connect(self._on_filter)

    def _reload_targets(self):
        if self.target_box is None or self.target_inst_box is None:
            return
        inst = self.target_inst_box.currentText() or ""
        try:
            rows = self.backend.get_mods_targets(inst) or []
        except Exception:
            rows = [{"label": tr("实例共享 mods 目录"), "value": ""}]
        self.target_box.blockSignals(True)
        self.target_box.clear()
        for r in rows:
            self.target_box.addItem(r.get("label") or "?")
        self.target_box.blockSignals(False)
        self._target_rows = rows

    def reject(self):
        self._dismissed = True
        super().reject()

    def accept(self):
        self._dismissed = True
        super().accept()

    def _on_err(self, err):
        self.status.setText(tr("加载失败: {0}").format(err))

    def _on_ok(self, rows):
        self._rows = list(rows or [])
        gvs = [tr("全部")]
        seen = set()
        for r in self._rows:
            for v in r.get("game_versions") or []:
                if v not in seen:
                    seen.add(v)
                    gvs.append(str(v))
        self.gv.blockSignals(True)
        self.gv.clear()
        self.gv.addItems(gvs)
        want = self.item.get("game_version") or ""
        if want and want in gvs:
            self.gv.setCurrentText(want)
        self.gv.blockSignals(False)
        self.status.setText(tr("{0} 个文件").format(len(self._rows)))
        self._limit = self.PAGE
        self._refill()

    def _on_filter(self, *_):
        self._limit = self.PAGE
        self._refill()

    def _matched(self):
        gv = self.gv.currentText()
        # 「全部」这个哨兵值要拿**原文**比，小写版只用来跟 loaders 匹配。
        # 以前统一 .lower() 后再和 tr("全部") 比：中文下 .lower() 恰好是恒等所以看不出问题，
        # 一旦切英文就是 "all" != "All"，加载器筛选被当成真实筛选条件，列表直接空掉。
        loader_text = self.loader.currentText()
        loader = loader_text.lower()
        all_label = tr("全部")
        out = []
        for row in self._rows:
            games = [str(x) for x in (row.get("game_versions") or [])]
            loaders = [str(x).lower() for x in (row.get("loaders") or [])]
            if gv and gv != all_label and games and gv not in games:
                continue
            if loader_text and loader_text != all_label and loaders and loader not in loaders:
                continue
            out.append(row)
        return out

    def _refill(self):
        while self.list_layout.count():
            it = self.list_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        matched = self._matched()
        shown = matched[: self._limit]
        for row in shown:
            self.list_layout.addWidget(self._row(row))
        if not shown:
            self.list_layout.addWidget(BodyLabel(tr("没有匹配的文件，试试放宽筛选。")))
        rest = len(matched) - len(shown)
        if rest > 0:
            more = PushButton(tr("加载更多（还有 {0}）").format(rest))
            more.clicked.connect(self._more)
            self.list_layout.addWidget(more)
        self.list_layout.addStretch(1)
        self.status.setText(tr("{0} 个匹配 / 共 {1} 个文件").format(len(matched), len(self._rows)))

    def _more(self):
        self._limit += self.PAGE
        self._refill()

    def _row(self, row: dict) -> QWidget:
        host = QFrame()
        host.setObjectName("pickRow")
        host.setStyleSheet(row_qss("pickRow"))
        lay = QHBoxLayout(host)
        lay.setContentsMargins(8, 8, 8, 8)
        info = QVBoxLayout()
        title = QLabel(row.get("version_number") or row.get("name") or row.get("filename") or "?")
        title.setStyleSheet(f"color: {Theme.title}; font-weight: 700; font-size: 13px; background: transparent;")
        meta = QLabel(
            f"{', '.join((row.get('game_versions') or [])[:4]) or '—'}  ·  "
            f"{', '.join(row.get('loaders') or []) or tr('任意')}  ·  "
            f"{row.get('date') or '—'}  ·  {fmt_downloads(row.get('downloads'))}  ·  "
            f"{row.get('release_type') or 'release'}"
        )
        meta.setStyleSheet(f"color: {Theme.muted}; font-size: 11px; background: transparent;")
        fn = QLabel(row.get("filename") or "")
        fn.setStyleSheet(f"color: {Theme.muted}; font-size: 11px; background: transparent;")
        info.addWidget(title)
        info.addWidget(meta)
        if row.get("filename"):
            info.addWidget(fn)
        lay.addLayout(info, 1)
        btn = PushButton(tr("安装"))
        # 定高不定宽：英文 “Install” 在 64px 里被裁边
        btn.setFixedHeight(28)
        btn.setMinimumWidth(64)
        btn.setStyleSheet(ghost_btn_qss())
        btn.clicked.connect(lambda _, r=row: self._pick(r))
        lay.addWidget(btn)
        return host

    def _pick(self, row):
        self.chosen = row
        self.accept()

    def _latest(self):
        self.chosen = {"latest": True}
        self.accept()

    def selected_extra(self) -> dict:
        extra = dict(self.item)
        if self.target_inst_box is not None and self.target_box is not None:
            inst = self.target_inst_box.currentText() or ""
            rows = getattr(self, "_target_rows", None) or []
            idx = self.target_box.currentIndex()
            vid = rows[idx].get("value", "") if 0 <= idx < len(rows) else ""
            if inst:
                extra["instance"] = inst
            extra["version"] = vid or ""
        if not self.chosen or self.chosen.get("latest"):
            return extra
        src = str(extra.get("source") or self.chosen.get("source") or "").lower()
        extra["source"] = extra.get("source") or self.chosen.get("source")
        if src.startswith("curse") or self.chosen.get("source") == "curseforge":
            extra["file_id"] = self.chosen.get("id")
            extra["version_id"] = self.chosen.get("id")
        else:
            extra["version_id"] = self.chosen.get("id")
        extra["filename"] = self.chosen.get("filename")
        return extra
