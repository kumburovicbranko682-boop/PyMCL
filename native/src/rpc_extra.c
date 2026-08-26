#include "pymcl.h"
#include <pthread.h>

/* ---------- python one-shot RPC (full parity with bridge.api) ---------- */

static int find_python(char *out, size_t n) {
    const char *env = getenv("PYMCL_PYTHON");
    if (env && env[0] && GetFileAttributesA(env) != INVALID_FILE_ATTRIBUTES) {
        snprintf(out, n, "%s", env);
        return 0;
    }
    {
        const char *known =
            "C:\\Users\\Administrator\\.workbuddy\\binaries\\python\\envs\\pymcl5\\Scripts\\python.exe";
        if (GetFileAttributesA(known) != INVALID_FILE_ATTRIBUTES) {
            snprintf(out, n, "%s", known);
            return 0;
        }
    }
    snprintf(out, n, "python");
    return 0;
}

cJSON *py_rpc_call(const char *method, cJSON *params) {
    return py_rpc_call_t(method, params, 120);
}

cJSON *py_rpc_call_t(const char *method, cJSON *params, int timeout_sec) {
    char py[PYMCL_PATH], script[PYMCL_PATH], pin[PYMCL_PATH], pout[PYMCL_PATH], tmpdir[PYMCL_PATH];
    static volatile LONG g_rpc_seq;
    LONG seq = InterlockedIncrement(&g_rpc_seq);
    find_python(py, sizeof(py));
    pymcl_path_join3(script, sizeof(script), g_root, "native\\tools", "py_rpc.py");
    if (!pymcl_file_exists(script)) {
        pymcl_path_join3(script, sizeof(script), g_root, "native/tools", "py_rpc.py");
    }
    if (!pymcl_file_exists(script)) {
        pymcl_set_error("py_rpc.py missing; method %s needs Python bridge", method);
        return NULL;
    }
    GetTempPathA(sizeof(tmpdir), tmpdir);
    /* 带序号：任务线程里的长调用和并发的短调用会同时在飞，纯 PID 命名会互相覆盖 */
    snprintf(pin, sizeof(pin), "%spymcl-rpc-in-%u-%ld.json", tmpdir,
             (unsigned)GetCurrentProcessId(), (long)seq);
    snprintf(pout, sizeof(pout), "%spymcl-rpc-out-%u-%ld.json", tmpdir,
             (unsigned)GetCurrentProcessId(), (long)seq);

    cJSON *body = params ? cJSON_Duplicate(params, 1) : cJSON_CreateObject();
    if (!cJSON_IsObject(body)) {
        cJSON_Delete(body);
        body = cJSON_CreateObject();
    }
    {
        char *txt = cJSON_PrintUnformatted(body);
        cJSON_Delete(body);
        if (!txt) { pymcl_set_error("params serialize failed"); return NULL; }
        pymcl_write_file(pin, txt, strlen(txt));
        free(txt);
    }

    const char *argv[16];
    int argc = 0;
    argv[argc++] = py;
    argv[argc++] = "-u";
    argv[argc++] = script;
    argv[argc++] = "--root";
    argv[argc++] = g_root;
    argv[argc++] = "--method";
    argv[argc++] = method;
    argv[argc++] = "--params";
    argv[argc++] = pin;
    argv[argc++] = "--out";
    argv[argc++] = pout;
    int rc = pymcl_run_process(argv, argc, g_root, NULL, NULL,
                               timeout_sec > 0 ? timeout_sec : 120);
    DeleteFileA(pin);
    cJSON *wrap = pymcl_read_json(pout);
    DeleteFileA(pout);
    if (!wrap) {
        pymcl_set_error("py_rpc failed (rc=%d) for %s", rc, method);
        return NULL;
    }
    if (!cJSON_IsTrue(cJSON_GetObjectItem(wrap, "ok"))) {
        const char *err = cJSON_GetStringValue(cJSON_GetObjectItem(wrap, "error"));
        pymcl_set_error("%s", err ? err : "py_rpc error");
        cJSON_Delete(wrap);
        return NULL;
    }
    cJSON *result = cJSON_DetachItemFromObject(wrap, "result");
    cJSON_Delete(wrap);
    if (!result) result = cJSON_CreateNull();
    return result;
}

/* ---------- helpers ---------- */

static const char *pstr(cJSON *o, const char *k, const char *def) {
    cJSON *v = cJSON_GetObjectItem(o, k);
    const char *s = cJSON_GetStringValue(v);
    return s ? s : def;
}

static void servers_path(const char *inst, char *out, size_t n) {
    char ip[PYMCL_PATH];
    instance_path(inst, ip, sizeof(ip));
    pymcl_path_join(out, n, ip, "servers.json");
}

static void playtime_path(char *out, size_t n) {
    pymcl_path_join(out, n, g_root, "playtime.json");
}

static void version_settings_path(const char *inst, const char *ver, char *out, size_t n) {
    char vd[PYMCL_PATH];
    instance_versions_dir(inst, vd, sizeof(vd));
    pymcl_path_join3(out, n, vd, ver, "pymcl.json");
}

static cJSON *vs_defaults(void) {
    return cJSON_Parse(
        "{\"isolation\":\"none\",\"memory_mb\":null,\"java\":\"自动选择\","
        "\"jvm_args\":\"\",\"game_args\":\"\",\"pre_launch\":\"\",\"post_launch\":\"\","
        "\"pre_launch_wait\":true,\"server\":\"\",\"port\":\"\",\"process_priority\":\"normal\","
        "\"icon\":\"\",\"hidden\":false,\"login_account\":\"\",\"auth_server\":\"\","
        "\"auth_server_name\":\"\",\"nide8_id\":\"\",\"gc\":\"\",\"window_title\":\"\","
        "\"window_mode\":\"window\",\"window_width\":null,\"window_height\":null,"
        "\"skip_assets\":false,\"offline_skin\":\"default\"}");
}

static int set_mod_enabled(const char *instance, const char *filename, int enabled) {
    if (!filename || !filename[0]) { pymcl_set_error("缺少文件名"); return -1; }
    char dir[PYMCL_PATH], src[PYMCL_PATH], dst[PYMCL_PATH];
    char ip[PYMCL_PATH];
    instance_path(instance, ip, sizeof(ip));
    pymcl_path_join(dir, sizeof(dir), ip, "mods");
    pymcl_ensure_dir(dir);

    char name[512];
    snprintf(name, sizeof(name), "%s", filename);
    size_t len = strlen(name);
    int is_dis = (len > 9 && pymcl_endswith(name, ".disabled"));
    if (enabled) {
        if (!is_dis) return 0;
        name[len - 9] = 0;
        pymcl_path_join(src, sizeof(src), dir, filename);
        pymcl_path_join(dst, sizeof(dst), dir, name);
    } else {
        if (is_dis) return 0;
        pymcl_path_join(src, sizeof(src), dir, filename);
        snprintf(name, sizeof(name), "%s.disabled", filename);
        pymcl_path_join(dst, sizeof(dst), dir, name);
    }
    if (!pymcl_file_exists(src)) { pymcl_set_error("模组不存在: %s", filename); return -1; }
    if (MoveFileExA(src, dst, MOVEFILE_REPLACE_EXISTING) == 0) {
        pymcl_set_error("重命名失败");
        return -1;
    }
    return 0;
}

static cJSON *list_mod_entries(const char *instance) {
    cJSON *names = list_instance_files(instance, "mods");
    cJSON *out = cJSON_CreateArray();
    cJSON *it;
    cJSON_ArrayForEach(it, names) {
        const char *fn = it->valuestring;
        if (!fn) continue;
        int enabled = !pymcl_endswith(fn, ".disabled");
        char base[512];
        snprintf(base, sizeof(base), "%s", fn);
        if (!enabled) {
            size_t n = strlen(base);
            if (n > 9) base[n - 9] = 0;
        }
        cJSON *row = cJSON_CreateObject();
        cJSON_AddStringToObject(row, "filename", fn);
        cJSON_AddStringToObject(row, "name", base);
        cJSON_AddBoolToObject(row, "enabled", enabled);
        cJSON_AddItemToArray(out, row);
    }
    cJSON_Delete(names);
    return out;
}

/* ---------- AI 流式子进程 ----------
 * ai_send 靠后台线程持续推 ai.delta/ai.done 事件；一次性 py_rpc 拿到
 * {"started":true} 就退出，线程被杀，UI 毫无反应。改为常驻子进程：
 * stdout 逐行回传事件由 C 转发到 SSE，stdin 下发 stop/confirm/answer。 */
static pthread_mutex_t g_ai_mu = PTHREAD_MUTEX_INITIALIZER;
static HANDLE g_ai_proc;
static HANDLE g_ai_stdin;
static sse_emit_fn g_ai_emit;

static int ai_write_line(const char *line) {
    pthread_mutex_lock(&g_ai_mu);
    HANDLE h = g_ai_stdin;
    int ok = 0;
    if (h) {
        DWORD wr = 0;
        ok = WriteFile(h, line, (DWORD)strlen(line), &wr, NULL) != 0;
        if (ok) WriteFile(h, "\n", 1, &wr, NULL);
    }
    pthread_mutex_unlock(&g_ai_mu);
    return ok;
}

static void *ai_pump(void *p) {
    HANDLE rd = (HANDLE)p;
    const size_t line_cap = 65536;
    char *line = (char *)malloc(line_cap);
    char buf[8192];
    size_t ll = 0;
    DWORD n;
    int terminal_seen = 0;
    while (line && ReadFile(rd, buf, sizeof(buf), &n, NULL) && n > 0) {
        for (DWORD i = 0; i < n; i++) {
            char c = buf[i];
            if (c == '\r') continue;
            if (c == '\n') {
                line[ll] = 0;
                if (ll > 0) {
                    cJSON *o = cJSON_Parse(line);
                    if (o) {
                        const char *ev = cJSON_GetStringValue(cJSON_GetObjectItem(o, "event"));
                        cJSON *data = cJSON_GetObjectItem(o, "data");
                        if (ev && g_ai_emit) {
                            cJSON *payload = data ? data : cJSON_CreateObject();
                            g_ai_emit(ev, payload);
                            if (!data) cJSON_Delete(payload);
                        }
                        if (ev && (strcmp(ev, "ai.done") == 0 || strcmp(ev, "ai.fail") == 0))
                            terminal_seen = 1;
                        cJSON_Delete(o);
                    }
                }
                ll = 0;
            } else if (ll + 1 < line_cap) {
                line[ll++] = c;
            }
        }
    }
    free(line);
    CloseHandle(rd);
    /* 子进程没发 ai.done/ai.fail 就死了（Python 崩溃、被杀）：以前这里
     * 静默收尾，UI 的对话框永远停在「处理中」。补一个终止事件。 */
    if (!terminal_seen && g_ai_emit) {
        cJSON *payload = cJSON_CreateObject();
        cJSON_AddStringToObject(payload, "text", "AI 子进程意外退出");
        cJSON_AddBoolToObject(payload, "stopped", 0);
        g_ai_emit("ai.fail", payload);
        cJSON_Delete(payload);
    }
    pthread_mutex_lock(&g_ai_mu);
    if (g_ai_stdin) { CloseHandle(g_ai_stdin); g_ai_stdin = NULL; }
    if (g_ai_proc) {
        WaitForSingleObject(g_ai_proc, 5000);
        CloseHandle(g_ai_proc);
        g_ai_proc = NULL;
    }
    pthread_mutex_unlock(&g_ai_mu);
    return NULL;
}

static int ai_spawn(const char *params_path) {
    char py[PYMCL_PATH], script[PYMCL_PATH];
    static char cmd[PYMCL_PATH * 4];
    find_python(py, sizeof(py));
    pymcl_path_join3(script, sizeof(script), g_root, "native\\tools", "py_ai_stream.py");
    if (!pymcl_file_exists(script))
        pymcl_path_join3(script, sizeof(script), g_root, "native/tools", "py_ai_stream.py");
    if (!pymcl_file_exists(script)) {
        pymcl_set_error("缺少 py_ai_stream.py；AI 助手需要 Python 后端");
        return -1;
    }

    SECURITY_ATTRIBUTES sa = { sizeof(sa), NULL, TRUE };
    HANDLE in_rd = NULL, in_wr = NULL, out_rd = NULL, out_wr = NULL;
    if (!CreatePipe(&out_rd, &out_wr, &sa, 0) || !CreatePipe(&in_rd, &in_wr, &sa, 0)) {
        pymcl_set_error("创建管道失败");
        if (out_rd) CloseHandle(out_rd);
        if (out_wr) CloseHandle(out_wr);
        return -1;
    }
    SetHandleInformation(out_rd, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(in_wr, HANDLE_FLAG_INHERIT, 0);

    snprintf(cmd, sizeof(cmd), "\"%s\" -u \"%s\" --root \"%s\" --params \"%s\"",
             py, script, g_root, params_path);

    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    memset(&si, 0, sizeof(si));
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = in_rd;
    si.hStdOutput = out_wr;
    si.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    if (!CreateProcessA(NULL, cmd, NULL, NULL, TRUE, CREATE_NO_WINDOW, NULL, g_root, &si, &pi)) {
        pymcl_set_error("无法启动 AI 助手进程");
        CloseHandle(in_rd); CloseHandle(in_wr);
        CloseHandle(out_rd); CloseHandle(out_wr);
        return -1;
    }
    CloseHandle(pi.hThread);
    CloseHandle(in_rd);
    CloseHandle(out_wr);

    pthread_mutex_lock(&g_ai_mu);
    g_ai_proc = pi.hProcess;
    g_ai_stdin = in_wr;
    pthread_mutex_unlock(&g_ai_mu);

    pthread_t th;
    pthread_create(&th, NULL, ai_pump, out_rd);
    pthread_detach(th);
    return 0;
}

static void format_playtime(long long sec, char *out, size_t n) {
    if (sec < 0) sec = 0;
    long long h = sec / 3600, m = (sec % 3600) / 60, s = sec % 60;
    if (h > 0) snprintf(out, n, "%lld 小时 %lld 分", h, m);
    else if (m > 0) snprintf(out, n, "%lld 分 %lld 秒", m, s);
    else snprintf(out, n, "%lld 秒", s);
}

/* Prefer native; on failure or complexity, Python. */
/* ---------- sysinfo（对齐 mclauncher/sysinfo.py 的字段形状） ---------- */

static void si_reg_str(const char *path, const char *name, char *out, size_t n) {
    out[0] = 0;
    HKEY k;
    if (RegOpenKeyExA(HKEY_LOCAL_MACHINE, path, 0, KEY_READ, &k) != ERROR_SUCCESS) return;
    DWORD type = 0, cb = (DWORD)(n - 1);
    if (RegQueryValueExA(k, name, NULL, &type, (LPBYTE)out, &cb) == ERROR_SUCCESS
        && (type == REG_SZ || type == REG_EXPAND_SZ)) {
        if (cb >= n) cb = (DWORD)(n - 1);
        out[cb] = 0;
    } else {
        out[0] = 0;
    }
    RegCloseKey(k);
}

static DWORD si_reg_dword(const char *path, const char *name) {
    HKEY k;
    DWORD val = 0, type = 0, cb = sizeof(val);
    if (RegOpenKeyExA(HKEY_LOCAL_MACHINE, path, 0, KEY_READ, &k) != ERROR_SUCCESS) return 0;
    if (RegQueryValueExA(k, name, NULL, &type, (LPBYTE)&val, &cb) != ERROR_SUCCESS
        || type != REG_DWORD)
        val = 0;
    RegCloseKey(k);
    return val;
}

/* " ".join(name.split())：ProcessorNameString 常带连串空格 */
static void si_collapse_spaces(char *s) {
    char *w = s;
    int in_space = 1;
    for (char *r = s; *r; r++) {
        if (*r == ' ' || *r == '\t' || *r == '\r' || *r == '\n') {
            if (!in_space) { *w++ = ' '; in_space = 1; }
        } else {
            *w++ = *r;
            in_space = 0;
        }
    }
    while (w > s && w[-1] == ' ') w--;
    *w = 0;
}

static int si_physical_cores(void) {
    DWORD len = 0;
    GetLogicalProcessorInformation(NULL, &len);
    if (!len) return 0;
    SYSTEM_LOGICAL_PROCESSOR_INFORMATION *buf = (SYSTEM_LOGICAL_PROCESSOR_INFORMATION *)malloc(len);
    if (!buf) return 0;
    int cores = 0;
    if (GetLogicalProcessorInformation(buf, &len)) {
        DWORD count = len / sizeof(*buf);
        for (DWORD i = 0; i < count; i++)
            if (buf[i].Relationship == RelationProcessorCore) cores++;
    }
    free(buf);
    return cores;
}

/* 对齐 _VIRTUAL_GPU 关键词表：虚拟显卡排到物理卡后面 */
static int si_gpu_is_virtual(const char *name) {
    static const char *keys[] = {
        "virtual", "basic render", "basic display", "remote desktop",
        "mumu", "parsec", "spacedesk", "usb display",
    };
    for (size_t i = 0; i < sizeof(keys) / sizeof(keys[0]); i++)
        if (pymcl_icontains(name, keys[i])) return 1;
    return 0;
}

static cJSON *si_gpus(void) {
    cJSON *physical = cJSON_CreateArray();
    cJSON *virt = cJSON_CreateArray();
    for (DWORD i = 0; i < 32; i++) {
        DISPLAY_DEVICEW dd;
        memset(&dd, 0, sizeof(dd));
        dd.cb = sizeof(dd);
        if (!EnumDisplayDevicesW(NULL, i, &dd, 0)) break;
        if (!dd.DeviceString[0]) continue;
        char *name = pymcl_wide_to_u8(dd.DeviceString);
        if (!name || !name[0]) { free(name); continue; }
        int dup = 0;
        cJSON *it;
        cJSON_ArrayForEach(it, physical) {
            const char *nm = cJSON_GetStringValue(cJSON_GetObjectItem(it, "name"));
            if (nm && strcmp(nm, name) == 0) dup = 1;
        }
        cJSON_ArrayForEach(it, virt) {
            const char *nm = cJSON_GetStringValue(cJSON_GetObjectItem(it, "name"));
            if (nm && strcmp(nm, name) == 0) dup = 1;
        }
        if (!dup) {
            cJSON *o = cJSON_CreateObject();
            cJSON_AddStringToObject(o, "name", name);
            cJSON_AddStringToObject(o, "driver", "");
            cJSON_AddNumberToObject(o, "vram_mb", 0);
            cJSON_AddItemToArray(si_gpu_is_virtual(name) ? virt : physical, o);
        }
        free(name);
    }
    while (cJSON_GetArraySize(virt) > 0)
        cJSON_AddItemToArray(physical, cJSON_DetachItemFromArray(virt, 0));
    cJSON_Delete(virt);
    return physical;
}

static double si_round1(double x) {
    return (double)((long long)(x * 10.0 + 0.5)) / 10.0;
}

static void si_add_disk(cJSON *arr, const char *path) {
    ULARGE_INTEGER freeb, totalb;
    if (!path[0] || !GetDiskFreeSpaceExA(path, &freeb, &totalb, NULL)) return;
    cJSON *it;
    cJSON_ArrayForEach(it, arr)
        if (strcmp(pstr(it, "path", ""), path) == 0) return;
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "path", path);
    cJSON_AddNumberToObject(o, "total_gb", si_round1((double)totalb.QuadPart / 1073741824.0));
    cJSON_AddNumberToObject(o, "free_gb", si_round1((double)freeb.QuadPart / 1073741824.0));
    cJSON_AddItemToArray(arr, o);
}

static cJSON *si_collect(cJSON *params) {
    cJSON *info = cJSON_CreateObject();
    {
        char ts[32];
        time_t now = time(NULL);
        struct tm tmv;
        localtime_s(&tmv, &now);
        strftime(ts, sizeof(ts), "%Y-%m-%dT%H:%M:%S", &tmv);
        cJSON_AddStringToObject(info, "collected_at", ts);
    }
    {
        char host[MAX_COMPUTERNAME_LENGTH + 2] = "";
        DWORD hn = sizeof(host);
        GetComputerNameA(host, &hn);
        cJSON_AddStringToObject(info, "hostname", host);
    }
    {
        static const char *nt = "SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion";
        char product[128], disp[64], build[32], display[256], plat[64];
        si_reg_str(nt, "ProductName", product, sizeof(product));
        si_reg_str(nt, "DisplayVersion", disp, sizeof(disp));
        si_reg_str(nt, "CurrentBuild", build, sizeof(build));
        if (!build[0]) si_reg_str(nt, "CurrentBuildNumber", build, sizeof(build));
        DWORD ubr = si_reg_dword(nt, "UBR");
        display[0] = 0;
        if (product[0]) snprintf(display, sizeof(display), "%s", product);
        if (disp[0]) snprintf(display + strlen(display), sizeof(display) - strlen(display),
                              "%s%s", display[0] ? " " : "", disp);
        if (build[0]) {
            snprintf(display + strlen(display), sizeof(display) - strlen(display),
                     "%s%s", display[0] ? " " : "", build);
            if (ubr) snprintf(display + strlen(display), sizeof(display) - strlen(display),
                              ".%lu", (unsigned long)ubr);
        }
        if (!display[0]) snprintf(display, sizeof(display), "Windows");
        snprintf(plat, sizeof(plat), "Windows-%s", build[0] ? build : "?");
        const char *machine = getenv("PROCESSOR_ARCHITECTURE");
        cJSON *o = cJSON_CreateObject();
        cJSON_AddStringToObject(o, "name", pymcl_os_name());
        cJSON_AddStringToObject(o, "platform", plat);
        cJSON_AddStringToObject(o, "system", "Windows");
        cJSON_AddStringToObject(o, "release", disp);
        cJSON_AddStringToObject(o, "version", build);
        cJSON_AddStringToObject(o, "display", display);
        cJSON_AddStringToObject(o, "arch", pymcl_arch());
        cJSON_AddStringToObject(o, "machine", machine ? machine : "");
        cJSON_AddItemToObject(info, "os", o);
    }
    {
        char name[256];
        si_reg_str("HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0",
                   "ProcessorNameString", name, sizeof(name));
        si_collapse_spaces(name);
        SYSTEM_INFO si;
        GetSystemInfo(&si);
        cJSON *o = cJSON_CreateObject();
        cJSON_AddStringToObject(o, "name", name);
        cJSON_AddNumberToObject(o, "cores_logical", (double)si.dwNumberOfProcessors);
        cJSON_AddNumberToObject(o, "cores_physical", si_physical_cores());
        cJSON_AddItemToObject(info, "cpu", o);
    }
    {
        MEMORYSTATUSEX ms;
        ms.dwLength = sizeof(ms);
        cJSON *o = cJSON_CreateObject();
        if (GlobalMemoryStatusEx(&ms)) {
            cJSON_AddNumberToObject(o, "total_mb", (double)(ms.ullTotalPhys / (1024 * 1024)));
            cJSON_AddNumberToObject(o, "avail_mb", (double)(ms.ullAvailPhys / (1024 * 1024)));
            cJSON_AddNumberToObject(o, "load_percent", (double)ms.dwMemoryLoad);
            cJSON_AddNumberToObject(o, "total_bytes", (double)ms.ullTotalPhys);
            cJSON_AddNumberToObject(o, "avail_bytes", (double)ms.ullAvailPhys);
        } else {
            cJSON_AddNumberToObject(o, "total_mb", 0);
            cJSON_AddNumberToObject(o, "avail_mb", 0);
            cJSON_AddNumberToObject(o, "load_percent", 0);
            cJSON_AddNumberToObject(o, "total_bytes", 0);
            cJSON_AddNumberToObject(o, "avail_bytes", 0);
        }
        cJSON_AddItemToObject(info, "memory", o);
    }
    cJSON_AddItemToObject(info, "gpus", si_gpus());
    {
        cJSON *disks = cJSON_CreateArray();
        char anchor[4] = "";
        if (g_root[0] && g_root[1] == ':') {
            anchor[0] = g_root[0]; anchor[1] = ':'; anchor[2] = '\\'; anchor[3] = 0;
        }
        si_add_disk(disks, anchor);
        si_add_disk(disks, "C:\\");
        cJSON_AddItemToObject(info, "disks", disks);
    }
    {
        cJSON *o = cJSON_CreateObject();
        cJSON_AddNumberToObject(o, "width", GetSystemMetrics(SM_CXSCREEN));
        cJSON_AddNumberToObject(o, "height", GetSystemMetrics(SM_CYSCREEN));
        int screens = GetSystemMetrics(SM_CMONITORS);
        cJSON_AddNumberToObject(o, "screens", screens > 0 ? screens : 1);
        cJSON_AddItemToObject(info, "display", o);
    }
    {
        int scan = cJSON_IsTrue(cJSON_GetObjectItem(params, "scan_system_java"));
        cJSON *src = scan ? java_all() : java_list_installed();
        cJSON *rows = cJSON_CreateArray();
        cJSON *j;
        int n = 0;
        cJSON_ArrayForEach(j, src) {
            if (n++ >= 16) break;
            cJSON *row = cJSON_CreateObject();
            cJSON_AddStringToObject(row, "name",
                                    cJSON_GetStringValue(cJSON_GetObjectItem(j, "name")) ?: "");
            cJSON *maj = cJSON_GetObjectItem(j, "major");
            cJSON_AddItemToObject(row, "major", maj ? cJSON_Duplicate(maj, 1) : cJSON_CreateNull());
            const char *exe = cJSON_GetStringValue(cJSON_GetObjectItem(j, "exe"));
            if (!exe) exe = cJSON_GetStringValue(cJSON_GetObjectItem(j, "path"));
            cJSON_AddStringToObject(row, "path", exe ?: "");
            cJSON_AddItemToArray(rows, row);
        }
        cJSON_Delete(src);
        cJSON_AddItemToObject(info, "java", rows);
    }
    {
        cJSON *o = cJSON_CreateObject();
        cJSON_AddStringToObject(o, "name", "PyMCL");
        cJSON_AddStringToObject(o, "version", PYMCL_APP_VERSION);
        cJSON_AddBoolToObject(o, "frozen", 1);
        cJSON_AddStringToObject(o, "python", "");
        cJSON_AddStringToObject(o, "root", g_root);
        cJSON_AddNumberToObject(o, "memory_mb", config_int("memory_mb", 0));
        cJSON_AddNumberToObject(o, "download_threads", config_int("download_threads", 0));
        cJSON_AddStringToObject(o, "download_source", config_str("download_source", "auto"));
        cJSON_AddStringToObject(o, "community_source", config_str("community_source", "auto"));
        cJSON_AddItemToObject(info, "launcher", o);
    }
    {
        cJSON *names = NULL;
        instance_list(&names);
        cJSON *rows = cJSON_CreateArray();
        cJSON *nm;
        int n = 0;
        cJSON_ArrayForEach(nm, names) {
            const char *name = cJSON_GetStringValue(nm);
            if (!name || n++ >= 24) continue;
            cJSON *row = cJSON_CreateObject();
            cJSON_AddStringToObject(row, "name", name);
            cJSON *vers = NULL;
            instance_installed_ids(name, &vers);
            while (cJSON_GetArraySize(vers) > 16)
                cJSON_DeleteItemFromArray(vers, 16);
            cJSON_AddItemToObject(row, "versions", vers);
            cJSON *mods = list_instance_files(name, "mods");
            cJSON_AddNumberToObject(row, "mod_count", cJSON_GetArraySize(mods));
            cJSON_Delete(mods);
            char jp[PYMCL_PATH];
            instance_java_pref(name, jp, sizeof(jp));
            cJSON_AddStringToObject(row, "java", jp);
            cJSON_AddItemToArray(rows, row);
        }
        cJSON_Delete(names);
        cJSON_AddItemToObject(info, "instances", rows);
    }
    {
        /* summarize()：display · cpu · ramGB · gpu0，空段跳过 */
        char summary[512] = "";
        const char *parts[4];
        char ram[16] = "";
        cJSON *osd = cJSON_GetObjectItem(info, "os");
        cJSON *cpu = cJSON_GetObjectItem(info, "cpu");
        cJSON *mem = cJSON_GetObjectItem(info, "memory");
        cJSON *gpus = cJSON_GetObjectItem(info, "gpus");
        double total_mb = cJSON_GetObjectItem(mem, "total_mb")
            ? cJSON_GetObjectItem(mem, "total_mb")->valuedouble : 0;
        if (total_mb > 0)
            snprintf(ram, sizeof(ram), "%dGB", (int)(total_mb / 1024.0 + 0.5));
        cJSON *gpu0 = cJSON_GetArrayItem(gpus, 0);
        parts[0] = pstr(osd, "display", "");
        parts[1] = pstr(cpu, "name", "");
        parts[2] = ram;
        parts[3] = gpu0 ? pstr(gpu0, "name", "") : "";
        for (int i = 0; i < 4; i++) {
            if (!parts[i][0]) continue;
            snprintf(summary + strlen(summary), sizeof(summary) - strlen(summary),
                     "%s%s", summary[0] ? " · " : "", parts[i]);
        }
        cJSON_AddStringToObject(info, "summary", summary);
    }
    return info;
}

/* 对齐 mclauncher/sysinfo.get_smart_recommendation 的推荐值算法 */
static cJSON *si_recommendation(void) {
    cJSON *rec = cJSON_CreateObject();
    int memory_mb = 4096, cpu_count = 4;
    double total_gb = 8.0;
    MEMORYSTATUSEX ms;
    ms.dwLength = sizeof(ms);
    if (GlobalMemoryStatusEx(&ms) && ms.ullTotalPhys) {
        total_gb = (double)ms.ullTotalPhys / (1024.0 * 1024.0 * 1024.0);
        if (total_gb >= 32) memory_mb = 12288;
        else if (total_gb >= 16) memory_mb = 8192;
        else if (total_gb >= 8) memory_mb = 4096;
        else memory_mb = 2048;
        int safe = (int)(total_gb * 0.75 * 1024);
        if (memory_mb > safe) memory_mb = safe > 1024 ? safe : 1024;
    }
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    if (si.dwNumberOfProcessors > 0) cpu_count = (int)si.dwNumberOfProcessors;
    else {
        int phys = si_physical_cores();
        if (phys > 0) cpu_count = phys;
    }
    cJSON_AddNumberToObject(rec, "memory_mb", memory_mb);
    cJSON_AddNumberToObject(rec, "java_major", 17);
    cJSON_AddNumberToObject(rec, "window_width", 854);
    cJSON_AddNumberToObject(rec, "window_height", 480);
    cJSON_AddStringToObject(rec, "gc_preset", "auto");
    cJSON_AddNumberToObject(rec, "cpu_count", cpu_count);
    cJSON_AddNumberToObject(rec, "total_ram_gb", si_round1(total_gb));
    return rec;
}

cJSON *rpc_align_call(const char *method, cJSON *params, sse_emit_fn emit) {
    if (!method) return NULL;

    /* ---- 多开 / 游戏运行状态（对齐 bridge/api.py） ----
     * 这三个方法以前落到一次性 py_rpc：子进程里 _game_proc 恒为 None，
     * is_game_running 永远回 false；set/allow 则在纯 C 安装下直接报
     * 「需要 Python」。游戏进程和 config.json 都在原生侧，就地回答。 */
    if (strcmp(method, "is_game_running") == 0)
        return cJSON_CreateBool(backend_game_alive());
    if (strcmp(method, "allow_multi_instance") == 0)
        return cJSON_CreateBool(config_bool("allow_multi_instance", 0));
    if (strcmp(method, "set_multi_instance") == 0) {
        config_set_bool("allow_multi_instance",
                        cJSON_IsTrue(cJSON_GetObjectItem(params, "allow")));
        config_save();
        return cJSON_CreateTrue();
    }

    /* ---- accounts ---- */
    if (strcmp(method, "get_account_rows") == 0) {
        cJSON *root = accounts_load();
        cJSON *out = cJSON_CreateArray();
        const char *active = cJSON_GetStringValue(cJSON_GetObjectItem(root, "active"));
        cJSON *it;
        cJSON_ArrayForEach(it, cJSON_GetObjectItem(root, "accounts")) {
            cJSON *row = cJSON_CreateObject();
            const char *nm = cJSON_GetStringValue(cJSON_GetObjectItem(it, "name")) ?: "";
            cJSON_AddStringToObject(row, "name", nm);
            cJSON_AddStringToObject(row, "type", cJSON_GetStringValue(cJSON_GetObjectItem(it, "type")) ?: "offline");
            cJSON_AddStringToObject(row, "uuid", cJSON_GetStringValue(cJSON_GetObjectItem(it, "uuid")) ?: "");
            cJSON_AddStringToObject(row, "api", cJSON_GetStringValue(cJSON_GetObjectItem(it, "api")) ?: "");
            cJSON_AddStringToObject(row, "avatar", "");
            cJSON_AddStringToObject(row, "body", "");
            cJSON_AddBoolToObject(row, "active", active && strcmp(active, nm) == 0);
            cJSON_AddItemToArray(out, row);
        }
        cJSON_Delete(root);
        return out;
    }
    if (strcmp(method, "add_offline_account") == 0) {
        const char *user = pstr(params, "username", pstr(params, "name", "Player"));
        cJSON *acc = account_offline(user);
        /* optional skin */
        const char *skin = pstr(params, "skin", "");
        if (skin[0]) cJSON_AddStringToObject(acc, "skin", skin);
        cJSON *root = accounts_load();
        cJSON *arr = cJSON_GetObjectItem(root, "accounts");
        if (!cJSON_IsArray(arr)) {
            arr = cJSON_CreateArray();
            cJSON_AddItemToObject(root, "accounts", arr);
        }
        cJSON_AddItemToArray(arr, cJSON_Duplicate(acc, 1));
        cJSON_ReplaceItemInObject(root, "active", cJSON_CreateString(cJSON_GetStringValue(cJSON_GetObjectItem(acc, "name"))));
        accounts_save(root);
        cJSON_Delete(root);
        if (emit) emit("ui_changed", cJSON_CreateObject());
        cJSON *name = cJSON_CreateString(cJSON_GetStringValue(cJSON_GetObjectItem(acc, "name")));
        cJSON_Delete(acc);
        return name;
    }
    if (strcmp(method, "remove_account") == 0) {
        const char *name = pstr(params, "name", "");
        cJSON *root = accounts_load();
        cJSON *arr = cJSON_GetObjectItem(root, "accounts");
        cJSON *next = cJSON_CreateArray();
        cJSON *it;
        cJSON_ArrayForEach(it, arr) {
            const char *nm = cJSON_GetStringValue(cJSON_GetObjectItem(it, "name"));
            if (nm && strcmp(nm, name) == 0) continue;
            cJSON_AddItemToArray(next, cJSON_Duplicate(it, 1));
        }
        cJSON_ReplaceItemInObject(root, "accounts", next);
        const char *active = cJSON_GetStringValue(cJSON_GetObjectItem(root, "active"));
        if (active && strcmp(active, name) == 0)
            cJSON_ReplaceItemInObject(root, "active", cJSON_CreateNull());
        accounts_save(root);
        cJSON_Delete(root);
        if (emit) emit("ui_changed", cJSON_CreateObject());
        return cJSON_CreateTrue();
    }
    if (strcmp(method, "set_active_account") == 0) {
        const char *name = pstr(params, "name", "");
        cJSON *root = accounts_load();
        cJSON_ReplaceItemInObject(root, "active", cJSON_CreateString(name));
        accounts_save(root);
        cJSON_Delete(root);
        if (emit) emit("ui_changed", cJSON_CreateObject());
        return cJSON_CreateString(name);
    }
    if (strcmp(method, "authlib_presets") == 0) {
        cJSON *out = cJSON_CreateArray();
        cJSON *a = cJSON_CreateObject();
        cJSON_AddStringToObject(a, "name", "Little Skin");
        cJSON_AddStringToObject(a, "api", "https://littleskin.cn/api/yggdrasil");
        cJSON_AddItemToArray(out, a);
        cJSON *b = cJSON_CreateObject();
        cJSON_AddStringToObject(b, "name", "Blessing Skin（自填）");
        cJSON_AddStringToObject(b, "api", "");
        cJSON_AddItemToArray(out, b);
        return out;
    }

    /* ---- mods ---- */
    if (strcmp(method, "enable_mod") == 0) {
        if (set_mod_enabled(pstr(params, "instance", "default"), pstr(params, "filename", ""), 1) != 0)
            return NULL;
        if (emit) emit("ui_changed", cJSON_CreateObject());
        return cJSON_CreateString(pstr(params, "filename", ""));
    }
    if (strcmp(method, "disable_mod") == 0) {
        if (set_mod_enabled(pstr(params, "instance", "default"), pstr(params, "filename", ""), 0) != 0)
            return NULL;
        if (emit) emit("ui_changed", cJSON_CreateObject());
        return cJSON_CreateString(pstr(params, "filename", ""));
    }
    if (strcmp(method, "get_installed_mod_entries") == 0)
        return list_mod_entries(pstr(params, "instance", "default"));
    if (strcmp(method, "open_global_mods") == 0) {
        char p[PYMCL_PATH];
        pymcl_path_join(p, sizeof(p), g_root, "global_mods");
        pymcl_ensure_dir(p);
        pymcl_open_folder(p);
        return cJSON_CreateTrue();
    }

    /* ---- servers ---- */
    if (strcmp(method, "list_servers") == 0) {
        char path[PYMCL_PATH];
        servers_path(pstr(params, "instance", "default"), path, sizeof(path));
        cJSON *arr = pymcl_read_json(path);
        if (!cJSON_IsArray(arr)) { cJSON_Delete(arr); arr = cJSON_CreateArray(); }
        cJSON *out = cJSON_CreateArray();
        int i = 0;
        cJSON *it;
        cJSON_ArrayForEach(it, arr) {
            if (!cJSON_IsObject(it)) continue;
            cJSON *row = cJSON_Duplicate(it, 1);
            cJSON_AddNumberToObject(row, "index", i++);
            if (!cJSON_GetObjectItem(row, "port")) cJSON_AddNumberToObject(row, "port", 25565);
            if (!cJSON_GetObjectItem(row, "name"))
                cJSON_AddStringToObject(row, "name", cJSON_GetStringValue(cJSON_GetObjectItem(row, "ip")) ?: "");
            cJSON_AddItemToArray(out, row);
        }
        cJSON_Delete(arr);
        return out;
    }
    if (strcmp(method, "add_server") == 0) {
        const char *inst = pstr(params, "instance", "default");
        char path[PYMCL_PATH];
        servers_path(inst, path, sizeof(path));
        cJSON *arr = pymcl_read_json(path);
        if (!cJSON_IsArray(arr)) { cJSON_Delete(arr); arr = cJSON_CreateArray(); }
        cJSON *e = cJSON_CreateObject();
        const char *ip = pstr(params, "ip", pstr(params, "address", ""));
        if (!ip[0]) { cJSON_Delete(arr); cJSON_Delete(e); pymcl_set_error("服务器地址不能为空"); return NULL; }
        cJSON_AddStringToObject(e, "ip", ip);
        cJSON_AddStringToObject(e, "name", pstr(params, "name", ip));
        int port = 25565;
        if (cJSON_IsNumber(cJSON_GetObjectItem(params, "port")))
            port = (int)cJSON_GetObjectItem(params, "port")->valuedouble;
        cJSON_AddNumberToObject(e, "port", port);
        cJSON_AddStringToObject(e, "description", pstr(params, "description", ""));
        cJSON_AddStringToObject(e, "icon", pstr(params, "icon", ""));
        cJSON_AddBoolToObject(e, "hidden", 0);
        cJSON_AddItemToArray(arr, e);
        pymcl_write_json(path, arr);
        cJSON *ret = cJSON_Duplicate(e, 1);
        cJSON_Delete(arr);
        if (emit) emit("ui_changed", cJSON_CreateObject());
        return ret;
    }
    if (strcmp(method, "delete_server") == 0) {
        const char *inst = pstr(params, "instance", "default");
        int idx = cJSON_IsNumber(cJSON_GetObjectItem(params, "index"))
                      ? (int)cJSON_GetObjectItem(params, "index")->valuedouble : -1;
        char path[PYMCL_PATH];
        servers_path(inst, path, sizeof(path));
        cJSON *arr = pymcl_read_json(path);
        if (!cJSON_IsArray(arr)) { cJSON_Delete(arr); return cJSON_CreateTrue(); }
        cJSON *next = cJSON_CreateArray();
        int i = 0;
        cJSON *it;
        cJSON_ArrayForEach(it, arr) {
            if (i++ == idx) continue;
            cJSON_AddItemToArray(next, cJSON_Duplicate(it, 1));
        }
        cJSON_Delete(arr);
        pymcl_write_json(path, next);
        cJSON_Delete(next);
        if (emit) emit("ui_changed", cJSON_CreateObject());
        return cJSON_CreateTrue();
    }
    if (strcmp(method, "update_server") == 0) {
        cJSON *r = py_rpc_call(method, params);
        if (r) return r;
        /* C-only 兜底：以前直接返回 true——「编辑服务器」看似保存实际没写，
         * 假成功。这里按下标真改真写。 */
        const char *inst = pstr(params, "instance", "default");
        int idx = cJSON_IsNumber(cJSON_GetObjectItem(params, "index"))
                      ? (int)cJSON_GetObjectItem(params, "index")->valuedouble : -1;
        char path[PYMCL_PATH];
        servers_path(inst, path, sizeof(path));
        cJSON *arr = pymcl_read_json(path);
        cJSON *entry = cJSON_IsArray(arr) ? cJSON_GetArrayItem(arr, idx) : NULL;
        if (!entry) {
            cJSON_Delete(arr);
            pymcl_set_error("服务器不存在: index=%d", idx);
            return NULL;
        }
        const char *skeys[] = { "name", "ip", "description", "icon" };
        for (size_t i = 0; i < sizeof(skeys) / sizeof(skeys[0]); i++) {
            const char *v = cJSON_GetStringValue(cJSON_GetObjectItem(params, skeys[i]));
            if (v) {
                cJSON_DeleteItemFromObject(entry, skeys[i]);
                cJSON_AddStringToObject(entry, skeys[i], v);
            }
        }
        cJSON *pn = cJSON_GetObjectItem(params, "port");
        if (cJSON_IsNumber(pn)) {
            cJSON_DeleteItemFromObject(entry, "port");
            cJSON_AddNumberToObject(entry, "port", (int)pn->valuedouble);
        }
        cJSON *hd = cJSON_GetObjectItem(params, "hidden");
        if (cJSON_IsBool(hd)) {
            cJSON_DeleteItemFromObject(entry, "hidden");
            cJSON_AddBoolToObject(entry, "hidden", cJSON_IsTrue(hd));
        }
        pymcl_write_json(path, arr);
        cJSON *ret = cJSON_Duplicate(entry, 1);
        cJSON_Delete(arr);
        if (emit) emit("ui_changed", cJSON_CreateObject());
        return ret;
    }
    if (strcmp(method, "import_servers") == 0) {
        /* EziApp 服务器页的「导入」以前在纯 C 桥下直接 unknown method；
         * 即使装了 Python，一次性 py_rpc 也只是碰巧和这里共用同一份
         * servers.json。逻辑对齐 bridge/api.py：先试 JSON 数组再按行解析。 */
        const char *inst = pstr(params, "instance", "default");
        const char *text = pstr(params, "text", "");
        char path[PYMCL_PATH];
        servers_path(inst, path, sizeof(path));
        cJSON *arr = pymcl_read_json(path);
        if (!cJSON_IsArray(arr)) { cJSON_Delete(arr); arr = cJSON_CreateArray(); }
        const char *head = text;
        while (*head == ' ' || *head == '\t' || *head == '\r' || *head == '\n'
               || *head == '\v' || *head == '\f') head++;
        cJSON *data = (*head == '[') ? cJSON_Parse(text) : NULL;
        int imported;
        if (cJSON_IsArray(data)) imported = pymcl_servers_import_json(arr, data);
        else imported = pymcl_servers_import_text(arr, text);
        cJSON_Delete(data);
        if (imported > 0) {
            if (pymcl_write_json(path, arr) != 0) {
                cJSON_Delete(arr);
                pymcl_set_error("写入 servers.json 失败: %s", path);
                return NULL;
            }
            if (emit) emit("ui_changed", cJSON_CreateObject());
        }
        cJSON_Delete(arr);
        return cJSON_CreateNumber(imported);
    }
    if (strcmp(method, "export_servers") == 0) {
        char path[PYMCL_PATH];
        servers_path(pstr(params, "instance", "default"), path, sizeof(path));
        cJSON *arr = pymcl_read_json(path);
        if (!cJSON_IsArray(arr)) { cJSON_Delete(arr); arr = cJSON_CreateArray(); }
        char *txt = pymcl_servers_export_text(arr);
        cJSON_Delete(arr);
        if (!txt) { pymcl_set_error("导出服务器列表失败"); return NULL; }
        cJSON *ret = cJSON_CreateString(txt);
        free(txt);
        return ret;
    }

    /* ---- playtime ---- */
    if (strcmp(method, "get_all_playtime") == 0) {
        /* 对齐 mclauncher/playtime.py：返回解包后的 instances 映射
         * （{实例: {total, versions}}）。以前原样返回整个文件
         * （{"instances": {...}}），WinUI 按前者遍历，时长页永远是空的。 */
        char path[PYMCL_PATH];
        playtime_path(path, sizeof(path));
        cJSON *j = pymcl_read_json(path);
        cJSON *insts = j ? cJSON_DetachItemFromObject(j, "instances") : NULL;
        cJSON_Delete(j);
        if (!cJSON_IsObject(insts)) {
            cJSON_Delete(insts);
            insts = cJSON_CreateObject();
        }
        return insts;
    }
    if (strcmp(method, "get_total_playtime") == 0) {
        /* EziApp 时长页把 get_all_playtime 和 get_total_playtime 放进同一个
         * Promise.all；以前后者 unknown method，整页报「加载游玩时长失败」，
         * 尽管数据就躺在 playtime.json 里。 */
        char path[PYMCL_PATH];
        playtime_path(path, sizeof(path));
        cJSON *j = pymcl_read_json(path);
        double total = pymcl_playtime_total(j);
        cJSON_Delete(j);
        return cJSON_CreateNumber(total);
    }
    if (strcmp(method, "format_playtime") == 0) {
        long long sec = 0;
        if (cJSON_IsNumber(cJSON_GetObjectItem(params, "seconds")))
            sec = (long long)cJSON_GetObjectItem(params, "seconds")->valuedouble;
        char buf[64];
        format_playtime(sec, buf, sizeof(buf));
        return cJSON_CreateString(buf);
    }
    if (strcmp(method, "clear_playtime") == 0) {
        const char *inst = pstr(params, "instance", "");
        char path[PYMCL_PATH];
        playtime_path(path, sizeof(path));
        if (!inst[0]) {
            cJSON *empty = cJSON_Parse("{\"instances\":{}}");
            pymcl_write_json(path, empty);
            cJSON_Delete(empty);
        } else {
            cJSON *j = pymcl_read_json(path);
            if (!j) j = cJSON_Parse("{\"instances\":{}}");
            cJSON *insts = cJSON_GetObjectItem(j, "instances");
            if (cJSON_IsObject(insts)) cJSON_DeleteItemFromObject(insts, inst);
            pymcl_write_json(path, j);
            cJSON_Delete(j);
        }
        return cJSON_CreateTrue();
    }

    /* ---- version settings ---- */
    if (strcmp(method, "get_version_settings") == 0) {
        char path[PYMCL_PATH];
        version_settings_path(pstr(params, "instance", "default"), pstr(params, "version", ""), path, sizeof(path));
        cJSON *def = vs_defaults();
        cJSON *stored = pymcl_read_json(path);
        if (cJSON_IsObject(stored)) {
            cJSON *it = stored->child;
            while (it) {
                cJSON *n = it->next;
                cJSON_DeleteItemFromObject(def, it->string);
                cJSON_AddItemToObject(def, it->string, cJSON_Duplicate(it, 1));
                it = n;
            }
        }
        cJSON_Delete(stored);
        return def;
    }
    if (strcmp(method, "save_version_settings") == 0) {
        const char *inst = pstr(params, "instance", "default");
        const char *ver = pstr(params, "version", "");
        cJSON *data = cJSON_GetObjectItem(params, "data");
        if (!cJSON_IsObject(data)) data = params;
        char path[PYMCL_PATH], parent[PYMCL_PATH];
        version_settings_path(inst, ver, path, sizeof(path));
        pymcl_parent(path, parent, sizeof(parent));
        pymcl_ensure_dir(parent);
        cJSON *cur = vs_defaults();
        cJSON *stored = pymcl_read_json(path);
        if (cJSON_IsObject(stored)) {
            cJSON *it = stored->child;
            while (it) {
                cJSON *n = it->next;
                cJSON_DeleteItemFromObject(cur, it->string);
                cJSON_AddItemToObject(cur, it->string, cJSON_Duplicate(it, 1));
                it = n;
            }
        }
        cJSON_Delete(stored);
        if (cJSON_IsObject(data)) {
            cJSON *it = data->child;
            while (it) {
                cJSON *n = it->next;
                if (it->string && strcmp(it->string, "instance") && strcmp(it->string, "version")
                    && strcmp(it->string, "data")) {
                    cJSON_DeleteItemFromObject(cur, it->string);
                    cJSON_AddItemToObject(cur, it->string, cJSON_Duplicate(it, 1));
                }
                it = n;
            }
        }
        pymcl_write_json(path, cur);
        if (emit) emit("ui_changed", cJSON_CreateObject());
        return cur;
    }

    /* ---- preflight / crash (python preferred, safe stub fallback) ---- */
    if (strcmp(method, "preflight_launch") == 0) {
        cJSON *r = py_rpc_call(method, params);
        if (r) return r;
        /* C-only 兜底：形状必须与 mclauncher/preflight.py 一致
         * （{"ok", "items": [{level, code, title, detail}]}）。以前这里发
         * "issues"+"message"，WinUI 只认 "items"+"title/detail"，预检结果
         * 被整个丢掉，失败也照样放行启动。 */
        const char *inst = pstr(params, "instance", "default");
        const char *ver = pstr(params, "version", "");
        cJSON *out = cJSON_CreateObject();
        cJSON *items = cJSON_CreateArray();
        if (!ver[0]) {
            cJSON *it = cJSON_CreateObject();
            cJSON_AddStringToObject(it, "level", "error");
            cJSON_AddStringToObject(it, "code", "no_version");
            cJSON_AddStringToObject(it, "title", "请先选择版本");
            cJSON_AddStringToObject(it, "detail", "没有指定要启动的游戏版本");
            cJSON_AddItemToArray(items, it);
        } else if (!instance_has_version(inst, ver)) {
            cJSON *it = cJSON_CreateObject();
            cJSON_AddStringToObject(it, "level", "error");
            cJSON_AddStringToObject(it, "code", "missing_version");
            cJSON_AddStringToObject(it, "title", "版本未安装");
            cJSON_AddStringToObject(it, "detail", "请先在下载页安装该版本");
            cJSON_AddItemToArray(items, it);
        }
        cJSON_AddBoolToObject(out, "ok", cJSON_GetArraySize(items) == 0);
        cJSON_AddItemToObject(out, "items", items);
        return out;
    }
    if (strcmp(method, "apply_crash_action") == 0) {
        cJSON *r = py_rpc_call(method, params);
        if (r) return r;
        cJSON *action = cJSON_GetObjectItem(params, "action");
        const char *aid = pstr(action, "id", "");
        if (strcmp(aid, "open_mods_folder") == 0 || strcmp(aid, "open_crash_file") == 0) {
            const char *inst = pstr(action, "instance", "default");
            char ip[PYMCL_PATH], mods[PYMCL_PATH];
            instance_path(inst, ip, sizeof(ip));
            pymcl_path_join(mods, sizeof(mods), ip, "mods");
            pymcl_ensure_dir(mods);
            pymcl_open_folder(mods);
            cJSON *o = cJSON_CreateObject();
            cJSON_AddBoolToObject(o, "ok", 1);
            cJSON_AddStringToObject(o, "message", "已打开目录");
            return o;
        }
        cJSON *o = cJSON_CreateObject();
        cJSON_AddBoolToObject(o, "ok", 0);
        cJSON_AddStringToObject(o, "message", "需要 Python 桥完成此修复动作");
        return o;
    }

    /* ---- sysinfo（python 优先拿 WMI 显卡/驱动细节，纯 C 时用 Win32 兜底） ----
     * EziApp 工具页的「查看系统信息 / 查看推荐配置」按钮以前在纯 C 桥上
     * 直接报 unknown method，两个按钮一点就错。 */
    if (strcmp(method, "collect_sysinfo") == 0) {
        cJSON *r = py_rpc_call(method, params);
        if (r) return r;
        return si_collect(params);
    }
    if (strcmp(method, "get_smart_recommendation") == 0) {
        cJSON *r = py_rpc_call(method, params);
        if (r) return r;
        return si_recommendation();
    }

    /* ---- AI 流式：常驻子进程转发事件（一次性 py_rpc 会立刻杀死流式线程） ---- */
    if (strcmp(method, "ai_send") == 0) {
        pthread_mutex_lock(&g_ai_mu);
        int busy = g_ai_proc != NULL && WaitForSingleObject(g_ai_proc, 0) == WAIT_TIMEOUT;
        pthread_mutex_unlock(&g_ai_mu);
        if (busy) {
            cJSON *o = cJSON_CreateObject();
            cJSON_AddBoolToObject(o, "ok", 0);
            cJSON_AddStringToObject(o, "message", "上一条还在处理");
            return o;
        }
        g_ai_emit = emit;
        char tmpdir[PYMCL_PATH], pin[PYMCL_PATH];
        static volatile LONG s_ai_seq;
        GetTempPathA(sizeof(tmpdir), tmpdir);
        snprintf(pin, sizeof(pin), "%spymcl-ai-%u-%ld.json", tmpdir,
                 (unsigned)GetCurrentProcessId(), (long)InterlockedIncrement(&s_ai_seq));
        {
            cJSON *body = params ? cJSON_Duplicate(params, 1) : cJSON_CreateObject();
            char *txt = cJSON_PrintUnformatted(body);
            cJSON_Delete(body);
            if (!txt) { pymcl_set_error("参数序列化失败"); return NULL; }
            pymcl_write_file(pin, txt, strlen(txt));
            free(txt);
        }
        if (ai_spawn(pin) != 0) {
            DeleteFileA(pin);
            return NULL;
        }
        cJSON *o = cJSON_CreateObject();
        cJSON_AddBoolToObject(o, "ok", 1);
        cJSON_AddBoolToObject(o, "started", 1);
        return o;
    }
    if (strcmp(method, "ai_stop") == 0 || strcmp(method, "ai_confirm") == 0
        || strcmp(method, "ai_answer") == 0) {
        pthread_mutex_lock(&g_ai_mu);
        int live = g_ai_stdin != NULL;
        pthread_mutex_unlock(&g_ai_mu);
        if (live) {
            char linebuf[4096];
            if (strcmp(method, "ai_stop") == 0) {
                snprintf(linebuf, sizeof(linebuf), "{\"op\":\"stop\"}");
            } else if (strcmp(method, "ai_confirm") == 0) {
                snprintf(linebuf, sizeof(linebuf), "{\"op\":\"confirm\",\"ok\":%s}",
                         cJSON_IsTrue(cJSON_GetObjectItem(params, "ok")) ? "true" : "false");
            } else {
                cJSON *res = cJSON_GetObjectItem(params, "result");
                char *rt = res ? cJSON_PrintUnformatted(res) : NULL;
                snprintf(linebuf, sizeof(linebuf), "{\"op\":\"answer\",\"result\":%s}",
                         rt ? rt : "null");
                free(rt);
            }
            ai_write_line(linebuf);
        }
        /* 没有在飞的子进程时无事可做；与 Python 桥语义一致返回 ok */
        cJSON *o = cJSON_CreateObject();
        cJSON_AddBoolToObject(o, "ok", 1);
        return o;
    }

    if (strcmp(method, "feedback_history") == 0) {
        /* 与 mclauncher/feedback.py 共用根目录的 feedback_history.json：
         * py_rpc 路径提交成功后写的就是这份文件。以前该方法 unknown，
         * EziApp 反馈页在 Promise.all 里直接整页挂掉，连提交表单都进不去。 */
        char path[PYMCL_PATH];
        pymcl_path_join(path, sizeof(path), g_root, "feedback_history.json");
        cJSON *rows = pymcl_read_json(path);
        if (!cJSON_IsArray(rows)) { cJSON_Delete(rows); rows = cJSON_CreateArray(); }
        return rows;
    }

    /* ---- feedback / help / news / update / cleaner / AI / terracotta ---- */
    if (strcmp(method, "submit_feedback") == 0 || strcmp(method, "help_articles") == 0
        || strcmp(method, "help_article") == 0 || strcmp(method, "cached_news") == 0
        || strcmp(method, "fetch_news") == 0 || strcmp(method, "check_update") == 0
        || strcmp(method, "cleaner_preview") == 0 || strcmp(method, "cleaner_apply") == 0
        || strcmp(method, "test_ai_connection") == 0 || strcmp(method, "ai_list_chats") == 0
        || strcmp(method, "ai_new_chat") == 0 || strcmp(method, "ai_delete_chat") == 0
        || strcmp(method, "ai_set_active") == 0 || strcmp(method, "terracotta_snapshot") == 0
        || strcmp(method, "terracotta_host") == 0 || strcmp(method, "terracotta_join") == 0
        || strcmp(method, "terracotta_idle") == 0
        || strcmp(method, "terracotta_allow_firewall") == 0
        || strcmp(method, "terracotta_open_firewall_settings") == 0
        || strcmp(method, "terracotta_shutdown") == 0 || strcmp(method, "lan_hint") == 0
        || strcmp(method, "list_loader_versions") == 0 || strcmp(method, "list_catalog_files") == 0
        || strcmp(method, "search_worlds") == 0) {
        /* 任务型方法（install_world / repair_version / export_modpack / start_* /
         * terracotta_prepare）不再走这里的一次性调用：backend.c 已把它们包进
         * 原生任务机制，UI 能拿到 task_added/finished 事件。 */
        cJSON *r = py_rpc_call(method, params);
        if (r) {
            if (strcmp(method, "terracotta_snapshot") == 0 && cJSON_IsObject(r)) {
                /* 一次性 Python 进程里 _game_proc 恒为 None，game_running 永远
                 * 是 false：EziApp 开房前永远弹「游戏还没启动」确认，进入世界
                 * 在游戏已开着时还会再启动一个。以原生 g_game 的真实状态为准。 */
                cJSON_DeleteItemFromObject(r, "game_running");
                cJSON_AddBoolToObject(r, "game_running", backend_game_alive());
            }
            return r;
        }
        /* graceful empty fallbacks so UI stays usable without Python */
        if (strcmp(method, "help_articles") == 0 || strcmp(method, "cached_news") == 0
            || strcmp(method, "fetch_news") == 0 || strcmp(method, "list_loader_versions") == 0
            || strcmp(method, "list_catalog_files") == 0 || strcmp(method, "search_worlds") == 0)
            return cJSON_CreateArray();
        if (strcmp(method, "terracotta_snapshot") == 0) {
            /* 形状对齐 mclauncher/terracotta.snapshot 的 unsupported 分支：
             * 以前发 state=idle + message，UI 只认 label/supported，状态栏
             * 一片空白还以为能用。 */
            cJSON *o = cJSON_CreateObject();
            cJSON_AddBoolToObject(o, "supported", 0);
            cJSON_AddBoolToObject(o, "installed", 0);
            cJSON_AddBoolToObject(o, "running", 0);
            cJSON_AddStringToObject(o, "state", "unsupported");
            cJSON_AddStringToObject(o, "label", "陶瓦联机需要 Python 后端");
            cJSON_AddStringToObject(o, "room", "");
            cJSON_AddStringToObject(o, "url", "");
            cJSON_AddStringToObject(o, "error", "");
            cJSON_AddBoolToObject(o, "game_running", backend_game_alive());
            return o;
        }
        if (strcmp(method, "lan_hint") == 0)
            return cJSON_CreateString("联机功能在纯 C 桥下有限，请安装 Python 环境以启用陶瓦。");
        /* 读操作降级成空列表让页面能渲染；但新建/删除/切换以前也返回空
         * store 假装成功——点「新建对话」什么都不发生、也不报错。改成诚实报错。 */
        if (strcmp(method, "ai_list_chats") == 0) {
            cJSON *o = cJSON_CreateObject();
            cJSON_AddItemToObject(o, "chats", cJSON_CreateArray());
            cJSON_AddStringToObject(o, "active_id", "");
            return o;
        }
        if (strcmp(method, "ai_new_chat") == 0 || strcmp(method, "ai_delete_chat") == 0
            || strcmp(method, "ai_set_active") == 0) {
            pymcl_set_error("AI 对话管理需要 Python 后端（未找到可用 Python）");
            return NULL;
        }
        if (strcmp(method, "check_update") == 0) {
            cJSON *o = cJSON_CreateObject();
            cJSON_AddBoolToObject(o, "update", 0);
            cJSON_AddStringToObject(o, "message", "当前为 C 桥；自更新需 Python");
            return o;
        }
        /* test_ai_connection 以前把「需要 Python」当成功结果返回，
         * UI 弹出「AI 连接成功」toast，正文却是无法使用的原因。诚实报错。 */
        if (strcmp(method, "test_ai_connection") == 0) {
            pymcl_set_error("AI 需要 Python 桥（未找到可用 Python）");
            return NULL;
        }
        /* submit_feedback 以前假成功返回 true：用户的反馈正文被 UI 清空、
         * 实际哪儿都没送到。诚实报错，让表单留在原地。 */
        if (strcmp(method, "submit_feedback") == 0) {
            pymcl_set_error("反馈上传需要 Python 后端（未找到可用 Python），内容未发送");
            return NULL;
        }
        if (strcmp(method, "terracotta_allow_firewall") == 0)
            return cJSON_CreateString("请手动放行防火墙，或安装 Python 后端");
        /* async-looking methods: return fake task id string won't work — return error */
        pymcl_set_error("方法 %s 需要 Python 桥（设置 PYMCL_PYTHON）", method);
        return NULL;
    }

    return NULL; /* not handled here */
}
