# -*- coding: utf-8 -*-
"""Find Chinese string literals in app/pages not wrapped in tr()/_()."""
import ast
import re
import sys
from pathlib import Path

CJK = re.compile(r"[\u4e00-\u9fff]")


def audit(path: Path):
    src = path.read_text("utf-8")
    tree = ast.parse(src)
    wrapped: set[tuple[int, int]] = set()

    class Marker(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call):
            fn = node.func
            name = None
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                name = fn.attr
            if name in ("tr", "_", "_tr"):
                for arg in node.args:
                    for sub in ast.walk(arg):
                        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                            wrapped.add((sub.lineno, sub.col_offset))
            self.generic_visit(node)

    Marker().visit(tree)
    docstrings: set[tuple[int, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                c = body[0].value
                docstrings.add((c.lineno, c.col_offset))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and CJK.search(node.value):
            if (node.lineno, node.col_offset) not in wrapped \
                    and (node.lineno, node.col_offset) not in docstrings:
                hits.append((node.lineno, node.value.replace("\n", "\\n")[:60]))
    return hits


base = Path("app/pages")
targets = sorted(base.glob("*.py")) + [Path("app/pcl_chrome.py"), Path("app/main_window.py"),
                                       Path("app/widgets.py"), Path("app/dashboard.py")]
for f in targets:
    hits = audit(f)
    if hits:
        print(f"== {f} ({len(hits)}) ==")
        for ln, txt in hits:
            print(f"  {ln}: {txt}")
