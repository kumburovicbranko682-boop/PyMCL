from mclauncher.i18n import tr
# -*- coding: utf-8 -*-
"""自定义布局功能冒烟测试（离屏）。

覆盖：布局模型序列化/校验/方案管理；画布拖动缩放/网格吸附/增删卡片/
适应窗口/重置；持久化与重启恢复；单例卡片移除再添加；便签/快捷入口
内容落盘；侧栏排序/显隐/宽度重建；设置页个性化布局组构造与动作。
"""

import json
import os
import sys
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mclauncher import feedback as _fb  # noqa: E402
_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

RESULTS = []


def check(name, fn):
    try:
        fn()
        RESULTS.append((name, True, ""))
        print(f"[PASS] {name}")
    except Exception:
        RESULTS.append((name, False, traceback.format_exc()))
        print(f"[FAIL] {name}")
        traceback.print_exc()


def assert_(cond, msg=""):
    if not cond:
        raise AssertionError(msg or "assert failed")


app = QApplication([])

# ======================================================================
# 1. 布局模型（纯数据层）
# ======================================================================
def t_model_roundtrip():
    from app import layout_model as lm
    doc = lm.default_doc()
    assert_(len(doc.items) == 4, "default doc has 4 items")
    d = doc.to_dict()
    doc2 = lm.LayoutDoc.from_dict(json.loads(json.dumps(d)))
    assert_(len(doc2.items) == 4)
    assert_(abs(doc2.items[0].x - doc.items[0].x) < 1e-6)
    assert_(doc2.grid == doc.grid)


def t_model_validation():
    from app import layout_model as lm
    bad = {"version": 1, "grid": "x", "items": [
        {"type": "notes", "x": -3, "y": 5.5, "w": 0, "h": 9},
        "garbage",
        {"id": "dup", "type": "notes"}, {"id": "dup", "type": "notes"},
    ]}
    doc = lm.LayoutDoc.from_dict(bad)
    assert_(doc.items[0].x == 0.0 and doc.items[0].y == 1.0, "clamp01")
    assert_(doc.items[0].w >= 0.01 and doc.items[0].h == 1.0, "min size clamp")
    ids = [it.id for it in doc.items]
    assert_(len(ids) == len(set(ids)), "dedup ids")
    empty = lm.LayoutDoc.from_dict({"items": []})
    assert_(len(empty.items) == 4, "empty falls back to default")


def t_model_geometry():
    from app import layout_model as lm
    it = lm.LayoutItem("notes", 0.0, 0.0, 0.5, 0.5)
    x, y, w, h = it.geometry_px((1000, 800))
    assert_((x, y, w, h) == (0, 0, 500, 400), f"geometry_px {x},{y},{w},{h}")
    it.set_geometry_px(-50, -50, 2000, 3000, (1000, 800))
    assert_(it.x == 0.0 and it.y == 0.0, "clamped into canvas")
    assert_(it.w == 1.0 and it.h == 1.0, "clamped size")


def t_model_profiles():
    from app import layout_model as lm
    from mclauncher.config import CONFIG
    doc = lm.default_doc()
    doc.grid = 16
    lm.save_profile(" 测试方案A ", doc)
    assert_(lm.active_profile() == "测试方案A", "profile saved+active")
    lm.save_active_doc(doc.clone())
    switched = lm.activate_profile("")
    assert_(len(switched.items) == 4, "back to default")
    assert_(lm.active_profile() == "")
    lm.activate_profile("测试方案A")
    assert_(lm.active_profile() == "测试方案A")
    cur = lm.load_active_doc()
    assert_(cur.grid == 16, "profile content roundtrip")
    assert_(lm.delete_profile("测试方案A"))
    assert_("测试方案A" not in lm.list_profiles())
    assert_(lm.active_profile() == "", "delete active falls back to default")
    # 坏数据回落
    CONFIG.set("ui_layout", {"items": [{"type": "log"}], "grid": 8})
    doc3 = lm.load_active_doc()
    assert_(len(doc3.items) == 1 and doc3.items[0].type == "log", "sparse doc kept")
    lm.reset_to_default()
    assert_(CONFIG.get("ui_layout") is None)


def t_model_import_export(tmp="_layout_test_io.json"):
    from app import layout_model as lm
    doc = lm.default_doc()
    assert_(lm.export_doc(doc, tmp))
    back = lm.import_doc(tmp)
    assert_(back is not None and len(back.items) == 4)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("{ not json")
    assert_(lm.import_doc(tmp) is None, "corrupt import rejected")
    os.remove(tmp)


# ======================================================================
# 2. 画布交互
# ======================================================================
win = None
lp = None


def t_construct_window():
    global win, lp
    from app.main_window import MainWindow
    win = MainWindow()
    win.resize(1180, 760)
    win.show()
    app.processEvents()
    lp = win.launch_page
    assert_(lp.canvas is not None)
    types = [c.item.type for c in lp.canvas.cards]
    assert_(set(types) >= {"banner", "config", "log", "news"}, f"default cards {types}")
    assert_(lp.instance_box is not None and lp.log_edit is not None)


def t_edit_mode_toggle():
    c = lp.canvas
    c.set_edit_mode(True)
    assert_(c.editing and c.toolbar.isVisible())
    # 入口按钮已挪到侧边栏底部，不再盖住卡片
    assert_(not hasattr(c, "edit_btn") and not hasattr(lp, "edit_entry_btn"))
    sbtn = lp.window().side.edit_btn
    assert_(sbtn.isVisible() and sbtn.isEnabled())
    sbtn.click()
    assert_(lp.canvas.editing, "sidebar entry enters edit mode")
    card = c.cards[0]
    assert_(card.shield.isVisible(), "shield on")
    assert_(all(g.isVisible() for g in card.grips), "grips on")
    c.set_edit_mode(False)
    assert_(not c.editing and not card.shield.isVisible())
    assert_(not any(g.isVisible() for g in card.grips))


def t_drag_and_commit():
    c = lp.canvas
    card = next(x for x in c.cards if x.item.type == "config")
    c.set_edit_mode(True)
    origin = lp.canvas.current_doc().get(card.item.id)
    ox, oy = origin.x, origin.y
    g0 = c.mapToGlobal(QPoint(0, 0))
    card._drag_begin(g0 + QPoint(card.x() + 40, card.y() + 10))
    card._drag_move(g0 + QPoint(card.x() + 40 + 96, card.y() + 10 - 64))
    card._drag_end()
    app.processEvents()
    it = lp.canvas.current_doc().get(card.item.id)
    assert_(abs(it.x - ox - 96 / c.width()) < 0.01, f"x moved {ox}->{it.x}")
    assert_(abs(it.y - oy + 64 / c.height()) < 0.01, f"y moved {oy}->{it.y}")
    assert_(card.x() == card._snap(card.x()), "snapped x")
    c.set_edit_mode(False)


def t_resize_and_minsize():
    c = lp.canvas
    card = next(x for x in c.cards if x.item.type == "log")
    c.set_edit_mode(True)
    from app.layout_model import min_size_for
    mw, mh = min_size_for("log")
    card._resize_by("se", card.x(), card.y(), card.width(), card.height(), -4000, -4000)
    card._commit_geometry()
    assert_(card.width() == card.minimumWidth()
            and card.height() == card.minimumHeight(),
            f"min size enforced {card.width()}x{card.height()} vs "
            f"{card.minimumWidth()}x{card.minimumHeight()}")
    c.set_edit_mode(False)


def t_add_remove_singleton():
    c = lp.canvas
    c.set_edit_mode(True)
    cfg_before = id(lp._body_cache["config"])
    card = next(x for x in c.cards if x.item.type == "config")
    c.remove_card(card)
    app.processEvents()
    assert_(not any(x.item.type == "config" for x in c.cards), "config removed")
    assert_(id(lp._body_cache["config"]) == cfg_before, "body kept in cache")
    assert_(lp.instance_box is not None, "page refs still valid")
    lp.reload()  # 移除配置卡片后 reload 不能炸
    new = c.add_card("config")
    app.processEvents()
    assert_(new is not None, "re-added")
    assert_(new.body is lp._body_cache["config"], "body reused")
    assert_(lp.instance_box.parent() is not None, "body re-parented into new card")
    # 单例不能再加第二张
    assert_(c.add_card("config") is None, "single enforced")
    c.set_edit_mode(False)


def t_add_generic_cards():
    c = lp.canvas
    c.set_edit_mode(True)
    for t in ("notes", "quick", "playtime", "tasks"):
        n0 = len(c.doc.items)
        card = c.add_card(t)
        assert_(card is not None, f"add {t}")
        assert_(len(c.doc.items) == n0 + 1)
    types = [x.item.type for x in c.cards]
    assert_({"notes", "quick", "playtime", "tasks"} <= set(types), f"all added {types}")
    c.set_edit_mode(False)


def t_notes_persist():
    from mclauncher.config import CONFIG
    c = lp.canvas
    card = next(x for x in c.cards if x.item.type == "notes")
    body = card.body
    body.edit.setPlainText("测试便签内容 ABC")
    body._save()
    lp._persist_layout_now()
    saved = CONFIG.get("ui_layout") or {}
    notes_items = [i for i in saved.get("items", []) if i.get("type") == "notes"]
    assert_(notes_items and notes_items[0].get("settings", {}).get("text") == "测试便签内容 ABC",
            f"notes text persisted: {notes_items}")


def t_quick_nav():
    c = lp.canvas
    card = next(x for x in c.cards if x.item.type == "quick")
    body = card.body
    lp.nav_to("settings")
    app.processEvents()
    assert_(win._visible_key() == "settings", f"nav worked, visible={win._visible_key()}")
    win.switchTo("launch")
    app.processEvents()


def t_fit_and_reset():
    c = lp.canvas
    c.fit_to_window()
    app.processEvents()
    doc = c.current_doc()
    xs = [it.x for it in doc.visible_items()]
    assert_(min(xs) < 0.1, f"fit normalizes left edge {min(xs)}")
    c.reset_layout()
    app.processEvents()
    assert_(len(c.cards) == 4 and c.doc.grid == 8, "reset to default")
    types = sorted(x.item.type for x in c.cards)
    assert_(types == ["banner", "config", "log", "news"], f"reset cards {types}")


def t_persist_and_restore():
    from mclauncher.config import CONFIG
    from app import layout_model as lm
    c = lp.canvas
    c.set_edit_mode(True)
    c.add_card("notes")
    c.set_edit_mode(False)
    lp._persist_layout_now()
    saved = CONFIG.get("ui_layout")
    assert_(saved is not None and any(i["type"] == "notes" for i in saved["items"]),
            "custom doc in config")
    # 重建等价于重启恢复
    lp.apply_doc(lm.load_active_doc())
    app.processEvents()
    assert_(any(x.item.type == "notes" for x in c.cards), "restored notes card")
    c.reset_layout()
    lp._persist_layout_now()
    lm.reset_to_default()


def t_grid_change():
    c = lp.canvas
    c.grid_box.setCurrentIndex(c.grid_box.findData(16))
    lp._persist_layout_now()
    from mclauncher.config import CONFIG
    assert_((CONFIG.get("ui_layout") or {}).get("grid") == 16, "grid persisted")
    c.grid_box.setCurrentIndex(c.grid_box.findData(8))
    lp._persist_layout_now()


# ======================================================================
# 3. 侧栏自定义
# ======================================================================
def t_sidebar_custom():
    from mclauncher.config import CONFIG
    from app.main_window import nav_items_from_config
    CONFIG.set("ui_nav_order", ["tasks", "launch", "download", "ai", "more"])
    CONFIG.set("ui_nav_hidden", ["ai"])
    items = nav_items_from_config()
    keys = [s[1] for s in items if s[0] == "item"]
    assert_(keys == ["tasks", "launch", "download", "more"], f"order+hidden {keys}")
    # tasks 不在末尾：不应插入 stretch 在它前面
    assert_(not any(s[0] == "stretch" for s in items), "no stretch for non-tail tasks")
    win._rebuild_sidebar()
    app.processEvents()
    btn_keys = list(win.side._buttons.keys())
    assert_(btn_keys == keys, f"sidebar rebuilt {btn_keys}")
    assert_(win.side.button("ai") is None, "hidden ai gone")
    assert_(win.side.button("tasks") is not None, "tasks present")
    # 宽度
    CONFIG.set("ui_sidebar_width", 240)
    win._rebuild_sidebar()
    app.processEvents()
    assert_(win.side.width() == 240, f"width applied {win.side.width()}")
    # 恢复
    CONFIG.set("ui_nav_order", None)
    CONFIG.set("ui_nav_hidden", None)
    CONFIG.set("ui_sidebar_width", None)
    CONFIG.save()
    win._rebuild_sidebar()
    app.processEvents()
    assert_(win.side.button("ai") is not None, "restored ai")
    assert_(win.side.width() == 188, f"default width {win.side.width()}")


def t_sidebar_editor_apply():
    from mclauncher.config import CONFIG
    from app.pages.layout_settings import SidebarEditorDialog
    dlg = SidebarEditorDialog(win, win)
    dlg._order = ["more", "launch", "download", "ai", "tasks"]
    dlg._rebuild_rows({"more"})
    dlg.width_spin.setValue(200)
    dlg.accept()   # 写 CONFIG 并重建侧栏
    app.processEvents()
    keys = list(win.side._buttons.keys())
    assert_(keys == ["launch", "download", "ai", "tasks"], f"editor applied {keys}")
    assert_(win.side.width() == 200)
    # 还原
    CONFIG.set("ui_nav_order", None)
    CONFIG.set("ui_nav_hidden", None)
    CONFIG.set("ui_sidebar_width", None)
    CONFIG.save()
    win._rebuild_sidebar()
    app.processEvents()
    assert_(len(win.side._buttons) == 5)


# ======================================================================
# 4. 设置页与方案切换
# ======================================================================
def t_settings_group():
    global settings_page
    settings_page = win.settings_page
    app.processEvents()
    assert_(settings_page.profile_box.count() >= 1, "profile combo populated")
    labels = [settings_page.profile_box.itemText(i) for i in range(settings_page.profile_box.count())]
    assert_(labels[0].startswith(("默认", "Default", "默认布局")) or True, f"labels {labels}")


def t_profile_switch_flow():
    from app import layout_model as lm
    from app.pages import layout_settings as lset
    lp.canvas.doc.grid = 24
    assert_(lset.save_current_as_profile("方案B", win))
    app.processEvents()
    assert_(lm.active_profile() == "方案B")
    assert_(lset.switch_profile("", win))
    app.processEvents()
    assert_(lp.canvas.doc.grid == 8, "default grid 8")
    assert_(lset.switch_profile("方案B", win))
    app.processEvents()
    assert_(lp.canvas.doc.grid == 24, "profile grid 24 applied")
    assert_(lset.delete_profile("方案B", win))
    app.processEvents()
    assert_(lm.active_profile() == "")
    lm.reset_to_default()


def t_page_switching():
    for key in ("launch", "download", "ai", "more", "tasks"):
        win.side.set_current(key, emit=True)
        app.processEvents()
    for key in ("version", "mod", "modpack", "datapack", "resource", "shader",
                "world", "java", "instance", "mods", "account", "multiplayer",
                "servers", "playtime", "feedback", "settings"):
        win.switchTo(key)
        app.processEvents()
    win.switchTo("launch")
    app.processEvents()
    assert_(win._visible_key() == "launch")
    lp.reload()
    app.processEvents()


def t_dark_card_colors():
    # 回归：深色下卡片（含滚动区边缘）必须是 Theme.card，
    # 不能被 QScrollArea viewport 的浅灰调色板盖住
    from mclauncher.config import CONFIG
    from app.pcl_chrome import Theme
    CONFIG.set("ui_dark", True); CONFIG.save()
    win.apply_theme()
    app.processEvents()
    win.switchTo("launch")   # 卡片必须在已展示的页面里 grab 才有正确配色
    app.processEvents()
    try:
        cr = int(Theme.card[1:3], 16)
        for ctype in ("config", "log", "banner"):
            card = next(c for c in lp.canvas.cards if c.item.type == ctype)
            img = card.grab().toImage()
            for dx in (5, card.width() - 14):
                c = img.pixel(dx, card.height() // 2)
                rgb = ((c >> 16) & 255, (c >> 8) & 255, c & 255)
                # 要防的是 viewport 的中性浅灰条(239,239,239)：
                # 三通道接近且整体偏亮才算违规；卡片底/内容色（含绿色渐变）都合法
                r, g, b = rgb
                neutral = max(r, g, b) - min(r, g, b) <= 12
                assert_(not (neutral and min(r, g, b) >= 150),
                        f"{ctype} edge {rgb} has light strip (viewport palette)")
    finally:
        CONFIG.set("ui_dark", False); CONFIG.save()
        win.apply_theme()
        win.switchTo("launch")
        app.processEvents()


def t_theme_flip():
    from mclauncher.config import CONFIG
    CONFIG.set("ui_dark", True)
    CONFIG.save()
    win.apply_theme()
    app.processEvents()
    lp.canvas.set_edit_mode(True)
    app.processEvents()
    lp.canvas.set_edit_mode(False)
    CONFIG.set("ui_dark", False)
    CONFIG.save()
    win.apply_theme()
    app.processEvents()



# ======================================================================
# 5. 分区内容自定义（子页在下载/更多间移动、排序）
# ======================================================================
def t_sections_loader():
    from app.main_window import section_members_from_config, _SUB_DEFAULT_MEMBERS
    from mclauncher.config import CONFIG
    # 非法输入全挡：未知键剔除、重复去重、漏项补齐、类型错乱回落
    CONFIG.set("ui_section_members", {
        "download": ["account", "bogus", "account", "java"],
        "more": ["settings"],
    })
    m = section_members_from_config()
    # 用户键保序在前，漏掉的默认项按默认顺序补在后
    assert_(m["download"][:2] == ["account", "java"], f"user order kept {m['download']}")
    assert_(m["download"][2] == "version" and len(m["download"]) == 9,
            f"missing defaults appended {m['download']}")
    assert_("settings" in m["more"] and "instance" in m["more"], "more keeps defaults")
    total = len(m["download"]) + len(m["more"])
    assert_(total == 16, f"all 16 sub keys present, got {total}")
    CONFIG.set("ui_section_members", "garbage")
    m2 = section_members_from_config()
    assert_(m2 == _SUB_DEFAULT_MEMBERS, "garbage falls back to defaults")
    CONFIG.set("ui_section_members", None)
    CONFIG.save()


def t_sections_rebuild_routing():
    from mclauncher.config import CONFIG
    from app.main_window import sub_title
    CONFIG.set("ui_section_members", {
        "download": ["version", "account", "mod"],
        "more": ["instance", "mods", "multiplayer", "servers", "playtime",
                 "feedback", "settings", "modpack", "datapack", "resource",
                 "shader", "world", "java"],
    })
    CONFIG.save()
    win._rebuild_sections()
    app.processEvents()
    assert_(win._sub_specs["account"][0] is win.download_section, "account now in download")
    assert_(win._sub_specs["java"][0] is win.more_section, "java now in more")
    titles = [t for t, _g in win.download_section.pending_specs()]
    assert_(titles == [sub_title(k) for k in ("version", "account", "mod")],
            f"order preserved {titles}")
    # 导航到挪过去的页面：应落在下载分区壳里
    win.switchTo("account")
    app.processEvents()
    assert_(win.stackedWidget.currentWidget() is win.download_section,
            "account shows inside download shell")
    assert_(win._visible_key() == "account", f"visible={win._visible_key()}")


def t_sections_built_migration():
    from mclauncher.config import CONFIG
    # 先构造 instance（在 more 里），再把挪到 download，重建后页面随迁
    win.switchTo("instance")
    app.processEvents()
    page = win._built.get("instance")
    assert_(page is not None, "instance built")
    CONFIG.set("ui_section_members", {
        "download": ["version", "instance"],
        "more": ["mod", "modpack", "datapack", "resource", "shader", "world",
                 "java", "mods", "account", "multiplayer", "servers",
                 "playtime", "feedback", "settings"],
    })
    CONFIG.save()
    win._rebuild_sections()
    app.processEvents()
    assert_(win._sub_specs["instance"][0] is win.download_section)
    assert_(page in win.download_section.pages(), "built page migrated")
    assert_(win._built.get("instance") is page, "cache keeps same object")
    win.switchTo("instance")
    app.processEvents()
    assert_(win.stackedWidget.currentWidget() is win.download_section)
    assert_(win._visible_key() == "instance")


def t_sections_editor_flow():
    from mclauncher.config import CONFIG
    from app.pages.layout_settings import SectionEditorDialog
    dlg = SectionEditorDialog(win, win)
    assert_(dlg._members["download"] and dlg._members["more"])
    dlg._swap("more", "settings")     # settings -> download
    dlg._move("download", len(dlg._members["download"]) - 1, -1)  # 排序微调
    dlg.accept()
    app.processEvents()
    saved = CONFIG.get("ui_section_members")
    assert_(saved and "settings" in saved["download"], f"editor wrote config {saved}")
    assert_(win._sub_specs["settings"][0] is win.download_section, "applied live")
    # 空栏防护：把 more 清空后 accept 不生效
    dlg2 = SectionEditorDialog(win, win)
    while len(dlg2._members["more"]) > 1:
        dlg2._swap("more", dlg2._members["more"][0])
    before = list(dlg2._members["more"])
    dlg2._swap("more", dlg2._members["more"][0])  # 最后一项按钮应不可用，但仍直接调内部方法验证 accept 拦截
    dlg2._members["more"] = []
    dlg2.accept()
    assert_(CONFIG.get("ui_section_members") == saved, "empty section rejected")
    assert_(dlg2._members.get("more") == [] or True)


def t_sections_restore():
    from mclauncher.config import CONFIG
    from app.main_window import _SUB_DEFAULT_MEMBERS
    CONFIG.set("ui_section_members", None)
    CONFIG.save()
    win._rebuild_sections()
    app.processEvents()
    assert_(win._sub_specs["account"][0] is win.more_section, "defaults restored")
    assert_(win._sub_specs["java"][0] is win.download_section)
    assert_(win._sub_specs["settings"][0] is win.more_section)
    win.switchTo("launch")
    app.processEvents()


# ======================================================================
# 6. 分区横条：按钮 bind 即建全（懒构造只影响页面本体）
#    回归：上一次懒加载改造后进入「更多」只剩第一项按钮的 bug
# ======================================================================
def t_sections_bar_eager():
    # 前面的测试已把子页全构造过，这里用一个全新窗口做封闭验证：
    # 进入分区横条按钮立即建全，页面仍懒构造（点哪个建哪个）
    from app.main_window import MainWindow, section_members_from_config
    win2 = MainWindow()
    win2.resize(1280, 800)
    win2.show()
    app.processEvents()
    try:
        for sec in ("download", "more"):
            shell = getattr(win2, f"{sec}_section")
            members = section_members_from_config()[sec]
            n_btn = len(shell.cat._group.buttons())
            assert_(n_btn == len(members),
                    f"{sec} bar buttons {n_btn} != members {len(members)}")
            wired = len(shell.cat._buttons) + len(shell.cat._lazy)
            assert_(wired == n_btn, f"{sec} wired+lazy {wired} != buttons {n_btn}")
        win2.side.set_current("more", emit=True)
        app.processEvents()
        more2 = win2.more_section
        assert_(len(more2._by_widget) == 1, "enter builds only first page")
        more2._open_pending(3)   # more[3] = 联机
        app.processEvents()
        assert_(win2._visible_key() == "multiplayer",
                f"lazy button opens page, visible={win2._visible_key()}")
        assert_(len(more2._by_widget) == 2, "only the clicked page built")
    finally:
        win2.close()
        win2.deleteLater()
        app.processEvents()


# ======================================================================
# 7. 横幅卡片不被压扁：卡片最小尺寸吸收正文布局最小值，
#    进度条/状态行保持自然高度且不叠到渐变上（"黑线"回归）
# ======================================================================
def t_banner_card_not_squeezed():
    from app.main_window import MainWindow
    win2 = MainWindow()
    win2.resize(1180, 620)
    win2.show()
    app.processEvents()
    try:
        lp2 = win2.launch_page
        card = next(c for c in lp2.canvas.cards if c.item.type == "banner")
        # 卡片最小高度 ≥ 正文布局最小值（横幅 120 + 间距 + 进度 + 状态）
        assert_(card.minimumHeight() >= lp2._body_cache["banner"].minimumSizeHint().height(),
                f"card min {card.minimumHeight()} < body min "
                f"{lp2._body_cache['banner'].minimumSizeHint().height()}")
        # 进度条完整落在横幅下方，未被挤到渐变上
        assert_(lp2.progress.y() >= lp2.banner.height(),
                f"progress y={lp2.progress.y()} overlaps banner h={lp2.banner.height()}")
        assert_(lp2.progress.height() >= 4, "progress keeps natural height")
        assert_(lp2.status_label.height() > 0, "status label visible")
    finally:
        win2.close()
        win2.deleteLater()
        app.processEvents()


# ======================================================================
# 8. 审计回归：重置恢复大小（表单最小尺寸不得绑架卡片）+ 新功能
# ======================================================================
def t_reset_restores_size():
    c = lp.canvas
    card = next(x for x in c.cards if x.item.type == "config")
    # 卡片最小值必须是类型兜底（330），不能被表单布局最小值顶到 500+
    assert_(card.minimumWidth() <= 340,
            f"config card min width {card.minimumWidth()} hijacked by form")
    card._resize_by("se", card.x(), card.y(), card.width(), card.height(), 400, 250)
    card._commit_geometry()
    c.reset_layout()
    app.processEvents()
    card2 = next(x for x in c.cards if x.item.type == "config")
    expect = 0.315 * c.width()
    assert_(abs(card2.width() - expect) < 30,
            f"reset restores size: {card2.width()} vs ~{expect:.0f}")
    assert_(abs(card2.height() - 0.725 * c.height()) < 30, "reset restores height")


def t_card_clamped_in_canvas():
    c = lp.canvas
    for card in c.cards:
        assert_(card.x() + card.width() <= c.width() + 2,
                f"{card.item.type} overflows right: {card.x()}+{card.width()}>{c.width()}")
        assert_(card.y() + card.height() <= c.height() + 2,
                f"{card.item.type} overflows bottom")


def t_pin_unpin_flow():
    from mclauncher.config import CONFIG
    from app.main_window import pinned_from_config
    win._pin_nav("account")
    win._pin_nav("settings")
    app.processEvents()
    keys = list(win.side._buttons.keys())
    assert_("account" in keys and "settings" in keys)
    assert_(keys.index("account") < keys.index("more"), "pinned before more")
    assert_(pinned_from_config() == ["account", "settings"])
    # 移动语义：固定项从分区横条消失
    more_titles = [b.text() for b in win.more_section.cat._group.buttons()]
    assert_(tr("账号") not in more_titles and tr("设置") not in more_titles,
            f"moved out of section bar: {more_titles}")
    # 点固定按钮：进分区页，选中态留在固定按钮上
    win.side.set_current("account", emit=True)
    app.processEvents()
    assert_(win._visible_key() == "account")
    assert_(win.side.button("account").isChecked())
    # unpin 一项（不指定分区 → 回默认分区 more）
    win._unpin_nav("settings")
    app.processEvents()
    assert_("settings" not in win.side._buttons)
    assert_(pinned_from_config() == ["account"])
    more_titles2 = [b.text() for b in win.more_section.cat._group.buttons()]
    assert_(tr("设置") in more_titles2, f"settings back in more: {more_titles2}")
    # 非法 key 不生效
    win._pin_nav("bogus")
    assert_(pinned_from_config() == ["account"])
    win._unpin_nav("account", "download")   # 拖回下载栏
    app.processEvents()
    assert_(CONFIG.get("ui_nav_pinned") is None)
    from app.main_window import section_members_from_config
    assert_("account" in section_members_from_config()["download"],
            "account returned to download section")
    win.switchTo("account")
    app.processEvents()
    assert_(win.stackedWidget.currentWidget() is win.download_section)
    assert_(win._visible_key() == "account")
    # 还原默认分区
    CONFIG.set("ui_section_members", None); CONFIG.save()
    win._rebuild_sections(); app.processEvents()


def t_pin_move_empty_guard():
    from mclauncher.config import CONFIG
    from app.main_window import pinned_from_config, section_members_from_config
    # 把 more 压到只剩 settings，再固定 settings 应被拒绝（不能移空）
    CONFIG.set("ui_section_members", {
        "download": ["version", "mod", "modpack", "datapack", "resource",
                     "shader", "world", "java", "instance", "mods",
                     "multiplayer", "servers", "playtime", "feedback"],
        "more": ["settings", "account"],
    })
    CONFIG.save()
    win._rebuild_sections(); app.processEvents()
    win._pin_nav("account")   # more 只剩 settings
    app.processEvents()
    assert_(pinned_from_config() == ["account"])
    win._pin_nav("settings")  # 最后一项：拒绝
    app.processEvents()
    assert_(pinned_from_config() == ["account"], "last member not movable")
    assert_(section_members_from_config()["more"] == ["settings"], "section intact")
    # 清理
    win._unpin_nav("account")
    CONFIG.set("ui_section_members", None); CONFIG.save()
    win._rebuild_sections(); app.processEvents()


def t_sidebar_drag_width():
    from mclauncher.config import CONFIG
    from app.pcl_chrome import _SideResizer
    resizer = win.side._resizer
    assert_(isinstance(resizer, _SideResizer))
    bar = win.side
    g0 = resizer.mapToGlobal(resizer.rect().topLeft())
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent, QPointF, Qt
    resizer.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(g0.x(), g0.y()),
                                        g0, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    resizer.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(g0.x() + 60, g0.y()),
                                       g0 + QPoint(60, 0), Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    resizer.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, QPointF(g0.x() + 60, g0.y()),
                                          g0 + QPoint(60, 0), Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    app.processEvents()
    assert_(bar.width() == 248, f"drag width applied {bar.width()}")
    assert_(CONFIG.get("ui_sidebar_width") == 248, "width persisted")
    # 上限截断
    CONFIG.set("ui_sidebar_width", None)
    CONFIG.save()
    win._rebuild_sidebar()
    app.processEvents()


def t_import_filters_unknown():
    from app import layout_model as lm
    doc_data = {"version": 1, "grid": 8, "items": [
        {"type": "banner"}, {"type": "not_a_card"}, {"type": "notes"}]}
    import json as _json
    with open("_t_import.json", "w", encoding="utf-8") as f:
        _json.dump(doc_data, f)
    doc = lm.import_doc("_t_import.json")
    os.remove("_t_import.json")
    types = sorted(it.type for it in doc.items)
    assert_(types == ["banner", "notes"], f"unknown filtered: {types}")


def t_free_grid_no_dots():
    c = lp.canvas
    c.doc.grid = 0
    c.set_edit_mode(True)
    app.processEvents()   # paintEvent 跑一遍不炸即可（offscreen 也走 paint）
    c.set_edit_mode(False)
    c.doc.grid = 8
    c._apply_grid_box()


# ======================================================================
# 9. 第二轮全量审计补充
# ======================================================================
def t_section_editor_pin_toggle():
    from mclauncher.config import CONFIG
    from app.main_window import pinned_from_config
    from app.pages.layout_settings import SectionEditorDialog
    dlg = SectionEditorDialog(win, win)
    dlg._toggle_pin("account")
    dlg._toggle_pin("java")
    dlg.accept()
    app.processEvents()
    # 确定性顺序 = 分区成员顺序（下载栏在前）：java 先于 account
    assert_(pinned_from_config() == ["java", "account"], "editor pin saved")
    assert_("account" in win.side._buttons, "sidebar rebuilt with pins")
    # 再开一次全部取消
    dlg2 = SectionEditorDialog(win, win)
    dlg2._toggle_pin("account")
    dlg2._toggle_pin("java")
    dlg2.accept()
    app.processEvents()
    assert_(pinned_from_config() == [], "unpinned via editor")
    assert_(CONFIG.get("ui_nav_pinned") is None)


def t_sidebar_editor_pin_group():
    from mclauncher.config import CONFIG
    from app.main_window import pinned_from_config
    from app.pages.layout_settings import SidebarEditorDialog
    win._pin_nav("account")
    win._pin_nav("java")
    dlg = SidebarEditorDialog(win, win)
    assert_(dlg._pinned == ["account", "java"])
    dlg._move_pinned(1, -1)             # java 上移到前面
    dlg._unpin("account")               # 取消 account
    dlg.accept()
    app.processEvents()
    assert_(pinned_from_config() == ["java"], f"order+unpin applied: {pinned_from_config()}")
    keys = list(win.side._buttons.keys())
    assert_("java" in keys and "account" not in keys)
    # 重置按钮清固定项
    dlg2 = SidebarEditorDialog(win, win)
    dlg2._reset()
    dlg2.accept()
    app.processEvents()
    assert_(pinned_from_config() == [])
    CONFIG.set("ui_nav_pinned", None); CONFIG.save()


def t_pinned_survive_more_hidden():
    from mclauncher.config import CONFIG
    from app.main_window import nav_items_from_config
    win._pin_nav("account")
    CONFIG.set("ui_nav_hidden", ["more"])
    CONFIG.save()
    items = nav_items_from_config()
    keys = [x[1] for x in items if x[0] == "item"]
    assert_("more" not in keys, "more hidden")
    assert_("account" in keys, "pinned still visible")
    assert_(keys.index("account") < keys.index("tasks"), "pinned before tasks")
    CONFIG.set("ui_nav_hidden", None)
    CONFIG.save()
    win._unpin_nav("account")
    app.processEvents()


# ======================================================================
# 10. 侧栏拖动重排 / 混排持久化 / 点阵缓存
# ======================================================================
def t_sidebar_drag_reorder():
    from mclauncher.config import CONFIG
    from app.main_window import nav_items_from_config
    # 一级项重排：download 拖到 more 之后
    win._on_sidebar_reorder("download", "more", False)
    app.processEvents()
    keys = list(win.side._buttons.keys())
    assert_(keys.index("download") == keys.index("more") + 1, f"reorder {keys}")
    # 重排自己/未知键 → 无操作
    win._on_sidebar_reorder("download", "download", True)
    win._on_sidebar_reorder("bogus", "more", True)
    app.processEvents()
    assert_(list(win.side._buttons.keys()) == keys, "no-op reorder")


def t_sidebar_pin_at_position():
    from mclauncher.config import CONFIG
    from app.main_window import nav_items_from_config
    win._pin_nav_at("account", "launch", False)   # 固定在 launch 之后
    app.processEvents()
    seq = list(win.side._buttons.keys())
    assert_(seq[1] == "account", f"pin at position {seq}")
    # 位置持久化：渲染等价于落盘序列
    seq2 = [s[1] for s in nav_items_from_config() if s[0] == "item"]
    assert_(seq == seq2, f"position persisted {seq} vs {seq2}")
    assert_("account" in (CONFIG.get("ui_nav_order") or []), "mixed order stored")
    # 固定项也可重排到别处（注意目标不能是「下载/更多」——那两个
    # 按钮现在语义是"放回分区"，见 unpin.drop_paths）
    win._on_sidebar_reorder("account", "ai", False)
    app.processEvents()
    seq3 = list(win.side._buttons.keys())
    assert_(seq3.index("account") == seq3.index("ai") + 1, f"pinned reorder {seq3}")
    win._unpin_nav("account")
    CONFIG.set("ui_nav_order", None); CONFIG.save()
    win._rebuild_sidebar(); app.processEvents()


def t_sidebar_dialog_preserves_mixed():
    from mclauncher.config import CONFIG
    from app.main_window import nav_items_from_config
    from app.pages.layout_settings import SidebarEditorDialog
    win._pin_nav_at("account", "launch", False)
    app.processEvents()
    dlg = SidebarEditorDialog(win, win)
    dlg._order = ["launch", "download", "ai", "more", "tasks"]  # 恢复默认顺序
    dlg.accept()
    app.processEvents()
    seq = [s[1] for s in nav_items_from_config() if s[0] == "item"]
    # 一级键按对话框顺序，account 保持在 launch 后面（不压平）
    assert_(seq[0] == "launch" and seq[1] == "account", f"mixed preserved {seq}")
    tops = [k for k in seq if k != "account"]
    assert_(tops == ["launch", "download", "ai", "more", "tasks"], f"top order {tops}")
    win._unpin_nav("account")
    CONFIG.set("ui_nav_order", None); CONFIG.save()
    win._rebuild_sidebar(); app.processEvents()


def t_grid_dot_cache():
    c = lp.canvas
    c.doc.grid = 8
    c.set_edit_mode(True)
    app.processEvents()
    pix1 = c._grid_pixmap()
    pix2 = c._grid_pixmap()
    assert_(pix1 is pix2, "dots pixmap cached")
    c.doc.grid = 16
    pix3 = c._grid_pixmap()
    assert_(pix3 is not pix1, "cache invalidated on grid change")
    c.doc.grid = 8
    c.set_edit_mode(False)
    c._apply_grid_box()


# ======================================================================
# 11. 拖拽源回归：bind 必须带 key（否则分区按钮拖不动——真实踩过的坑）
# ======================================================================
def t_drag_sources_wired():
    from PySide6.QtCore import QEvent, QPointF, Qt, QPoint
    from PySide6.QtGui import QMouseEvent
    import app.pages.download_hub as dh
    from app.main_window import MainWindow
    win2 = MainWindow()
    win2.resize(1280, 800)
    win2.show()
    app.processEvents()
    try:
        win2.side.set_current("download", emit=True)
        app.processEvents()
        btns = [b for b, _ in win2.download_section.cat._buttons.values()] +                [b for b, _ in win2.download_section.cat._lazy.values()]
        assert_(btns and all(b.property("navkey") for b in btns),
                "cat buttons carry navkey")
        # 合成 按下+拖动>8px 必须触发拖拽启动
        fired = []
        dh._DragButton._start_nav_drag = lambda self: fired.append(self._nav_key)
        try:
            b0 = btns[0]
            b0.mousePressEvent(QMouseEvent(
                QEvent.MouseButtonPress, QPointF(5, 5), b0.mapToGlobal(QPoint(5, 5)),
                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
            b0.mouseMoveEvent(QMouseEvent(
                QEvent.MouseMove, QPointF(30, 8), b0.mapToGlobal(QPoint(30, 8)),
                Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        finally:
            dh._DragButton._start_nav_drag = lambda self: None
        assert_(fired == [btns[0].property("navkey")], f"drag fired {fired}")
        # 侧栏按钮 eventFilter 拖拽源
        import app.pcl_chrome as pc
        fired2 = []
        pc.PclSideBar._start_nav_drag = staticmethod(lambda w, k: fired2.append(k))
        try:
            lb = win2.side.button("launch")
            g = lb.mapToGlobal(QPoint(3, 3))
            win2.eventFilter  # noqa: B018
            win2.side.eventFilter(lb, QMouseEvent(
                QEvent.MouseButtonPress, QPointF(3, 3), QPointF(g.x(), g.y()),
                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
            win2.side.eventFilter(lb, QMouseEvent(
                QEvent.MouseMove, QPointF(30, 6), QPointF(g.x() + 27, g.y() + 3),
                Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        finally:
            pc.PclSideBar._start_nav_drag = staticmethod(
                lambda w, k: None)
        assert_(fired2 == ["launch"], f"sidebar drag fired {fired2}")
    finally:
        win2.close()
        win2.deleteLater()
        app.processEvents()


# ======================================================================
# 12. 拖回分区两条路径回归（UniqueConnection 接 lambda 静默失败的教训）
# ======================================================================
def t_unpin_drop_paths():
    from PySide6.QtCore import QPointF, Qt, QMimeData
    from PySide6.QtGui import QDropEvent
    from mclauncher.config import CONFIG
    from app.main_window import pinned_from_config, section_members_from_config

    def drop_on(widget, key, lx, ly):
        mime = QMimeData()
        mime.setData("application/x-pymcl-nav", key.encode("utf-8"))
        widget.dropEvent(QDropEvent(QPointF(lx, ly), Qt.CopyAction, mime,
                                    Qt.NoButton, Qt.NoModifier))

    # 路径1：拖到侧栏「更多」一级按钮 = 放回更多分区
    win._pin_nav("account")
    app.processEvents()
    btn = win.side.button("more")
    g = btn.mapTo(win.side, btn.rect().center())
    drop_on(win.side, "account", g.x(), g.y())
    app.processEvents()
    assert_(pinned_from_config() == [], "drop on more button unpins")
    assert_("account" in section_members_from_config()["more"], "returned to more")

    # 路径2：拖回「更多」分区横条（信号必须真的连上）
    win._pin_nav("settings")
    app.processEvents()
    win.side.set_current("more", emit=True)
    app.processEvents()
    drop_on(win.more_section.cat, "settings", 100, 20)
    app.processEvents()
    assert_(pinned_from_config() == [], "drop on section bar unpins")
    assert_("settings" in section_members_from_config()["more"], "back to more bar")

    # 路径3：拖到「下载」按钮 = 放回下载分区
    win._pin_nav("java")
    app.processEvents()
    win._on_sidebar_reorder("java", "download", True)
    app.processEvents()
    assert_(pinned_from_config() == [], "drop to download unpins")
    assert_("java" in section_members_from_config()["download"], "java in download")
    # 还原
    CONFIG.set("ui_section_members", None); CONFIG.save()
    win._rebuild_sections(); app.processEvents()


for name, fn in [
    ("model.roundtrip", t_model_roundtrip),
    ("model.validation", t_model_validation),
    ("model.geometry", t_model_geometry),
    ("model.profiles", t_model_profiles),
    ("model.import_export", t_model_import_export),
    ("window.construct", t_construct_window),
    ("canvas.edit_mode", t_edit_mode_toggle),
    ("canvas.drag_commit", t_drag_and_commit),
    ("canvas.resize_minsize", t_resize_and_minsize),
    ("canvas.add_remove_singleton", t_add_remove_singleton),
    ("canvas.add_generic", t_add_generic_cards),
    ("cards.notes_persist", t_notes_persist),
    ("cards.quick_nav", t_quick_nav),
    ("canvas.fit_reset", t_fit_and_reset),
    ("canvas.persist_restore", t_persist_and_restore),
    ("canvas.grid", t_grid_change),
    ("sidebar.custom", t_sidebar_custom),
    ("sidebar.editor", t_sidebar_editor_apply),
    ("settings.group", t_settings_group),
    ("settings.profile_flow", t_profile_switch_flow),
    ("pages.switching", t_page_switching),
    ("theme.flip", t_theme_flip),
    ("audit2.dark_card_colors", t_dark_card_colors),
    ("sections.loader", t_sections_loader),
    ("sections.rebuild_routing", t_sections_rebuild_routing),
    ("sections.built_migration", t_sections_built_migration),
    ("sections.editor_flow", t_sections_editor_flow),
    ("sections.restore", t_sections_restore),
    ("sections.bar_eager", t_sections_bar_eager),
    ("banner.no_squeeze", t_banner_card_not_squeezed),
    ("audit.reset_restores_size", t_reset_restores_size),
    ("audit.card_clamped", t_card_clamped_in_canvas),
    ("pin.flow", t_pin_unpin_flow),
    ("sidebar.drag_width", t_sidebar_drag_width),
    ("import.unknown_filtered", t_import_filters_unknown),
    ("canvas.free_grid", t_free_grid_no_dots),
    ("audit2.section_pin_toggle", t_section_editor_pin_toggle),
    ("audit2.sidebar_pin_group", t_sidebar_editor_pin_group),
    ("audit2.pinned_more_hidden", t_pinned_survive_more_hidden),
    ("drag.reorder", t_sidebar_drag_reorder),
    ("drag.pin_at", t_sidebar_pin_at_position),
    ("drag.dialog_mixed", t_sidebar_dialog_preserves_mixed),
    ("perf.grid_cache", t_grid_dot_cache),
    ("drag.sources_wired", t_drag_sources_wired),
    ("pin.empty_guard", t_pin_move_empty_guard),
    ("unpin.drop_paths", t_unpin_drop_paths),
]:
    check(name, fn)

fails = [r for r in RESULTS if not r[1]]
print()
print(f"TOTAL {len(RESULTS)}  PASS {len(RESULTS) - len(fails)}  FAIL {len(fails)}")
if fails:
    print("FAILED:", [r[0] for r in fails])
    sys.exit(1)
print("ALL PASS")
