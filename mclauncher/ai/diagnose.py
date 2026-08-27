# -*- coding: utf-8 -*-
"""启动失败：转给 PCL 同款崩溃分析，保留旧接口给 AI 工具。"""

from __future__ import annotations

from mclauncher.crash import analyze_instance, collect_logs, log_excerpt
from mclauncher.instances import Instance


def diagnose(instance: Instance, extra_log: str = "") -> dict:
    report = analyze_instance(instance, extra_log=extra_log, manual=True)
    findings = []
    for row in report.get("reasons") or []:
        extra = row.get("extra") or []
        findings.append({
            "code": row.get("code"),
            "title": row.get("title") or row.get("code"),
            "snippet": "\n".join(extra)[:400] if extra else (report.get("summary") or ""),
        })
    return {
        "instance": report.get("instance") or instance.name,
        "version": report.get("version") or "",
        "has_latest": bool(report.get("has_latest")),
        "has_crash": bool(report.get("has_crash")),
        "has_hs_err": bool(report.get("has_hs_err")),
        "latest_path": next((f for f in report.get("files") or [] if f.lower().endswith("latest.log")), ""),
        "crash_path": report.get("direct_file") or "",
        "findings": findings,
        # 可一键执行的修复（crash.build_actions 生成），UI 渲染成按钮
        "actions": list(report.get("actions") or []),
        "hint": report.get("summary") or report.get("detail") or "",
        "detail": report.get("detail") or "",
        # 尾部日志截短：完整版靠 get_latest_log / get_crash_report 再要
        "latest_tail": (report.get("log_mc") or "")[-3000:],
        "crash_tail": (report.get("log_crash") or "")[-3000:],
    }


__all__ = ["diagnose", "collect_logs", "log_excerpt", "analyze_instance"]
