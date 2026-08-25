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
    return py_rpc_call_c(method, params, 120, NULL, NULL);
}

cJSON *py_rpc_call_t(const char *method, cJSON *params, int timeout_secs) {
    return py_rpc_call_c(method, params, timeout_secs, NULL, NULL);
}

cJSON *py_rpc_call_c(const char *method, cJSON *params, int timeout_secs,
                     int (*cancel)(void *), void *cud) {
    char py[PYMCL_PATH], script[PYMCL_PATH], pin[PYMCL_PATH], pout[PYMCL_PATH], tmpdir[PYMCL_PATH];
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
    /* 任务线程里的长调用和 UI 线程的快调用会并发；文件名只按 pid 会互踩。 */
    {
        static volatile LONG seq;
        unsigned n = (unsigned)InterlockedIncrement((volatile LONG *)&seq);
        snprintf(pin, sizeof(pin), "%spymcl-rpc-in-%u-%u.json", tmpdir, (unsigned)GetCurrentProcessId(), n);
        snprintf(pout, sizeof(pout), "%spymcl-rpc-out-%u-%u.json", tmpdir, (unsigned)GetCurrentProcessId(), n);
    }

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
    int rc = pymcl_run_process_cancelable(argv, argc, g_root, NULL, NULL,
                                          timeout_secs > 0 ? timeout_secs : 120,
                                          cancel, cud);
    DeleteFileA(pin);
    if (rc == PYMCL_PROC_CANCELLED) {
        /* 用户取消：子进程已被杀，别再去读残缺的输出文件 */
        DeleteFileA(pout);
        pymcl_set_error("已取消");
        return NULL;
    }
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

static void format_playtime(long long sec, char *out, size_t n) {
    if (sec < 0) sec = 0;
    long long h = sec / 3600, m = (sec % 3600) / 60, s = sec % 60;
    if (h > 0) snprintf(out, n, "%lld 小时 %lld 分", h, m);
    else if (m > 0) snprintf(out, n, "%lld 分 %lld 秒", m, s);
    else snprintf(out, n, "%lld 秒", s);
}

/* URL 路径段编码（UTF-8 字节逐个百分号编码，字母数字与 -_. 除外） */
static void path_enc(const char *s, char *out, size_t n) {
    size_t o = 0;
    out[0] = 0;
    for (s = s ? s : ""; *s && o + 4 < n; s++) {
        unsigned char c = (unsigned char)*s;
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')
            || c == '-' || c == '_' || c == '.')
            out[o++] = (char)c;
        else { snprintf(out + o, n - o, "%%%02X", c); o = strlen(out); }
    }
    out[o] = 0;
}

/* 皮肤站根地址：去掉尾部 /api/yggdrasil 或 /yggdrasil，只留 scheme://host。
 * 与 mclauncher/skin.py 的 _site_origin 一致。 */
static void skin_origin(const char *api, char *out, size_t n) {
    char raw[512];
    snprintf(raw, sizeof(raw), "%s", api ? api : "");
    size_t len = strlen(raw);
    while (len && raw[len - 1] == '/') raw[--len] = 0;
    const char *sufs[] = { "/api/yggdrasil", "/yggdrasil" };
    for (int i = 0; i < 2; i++) {
        size_t sl = strlen(sufs[i]);
        if (len > sl && strcmp(raw + len - sl, sufs[i]) == 0) { raw[len - sl] = 0; break; }
    }
    char *p = strstr(raw, "://");
    if (p) {
        char *slash = strchr(p + 3, '/');
        if (slash) *slash = 0;
        snprintf(out, n, "%s", raw);
    } else {
        char *slash = strchr(raw, '/');
        if (slash) *slash = 0;
        snprintf(out, n, "https://%s", raw);
    }
}

/* 头像 / 全身预览 URL，规则与 mclauncher/skin.py 的 avatar_url / body_url 一致。 */
static void skin_urls(const char *name, const char *type, const char *uuid, const char *api,
                      char *avatar, size_t an, char *body, size_t bn) {
    char nameq[384];
    path_enc((name && name[0]) ? name : "Steve", nameq, sizeof(nameq));
    /* uuid 规范成 32 位小写 hex；不是合法 UUID 就当没有 */
    char hexid[64] = {0};
    size_t o = 0;
    for (const char *s = uuid ? uuid : ""; *s && o < 33; s++) {
        char c = *s;
        if (c == '-') continue;
        if (c >= 'A' && c <= 'F') c = (char)(c - 'A' + 'a');
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) { o = 0; break; }
        hexid[o++] = c;
    }
    if (o != 32) hexid[0] = 0; else hexid[32] = 0;
    if (type && strcmp(type, "authlib") == 0 && api && api[0]) {
        char origin[512];
        skin_origin(api, origin, sizeof(origin));
        snprintf(avatar, an, "%s/avatar/%s", origin, nameq);
        snprintf(body, bn, "%s/preview/%s", origin, nameq);
        return;
    }
    if (type && strcmp(type, "microsoft") == 0 && hexid[0]) {
        snprintf(avatar, an, "https://crafatar.com/avatars/%s?overlay=true&size=128", hexid);
        snprintf(body, bn, "https://crafatar.com/renders/body/%s?overlay=true&scale=6", hexid);
        return;
    }
    snprintf(avatar, an, "https://mc-heads.net/avatar/%s/128", nameq);
    snprintf(body, bn, "https://mc-heads.net/body/%s/180", nameq);
}

/* Prefer native; on failure or complexity, Python. */
cJSON *rpc_align_call(const char *method, cJSON *params, sse_emit_fn emit) {
    if (!method) return NULL;

    /* ---- accounts ---- */
    if (strcmp(method, "get_account_rows") == 0) {
        cJSON *root = accounts_load();
        cJSON *out = cJSON_CreateArray();
        const char *active = cJSON_GetStringValue(cJSON_GetObjectItem(root, "active"));
        cJSON *it;
        cJSON_ArrayForEach(it, cJSON_GetObjectItem(root, "accounts")) {
            cJSON *row = cJSON_CreateObject();
            const char *nm = cJSON_GetStringValue(cJSON_GetObjectItem(it, "name")) ?: "";
            const char *type = cJSON_GetStringValue(cJSON_GetObjectItem(it, "type")) ?: "offline";
            const char *uuid = cJSON_GetStringValue(cJSON_GetObjectItem(it, "uuid")) ?: "";
            const char *api = cJSON_GetStringValue(cJSON_GetObjectItem(it, "api")) ?: "";
            cJSON_AddStringToObject(row, "name", nm);
            cJSON_AddStringToObject(row, "type", type);
            cJSON_AddStringToObject(row, "uuid", uuid);
            cJSON_AddStringToObject(row, "api", api);
            /* 头像 / 全身预览：以前恒为空串，账号页在 C 桥下永远不显示头像。
             * URL 规则与 mclauncher/skin.py 一致。 */
            char avatar[768], body[768];
            skin_urls(nm, type, uuid, api, avatar, sizeof(avatar), body, sizeof(body));
            cJSON_AddStringToObject(row, "avatar", avatar);
            cJSON_AddStringToObject(row, "body", body);
            cJSON_AddBoolToObject(row, "active", active && strcmp(active, nm) == 0);
            cJSON_AddItemToArray(out, row);
        }
        cJSON_Delete(root);
        return out;
    }
    if (strcmp(method, "add_offline_account") == 0) {
        const char *user = pstr(params, "username", pstr(params, "name", "Player"));
        cJSON *acc = account_offline(user);
        account_apply_offline_skin(acc, pstr(params, "skin", ""));
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
        /* add/delete 早就是原生实现，唯独 update 走 py_rpc，而且 Python 不可用时
         * 返回 True 假成功——EziApp 编辑服务器点保存，报「已保存」，改动全丢。
         * 语义与 mclauncher/servers.py 的 update_server 一致：按键局部更新。 */
        const char *inst = pstr(params, "instance", "default");
        int idx = cJSON_IsNumber(cJSON_GetObjectItem(params, "index"))
                      ? (int)cJSON_GetObjectItem(params, "index")->valuedouble : -1;
        char path[PYMCL_PATH];
        servers_path(inst, path, sizeof(path));
        cJSON *arr = pymcl_read_json(path);
        if (!cJSON_IsArray(arr) || idx < 0 || idx >= cJSON_GetArraySize(arr)) {
            cJSON_Delete(arr);
            pymcl_set_error("服务器索引 %d 不存在", idx);
            return NULL;
        }
        cJSON *entry = cJSON_GetArrayItem(arr, idx);
        if (!cJSON_IsObject(entry)) {
            cJSON_Delete(arr);
            pymcl_set_error("服务器数据损坏: %d", idx);
            return NULL;
        }
        cJSON *v;
        if ((v = cJSON_GetObjectItem(params, "ip")) != NULL) {
            const char *ip = cJSON_GetStringValue(v);
            if (!ip || !ip[0]) {
                cJSON_Delete(arr);
                pymcl_set_error("服务器地址不能为空");
                return NULL;
            }
            cJSON_ReplaceItemInObject(entry, "ip", cJSON_CreateString(ip));
        }
        if ((v = cJSON_GetObjectItem(params, "port")) != NULL && cJSON_IsNumber(v)) {
            int port = (int)v->valuedouble;
            if (port < 1 || port > 65535) {
                cJSON_Delete(arr);
                pymcl_set_error("端口号必须在 1-65535 之间");
                return NULL;
            }
            if (cJSON_GetObjectItem(entry, "port"))
                cJSON_ReplaceItemInObject(entry, "port", cJSON_CreateNumber(port));
            else
                cJSON_AddNumberToObject(entry, "port", port);
        }
        const char *skeys[] = { "name", "description", "icon" };
        for (size_t i = 0; i < sizeof(skeys) / sizeof(skeys[0]); i++) {
            v = cJSON_GetObjectItem(params, skeys[i]);
            if (v && cJSON_IsString(v)) {
                if (cJSON_GetObjectItem(entry, skeys[i]))
                    cJSON_ReplaceItemInObject(entry, skeys[i], cJSON_CreateString(v->valuestring));
                else
                    cJSON_AddStringToObject(entry, skeys[i], v->valuestring);
            }
        }
        if ((v = cJSON_GetObjectItem(params, "hidden")) != NULL && cJSON_IsBool(v)) {
            if (cJSON_GetObjectItem(entry, "hidden"))
                cJSON_ReplaceItemInObject(entry, "hidden", cJSON_CreateBool(cJSON_IsTrue(v)));
            else
                cJSON_AddBoolToObject(entry, "hidden", cJSON_IsTrue(v));
        }
        pymcl_write_json(path, arr);
        cJSON *ret = cJSON_Duplicate(entry, 1);
        cJSON_AddNumberToObject(ret, "index", idx);
        if (!cJSON_GetObjectItem(ret, "port")) cJSON_AddNumberToObject(ret, "port", 25565);
        if (!cJSON_GetObjectItem(ret, "hidden")) cJSON_AddBoolToObject(ret, "hidden", 0);
        cJSON_Delete(arr);
        if (emit) emit("ui_changed", cJSON_CreateObject());
        return ret;
    }

    /* ---- playtime ---- */
    if (strcmp(method, "get_all_playtime") == 0) {
        char path[PYMCL_PATH];
        playtime_path(path, sizeof(path));
        cJSON *j = pymcl_read_json(path);
        if (!j) j = cJSON_Parse("{\"instances\":{}}");
        return j;
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
        /* C-only: basic existence check */
        const char *inst = pstr(params, "instance", "default");
        const char *ver = pstr(params, "version", "");
        cJSON *out = cJSON_CreateObject();
        cJSON *issues = cJSON_CreateArray();
        if (!ver[0]) {
            cJSON *iss = cJSON_CreateObject();
            cJSON_AddStringToObject(iss, "level", "error");
            cJSON_AddStringToObject(iss, "code", "no_version");
            cJSON_AddStringToObject(iss, "message", "请先选择版本");
            cJSON_AddItemToArray(issues, iss);
        } else if (!instance_has_version(inst, ver)) {
            cJSON *iss = cJSON_CreateObject();
            cJSON_AddStringToObject(iss, "level", "error");
            cJSON_AddStringToObject(iss, "code", "missing_version");
            cJSON_AddStringToObject(iss, "message", "版本未安装");
            cJSON_AddItemToArray(issues, iss);
        }
        cJSON_AddItemToObject(out, "issues", issues);
        cJSON_AddBoolToObject(out, "ok", cJSON_GetArraySize(issues) == 0);
        cJSON_AddBoolToObject(out, "can_launch", cJSON_GetArraySize(issues) == 0);
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

    /* ---- AI 流式会话在一次性 py_rpc 下不可能工作 ----
     * ai_send 在 Python 侧起后台线程、靠 ai.* 事件流式回推；py_rpc 子进程
     * 一退出线程就死，事件总线也没人转发。以前把这四个丢给 py_rpc：
     * ai_send 让 UI 永远卡在「正在想…」，ai_stop/ai_confirm/ai_answer 在
     * 注定死亡的子进程里 set 事件，返回 {"ok":true} 纯属假成功。 */
    if (strcmp(method, "ai_send") == 0 || strcmp(method, "ai_stop") == 0
        || strcmp(method, "ai_confirm") == 0 || strcmp(method, "ai_answer") == 0) {
        pymcl_set_error("AI 助手需要常驻 Python 桥：请用 python main.py 启动，"
                        "或设置 PYMCL_BRIDGE=python 后重开启动器");
        return NULL;
    }

    /* ---- 陶瓦联机同理：模块级状态 + 常驻内核子进程，一次性 py_rpc 每次都是
     * 全新进程。以前 host/join 在孤儿进程里改状态、prepare 的任务随进程消失、
     * snapshot 永远回「空闲」——全是貌似成功的空操作。 ---- */
    if (strcmp(method, "terracotta_host") == 0 || strcmp(method, "terracotta_join") == 0
        || strcmp(method, "terracotta_idle") == 0 || strcmp(method, "terracotta_prepare") == 0) {
        pymcl_set_error("陶瓦联机需要常驻 Python 桥：请用 python main.py 启动，"
                        "或设置 PYMCL_BRIDGE=python 后重开启动器");
        return NULL;
    }
    if (strcmp(method, "terracotta_shutdown") == 0)
        return cJSON_CreateTrue();  /* C 桥下不可能有在跑的内核，确保停止=已满足 */
    if (strcmp(method, "terracotta_snapshot") == 0) {
        cJSON *o = cJSON_CreateObject();
        cJSON_AddBoolToObject(o, "supported", 0);  /* false 才不会自动触发注定失败的 prepare */
        cJSON_AddBoolToObject(o, "installed", 0);
        cJSON_AddBoolToObject(o, "running", 0);
        cJSON_AddStringToObject(o, "state", "unsupported");
        cJSON_AddStringToObject(o, "label", "陶瓦联机需要常驻 Python 桥（python main.py）");
        cJSON_AddStringToObject(o, "room", "");
        cJSON_AddStringToObject(o, "error", "");
        return o;
    }

    /* ---- feedback / help / news / update / cleaner / AI / terracotta ---- */
    if (strcmp(method, "submit_feedback") == 0 || strcmp(method, "help_articles") == 0
        || strcmp(method, "help_article") == 0 || strcmp(method, "cached_news") == 0
        || strcmp(method, "fetch_news") == 0 || strcmp(method, "check_update") == 0
        || strcmp(method, "cleaner_preview") == 0 || strcmp(method, "cleaner_apply") == 0
        || strcmp(method, "test_ai_connection") == 0 || strcmp(method, "ai_list_chats") == 0
        || strcmp(method, "ai_new_chat") == 0 || strcmp(method, "ai_delete_chat") == 0
        || strcmp(method, "ai_set_active") == 0
        || strcmp(method, "terracotta_allow_firewall") == 0
        || strcmp(method, "terracotta_open_firewall_settings") == 0
        || strcmp(method, "lan_hint") == 0
        || strcmp(method, "list_loader_versions") == 0 || strcmp(method, "list_catalog_files") == 0
        || strcmp(method, "search_worlds") == 0) {
        cJSON *r = py_rpc_call(method, params);
        if (r) return r;
        /* graceful empty fallbacks so UI stays usable without Python */
        if (strcmp(method, "help_articles") == 0 || strcmp(method, "cached_news") == 0
            || strcmp(method, "fetch_news") == 0 || strcmp(method, "list_loader_versions") == 0
            || strcmp(method, "list_catalog_files") == 0 || strcmp(method, "search_worlds") == 0)
            return cJSON_CreateArray();
        if (strcmp(method, "lan_hint") == 0)
            return cJSON_CreateString("联机功能在纯 C 桥下有限，请安装 Python 环境以启用陶瓦。");
        if (strcmp(method, "ai_list_chats") == 0 || strcmp(method, "ai_new_chat") == 0
            || strcmp(method, "ai_delete_chat") == 0 || strcmp(method, "ai_set_active") == 0) {
            cJSON *o = cJSON_CreateObject();
            cJSON_AddItemToObject(o, "chats", cJSON_CreateArray());
            cJSON_AddStringToObject(o, "active_id", "");
            return o;
        }
        if (strcmp(method, "check_update") == 0) {
            cJSON *o = cJSON_CreateObject();
            cJSON_AddBoolToObject(o, "update", 0);
            cJSON_AddStringToObject(o, "message", "当前为 C 桥；自更新需 Python");
            return o;
        }
        if (strcmp(method, "test_ai_connection") == 0)
            return cJSON_CreateString("AI 需要 Python 桥（未找到可用 Python）");
        if (strcmp(method, "submit_feedback") == 0) {
            /* 以前这里返回 true 假装发送成功，反馈其实被整个丢掉。 */
            pymcl_set_error("反馈未发送：需要 Python 环境（设置 PYMCL_PYTHON 或安装 Python）");
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
