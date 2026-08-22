# -*- coding: utf-8 -*-
"""缩放联动冒烟：与被拖边贴合的相邻卡片跟随让位/补位。

场景：左卡 1/3 + 右卡 2/3 共享一条竖边；把左卡拉宽到 2/3，右卡应自动
缩成 1/3（贴合、不重叠、不留缝），松手后两卡的文档几何都要落盘。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent))

from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon as FIF

app = QApplication([])

from app.dashboard import DashboardCanvas, CardSpec
from app.layout_model import LayoutDoc, LayoutItem

FAILED = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def make_canvas(items: list[LayoutItem]) -> DashboardCanvas:
    canvas = DashboardCanvas({it.type: CardSpec(it.type, lambda: it.type, FIF.TAG,
                                                 lambda: it.type, lambda c, i: None)
                              for it in items})
    canvas.resize(900, 600)
    canvas.build_from_doc(LayoutDoc(list(items), grid=0))
    canvas.set_edit_mode(True)
    return canvas


def near(a, b, tol=3):
    return abs(a - b) <= tol


def main():
    # ---- 1. 横向：左卡拉宽，右卡让位 -----------------------------------
    doc_items = [LayoutItem("left", 0.0, 0.0, 1 / 3, 1.0),
                 LayoutItem("right", 1 / 3, 0.0, 2 / 3, 1.0)]
    canvas = make_canvas(doc_items)
    left = canvas.cards[0]
    right = canvas.cards[1]
    # 左卡 e 手柄拖到 2/3 处（300 -> 600）
    left._resize_by("e", left.x(), left.y(), left.width(), left.height(), 300, 0)
    left._commit_geometry()
    check("grow.right_follows", near(right.x(), 600) and near(right.width(), 300),
          f"right=({right.x()},{right.width()})")
    check("grow.left_widens", near(left.width(), 600), f"left w={left.width()}")
    check("grow.doc_persisted", near(right.item.x, 2 / 3, 0.01) and near(right.item.w, 1 / 3, 0.01),
          f"item=({right.item.x:.3f},{right.item.w:.3f})")

    # ---- 2. 反向：拖回 1/4，右卡补位变宽 --------------------------------
    left._resize_by("e", left.x(), left.y(), left.width(), left.height(), -375, 0)
    left._commit_geometry()
    check("shrink.right_fills", near(right.x(), 225) and near(right.width(), 675),
          f"right=({right.x()},{right.width()})")

    # ---- 3. 右卡 w 手柄左拖：左卡跟着变宽（不小于左卡最小宽） ----------
    guard = left.minimumWidth()
    right._resize_by("w", right.x(), right.y(), right.width(), right.height(), -100, 0)
    right._commit_geometry()
    check("west.left_follows",
          near(left.x() + left.width(), max(125, guard)) and near(right.x(), max(125, guard)),
          f"guard={guard} edge=({left.x() + left.width()},{right.x()})")

    # ---- 4. 无贴合邻居：保持自由缩放 ------------------------------------
    solo_items = [LayoutItem("solo", 0.1, 0.1, 0.3, 0.3),
                  LayoutItem("far", 0.7, 0.7, 0.25, 0.25)]
    canvas2 = make_canvas(solo_items)
    solo = canvas2.cards[0]
    solo._resize_by("e", solo.x(), solo.y(), solo.width(), solo.height(), 120, 0)
    solo._commit_geometry()
    far = canvas2.cards[1]
    check("free.unlinked_untouched", near(far.x(), 630) and near(solo.width(), 270 + 120),
          f"solo_w={solo.width()} far_x={far.x()}")

    # ---- 5. 邻居最小尺寸保护 -------------------------------------------
    doc_items = [LayoutItem("left", 0.0, 0.0, 1 / 3, 1.0),
                 LayoutItem("right", 1 / 3, 0.0, 2 / 3, 1.0)]
    canvas3 = make_canvas(doc_items)
    left, right = canvas3.cards[0], canvas3.cards[1]
    right.setMinimumWidth(500)  # 右卡最多让到 400
    left._resize_by("e", left.x(), left.y(), left.width(), left.height(), 450, 0)
    left._commit_geometry()
    check("guard.min_width", near(left.x() + left.width(), 400) and near(right.x(), 400),
          f"edge={left.x() + left.width()} right_x={right.x()} right_w={right.width()}")

    # ---- 6. 纵向：上卡变高，下卡让位 ------------------------------------
    doc_items = [LayoutItem("top", 0.0, 0.0, 1.0, 0.3),
                 LayoutItem("bottom", 0.0, 0.3, 1.0, 0.7)]
    canvas4 = make_canvas(doc_items)
    top, bottom = canvas4.cards[0], canvas4.cards[1]
    top._resize_by("s", top.x(), top.y(), top.width(), top.height(), 0, 60)
    top._commit_geometry()
    check("vert.bottom_follows", near(bottom.y(), 240) and near(bottom.height(), 360),
          f"bottom=({bottom.y()},{bottom.height()})")
    check("vert.doc_persisted", near(bottom.item.y, 0.4, 0.01), f"item.y={bottom.item.y:.3f}")

    # ---- 7. 带缝（默认布局 12~18px 栏间缝）也要联动，且保持原缝 ----------
    doc_items = [LayoutItem("left", 0.0, 0.0, 0.32, 1.0),
                 LayoutItem("right", 0.34, 0.0, 0.66, 1.0)]
    canvas5 = make_canvas(doc_items)
    left, right = canvas5.cards[0], canvas5.cards[1]
    gap = right.x() - (left.x() + left.width())
    left._resize_by("e", left.x(), left.y(), left.width(), left.height(), 90, 0)
    left._commit_geometry()
    check("gap.kept_and_pushed",
          near(right.x() - (left.x() + left.width()), gap)
          and near(right.x() + right.width(), 900),
          f"gap={gap} now={right.x() - (left.x() + left.width())} right_r={right.x() + right.width()}")

    print()
    if FAILED:
        print(f"FAILED: {len(FAILED)} -> {FAILED}")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
