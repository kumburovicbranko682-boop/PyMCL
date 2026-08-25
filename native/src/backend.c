#include "pymcl.h"
#include <pthread.h>
#include <string.h>
#include <time.h>

static sse_emit_fn g_emit;
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;
static int g_task_n;

/* 运行中游戏注册表（多开管理，对齐 Python backend._game_procs） */
#define MAX_GAMES 16
typedef struct {
    HANDLE proc;
    DWORD pid;
    char task_id[32];
    char instance[128];
    char version[128];
    char account[64];
    double started;
} game_slot;
static game_slot g_games[MAX_GAMES];

static int game_slot_alive(const game_slot *s) {
    DWORD code = 0;
    if (!s->proc) return 0;
    if (GetExitCodeProcess(s->proc, &code) && code != STILL_ACTIVE) return 0;
    return 1;
}

static int games_running(void) {
    int any = 0;
    pthread_mutex_lock(&g_mu);
    for (int i = 0; i < MAX_GAMES; i++)
        if (game_slot_alive(&g_games[i])) { any = 1; break; }
    pthread_mutex_unlock(&g_mu);
    return any;
}

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
        ctx_log(t, "安装到实例");
        if (loader && loader[0] && strcmp(loader, "无") != 0) {
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
    } else if (strcmp(t->method, "install_mod") == 0) {
        const char *name = pstr(t->args, "name", "");
        const char *inst = pstr(t->args, "instance", "default");
        cJSON *extra = cJSON_GetObjectItem(t->args, "extra");
        ok = install_mod(inst, name, extra, &ctx) == 0;
        if (ok) snprintf(msg, sizeof(msg), "模组安装完成");
    } else if (strcmp(t->method, "install_modpack") == 0) {
        ok = install_modpack(pstr(t->args, "name", ""), pstr(t->args, "source", "Modrinth"),
                             cJSON_GetObjectItem(t->args, "extra"), &ctx) == 0;
        if (ok) snprintf(msg, sizeof(msg), "整合包安装完成");
    } else if (strcmp(t->method, "install_shader") == 0 ||
               strcmp(t->method, "install_resourcepack") == 0 ||
               strcmp(t->method, "install_datapack") == 0) {
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
        else if (!config_bool("allow_multi_instance", 0) && games_running()) {
            pymcl_set_error("游戏正在运行中\n若要同时运行多个游戏，请到设置开启「允许多开」");
        }
        else {
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
                char jpbuf[PYMCL_PATH];
                const char *prefer;
                if (!java || pymcl_ieq(java, PYMCL_JAVA_AUTO)) {
                    instance_java_pref(inst, jpbuf, sizeof(jpbuf));
                    prefer = jpbuf;
                } else prefer = java;
                cJSON *jprobe = vj ? vj : cJSON_Parse("{}");
                char *jexe = java_resolve_launch(jprobe, prefer, &ctx);
                if (jprobe != vj) cJSON_Delete(jprobe);
                if (vj) cJSON_Delete(vj);
                char **argv = NULL; int argc = 0; char natives[PYMCL_PATH];
                char ip[PYMCL_PATH];
                instance_path(inst, ip, sizeof(ip));
                if (jexe && build_launch_command(inst, ver, props, jexe, mem, w, h, &argv, &argc, natives, sizeof(natives)) == 0) {
                    ctx_log(t, "正在启动游戏进程…");
                    HANDLE rd = NULL;
                    HANDLE proc = game_spawn((const char **)argv, argc, ip, &rd);
                    int gslot = -1;
                    pthread_mutex_lock(&g_mu);
                    if (proc) {
                        for (int i = 0; i < MAX_GAMES; i++)
                            if (!g_games[i].proc) { gslot = i; break; }
                        if (gslot >= 0) {
                            game_slot *s = &g_games[gslot];
                            s->proc = proc;
                            s->pid = GetProcessId(proc);
                            snprintf(s->task_id, sizeof(s->task_id), "%s", t->id);
                            snprintf(s->instance, sizeof(s->instance), "%s", inst);
                            snprintf(s->version, sizeof(s->version), "%s", ver);
                            snprintf(s->account, sizeof(s->account), "%s",
                                     (account[0] && strcmp(account, "离线模式") != 0) ? account : user);
                            s->started = (double)time(NULL);
                        }
                    }
                    pthread_mutex_unlock(&g_mu);
                    if (proc) {
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
                        pthread_mutex_lock(&g_mu);
                        if (gslot >= 0 && g_games[gslot].proc == proc)
                            memset(&g_games[gslot], 0, sizeof(g_games[gslot]));
                        pthread_mutex_unlock(&g_mu);
                        CloseHandle(rd); CloseHandle(proc);
                        if (t->cancelled) { ok = 1; snprintf(msg, sizeof(msg), "已停止游戏"); }
                        else {
                            long scode = (long)code;
                            if (code > 0x7FFFFFFFu) scode = (long)(code - 0x100000000ull);
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
        }
    } else if (strcmp(t->method, "start_microsoft_login") == 0) {
        cJSON *acc = NULL;
        ok = ms_login(&ctx, on_login_code, t, &acc) == 0;
        if (ok && acc) {
            snprintf(msg, sizeof(msg), "已登录 %s", cJSON_GetStringValue(cJSON_GetObjectItem(acc, "name")) ?: "");
            cJSON_Delete(acc);
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
    pthread_mutex_lock(&g_mu);
    for (int i = 0; i < MAX_GAMES; i++)
        if (g_games[i].proc) game_kill(g_games[i].proc);
    pthread_mutex_unlock(&g_mu);
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
        cJSON_AddStringToObject(o, "root", g_root);
        return o;
    }
    if (strcmp(method, "save_settings") == 0 || strcmp(method, "update_settings") == 0) {
        cJSON *d = params;
        cJSON *inner = cJSON_GetObjectItem(d, "data");
        if (!cJSON_IsObject(inner)) inner = cJSON_GetObjectItem(d, "settings");
        if (cJSON_IsObject(inner)) d = inner;
        config_set_bool("shared_libraries", cJSON_IsTrue(cJSON_GetObjectItem(d, "share_libraries")));
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
    if (strcmp(method, "search_mods") == 0)
        return search_mods(pstr(params, "query", ""), pstr(params, "source", ""));
    if (strcmp(method, "search_modpacks") == 0)
        return search_modpacks(pstr(params, "query", ""), pstr(params, "source", ""));
    if (strcmp(method, "search_shaders") == 0)
        return search_content("shader", pstr(params, "query", ""), pstr(params, "source", ""));
    if (strcmp(method, "search_resourcepacks") == 0)
        return search_content("resourcepack", pstr(params, "query", ""), pstr(params, "source", ""));
    if (strcmp(method, "search_datapacks") == 0)
        return search_content("datapack", pstr(params, "query", ""), pstr(params, "source", ""));
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
        /* 多开时按任务结束对应的游戏进程 */
        for (int i = 0; i < MAX_GAMES; i++)
            if (g_games[i].proc && strcmp(g_games[i].task_id, tid) == 0)
                game_kill(g_games[i].proc);
        pthread_mutex_unlock(&g_mu);
        return cJSON_CreateTrue();
    }
    if (strcmp(method, "is_game_running") == 0)
        return cJSON_CreateBool(games_running());
    if (strcmp(method, "get_running_games") == 0) {
        cJSON *out = cJSON_CreateArray();
        double now = (double)time(NULL);
        pthread_mutex_lock(&g_mu);
        for (int i = 0; i < MAX_GAMES; i++) {
            if (!game_slot_alive(&g_games[i])) continue;
            cJSON *row = cJSON_CreateObject();
            cJSON_AddNumberToObject(row, "pid", (double)g_games[i].pid);
            cJSON_AddStringToObject(row, "task_id", g_games[i].task_id);
            cJSON_AddStringToObject(row, "instance", g_games[i].instance);
            cJSON_AddStringToObject(row, "version", g_games[i].version);
            cJSON_AddStringToObject(row, "account", g_games[i].account);
            double up = now - g_games[i].started;
            cJSON_AddNumberToObject(row, "uptime", up > 0 ? up : 0);
            cJSON_AddItemToArray(out, row);
        }
        pthread_mutex_unlock(&g_mu);
        return out;
    }
    if (strcmp(method, "stop_game") == 0) {
        int pid = pint(params, "pid", 0);
        int n = 0;
        pthread_mutex_lock(&g_mu);
        for (int i = 0; i < MAX_GAMES; i++) {
            if (!game_slot_alive(&g_games[i])) continue;
            if (pid && (DWORD)pid != g_games[i].pid) continue;
            game_kill(g_games[i].proc);
            n++;
        }
        pthread_mutex_unlock(&g_mu);
        return cJSON_CreateNumber(n);
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
