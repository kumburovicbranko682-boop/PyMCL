# -*- coding: utf-8 -*-
"""轻量帮助 / 常见问题（对齐 PCL「能查到答案」的最低体验，不做独立帮助站）。"""
from __future__ import annotations

ARTICLES: list[dict] = [
    {
        "id": "launch-fail",
        "title": "启动失败 / 闪退怎么办",
        "body": (
            "1. 看崩溃弹窗里的「建议操作」，能禁用嫌疑 Mod、提高内存、下载合适 Java、修复版本文件。\n"
            "2. 启动页点启动前会做预检：磁盘不足、Mods 被解压成文件夹、Java 过旧会直接拦住。\n"
            "3. 仍不行：到「反馈」页把错误报告发给开发者（同意上传后才会发送）。"
        ),
    },
    {
        "id": "java",
        "title": "Java 怎么选",
        "body": (
            "启动时默认自动匹配。也可在「下载 → Java」按版本下载：\n"
            "· Java 8：1.16 及更早\n"
            "· Java 17：1.18 – 1.20.4\n"
            "· Java 21：1.20.5 – 1.21.11\n"
            "· Java 25：26.1 及更新（年式版本号）\n"
            "发行版推荐 Adoptium；也可用 Zulu / Microsoft。"
        ),
    },
    {
        "id": "mods",
        "title": "模组 / 整合包安装",
        "body": (
            "到「下载」页搜索 Modrinth / CurseForge（国内走镜像）。\n"
            "原版版本不会加载 mods 文件夹里的 jar，需要先装 Fabric / Forge / Quilt / NeoForge。\n"
            "不要把 .jar 解压成文件夹，否则会预检失败。"
        ),
    },
    {
        "id": "account",
        "title": "账号与正版登录",
        "body": (
            "支持离线、微软设备码、皮肤站（Yggdrasil）、统一通行证（Nide8）。\n"
            "微软登录请按弹窗打开链接并输入代码；关掉窗口会取消后台轮询。"
        ),
    },
    {
        "id": "multiplayer",
        "title": "陶瓦联机",
        "body": (
            "「联机」页可开房 / 加入。房间号形如 U/XXXX-XXXX-XXXX-XXXX。\n"
            "双方都要用兼容的陶瓦内核；防火墙提示按页面指引放行。"
        ),
    },
    {
        "id": "isolation",
        "title": "版本隔离与存档",
        "body": (
            "每个实例是独立的 .minecraft。版本设置里可选隔离档位。\n"
            "存档可在版本相关对话框里备份 / 还原；删世界前建议先备份。"
        ),
    },
]


def list_articles() -> list[dict]:
    return [{"id": a["id"], "title": a["title"]} for a in ARTICLES]


def get_article(article_id: str) -> dict | None:
    aid = (article_id or "").strip()
    for a in ARTICLES:
        if a["id"] == aid:
            return dict(a)
    return None


def search_articles(query: str = "") -> list[dict]:
    q = (query or "").strip().lower()
    if not q:
        return [dict(a) for a in ARTICLES]
    out = []
    for a in ARTICLES:
        blob = (a["title"] + "\n" + a["body"]).lower()
        if q in blob:
            out.append(dict(a))
    return out
