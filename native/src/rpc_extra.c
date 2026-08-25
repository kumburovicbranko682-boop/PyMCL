#include "pymcl.h"
#include <pthread.h>
#include <time.h>

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

/* py_rpc 子进程协议：
   EVENT {...}  -> 实时转发到 SSE（进度 / 登录码 / finished 等）
   RESULT {...} -> 结果一出来就先还给调用方；start_task 型方法的子进程
                   之后会留着陪任务跑完，事件继续经读线程转发。 */

static sse_emit_fn g_py_emit;
static volatile long g_pyrpc_seq;

void py_rpc_set_emit(sse_emit_fn fn) { g_py_emit = fn; }

typedef struct {
    pthread_mutex_t mu;
    pthread_cond_t cv;
    cJSON *result;   /* RESULT 行的 payload，主线程取走 */
    int finished;    /* 子进程已退出 */
    int rc;
    int refs;
    char pin[PYMCL_PATH];
    char pout[PYMCL_PATH];
    char *argv_store[16];
    int argc;
} pyrpc_job_t;

static void pyrpc_job_free(pyrpc_job_t *j) {
    for (int i = 0; i < j->argc; i++)
        free(j->argv_store[i]);
    DeleteFileA(j->pout);
    pthread_mutex_destroy(&j->mu);
    pthread_cond_destroy(&j->cv);
    free(j);
}

static void pyrpc_job_unref(pyrpc_job_t *j) {
    pthread_mutex_lock(&j->mu);
    int refs = --j->refs;
    pthread_mutex_unlock(&j->mu);
    if (refs == 0) pyrpc_job_free(j);
}

static void pyrpc_on_line(void *ud, const char *line) {
    pyrpc_job_t *j = (pyrpc_job_t *)ud;
    if (strncmp(line, "EVENT ", 6) == 0) {
        cJSON *o = cJSON_Parse(line + 6);
        if (cJSON_IsObject(o)) {
            const char *ev = cJSON_GetStringValue(cJSON_GetObjectItem(o, "event"));
            cJSON *data = cJSON_GetObjectItem(o, "data");
            if (ev && ev[0] && g_py_emit)
                g_py_emit(ev, data);
        }
        cJSON_Delete(o);
        return;
    }
    if (strncmp(line, "RESULT ", 7) == 0) {
        cJSON *o = cJSON_Parse(line + 7);
        if (o) {
            pthread_mutex_lock(&j->mu);
            if (!j->result) {
                j->result = o;
                o = NULL;
            }
            pthread_cond_broadcast(&j->cv);
            pthread_mutex_unlock(&j->mu);
        }
        cJSON_Delete(o);
    }
}

static void *pyrpc_thread(void *ud) {
    pyrpc_job_t *j = (pyrpc_job_t *)ud;
    /* 子进程自己限时（任务等待上限 3600s），这里稍微放宽兜底。 */
    int rc = pymcl_run_process((const char **)j->argv_store, j->argc, g_root, pyrpc_on_line, j, 3900);
    DeleteFileA(j->pin);
    pthread_mutex_lock(&j->mu);
    j->rc = rc;
    j->finished = 1;
    pthread_cond_broadcast(&j->cv);
    pthread_mutex_unlock(&j->mu);
    pyrpc_job_unref(j);
    return NULL;
}

cJSON *py_rpc_call(const char *method, cJSON *params) {
    char py[PYMCL_PATH], script[PYMCL_PATH], tmpdir[PYMCL_PATH];
    find_python(py, sizeof(py));
    pymcl_path_join3(script, sizeof(script), g_root, "native\\tools", "py_rpc.py");
    if (!pymcl_file_exists(script)) {
        pymcl_path_join3(script, sizeof(script), g_root, "native/tools", "py_rpc.py");
    }
    if (!pymcl_file_exists(script)) {
        pymcl_set_error("py_rpc.py missing; method %s needs Python bridge", method);
        return NULL;
    }
    pyrpc_job_t *j = (pyrpc_job_t *)calloc(1, sizeof(*j));
    if (!j) { pymcl_set_error("内存不足"); return NULL; }
    pthread_mutex_init(&j->mu, NULL);
    pthread_cond_init(&j->cv, NULL);
    j->refs = 2; /* 主线程 + 读线程 */
    {
        long seq = InterlockedIncrement(&g_pyrpc_seq);
        GetTempPathA(sizeof(tmpdir), tmpdir);
        snprintf(j->pin, sizeof(j->pin), "%spymcl-rpc-in-%u-%ld.json",
                 tmpdir, (unsigned)GetCurrentProcessId(), seq);
        snprintf(j->pout, sizeof(j->pout), "%spymcl-rpc-out-%u-%ld.json",
                 tmpdir, (unsigned)GetCurrentProcessId(), seq);
    }

    cJSON *body = params ? cJSON_Duplicate(params, 1) : cJSON_CreateObject();
    if (!cJSON_IsObject(body)) {
        cJSON_Delete(body);
        body = cJSON_CreateObject();
    }
    {
        char *txt = cJSON_PrintUnformatted(body);
        cJSON_Delete(body);
        if (!txt) {
            pyrpc_job_unref(j);
            pyrpc_job_unref(j);
            pymcl_set_error("params serialize failed");
            return NULL;
        }
        pymcl_write_file(j->pin, txt, strlen(txt));
        free(txt);
    }

    /* 读线程可能活得比本栈帧久，argv 必须堆拷贝。 */
    j->argc = 0;
    j->argv_store[j->argc++] = pymcl_strdup(py);
    j->argv_store[j->argc++] = pymcl_strdup("-u");
    j->argv_store[j->argc++] = pymcl_strdup(script);
    j->argv_store[j->argc++] = pymcl_strdup("--root");
    j->argv_store[j->argc++] = pymcl_strdup(g_root);
    j->argv_store[j->argc++] = pymcl_strdup("--method");
    j->argv_store[j->argc++] = pymcl_strdup(method);
    j->argv_store[j->argc++] = pymcl_strdup("--params");
    j->argv_store[j->argc++] = pymcl_strdup(j->pin);
    j->argv_store[j->argc++] = pymcl_strdup("--out");
    j->argv_store[j->argc++] = pymcl_strdup(j->pout);

    pthread_t th;
    if (pthread_create(&th, NULL, pyrpc_thread, j) != 0) {
        DeleteFileA(j->pin);
        pyrpc_job_unref(j);
        pyrpc_job_unref(j);
        pymcl_set_error("无法启动 py_rpc 线程");
        return NULL;
    }
    pthread_detach(th);

    /* 等 RESULT 行或子进程退出，上限 120s（与旧行为一致）。 */
    struct timespec ts;
    ts.tv_sec = time(NULL) + 120;
    ts.tv_nsec = 0;
    pthread_mutex_lock(&j->mu);
    while (!j->result && !j->finished) {
        if (pthread_cond_timedwait(&j->cv, &j->mu, &ts) != 0)
            break;
    }
    cJSON *wrap = j->result;
    j->result = NULL;
    int finished = j->finished;
    int rc = j->rc;
    pthread_mutex_unlock(&j->mu);

    if (!wrap && finished)
        wrap = pymcl_read_json(j->pout); /* 旧版脚本没有 RESULT 行时的回落 */
    pyrpc_job_unref(j);

    if (!wrap) {
        if (finished)
            pymcl_set_error("py_rpc failed (rc=%d) for %s", rc, method);
        else
            pymcl_set_error("py_rpc timeout for %s", method);
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

/* Prefer native; on failure or complexity, Python. */
/* ---------- feedback submission without Python ---------- */

static void feedback_base_url(char *out, size_t n) {
    const char *url = config_str("feedback_url", "");
    if (!url[0]) {
        const char *env = getenv("PYMCL_FEEDBACK_URL");
        /* 默认地址与 mclauncher/feedback_defaults.py 保持一致 */
        url = (env && env[0]) ? env : "http://114.66.28.184:53611";
    }
    snprintf(out, n, "%s", url);
    for (size_t len = strlen(out); len > 0 && out[len - 1] == '/'; len--)
        out[len - 1] = 0;
}

static void feedback_device_id(char *out, size_t n) {
    const char *stored = config_str("device_id", "");
    if (stored[0]) {
        snprintf(out, n, "%s", stored);
        return;
    }
    char path[PYMCL_PATH];
    pymcl_path_join(path, sizeof(path), g_root, "device_id");
    char *txt = NULL;
    size_t len = 0;
    if (pymcl_read_file(path, &txt, &len) == 0 && txt) {
        while (len > 0 && (unsigned char)txt[len - 1] <= ' ')
            len--;
        if (len > 0 && len < n) {
            snprintf(out, n, "%.*s", (int)len, txt);
            free(txt);
            return;
        }
        free(txt);
    }
    {
        static const char hex[] = "0123456789abcdef";
        unsigned seed = (unsigned)(GetTickCount64() ^ GetCurrentProcessId() ^ (uintptr_t)out);
        srand(seed);
        size_t i;
        for (i = 0; i < 32 && i + 1 < n; i++)
            out[i] = hex[rand() & 15];
        out[i] = 0;
    }
    pymcl_write_file(path, out, strlen(out));
    config_set_str("device_id", out);
    config_save();
}

static void feedback_history_add(const char *id, const char *category, const char *title) {
    char path[PYMCL_PATH];
    pymcl_path_join(path, sizeof(path), g_root, "feedback_history.json");
    cJSON *rows = pymcl_read_json(path);
    if (!cJSON_IsArray(rows)) {
        cJSON_Delete(rows);
        rows = cJSON_CreateArray();
    }
    cJSON *row = cJSON_CreateObject();
    cJSON_AddStringToObject(row, "id", id ? id : "");
    cJSON_AddNumberToObject(row, "ts", (double)time(NULL));
    cJSON_AddStringToObject(row, "category", category ? category : "other");
    cJSON_AddStringToObject(row, "title", title ? title : "");
    cJSON_AddBoolToObject(row, "ok", 1);
    cJSON_InsertItemInArray(rows, 0, row);
    while (cJSON_GetArraySize(rows) > 30)
        cJSON_DeleteItemFromArray(rows, cJSON_GetArraySize(rows) - 1);
    pymcl_write_json(path, rows);
    cJSON_Delete(rows);
}

static cJSON *feedback_submit_native(cJSON *params) {
    if (!config_bool("feedback_consent", 0)) {
        pymcl_set_error("需要先同意上传诊断数据。第一次打开启动器时会询问，也可在设置里开启。");
        return NULL;
    }
    static const char *cats[] = {"bug", "crash", "download", "multiplayer", "ai", "ui", "suggest", "other"};
    const char *cat = pstr(params, "category", "other");
    int known = 0;
    for (size_t i = 0; i < sizeof(cats) / sizeof(cats[0]); i++)
        if (strcmp(cat, cats[i]) == 0) { known = 1; break; }
    if (!known) cat = "other";
    const char *title = pstr(params, "title", "");
    const char *body = pstr(params, "body", "");
    const char *contact = pstr(params, "contact", "");
    while (*title && (unsigned char)*title <= ' ') title++;
    while (*body && (unsigned char)*body <= ' ') body++;
    if (!title[0] && !body[0]) {
        pymcl_set_error("请填写标题或内容");
        return NULL;
    }
    char tbuf[121];
    if (title[0]) {
        snprintf(tbuf, sizeof(tbuf), "%.120s", title);
    } else {
        size_t i = 0;
        while (body[i] && body[i] != '\n' && body[i] != '\r' && i < 80)
            i++;
        snprintf(tbuf, sizeof(tbuf), "%.*s", (int)i, body);
        if (!tbuf[0])
            snprintf(tbuf, sizeof(tbuf), "未命名反馈");
    }
    char dev[64];
    feedback_device_id(dev, sizeof(dev));

    cJSON *payload = cJSON_CreateObject();
    cJSON_AddStringToObject(payload, "device_id", dev);
    cJSON_AddStringToObject(payload, "category", cat);
    cJSON_AddStringToObject(payload, "title", tbuf);
    cJSON_AddStringToObject(payload, "body", body);
    cJSON_AddStringToObject(payload, "contact", contact);
    cJSON_AddStringToObject(payload, "app_version", PYMCL_APP_VERSION);
    cJSON_AddNullToObject(payload, "crash");
    char *txt = cJSON_PrintUnformatted(payload);
    cJSON_Delete(payload);
    if (!txt) {
        pymcl_set_error("反馈内容序列化失败");
        return NULL;
    }

    char base[512], url[600], hdr[128];
    feedback_base_url(base, sizeof(base));
    if (!base[0]) {
        free(txt);
        pymcl_set_error("未配置反馈服务器。开发者请启动 feedback_hub，并在设置里填写地址。");
        return NULL;
    }
    snprintf(url, sizeof(url), "%s/api/v1/feedback", base);
    snprintf(hdr, sizeof(hdr), "X-PyMCL-Client: PyMCL/%s\nUser-Agent: PyMCL/%s",
             PYMCL_APP_VERSION, PYMCL_APP_VERSION);
    http_resp resp;
    int rc = http_post_json(url, txt, &resp, hdr, 25);
    free(txt);
    if (rc != 0) {
        char emsg[256];
        snprintf(emsg, sizeof(emsg), "%s", pymcl_error());
        http_resp_free(&resp);
        pymcl_set_error("连不上反馈服务器: %s", emsg);
        return NULL;
    }
    cJSON *data = cJSON_Parse(resp.body ? resp.body : "");
    http_resp_free(&resp);
    if (!cJSON_IsObject(data)) {
        cJSON_Delete(data);
        pymcl_set_error("反馈服务器返回了无法解析的内容");
        return NULL;
    }
    cJSON *okv = cJSON_GetObjectItem(data, "ok");
    if (okv && cJSON_IsFalse(okv)) {
        const char *err = cJSON_GetStringValue(cJSON_GetObjectItem(data, "error"));
        pymcl_set_error("%s", (err && err[0]) ? err : "提交失败");
        cJSON_Delete(data);
        return NULL;
    }
    {
        const char *fid = cJSON_GetStringValue(cJSON_GetObjectItem(data, "id"));
        feedback_history_add(fid ? fid : "", cat, tbuf);
    }
    return data;
}

cJSON *rpc_align_call(const char *method, cJSON *params, sse_emit_fn emit, int *handled) {
    /* 默认「已处理」：所有匹配分支都在中途 return，失败时错误信息必须原样
       送回 UI，不能让调用方再拿同一方法去 py_rpc 重跑一遍（副作用会翻倍，
       ai_send 这类被有意拦下的方法更会被重新放行）。
       只有走到函数末尾才翻成「未处理」。 */
    if (handled) *handled = 1;
    if (!method) {
        if (handled) *handled = 0;
        return NULL;
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
        /* 失败就报失败。以前这里回 true 假装保存成功，用户改完
           服务器条目看到「已保存」，其实什么都没写进去。 */
        return py_rpc_call(method, params);
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

    /* ---- feedback: history is a plain file, submission has a real native path ---- */
    if (strcmp(method, "feedback_history") == 0) {
        char path[PYMCL_PATH];
        pymcl_path_join(path, sizeof(path), g_root, "feedback_history.json");
        cJSON *rows = pymcl_read_json(path);
        if (!cJSON_IsArray(rows)) {
            cJSON_Delete(rows);
            rows = cJSON_CreateArray();
        }
        return rows;
    }
    if (strcmp(method, "submit_feedback") == 0) {
        /* Python 优先（会附带 sysinfo）；没有 Python 时用原生 HTTP 真正提交，
           绝不能假装成功把用户反馈丢掉。 */
        cJSON *r = py_rpc_call(method, params);
        if (r) return r;
        return feedback_submit_native(params);
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

    /* AI 对话是常驻会话：流式 delta 与停止/确认/追问都依赖同一进程的内存态，
       一次性 py_rpc 子进程做不到——ai_send 返回后子进程退出会当场杀掉 AI 线程，
       UI 只会永远停在「正在想…」。宁可明确报错，也不能假装已发送。
       （ai_list_chats 等纯磁盘操作仍可走 py_rpc。） */
    if (strcmp(method, "ai_send") == 0 || strcmp(method, "ai_stop") == 0
        || strcmp(method, "ai_confirm") == 0 || strcmp(method, "ai_answer") == 0) {
        pymcl_set_error("AI 对话需要 Python 桥进程：请设置 PYMCL_BRIDGE=python 后重启，"
                        "或直接运行 bridge/server.py");
        return NULL;
    }

    /* ---- feedback / help / news / update / cleaner / AI / terracotta ---- */
    if (strcmp(method, "help_articles") == 0
        || strcmp(method, "help_article") == 0 || strcmp(method, "cached_news") == 0
        || strcmp(method, "fetch_news") == 0 || strcmp(method, "check_update") == 0
        || strcmp(method, "cleaner_preview") == 0 || strcmp(method, "cleaner_apply") == 0
        || strcmp(method, "test_ai_connection") == 0 || strcmp(method, "ai_list_chats") == 0
        || strcmp(method, "ai_new_chat") == 0 || strcmp(method, "ai_delete_chat") == 0
        || strcmp(method, "ai_set_active") == 0 || strcmp(method, "terracotta_snapshot") == 0
        || strcmp(method, "terracotta_host") == 0 || strcmp(method, "terracotta_join") == 0
        || strcmp(method, "terracotta_idle") == 0 || strcmp(method, "terracotta_prepare") == 0
        || strcmp(method, "terracotta_allow_firewall") == 0
        || strcmp(method, "terracotta_open_firewall_settings") == 0
        || strcmp(method, "terracotta_shutdown") == 0 || strcmp(method, "lan_hint") == 0
        || strcmp(method, "list_loader_versions") == 0 || strcmp(method, "list_catalog_files") == 0
        || strcmp(method, "search_worlds") == 0 || strcmp(method, "install_world") == 0
        || strcmp(method, "repair_version") == 0 || strcmp(method, "export_modpack") == 0
        || strcmp(method, "start_authlib_login") == 0 || strcmp(method, "start_nide8_login") == 0
        || strcmp(method, "start_self_update") == 0 || strcmp(method, "start_mod_updates") == 0) {
        cJSON *r = py_rpc_call(method, params);
        if (r) return r;
        /* graceful empty fallbacks so UI stays usable without Python */
        if (strcmp(method, "help_articles") == 0 || strcmp(method, "cached_news") == 0
            || strcmp(method, "fetch_news") == 0 || strcmp(method, "list_loader_versions") == 0
            || strcmp(method, "list_catalog_files") == 0 || strcmp(method, "search_worlds") == 0)
            return cJSON_CreateArray();
        if (strcmp(method, "terracotta_snapshot") == 0) {
            cJSON *o = cJSON_CreateObject();
            cJSON_AddStringToObject(o, "state", "idle");
            cJSON_AddStringToObject(o, "message", "陶瓦联机需要 Python 后端");
            return o;
        }
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
        if (strcmp(method, "terracotta_allow_firewall") == 0)
            return cJSON_CreateString("请手动放行防火墙，或安装 Python 后端");
        /* 其余（登录、修复、清理这类有副作用的）失败就失败：pymcl_error 里
           是 py_rpc 带回的真实原因（比如「邮箱或密码错误」），别再用一句
           笼统的「需要 Python 桥」把它盖掉。 */
        return NULL;
    }

    if (handled) *handled = 0;
    return NULL; /* not handled here */
}
