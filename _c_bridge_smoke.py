# -*- coding: utf-8 -*-
import json, subprocess, os, secrets, urllib.request

root = os.getcwd()
exe = os.path.join(root, "native", "build", "pymcl-bridge.exe")
token = secrets.token_urlsafe(32)
p = subprocess.Popen(
    [exe, "--root", root, "--host", "127.0.0.1", "--port", "0", "--token", token],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
)
line = p.stdout.readline().strip()
print("BOOT", line)
port = None
for part in line.split():
    if part.startswith("port="):
        port = int(part.split("=", 1)[1])
assert port, line
hdr = {"Content-Type": "application/json", "X-PyMCL-Bridge-Token": token}

def rpc(m, params=None):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": params or {}}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/rpc", data=body, headers=hdr)
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())

methods = [
    "get_instances", "get_accounts", "get_account_rows", "get_settings",
    "get_all_playtime", "list_servers", "authlib_presets", "get_version_list",
    "preflight_launch", "ai_list_chats", "terracotta_snapshot", "help_articles",
    "format_playtime", "add_offline_account",
    "get_total_playtime", "feedback_history", "export_servers", "import_servers",
]
ok = 0
fail = []
for m in methods:
    params = {}
    if m in ("list_servers", "preflight_launch"):
        params = {"instance": "default", "version": ""}
    if m == "format_playtime":
        params = {"seconds": 3661}
    if m == "add_offline_account":
        params = {"username": "_c_align_probe"}
    if m == "export_servers":
        params = {"instance": "default"}
    if m == "import_servers":
        # 空文本导入 0 条：验证方法可达且不写盘，不污染 default 实例
        params = {"instance": "default", "text": ""}
    try:
        res = rpc(m, params)
        if res.get("error"):
            fail.append((m, res["error"]))
            print("FAIL", m, res["error"])
        else:
            ok += 1
            print("OK", m)
    except Exception as e:
        fail.append((m, str(e)))
        print("EXC", m, e)

# cleanup probe account
try:
    rpc("remove_account", {"name": "_c_align_probe"})
except Exception:
    pass

print("SUMMARY", ok, "/", len(methods), "fails", len(fail))
p.terminate()
raise SystemExit(0 if not fail else 1)
