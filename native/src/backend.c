#include "pymcl.h"
#include <pthread.h>
#include <string.h>
#include <time.h>

static sse_emit_fn g_emit;
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;
static int g_task_n;
static HANDLE g_game;
static char g_launch_id[32];

typedef struct {
    char id[32];
    char title[256];
    int cancelled;
    pthread_t th;
    char method[64];
    cJSON *args;
} task_t;

static task_t *g_tasks[32];
static int g_ntasks;
static cJSON *g_last_crash;

#define CRASH_TAIL 200
static int find_python(char *out, size_t n) {
    const char *env = getenv("PYMCL_PYTHON");
    if (env && env[0] && GetFileAttributesA(env) != INVALID_FILE_ATTRIBUTES) {
        snprintf(out, n, "%s", env);
        return 0;
    }
    {
        const char *known = "C:\\Users\\Administrator\\.workbuddy\\binaries\\python\\envs\\pymcl5\\Scripts\\python.exe";
        if (GetFileAttributesA(known) != INVALID_FILE_ATTRIBUTES) {
            snprintf(out, n, "%s", known);
            return 0;
        }
    }
    snprintf(out, n, "python");
    return 0;
}

static cJSON *analyze_game_crash(const char *inst, const char *ver, long code,
                                 char **tail, int tn, int ts, double started) {
    char py[PYMCL_PATH], outf[PYMCL_PATH], jsonf[PYMCL_PATH], codebuf[32], startbuf[32];
    find_python(py, sizeof(py));
    snprintf(outf, sizeof(outf), "%s\\game-output-tail.txt", g_root);
    snprintf(jsonf, sizeof(jsonf), "%s\\last-crash.json", g_root);
    FILE *f = fopen(outf, "wb");
    if (f) {
        int start = (tn == CRASH_TAIL) ? ts : 0;
        for (int i = 0; i < tn; i++) {
            const char *s = tail[(start + i) % CRASH_TAIL];
            if (s) { fputs(s, f); fputc('\n', f); }
        }
        fclose(f);
    }
    snprintf(codebuf, sizeof(codebuf), "%ld", code);
    snprintf(startbuf, sizeof(startbuf), "%.0f", started);
    const char *argv[20];
    int argc = 0;
    argv[argc++] = py;
    argv[argc++] = "-u";
    argv[argc++] = "-m";
    argv[argc++] = "mclauncher.crash";
    argv[argc++] = "--instance";
    argv[argc++] = inst;
    argv[argc++] = "--version";
    argv[argc++] = ver ? ver : "";
    argv[argc++] = "--exit-code";
    argv[argc++] = codebuf;
    argv[argc++] = "--output";
    argv[argc++] = outf;
    argv[argc++] = "--json-out";
    argv[argc++] = jsonf;
    argv[argc++] = "--started-at";
    argv[argc++] = startbuf;
    pymcl_run_process(argv, argc, g_root, NULL, NULL, 45);
    cJSON *rep = pymcl_read_json(jsonf);
    if (rep) {
        if (g_last_crash) cJSON_Delete(g_last_crash);
        g_last_crash = cJSON_Duplicate(rep, 1);
    }
    return rep;
}

static void emit(const char *ev, cJSON *data) {
    if (g_emit) g_emit(ev, data);
}

static void emit_kv(const char *ev, const char *fmt, ...) {
    /* unused helper kept for future */
    (void)ev; (void)fmt;
}

static int task_count(void) {
    pthread_mutex_lock(&g_mu);
    int n = g_ntasks;
    pthread_mutex_unlock(&g_mu);
    return n;
}

static void emit_count(void) {
    cJSON *o = cJSON_CreateObject();
    cJSON_AddNumberToObject(o, "count", task_count());
    emit("task_count_changed", o);
    cJSON_Delete(o);
}

static void ctx_progress(void *ud, const char *msg, long long done, long long total) {
    task_t *t = (task_t *)ud;
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "task_id", t->id);
    cJSON_AddNumberToObject(o, "current", (double)done);
    cJSON_AddNumberToObject(o, "total", (double)total);
    cJSON_AddStringToObject(o, "message", msg ? msg : "");
    emit("progress", o);
    cJSON_Delete(o);
}
static void ctx_log(void *ud, const char *text) {
    task_t *t = (task_t *)ud;
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "task_id", t->id);
    cJSON_AddStringToObject(o, "text", text ? text : "");
    emit("log", o);
    cJSON_Delete(o);
}
static int ctx_cancel(void *ud) {
    task_t *t = (task_t *)ud;
    return t->cancelled;
}

static void finish_task(task_t *t, int ok, const char *msg) {
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "task_id", t->id);
    cJSON_AddBoolToObject(o, "success", ok);
    cJSON_AddStringToObject(o, "message", msg ? msg : (ok ? "任务完成" : pymcl_error()));
    emit("finished", o);
    cJSON_Delete(o);
    if (ok) emit("ui_changed", cJSON_CreateObject());
    pthread_mutex_lock(&g_mu);
    for (int i = 0; i < g_ntasks; i++) {
        if (g_tasks[i] == t) {
            g_tasks[i] = g_tasks[g_ntasks - 1];
            g_tasks[g_ntasks - 1] = NULL;
            g_ntasks--;
            break;
        }
    }
    pthread_mutex_unlock(&g_mu);
    emit_count();
    cJSON_Delete(t->args);
    t->args = NULL;
    free(t);
}

static const char *pstr(cJSON *a, const char *k, const char *def) {
    const char *s = a ? cJSON_GetStringValue(cJSON_GetObjectItem(a, k)) : NULL;
    return s ? s : def;
}
static int pint(cJSON *a, const char *k, int def) {
    cJSON *v = a ? cJSON_GetObjectItem(a, k) : NULL;
    if (cJSON_IsNumber(v)) return (int)v->valuedouble;
    if (cJSON_IsString(v) && v->valuestring) return atoi(v->valuestring);
    return def;
}

/* ---- 版本设置（pymcl.json）：对齐 mclauncher/launch_flow.prepare ----
 * 以前 WinUI「版本设置」对话框保存成功（get/save RPC 都在），但 C 桥启动
 * 时一个键都不读——隔离/内存/JVM/GC/直连/全屏/前后命令全部是死设置。 */
static cJSON *load_version_settings(const char *inst, const char *ver) {
    char vd[PYMCL_PATH], sp[PYMCL_PATH];
    instance_versions_dir(inst, vd, sizeof(vd));
    pymcl_path_join3(sp, sizeof(sp), vd, ver, "pymcl.json");
    cJSON *j = pymcl_read_json(sp);
    if (!cJSON_IsObject(j)) {
        cJSON_Delete(j);
        j = cJSON_CreateObject();
    }
    return j;
}

/* 对齐 version_settings._junction：已有非空目录不动；空目录/旧链接先删再建。 */
static void vs_junction(const char *link, const char *target) {
    wchar_t *wl = pymcl_u8_to_wide(link);
    DWORD attr = GetFileAttributesW(wl);
    if (attr != INVALID_FILE_ATTRIBUTES) {
        BOOL removed = (attr & FILE_ATTRIBUTE_DIRECTORY)
            ? RemoveDirectoryW(wl)   /* 只有空目录/交接点删得掉，实目录保留 */
            : DeleteFileW(wl);
        if (!removed) { free(wl); return; }
    }
    free(wl);
    pymcl_ensure_dir(target);
    char parent[PYMCL_PATH];
    pymcl_parent(link, parent, sizeof(parent));
    pymcl_ensure_dir(parent);
    const char *argv[] = { "cmd", "/c", "mklink", "/J", link, target };
    pymcl_run_process(argv, 6, NULL, NULL, NULL, 30);
}

/* 对齐 version_settings.apply_isolation：返回游戏目录并铺好共享链接。 */
static void vs_apply_isolation(const char *inst, const char *ver, const char *iso,
                               char *gdir, size_t n) {
    char ip[PYMCL_PATH];
    instance_path(inst, ip, sizeof(ip));
    if (!iso || (strcmp(iso, "saves") && strcmp(iso, "mods") && strcmp(iso, "all"))) {
        snprintf(gdir, n, "%s", ip);
        return;
    }
    char vd[PYMCL_PATH], link[PYMCL_PATH], tgt[PYMCL_PATH], sub[PYMCL_PATH];
    instance_versions_dir(inst, vd, sizeof(vd));
    pymcl_path_join(gdir, n, vd, ver);
    pymcl_ensure_dir(gdir);
    if (strcmp(iso, "saves") == 0) {
        const char *names[] = { "mods", "config", "resourcepacks", "shaderpacks", "downloads" };
        for (size_t i = 0; i < 5; i++) {
            pymcl_path_join(link, sizeof(link), gdir, names[i]);
            pymcl_path_join(tgt, sizeof(tgt), ip, names[i]);
            vs_junction(link, tgt);
        }
        pymcl_path_join(sub, sizeof(sub), gdir, "saves");
        pymcl_ensure_dir(sub);
    } else if (strcmp(iso, "mods") == 0) {
        const char *names[] = { "saves", "resourcepacks", "shaderpacks", "screenshots" };
        for (size_t i = 0; i < 4; i++) {
            pymcl_path_join(link, sizeof(link), gdir, names[i]);
            pymcl_path_join(tgt, sizeof(tgt), ip, names[i]);
            vs_junction(link, tgt);
        }
        const char *own[] = { "mods", "config" };
        for (size_t i = 0; i < 2; i++) {
            pymcl_path_join(sub, sizeof(sub), gdir, own[i]);
            pymcl_ensure_dir(sub);
        }
    } else {
        const char *own[] = { "mods", "config", "saves", "resourcepacks", "shaderpacks" };
        for (size_t i = 0; i < 5; i++) {
            pymcl_path_join(sub, sizeof(sub), gdir, own[i]);
            pymcl_ensure_dir(sub);
        }
    }
}

/* 对齐 launch_flow.run_hook：启动前/退出后命令走 shell（cmd /c），输出进任务日志。 */
static void hook_run(task_t *t, const char *command, const char *cwd, int wait) {
    if (!command || !command[0]) return;
    char lb[1200];
    snprintf(lb, sizeof(lb), "运行启动脚本: %s", command);
    ctx_log(t, lb);
    const char *hargv[] = { "cmd", "/c", command };
    if (!wait) {
        HANDLE p = pymcl_spawn_process(hargv, 3, cwd, NULL);
        if (p) CloseHandle(p);
        return;
    }
    int rc = pymcl_run_process(hargv, 3, cwd, ctx_log, t, 0);
    if (rc) {
        snprintf(lb, sizeof(lb), "脚本退出码 %d", rc);
        ctx_log(t, lb);
    }
}

/* 对齐 mclauncher/launcher._set_priority。 */
static void vs_apply_priority(HANDLE proc, cJSON *vset) {
    const char *p = cJSON_GetStringValue(cJSON_GetObjectItem(vset, "process_priority"));
    if (!p || !p[0]) p = config_str("default_priority", "normal");
    DWORD v = 0;
    if (pymcl_ieq(p, "low")) v = IDLE_PRIORITY_CLASS;
    else if (pymcl_ieq(p, "below")) v = BELOW_NORMAL_PRIORITY_CLASS;
    else if (pymcl_ieq(p, "high")) v = HIGH_PRIORITY_CLASS;
    else if (pymcl_ieq(p, "realtime")) v = REALTIME_PRIORITY_CLASS;
    if (v) SetPriorityClass(proc, v);
}

/* 目录搜索是否带了「游戏版本 / 分类」筛选（"全部"/空串视作未筛选）。 */
static int search_has_filters(cJSON *params) {
    cJSON *x = cJSON_GetObjectItem(params, "extra");
    if (!cJSON_IsObject(x)) return 0;
    const char *gv = cJSON_GetStringValue(cJSON_GetObjectItem(x, "game_version"));
    if (gv && gv[0]) return 1;
    const char *cat = cJSON_GetStringValue(cJSON_GetObjectItem(x, "category"));
    if (cat && cat[0] && strcmp(cat, "全部") != 0 && !pymcl_ieq(cat, "all"))
        return 1;
    return 0;
}

/* 目录页「安装所选」会在 extra 里钉住 version_id/file_id。原生安装器一律装
 * 最新版，钉了版本的请求必须落到 Python 实现，否则用户挑的旧版被静默换掉。 */
static int extra_pins_version(cJSON *args) {
    cJSON *x = cJSON_GetObjectItem(args, "extra");
    if (!cJSON_IsObject(x)) return 0;
    const char *keys[] = { "version_id", "file_id" };
    for (size_t i = 0; i < 2; i++) {
        cJSON *v = cJSON_GetObjectItem(x, keys[i]);
        if (!v || cJSON_IsNull(v)) continue;
        if (cJSON_IsString(v)) {
            if (v->valuestring && v->valuestring[0]) return 1;
        } else return 1;
    }
    return 0;
}

static void on_login_code(void *ud, const char *code, const char *uri) {
    (void)ud;
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "code", code ? code : "");
    cJSON_AddStringToObject(o, "uri", uri ? uri : "");
    emit("login_code", o);
    cJSON_Delete(o);
    cJSON *st = cJSON_CreateObject();
    cJSON_AddStringToObject(st, "text", "请在浏览器完成授权");
    emit("login_status", st);
    cJSON_Delete(st);
}

static void *task_run(void *p) {
    task_t *t = (task_t *)p;
    pymcl_ctx ctx = { ctx_progress, ctx_log, ctx_cancel, t, config_int("download_threads", 8) };
    int ok = 0;
    char msg[256] = {0};
    if (strcmp(t->method, "install_game") == 0) {
        const char *ver = pstr(t->args, "version", "");
        const char *loader = pstr(t->args, "loader", "无");
        const char *lv = pstr(t->args, "loader_version", "");
        const char *inst = pstr(t->args, "instance", config_str("default_instance", "default"));
        /* WinUI/EziApp 安装向导的 OptiFine / LiteLoader / 跳过 assets 勾选走
         * extra；原生安装器不支持这些，以前直接忽略——勾了 OptiFine 也报
         * 「安装完成」。带任一勾选就整体交给 Python 实现，装不了就明确报错。 */
        cJSON *xtra = cJSON_GetObjectItem(t->args, "extra");
        if (cJSON_IsTrue(cJSON_GetObjectItem(xtra, "optifine"))
            || cJSON_IsTrue(cJSON_GetObjectItem(xtra, "liteloader"))
            || cJSON_IsTrue(cJSON_GetObjectItem(xtra, "skip_assets"))) {
            ctx_progress(t, "正在安装（Python 后端）…", 0, 0);
            cJSON *r = py_rpc_call_t(t->method, t->args, 7200);
            ok = r != NULL;
            if (r) {
                const char *s = cJSON_GetStringValue(r);
                if (s && s[0]) snprintf(msg, sizeof(msg), "%s", s);
                cJSON_Delete(r);
            }
        } else if (loader && loader[0] && strcmp(loader, "无") != 0) {
            char vid[256];
            ok = install_loader(inst, loader, lv[0] ? lv : NULL, ver, &ctx, vid, sizeof(vid)) == 0;
            if (ok) snprintf(msg, sizeof(msg), "加载器安装完成: %s", vid);
        } else {
            ok = install_version(inst, ver, &ctx) == 0;
            if (ok) snprintf(msg, sizeof(msg), "版本 %s 安装完成", ver);
        }
    } else if (strcmp(t->method, "download_java") == 0) {
        int maj = pint(t->args, "major", 17);
        char *exe = java_install_adoptium(maj, NULL, &ctx);
        ok = exe != NULL;
        if (ok) snprintf(msg, sizeof(msg), "Java %d 就绪: %s", maj, exe);
        free(exe);
    } else if (strcmp(t->method, "install_mod") == 0 && !extra_pins_version(t->args)) {
        const char *name = pstr(t->args, "name", "");
        const char *inst = pstr(t->args, "instance", "default");
        cJSON *extra = cJSON_GetObjectItem(t->args, "extra");
        ok = install_mod(inst, name, extra, &ctx) == 0;
        if (ok) snprintf(msg, sizeof(msg), "模组安装完成");
    } else if (strcmp(t->method, "install_modpack") == 0 && !extra_pins_version(t->args)) {
        ok = install_modpack(pstr(t->args, "name", ""), pstr(t->args, "source", "Modrinth"),
                             cJSON_GetObjectItem(t->args, "extra"), &ctx) == 0;
        if (ok) snprintf(msg, sizeof(msg), "整合包安装完成");
    } else if ((strcmp(t->method, "install_shader") == 0 ||
                strcmp(t->method, "install_resourcepack") == 0 ||
                strcmp(t->method, "install_datapack") == 0)
               && !extra_pins_version(t->args)) {
        const char *kind = strstr(t->method, "shader") ? "shader" :
            strstr(t->method, "resource") ? "resourcepack" : "datapack";
        ok = install_content(kind, pstr(t->args, "instance", "default"),
                             pstr(t->args, "name", ""), cJSON_GetObjectItem(t->args, "extra"), &ctx) == 0;
        if (ok) snprintf(msg, sizeof(msg), "完成");
    } else if (strcmp(t->method, "launch_game") == 0) {
        const char *inst = pstr(t->args, "instance", "default");
        const char *ver = pstr(t->args, "version", "");
        const char *account = pstr(t->args, "account", "离线模式");
        const char *user = pstr(t->args, "username", "Player");
        int mem = pint(t->args, "memory_mb", 4096);
        int w = pint(t->args, "width", 854);
        int h = pint(t->args, "height", 480);
        const char *java = pstr(t->args, "java", PYMCL_JAVA_AUTO);
        if (!ver[0]) { pymcl_set_error("请先选择版本"); }
        else {
            cJSON *vset = load_version_settings(inst, ver);
            const char *bound = cJSON_GetStringValue(cJSON_GetObjectItem(vset, "login_account"));
            if (bound && bound[0]) {
                account = bound;
                char lb[512];
                snprintf(lb, sizeof(lb), "该版本绑定账号: %s", bound);
                ctx_log(t, lb);
            }
            cJSON *vnum = cJSON_GetObjectItem(vset, "memory_mb");
            if (cJSON_IsNumber(vnum) && vnum->valuedouble > 0) mem = (int)vnum->valuedouble;
            vnum = cJSON_GetObjectItem(vset, "window_width");
            if (cJSON_IsNumber(vnum) && vnum->valuedouble > 0) w = (int)vnum->valuedouble;
            vnum = cJSON_GetObjectItem(vset, "window_height");
            if (cJSON_IsNumber(vnum) && vnum->valuedouble > 0) h = (int)vnum->valuedouble;
            const char *wmode = cJSON_GetStringValue(cJSON_GetObjectItem(vset, "window_mode"));
            if (!wmode || !wmode[0]) wmode = config_str("window_mode", "window");
            int fullscreen = strcmp(wmode, "maximize") == 0 || strcmp(wmode, "fullscreen") == 0;
            if (fullscreen) {
                if (w < 1280) w = 1280;
                if (h < 720) h = 720;
            }
            const char *n8 = cJSON_GetStringValue(cJSON_GetObjectItem(vset, "nide8_id"));
            const char *asrv = cJSON_GetStringValue(cJSON_GetObjectItem(vset, "auth_server"));
            if ((n8 && n8[0]) || (asrv && asrv[0]))
                ctx_log(t, "该版本设置了统一通行证/皮肤站登录，C 后端暂不支持注入 javaagent，本次启动未生效");
            config_set_str("default_instance", inst);
            config_save();
            cJSON *acc = NULL;
            if (!account[0] || strcmp(account, "离线模式") == 0)
                acc = account_offline(user);
            else {
                cJSON *root = accounts_load();
                cJSON *it;
                cJSON_ArrayForEach(it, cJSON_GetObjectItem(root, "accounts")) {
                    if (strcmp(cJSON_GetStringValue(cJSON_GetObjectItem(it, "name")) ?: "", account) == 0)
                        acc = cJSON_Duplicate(it, 1);
                }
                cJSON_Delete(root);
                if (acc) {
                    cJSON *v = account_ensure_valid(acc);
                    cJSON_Delete(acc);
                    acc = v;
                }
            }
            if (!acc && account[0] && strcmp(account, "离线模式") != 0)
                pymcl_set_error("账号不存在: %s", account);
            else {
                if (!acc) acc = account_offline(user);
                cJSON *props = account_launch_props(acc);
                cJSON *vj = instance_resolved_version(inst, ver);
                if (!vj) vj = instance_version_json(inst, ver);
                /* Java 优先级对齐 bridge/api.py：版本设置 > 调用参数 >
                 * 实例偏好 > 全局 default_java。 */
                char jpbuf[PYMCL_PATH];
                const char *prefer = java;
                const char *vjava = cJSON_GetStringValue(cJSON_GetObjectItem(vset, "java"));
                if (vjava && vjava[0] && !pymcl_ieq(vjava, PYMCL_JAVA_AUTO)) prefer = vjava;
                if (!prefer || !prefer[0] || pymcl_ieq(prefer, PYMCL_JAVA_AUTO)) {
                    instance_java_pref(inst, jpbuf, sizeof(jpbuf));
                    prefer = jpbuf;
                }
                if (!prefer[0] || pymcl_ieq(prefer, PYMCL_JAVA_AUTO)) {
                    const char *dj = config_str("default_java", "");
                    if (dj[0]) prefer = dj;
                }
                cJSON *jprobe = vj ? vj : cJSON_Parse("{}");
                char *jexe = java_resolve_launch(jprobe, prefer, &ctx);
                if (jprobe != vj) cJSON_Delete(jprobe);
                if (vj) cJSON_Delete(vj);
                char **argv = NULL; int argc = 0; char natives[PYMCL_PATH];
                /* 版本隔离：游戏目录可能是 versions/<ver> 而非实例根。 */
                char gdir[PYMCL_PATH];
                const char *iso = cJSON_GetStringValue(cJSON_GetObjectItem(vset, "isolation"));
                if (!iso || !iso[0]) iso = config_str("default_isolation", "none");
                vs_apply_isolation(inst, ver, iso, gdir, sizeof(gdir));
                /* GC 预设 + 版本 JVM 参数（gc.apply 语义）。 */
                const char *gck = cJSON_GetStringValue(cJSON_GetObjectItem(vset, "gc"));
                if (!gck || !gck[0]) gck = config_str("gc_preset", "auto");
                char xjvm[8192];
                gc_preset_apply(gck,
                                cJSON_GetStringValue(cJSON_GetObjectItem(vset, "jvm_args")) ?: "",
                                xjvm, sizeof(xjvm));
                /* 启动前命令（pre_launch / pre_launch_wait）。 */
                hook_run(t, cJSON_GetStringValue(cJSON_GetObjectItem(vset, "pre_launch")), gdir,
                         !cJSON_IsFalse(cJSON_GetObjectItem(vset, "pre_launch_wait")));
                if (jexe && build_launch_command(inst, ver, props, jexe, mem, w, h,
                                                 xjvm, gdir, &argv, &argc, natives, sizeof(natives)) == 0) {
                    /* 附加游戏参数 = 调用方 extra_game_args（WinUI 服务器直连）
                     * + 版本设置 game_args + 直连 server/port + 全屏。
                     * 对齐 launch_flow.prepare 的 extras 组装。 */
                    char **extras = NULL; int nx = 0;
                    int has_server = 0, has_fs = 0;
                    cJSON *xargs = cJSON_GetObjectItem(t->args, "extra_game_args");
                    cJSON *xa;
                    if (cJSON_IsArray(xargs)) cJSON_ArrayForEach(xa, xargs) {
                        const char *s = cJSON_GetStringValue(xa);
                        if (!s || !s[0]) continue;
                        if (strcmp(s, "--server") == 0) has_server = 1;
                        if (strcmp(s, "--fullscreen") == 0) has_fs = 1;
                        extras = (char **)realloc(extras, sizeof(char *) * (size_t)(nx + 1));
                        extras[nx++] = pymcl_strdup(s);
                    }
                    char **ga = NULL;
                    int nga = pymcl_split_args(
                        cJSON_GetStringValue(cJSON_GetObjectItem(vset, "game_args")) ?: "", &ga);
                    for (int i = 0; i < nga; i++) {
                        if (strcmp(ga[i], "--server") == 0) has_server = 1;
                        if (strcmp(ga[i], "--fullscreen") == 0) has_fs = 1;
                        extras = (char **)realloc(extras, sizeof(char *) * (size_t)(nx + 1));
                        extras[nx++] = ga[i];
                    }
                    free(ga);
                    const char *srv = cJSON_GetStringValue(cJSON_GetObjectItem(vset, "server"));
                    if (srv && srv[0] && !has_server) {
                        cJSON *pv = cJSON_GetObjectItem(vset, "port");
                        int pnum = cJSON_IsNumber(pv) ? (int)pv->valuedouble
                                   : (cJSON_IsString(pv) && pv->valuestring) ? atoi(pv->valuestring) : 0;
                        char ports[32];
                        snprintf(ports, sizeof(ports), "%d", pnum > 0 ? pnum : 25565);
                        extras = (char **)realloc(extras, sizeof(char *) * (size_t)(nx + 4));
                        extras[nx++] = pymcl_strdup("--server");
                        extras[nx++] = pymcl_strdup(srv);
                        extras[nx++] = pymcl_strdup("--port");
                        extras[nx++] = pymcl_strdup(ports);
                    }
                    if (fullscreen && !has_fs) {
                        extras = (char **)realloc(extras, sizeof(char *) * (size_t)(nx + 1));
                        extras[nx++] = pymcl_strdup("--fullscreen");
                    }
                    if (nx > 0) {
                        char **bigger = (char **)realloc(argv, sizeof(char *) * (size_t)(argc + nx));
                        if (bigger) {
                            argv = bigger;
                            for (int i = 0; i < nx; i++) argv[argc++] = extras[i];
                        } else {
                            for (int i = 0; i < nx; i++) free(extras[i]);
                        }
                    }
                    free(extras);
                    ctx_log(t, "正在启动游戏进程…");
                    HANDLE rd = NULL;
                    HANDLE proc = game_spawn((const char **)argv, argc, gdir, &rd);
                    pthread_mutex_lock(&g_mu);
                    g_game = proc;
                    snprintf(g_launch_id, sizeof(g_launch_id), "%s", t->id);
                    pthread_mutex_unlock(&g_mu);
                    if (proc) {
                        vs_apply_priority(proc, vset);
                        /* WinUI 主窗口靠 game_started/game_exited 做「启动后隐藏
                         * 启动器/退出后恢复」；Python 桥一直在发，C 桥以前不发，
                         * launcher_visibility 设置在 C 桥下整个是死的。 */
                        {
                            cJSON *o = cJSON_CreateObject();
                            emit("game_started", o);
                            cJSON_Delete(o);
                        }
                        char buf[4096]; DWORD got;
                        char *tail[CRASH_TAIL];
                        int tn = 0, ts = 0;
                        memset(tail, 0, sizeof(tail));
                        double started = (double)time(NULL);
                        while (ReadFile(rd, buf, sizeof(buf) - 1, &got, NULL) && got) {
                            buf[got] = 0;
                            char *line = buf;
                            while (line && *line) {
                                char *nl = strchr(line, '\n');
                                if (nl) *nl = 0;
                                if (line[0] && line[0] != '\r') {
                                    ctx_log(t, line);
                                    if (tn < CRASH_TAIL) tail[tn++] = _strdup(line);
                                    else {
                                        free(tail[ts]);
                                        tail[ts] = _strdup(line);
                                        ts = (ts + 1) % CRASH_TAIL;
                                    }
                                }
                                line = nl ? nl + 1 : NULL;
                            }
                            if (t->cancelled) { game_kill(proc); break; }
                        }
                        WaitForSingleObject(proc, INFINITE);
                        DWORD code = 0;
                        GetExitCodeProcess(proc, &code);
                        CloseHandle(rd); CloseHandle(proc);
                        pthread_mutex_lock(&g_mu);
                        if (g_game == proc) g_game = NULL;
                        pthread_mutex_unlock(&g_mu);
                        long scode = (long)code;
                        if (code > 0x7FFFFFFFu) scode = (long)(code - 0x100000000ull);
                        /* 对齐 bridge/api.py：finally 里必发，取消也发。 */
                        {
                            cJSON *o = cJSON_CreateObject();
                            cJSON_AddNumberToObject(o, "code", (double)scode);
                            emit("game_exited", o);
                            cJSON_Delete(o);
                        }
                        /* 退出后命令（post_launch），取消时不跑。 */
                        if (!t->cancelled)
                            hook_run(t, cJSON_GetStringValue(cJSON_GetObjectItem(vset, "post_launch")),
                                     gdir, 1);
                        if (t->cancelled) { ok = 1; snprintf(msg, sizeof(msg), "已停止游戏"); }
                        else {
                            cJSON *rep = analyze_game_crash(inst, ver, scode, tail, tn, ts, started);
                            int crashed = 0;
                            const char *summary = NULL;
                            if (rep) {
                                cJSON *ic = cJSON_GetObjectItem(rep, "is_crash");
                                crashed = cJSON_IsTrue(ic);
                                summary = cJSON_GetStringValue(cJSON_GetObjectItem(rep, "summary"));
                                if (crashed) {
                                    cJSON_AddStringToObject(rep, "task_id", t->id);
                                    emit("crash", rep);
                                }
                                cJSON_Delete(rep);
                            }
                            if (crashed) {
                                ok = 0;
                                snprintf(msg, sizeof(msg), "%s", summary && summary[0] ? summary : "游戏崩溃");
                                pymcl_set_error("%s", msg);
                            } else if (code == 0) {
                                ok = 1;
                                snprintf(msg, sizeof(msg), "游戏已退出");
                            } else {
                                pymcl_set_error("游戏退出码 %lu", (unsigned long)code);
                                ok = 0;
                            }
                        }
                        for (int i = 0; i < tn; i++) free(tail[i]);
                    } else pymcl_set_error("无法启动游戏进程");
                    for (int i = 0; i < argc; i++) free(argv[i]);
                    free(argv);
                }
                free(jexe);
                cJSON_Delete(props);
                cJSON_Delete(acc);
            }
            cJSON_Delete(vset);
        }
    } else if (strcmp(t->method, "start_microsoft_login") == 0) {
        cJSON *acc = NULL;
        ok = ms_login(&ctx, on_login_code, t, &acc) == 0;
        if (ok && acc) {
            snprintf(msg, sizeof(msg), "已登录 %s", cJSON_GetStringValue(cJSON_GetObjectItem(acc, "name")) ?: "");
            cJSON_Delete(acc);
        }
    } else {
        /* 没有原生实现的任务：交给 Python 一次性进程，py_rpc.py 会等到
         * 里面的任务真正结束才返回，这里拿到的就是最终成败。
         * 无细粒度进度（子进程没有事件通道），先报一个不确定进度。 */
        ctx_progress(t, "正在处理（Python 后端）…", 0, 0);
        cJSON *r = py_rpc_call_t(t->method, t->args, 7200);
        ok = r != NULL;
        if (r) {
            const char *s = cJSON_GetStringValue(r);
            if (s && s[0]) snprintf(msg, sizeof(msg), "%s", s);
            cJSON_Delete(r);
        }
    }
    if (!msg[0]) snprintf(msg, sizeof(msg), "%s", ok ? "任务完成" : (t->cancelled ? "已取消" : pymcl_error()));
    finish_task(t, ok && !t->cancelled, t->cancelled ? "已取消" : msg);
    return NULL;
}

static cJSON *start_task(const char *title, const char *method, cJSON *args) {
    pthread_mutex_lock(&g_mu);
    if (g_ntasks >= 32) { pthread_mutex_unlock(&g_mu); pymcl_set_error("任务过多"); return NULL; }
    task_t *t = (task_t *)calloc(1, sizeof(*t));
    if (!t) { pthread_mutex_unlock(&g_mu); pymcl_set_error("内存不足"); return NULL; }
    snprintf(t->id, sizeof(t->id), "task-%d", ++g_task_n);
    snprintf(t->title, sizeof(t->title), "%s", title);
    snprintf(t->method, sizeof(t->method), "%s", method);
    t->args = args ? cJSON_Duplicate(args, 1) : cJSON_CreateObject();
    g_tasks[g_ntasks++] = t;
    pthread_mutex_unlock(&g_mu);
    cJSON *ad = cJSON_CreateObject();
    cJSON_AddStringToObject(ad, "task_id", t->id);
    cJSON_AddStringToObject(ad, "title", title);
    emit("task_added", ad);
    cJSON_Delete(ad);
    emit_count();
    pthread_create(&t->th, NULL, task_run, t);
    pthread_detach(t->th);
    return cJSON_CreateString(t->id);
}

static const char *ensure_inst(const char *name) {
    if (name && name[0]) return name;
    return config_str("default_instance", "default");
}

static cJSON *rpc_get_instances(void) {
    cJSON *names = NULL;
    instance_list(&names);
    if (cJSON_GetArraySize(names) == 0) {
        instance_create(config_str("default_instance", "default"), NULL);
        cJSON_Delete(names);
        instance_list(&names);
    }
    cJSON *out = cJSON_CreateArray();
    cJSON *it;
    cJSON_ArrayForEach(it, names) {
        const char *nm = it->valuestring;
        cJSON *ids = NULL;
        instance_installed_ids(nm, &ids);
        cJSON *meta = instance_meta(nm);
        cJSON *pack = cJSON_GetObjectItem(meta, "modpack");
        const char *packn = cJSON_IsObject(pack) ? cJSON_GetStringValue(cJSON_GetObjectItem(pack, "name")) : NULL;
        const char *mc = packn ? packn : cJSON_GetStringValue(cJSON_GetObjectItem(meta, "mc_version"));
        if (!mc && cJSON_GetArraySize(ids) > 0) mc = cJSON_GetArrayItem(ids, 0)->valuestring;
        if (!mc) mc = "未安装版本";
        char jp[PYMCL_PATH];
        instance_java_pref(nm, jp, sizeof(jp));
        cJSON *row = cJSON_CreateObject();
        cJSON_AddStringToObject(row, "name", nm);
        cJSON_AddNumberToObject(row, "versions", cJSON_GetArraySize(ids));
        const char *mcver = cJSON_GetStringValue(cJSON_GetObjectItem(meta, "mc_version"));
        const char *packver = cJSON_IsObject(pack) ? cJSON_GetStringValue(cJSON_GetObjectItem(pack, "version")) : NULL;
        cJSON_AddStringToObject(row, "mc", mc);
        cJSON_AddStringToObject(row, "pack", packn ? packn : "");
        cJSON_AddStringToObject(row, "mc_version", mcver ? mcver : "");
        cJSON_AddStringToObject(row, "pack_version", packver ? packver : "");
        cJSON_AddStringToObject(row, "java", jp);
        cJSON_AddStringToObject(row, "java_label", pymcl_ieq(jp, PYMCL_JAVA_AUTO) ? PYMCL_JAVA_AUTO : pymcl_basename(jp));
        cJSON_AddItemToArray(out, row);
        cJSON_Delete(ids);
        cJSON_Delete(meta);
    }
    cJSON_Delete(names);
    return out;
}

static cJSON *version_rows(cJSON *map) {
    cJSON *out = cJSON_CreateArray();
    cJSON *it;
    cJSON_ArrayForEach(it, map) {
        cJSON *row = cJSON_CreateObject();
        cJSON_AddStringToObject(row, "version", it->string);
        const char *ty = cJSON_GetStringValue(cJSON_GetObjectItem(it, "type"));
        if (ty && strcmp(ty, "release") == 0) cJSON_AddStringToObject(row, "type", "release");
        else if (ty && (strcmp(ty, "old_alpha") == 0 || strcmp(ty, "old_beta") == 0))
            cJSON_AddStringToObject(row, "type", ty);
        else cJSON_AddStringToObject(row, "type", "snapshot");
        const char *dt = cJSON_GetStringValue(cJSON_GetObjectItem(it, "releaseTime"));
        if (!dt) dt = cJSON_GetStringValue(cJSON_GetObjectItem(it, "time"));
        char d[16] = {0};
        if (dt) { memcpy(d, dt, 10); d[10] = 0; }
        cJSON_AddStringToObject(row, "date", d);
        cJSON_AddItemToArray(out, row);
    }
    return out;
}

static cJSON *rpc_java_list(int scan) {
    cJSON *src = scan ? java_all() : java_list_installed();
    cJSON *out = cJSON_CreateArray();
    cJSON *j;
    cJSON_ArrayForEach(j, src) {
        cJSON *row = cJSON_CreateObject();
        cJSON_AddStringToObject(row, "name", cJSON_GetStringValue(cJSON_GetObjectItem(j, "name")) ?: "Java");
        cJSON *maj = cJSON_GetObjectItem(j, "major");
        char ms[16];
        if (cJSON_IsNumber(maj)) snprintf(ms, sizeof(ms), "%d", (int)maj->valuedouble);
        else snprintf(ms, sizeof(ms), "%s", cJSON_GetStringValue(maj) ?: "?");
        cJSON_AddStringToObject(row, "major", ms);
        cJSON_AddStringToObject(row, "path", cJSON_GetStringValue(cJSON_GetObjectItem(j, "exe")) ?: "");
        cJSON_AddItemToArray(out, row);
    }
    cJSON_Delete(src);
    return out;
}

void backend_init(sse_emit_fn emit_fn) {
    g_emit = emit_fn;
    catalog_init();
    cJSON *n = NULL;
    instance_list(&n);
    if (cJSON_GetArraySize(n) == 0)
        instance_create(config_str("default_instance", "default"), NULL);
    cJSON_Delete(n);
}

void backend_shutdown(void) {
    if (g_game) game_kill(g_game);
}

cJSON *backend_call(const char *method, cJSON *params) {
    if (!method) return NULL;
    if (strcmp(method, "get_settings") == 0) {
        cJSON *o = cJSON_CreateObject();
        cJSON_AddBoolToObject(o, "share_libraries", config_bool("shared_libraries", 0));
        cJSON_AddBoolToObject(o, "share_assets", config_bool("shared_assets", 0));
        cJSON_AddNumberToObject(o, "download_threads", config_int("download_threads", 8));
        cJSON_AddNumberToObject(o, "default_memory_mb", config_int("memory_mb", 4096));
        cJSON *res = cJSON_CreateArray();
        cJSON_AddItemToArray(res, cJSON_CreateNumber(config_int("width", 854)));
        cJSON_AddItemToArray(res, cJSON_CreateNumber(config_int("height", 480)));
        cJSON_AddItemToObject(o, "default_resolution", res);
        cJSON_AddStringToObject(o, "ms_client_id", config_str("microsoft_client_id", ""));
        cJSON_AddStringToObject(o, "curseforge_api_key", config_str("curseforge_api_key", ""));
        /* WinUI 设置页/主页还绑定这些键；漏掉会让界面永远显示默认值 */
        cJSON_AddStringToObject(o, "ai_mode", config_str("ai_mode", "public"));
        cJSON_AddStringToObject(o, "ai_gateway_url", config_str("ai_gateway_url", ""));
        cJSON_AddStringToObject(o, "ai_base_url", config_str("ai_base_url", ""));
        cJSON_AddStringToObject(o, "ai_api_key", config_str("ai_api_key", ""));
        cJSON_AddStringToObject(o, "ai_model", config_str("ai_model", "deepseek-v4-flash"));
        cJSON_AddStringToObject(o, "default_isolation", config_str("default_isolation", "none"));
        cJSON_AddStringToObject(o, "default_jvm_args", config_str("default_jvm_args", ""));
        cJSON_AddStringToObject(o, "launcher_visibility", config_str("launcher_visibility", "keep"));
        cJSON_AddStringToObject(o, "gc_preset", config_str("gc_preset", "auto"));
        cJSON_AddStringToObject(o, "download_source", config_str("download_source", "auto"));
        cJSON_AddNumberToObject(o, "download_limit_kbps", config_int("download_limit_kbps", 0));
        cJSON_AddStringToObject(o, "homepage_mode", config_str("homepage_mode", "news"));
        cJSON_AddStringToObject(o, "custom_homepage", config_str("custom_homepage", ""));
        cJSON_AddBoolToObject(o, "auto_check_update", config_bool("auto_check_update", 1));
        cJSON_AddBoolToObject(o, "ui_fly_animation", config_bool("ui_fly_animation", 1));
        cJSON_AddNumberToObject(o, "ui_fly_duration_ms", config_int("ui_fly_duration_ms", 620));
        cJSON_AddBoolToObject(o, "ui_dark", config_bool("ui_dark", 0));
        cJSON_AddStringToObject(o, "root", g_root);
        return o;
    }
    if (strcmp(method, "save_settings") == 0 || strcmp(method, "update_settings") == 0) {
        cJSON *d = params;
        cJSON *inner = cJSON_GetObjectItem(d, "data");
        if (!cJSON_IsObject(inner)) inner = cJSON_GetObjectItem(d, "settings");
        if (cJSON_IsObject(inner)) d = inner;
        /* 局部更新语义（对齐 bridge/api.py）：只写提交了的键。
         * 早先 bool 不判存在性，部分提交会把没带的开关静默写成 false。 */
        if (cJSON_IsBool(cJSON_GetObjectItem(d, "share_libraries")))
            config_set_bool("shared_libraries", cJSON_IsTrue(cJSON_GetObjectItem(d, "share_libraries")));
        if (cJSON_IsBool(cJSON_GetObjectItem(d, "share_assets")))
            config_set_bool("shared_assets", cJSON_IsTrue(cJSON_GetObjectItem(d, "share_assets")));
        if (cJSON_IsNumber(cJSON_GetObjectItem(d, "download_threads")))
            config_set_int("download_threads", (int)cJSON_GetObjectItem(d, "download_threads")->valuedouble);
        if (cJSON_IsNumber(cJSON_GetObjectItem(d, "default_memory_mb")))
            config_set_int("memory_mb", (int)cJSON_GetObjectItem(d, "default_memory_mb")->valuedouble);
        cJSON *res = cJSON_GetObjectItem(d, "default_resolution");
        if (cJSON_IsArray(res) && cJSON_GetArraySize(res) >= 2) {
            config_set_int("width", (int)cJSON_GetArrayItem(res, 0)->valuedouble);
            config_set_int("height", (int)cJSON_GetArrayItem(res, 1)->valuedouble);
        }
        if (cJSON_IsString(cJSON_GetObjectItem(d, "ms_client_id")))
            config_set_str("microsoft_client_id", cJSON_GetObjectItem(d, "ms_client_id")->valuestring);
        if (cJSON_IsString(cJSON_GetObjectItem(d, "curseforge_api_key")))
            config_set_str("curseforge_api_key", cJSON_GetObjectItem(d, "curseforge_api_key")->valuestring);
        /* WinUI 设置页其余键：以前直接丢弃，界面提示“已保存”但什么都没写。 */
        {
            static const char *str_keys[] = {
                "ai_mode", "ai_gateway_url", "ai_base_url", "ai_api_key", "ai_model",
                "default_isolation", "default_jvm_args", "launcher_visibility",
                "gc_preset", "download_source", "homepage_mode", "custom_homepage",
            };
            for (size_t i = 0; i < sizeof(str_keys) / sizeof(str_keys[0]); i++) {
                cJSON *v = cJSON_GetObjectItem(d, str_keys[i]);
                if (cJSON_IsString(v)) config_set_str(str_keys[i], v->valuestring);
            }
        }
        if (cJSON_IsNumber(cJSON_GetObjectItem(d, "download_limit_kbps")))
            config_set_int("download_limit_kbps", (int)cJSON_GetObjectItem(d, "download_limit_kbps")->valuedouble);
        if (cJSON_IsNumber(cJSON_GetObjectItem(d, "ui_fly_duration_ms")))
            config_set_int("ui_fly_duration_ms", (int)cJSON_GetObjectItem(d, "ui_fly_duration_ms")->valuedouble);
        if (cJSON_IsBool(cJSON_GetObjectItem(d, "auto_check_update")))
            config_set_bool("auto_check_update", cJSON_IsTrue(cJSON_GetObjectItem(d, "auto_check_update")));
        if (cJSON_IsBool(cJSON_GetObjectItem(d, "ui_fly_animation")))
            config_set_bool("ui_fly_animation", cJSON_IsTrue(cJSON_GetObjectItem(d, "ui_fly_animation")));
        if (cJSON_IsBool(cJSON_GetObjectItem(d, "ui_dark")))
            config_set_bool("ui_dark", cJSON_IsTrue(cJSON_GetObjectItem(d, "ui_dark")));
        config_save();
        return cJSON_CreateTrue();
    }
    if (strcmp(method, "get_instances") == 0) return rpc_get_instances();
    if (strcmp(method, "create_instance") == 0) {
        if (instance_create(pstr(params, "name", ""), NULL) != 0) return NULL;
        emit("ui_changed", cJSON_CreateObject());
        return cJSON_CreateTrue();
    }
    if (strcmp(method, "delete_instance") == 0) {
        if (instance_delete(pstr(params, "name", "")) != 0) return NULL;
        emit("ui_changed", cJSON_CreateObject());
        return cJSON_CreateTrue();
    }
    if (strcmp(method, "rename_instance") == 0) {
        if (instance_rename(pstr(params, "name", ""), pstr(params, "new_name", "")) != 0) return NULL;
        emit("ui_changed", cJSON_CreateObject());
        return cJSON_CreateTrue();
    }
    if (strcmp(method, "open_instance_folder") == 0) {
        char ip[PYMCL_PATH];
        instance_path(pstr(params, "name", ""), ip, sizeof(ip));
        pymcl_open_folder(ip);
        return cJSON_CreateTrue();
    }
    if (strcmp(method, "get_version_list") == 0) {
        char mf[PYMCL_PATH];
        pymcl_path_join3(mf, sizeof(mf), g_root, "cache", "version_manifest.json");
        cJSON *cached = pymcl_read_json(mf);
        cJSON *map = cJSON_CreateObject();
        cJSON *v;
        cJSON_ArrayForEach(v, cJSON_GetObjectItem(cached, "versions")) {
            const char *id = cJSON_GetStringValue(cJSON_GetObjectItem(v, "id"));
            if (id) cJSON_AddItemToObject(map, id, cJSON_Duplicate(v, 1));
        }
        cJSON_Delete(cached);
        cJSON *rows = version_rows(map);
        cJSON_Delete(map);
        return rows;
    }
    if (strcmp(method, "fetch_version_list") == 0) {
        cJSON *map = manifest_list_remote(1);
        cJSON *rows = version_rows(map);
        cJSON_Delete(map);
        return rows;
    }
    if (strcmp(method, "get_installed_versions") == 0) {
        const char *inst = pstr(params, "instance", "");
        if (inst[0]) {
            cJSON *ids = NULL;
            instance_installed_ids(inst, &ids);
            /* 对齐 bridge/api.py：默认滤掉版本设置里 hidden 的版本。
             * 以前 C 桥不滤，用户在 Qt 里隐藏的版本换到 WinUI/WPF 又全冒出来。 */
            cJSON *inc = cJSON_GetObjectItem(params, "include_hidden");
            if (!cJSON_IsTrue(inc) && !config_bool("show_hidden_versions", 0)
                && cJSON_IsArray(ids)) {
                cJSON *out = cJSON_CreateArray();
                char vd[PYMCL_PATH], sp[PYMCL_PATH];
                instance_versions_dir(inst, vd, sizeof(vd));
                cJSON *v;
                cJSON_ArrayForEach(v, ids) {
                    const char *vid = cJSON_GetStringValue(v);
                    if (!vid) continue;
                    pymcl_path_join3(sp, sizeof(sp), vd, vid, "pymcl.json");
                    cJSON *vs = pymcl_read_json(sp);
                    int hid = vs && cJSON_IsTrue(cJSON_GetObjectItem(vs, "hidden"));
                    cJSON_Delete(vs);
                    if (!hid) cJSON_AddItemToArray(out, cJSON_CreateString(vid));
                }
                cJSON_Delete(ids);
                return out;
            }
            return ids;
        }
        cJSON *names = NULL, *out = cJSON_CreateArray();
        instance_list(&names);
        cJSON *it;
        cJSON_ArrayForEach(it, names) {
            cJSON *ids = NULL;
            instance_installed_ids(it->valuestring, &ids);
            cJSON *v;
            cJSON_ArrayForEach(v, ids) {
                char s[256];
                snprintf(s, sizeof(s), "%s / %s", it->valuestring, v->valuestring);
                cJSON_AddItemToArray(out, cJSON_CreateString(s));
            }
            cJSON_Delete(ids);
        }
        cJSON_Delete(names);
        return out;
    }
    if (strcmp(method, "uninstall_version") == 0) {
        const char *spec = pstr(params, "spec", "");
        char inst[128], vid[128];
        const char *sep = strstr(spec, " / ");
        if (sep) {
            snprintf(inst, sizeof(inst), "%.*s", (int)(sep - spec), spec);
            snprintf(vid, sizeof(vid), "%s", sep + 3);
        } else {
            snprintf(inst, sizeof(inst), "%s", config_str("default_instance", "default"));
            snprintf(vid, sizeof(vid), "%s", spec);
        }
        if (uninstall_version(inst, vid) != 0) return NULL;
        emit("ui_changed", cJSON_CreateObject());
        return cJSON_CreateTrue();
    }
    if (strcmp(method, "get_java_list") == 0)
        return rpc_java_list(cJSON_IsTrue(cJSON_GetObjectItem(params, "scan_system")));
    if (strcmp(method, "java_combo_options") == 0) {
        const char *inst = pstr(params, "instance", "default");
        int scan = cJSON_IsTrue(cJSON_GetObjectItem(params, "scan_system"));
        cJSON *opts = cJSON_CreateArray();
        cJSON *a = cJSON_CreateObject();
        cJSON_AddStringToObject(a, "label", PYMCL_JAVA_AUTO);
        cJSON_AddStringToObject(a, "value", PYMCL_JAVA_AUTO);
        cJSON_AddItemToArray(opts, a);
        cJSON *list = rpc_java_list(scan);
        cJSON *j;
        cJSON_ArrayForEach(j, list) {
            const char *p = cJSON_GetStringValue(cJSON_GetObjectItem(j, "path"));
            if (!p || !p[0]) continue;
            cJSON *o = cJSON_CreateObject();
            cJSON_AddStringToObject(o, "label", cJSON_GetStringValue(cJSON_GetObjectItem(j, "name")) ?: p);
            cJSON_AddStringToObject(o, "value", p);
            cJSON_AddItemToArray(opts, o);
        }
        cJSON_Delete(list);
        char stored[PYMCL_PATH];
        instance_java_pref(inst, stored, sizeof(stored));
        /* 对齐 bridge/api.py：已保存但不在扫描结果里的 Java 要补一条，
         * 否则 WinUI 下拉框显示「自动选择」，用户一点确定就把
         * 自定义 Java 偏好静默覆盖掉了。 */
        if (stored[0] && !pymcl_ieq(stored, PYMCL_JAVA_AUTO)) {
            int seen = 0;
            cJSON *o;
            cJSON_ArrayForEach(o, opts) {
                if (strcmp(cJSON_GetStringValue(cJSON_GetObjectItem(o, "value")) ?: "", stored) == 0)
                    seen = 1;
            }
            if (!seen) {
                cJSON *ex = cJSON_CreateObject();
                char lb[PYMCL_PATH + 16];
                snprintf(lb, sizeof(lb), "已保存 (%s)", stored);
                cJSON_AddStringToObject(ex, "label", lb);
                cJSON_AddStringToObject(ex, "value", stored);
                cJSON_AddItemToArray(opts, ex);
            }
        }
        return opts;
    }
    if (strcmp(method, "java_combo_label_for") == 0) {
        const char *inst = pstr(params, "instance", "default");
        char stored[PYMCL_PATH];
        instance_java_pref(inst, stored, sizeof(stored));
        cJSON *opts = cJSON_GetObjectItem(params, "options");
        cJSON *o;
        cJSON_ArrayForEach(o, opts) {
            if (strcmp(cJSON_GetStringValue(cJSON_GetObjectItem(o, "value")) ?: "", stored) == 0)
                return cJSON_CreateString(cJSON_GetStringValue(cJSON_GetObjectItem(o, "label")) ?: PYMCL_JAVA_AUTO);
        }
        return cJSON_CreateString(PYMCL_JAVA_AUTO);
    }
    if (strcmp(method, "set_instance_java") == 0) {
        instance_set_java_pref(pstr(params, "name", ""), pstr(params, "java", PYMCL_JAVA_AUTO));
        return cJSON_CreateTrue();
    }
    if (strcmp(method, "get_instance_java") == 0) {
        char jp[PYMCL_PATH];
        instance_java_pref(pstr(params, "name", ""), jp, sizeof(jp));
        return cJSON_CreateString(jp);
    }
    if (strcmp(method, "get_accounts") == 0) {
        cJSON *out = cJSON_CreateArray();
        cJSON_AddItemToArray(out, cJSON_CreateString("离线模式"));
        cJSON *root = accounts_load();
        cJSON *it;
        cJSON_ArrayForEach(it, cJSON_GetObjectItem(root, "accounts")) {
            const char *nm = cJSON_GetStringValue(cJSON_GetObjectItem(it, "name"));
            if (nm && nm[0]) cJSON_AddItemToArray(out, cJSON_CreateString(nm));
        }
        cJSON_Delete(root);
        return out;
    }
    if (strcmp(method, "search_mods") == 0 || strcmp(method, "search_modpacks") == 0
        || strcmp(method, "search_shaders") == 0 || strcmp(method, "search_resourcepacks") == 0
        || strcmp(method, "search_datapacks") == 0) {
        /* 目录页的「游戏版本 / 分类」筛选只有 Python 端实现；以前 C 桥把
         * extra 直接丢掉，筛选条件静默失效。带筛选就转发 Python，转发不了
         * 明确报错，绝不能装作筛过了。 */
        if (search_has_filters(params)) {
            cJSON *r = py_rpc_call(method, params);
            if (r) return r;
            pymcl_set_error("版本/分类筛选需要 Python 后端；清除筛选可用内置搜索");
            return NULL;
        }
        if (strcmp(method, "search_mods") == 0)
            return search_mods(pstr(params, "query", ""), pstr(params, "source", ""));
        if (strcmp(method, "search_modpacks") == 0)
            return search_modpacks(pstr(params, "query", ""), pstr(params, "source", ""));
        const char *kind = strstr(method, "shader") ? "shader" :
            strstr(method, "resource") ? "resourcepack" : "datapack";
        return search_content(kind, pstr(params, "query", ""), pstr(params, "source", ""));
    }
    if (strcmp(method, "get_installed_mods") == 0)
        return list_instance_files(pstr(params, "instance", "default"), "mods");
    if (strcmp(method, "get_installed_shaders") == 0)
        return list_instance_files(pstr(params, "instance", "default"), "shaderpacks");
    if (strcmp(method, "get_installed_resourcepacks") == 0)
        return list_instance_files(pstr(params, "instance", "default"), "resourcepacks");
    if (strcmp(method, "get_installed_datapacks") == 0)
        return list_instance_files(pstr(params, "instance", "default"), "datapacks");
    if (strcmp(method, "delete_mod") == 0) {
        delete_instance_file(pstr(params, "instance", "default"), "mods", pstr(params, "filename", ""));
        return cJSON_CreateTrue();
    }
    if (strcmp(method, "get_crash") == 0) {
        if (g_last_crash) return cJSON_Duplicate(g_last_crash, 1);
        return cJSON_CreateObject();
    }
    if (strcmp(method, "export_crash_report") == 0) {
        char py[PYMCL_PATH], jsonf[PYMCL_PATH], destf[PYMCL_PATH];
        const char *dest = pstr(params, "dest", "");
        find_python(py, sizeof(py));
        snprintf(jsonf, sizeof(jsonf), "%s\\last-crash.json", g_root);
        if (dest[0]) snprintf(destf, sizeof(destf), "%s", dest);
        else snprintf(destf, sizeof(destf), "%s\\crash-report.zip", g_root);
        const char *argv[10];
        int argc = 0;
        argv[argc++] = py;
        argv[argc++] = "-u";
        argv[argc++] = "-m";
        argv[argc++] = "mclauncher.crash";
        argv[argc++] = "--from-json";
        argv[argc++] = jsonf;
        argv[argc++] = "--export";
        argv[argc++] = destf;
        pymcl_run_process(argv, argc, g_root, NULL, NULL, 30);
        return cJSON_CreateString(destf);
    }
    if (strcmp(method, "open_crash_file") == 0) {
        const char *path = pstr(params, "path", "");
        if (!path[0] && g_last_crash) {
            const char *df = cJSON_GetStringValue(cJSON_GetObjectItem(g_last_crash, "direct_file"));
            path = df ? df : "";
        }
        if (!path[0]) { pymcl_set_error("没有可打开的日志文件"); return NULL; }
        pymcl_open_folder(path);
        return cJSON_CreateString(path);
    }
    if (strcmp(method, "cancel_task") == 0) {
        const char *tid = pstr(params, "task_id", "");
        pthread_mutex_lock(&g_mu);
        for (int i = 0; i < g_ntasks; i++)
            if (g_tasks[i] && strcmp(g_tasks[i]->id, tid) == 0) g_tasks[i]->cancelled = 1;
        if (strcmp(g_launch_id, tid) == 0 && g_game) game_kill(g_game);
        pthread_mutex_unlock(&g_mu);
        return cJSON_CreateTrue();
    }
    if (strcmp(method, "list_tasks") == 0) {
        cJSON *arr = cJSON_CreateArray();
        pthread_mutex_lock(&g_mu);
        for (int i = 0; i < g_ntasks; i++) {
            if (!g_tasks[i]) continue;
            cJSON *o = cJSON_CreateObject();
            cJSON_AddStringToObject(o, "id", g_tasks[i]->id);
            cJSON_AddStringToObject(o, "title", g_tasks[i]->title);
            cJSON_AddStringToObject(o, "status",
                                    g_tasks[i]->cancelled ? "cancelling" : "running");
            cJSON_AddItemToArray(arr, o);
        }
        pthread_mutex_unlock(&g_mu);
        return arr;
    }
    if (strcmp(method, "install_game") == 0)
        return start_task("安装游戏", method, params);
    if (strcmp(method, "download_java") == 0)
        return start_task("下载 Java", method, params);
    if (strcmp(method, "install_mod") == 0)
        return start_task("安装模组", method, params);
    if (strcmp(method, "install_modpack") == 0)
        return start_task("安装整合包", method, params);
    if (strcmp(method, "install_shader") == 0)
        return start_task("安装光影", method, params);
    if (strcmp(method, "install_resourcepack") == 0)
        return start_task("安装资源包", method, params);
    if (strcmp(method, "install_datapack") == 0)
        return start_task("安装数据包", method, params);
    if (strcmp(method, "launch_game") == 0)
        return start_task("启动游戏", method, params);
    if (strcmp(method, "start_microsoft_login") == 0)
        return start_task("微软登录", method, params);
    /* 下面这些没有原生实现，但必须以任务身份跑：以前直接丢给一次性 py_rpc，
     * 子进程拿到 task id 就退出，工作线程被杀死——UI 显示“已排队”实际什么都没发生。 */
    if (strcmp(method, "install_world") == 0)
        return start_task("安装世界", method, params);
    if (strcmp(method, "repair_version") == 0)
        return start_task("修复版本", method, params);
    if (strcmp(method, "export_modpack") == 0)
        return start_task("导出整合包", method, params);
    if (strcmp(method, "start_authlib_login") == 0)
        return start_task("皮肤站登录", method, params);
    if (strcmp(method, "start_nide8_login") == 0)
        return start_task("统一通行证登录", method, params);
    if (strcmp(method, "start_self_update") == 0)
        return start_task("更新启动器", method, params);
    if (strcmp(method, "start_mod_updates") == 0)
        return start_task("检查模组更新", method, params);
    if (strcmp(method, "terracotta_prepare") == 0)
        return start_task("准备陶瓦联机", method, params);

    /* Align remaining RPC with Python bridge/api.py (native first, then py_rpc). */
    {
        cJSON *aligned = rpc_align_call(method, params, emit);
        if (aligned) return aligned;
    }
    {
        cJSON *via_py = py_rpc_call(method, params);
        if (via_py) return via_py;
    }
    pymcl_set_error("unknown method: %s", method);
    return NULL;
}
