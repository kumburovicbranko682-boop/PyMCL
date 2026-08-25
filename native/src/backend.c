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

/* shell 原样执行钩子命令。不能走 pymcl_spawn_process：join_cmdline 会按
 * argv 重新加引号并反斜杠转义，用户写的完整命令行（含引号/管道）会被改写。
 * 对齐 Python 端 subprocess shell=True 的 cmd /c <原文> 语义。 */
static HANDLE hook_spawn(const char *command, const char *cwd, HANDLE *out_read) {
    char line[4096];
    snprintf(line, sizeof(line), "cmd /c %s", command);
    wchar_t *wcmd = pymcl_u8_to_wide(line);
    wchar_t *wcwd = cwd ? pymcl_u8_to_wide(cwd) : NULL;
    SECURITY_ATTRIBUTES sa = { sizeof(sa), NULL, TRUE };
    HANDLE rd = NULL, wr = NULL;
    STARTUPINFOW si; PROCESS_INFORMATION pi;
    memset(&si, 0, sizeof(si)); memset(&pi, 0, sizeof(pi));
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;
    if (out_read) {
        if (!CreatePipe(&rd, &wr, &sa, 0)) { free(wcmd); free(wcwd); return NULL; }
        SetHandleInformation(rd, HANDLE_FLAG_INHERIT, 0);
        si.dwFlags |= STARTF_USESTDHANDLES;
        si.hStdOutput = wr;
        si.hStdError = wr;
        si.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    }
    BOOL ok = CreateProcessW(NULL, wcmd, NULL, NULL, out_read ? TRUE : FALSE,
                             CREATE_NO_WINDOW, NULL, wcwd, &si, &pi);
    free(wcmd); free(wcwd);
    if (wr) CloseHandle(wr);
    if (!ok) { if (rd) CloseHandle(rd); return NULL; }
    CloseHandle(pi.hThread);
    if (out_read) *out_read = rd;
    return pi.hProcess;
}

/* 版本设置的「启动前 / 退出后命令」（对齐 launch_flow.run_hook）。
 * 以前这两条命令在 C 桥启动时从不执行——保存成功但形同虚设。
 * 等待模式回放前 40 行输出，退出码非零时明确告知；失败不阻断启动，
 * 与 Python 行为一致。 */
static int run_launch_hook(task_t *t, const char *command, const char *cwd, int wait) {
    char cmd[2048];
    snprintf(cmd, sizeof(cmd), "%s", command ? command : "");
    char *s = cmd;
    while (*s == ' ' || *s == '\t') s++;
    size_t L = strlen(s);
    while (L && (unsigned char)s[L - 1] <= ' ') s[--L] = 0;
    if (!*s) return 0;
    char msg[2200];
    snprintf(msg, sizeof(msg), "运行启动脚本: %s", s);
    ctx_log(t, msg);
    if (!wait) {
        HANDLE p = hook_spawn(s, cwd, NULL);
        if (!p) ctx_log(t, "启动脚本无法启动");
        else CloseHandle(p);
        return 0;
    }
    HANDLE rd = NULL;
    HANDLE p = hook_spawn(s, cwd, &rd);
    if (!p) { ctx_log(t, "启动脚本无法启动"); return -1; }
    char buf[4096], acc[4096]; size_t al = 0; DWORD got;
    int lines = 0;
    while (ReadFile(rd, buf, sizeof(buf), &got, NULL) && got) {
        for (DWORD i = 0; i < got; i++) {
            if (buf[i] == '\n' || al >= sizeof(acc) - 2) {
                acc[al] = 0;
                if (al && acc[al - 1] == '\r') acc[al - 1] = 0;
                if (acc[0] && lines < 40) { ctx_log(t, acc); lines++; }
                al = 0;
            } else acc[al++] = buf[i];
        }
    }
    WaitForSingleObject(p, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(p, &code);
    CloseHandle(rd);
    CloseHandle(p);
    if (code) {
        snprintf(msg, sizeof(msg), "脚本退出码 %ld", (long)code);
        ctx_log(t, msg);
    }
    return (int)code;
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
        cJSON *extra = cJSON_GetObjectItem(t->args, "extra");
        int want_combo = cJSON_IsTrue(cJSON_GetObjectItem(extra, "optifine"))
                      || cJSON_IsTrue(cJSON_GetObjectItem(extra, "liteloader"))
                      || pymcl_ieq(loader, "optifine") || pymcl_ieq(loader, "liteloader");
        if (want_combo) {
            /* OptiFine / LiteLoader 组合装只有 Python 侧有实现；原生路径
             * 以前直接忽略这两个勾选，装个原版还报「安装完成」。 */
            ctx_log(t, "OptiFine/LiteLoader 组合安装交由 Python 后端…");
            /* 传入取消回调：用户点「取消」时真正杀掉 Python 子进程 */
            cJSON *r = py_rpc_call_c(t->method, t->args, 3600, ctx_cancel, t);
            ok = r != NULL;
            if (ok) {
                const char *s = cJSON_GetStringValue(r);
                snprintf(msg, sizeof(msg), "%s", (s && s[0]) ? s : "任务完成");
                cJSON_Delete(r);
            }
        } else {
            /* 与 Python 侧一致：勾选 extra.skip_assets 或全局设置 skip_assets 生效。
             * 以前这个勾选在原生安装路径被整个忽略。 */
            cJSON *sk = cJSON_GetObjectItem(extra, "skip_assets");
            ctx.skip_assets = sk ? cJSON_IsTrue(sk) : config_bool("skip_assets", 0);
            ctx_log(t, "安装到实例");
            if (loader && loader[0] && strcmp(loader, "无") != 0) {
                char vid[256];
                ok = install_loader(inst, loader, lv[0] ? lv : NULL, ver, &ctx, vid, sizeof(vid)) == 0;
                if (ok) snprintf(msg, sizeof(msg), "加载器安装完成: %s", vid);
            } else {
                ok = install_version(inst, ver, &ctx) == 0;
                if (ok) snprintf(msg, sizeof(msg), "版本 %s 安装完成", ver);
            }
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
        else {
            config_set_str("default_instance", inst);
            config_save();
            cJSON *acc = NULL;
            if (!account[0] || strcmp(account, "离线模式") == 0) {
                acc = account_offline(user);
                /* 设置页选的全局离线皮肤（Steve/Alex 固定 UUID）。Python 桥
                 * 启动时一直应用，这里以前忽略，同一账号两个桥皮肤不一致。 */
                account_apply_offline_skin(acc, config_str("offline_skin", ""));
            } else {
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
                    /* 实例也是「自动」时用全局默认 Java（设置页「设为默认」写的键）。
                     * Python 桥一直是这个次序，这里以前直接跳过全局默认。 */
                    if (pymcl_ieq(jpbuf, PYMCL_JAVA_AUTO)) {
                        const char *gd = config_str("default_java", "");
                        if (gd[0]) snprintf(jpbuf, sizeof(jpbuf), "%s", gd);
                    }
                    prefer = jpbuf;
                } else prefer = java;
                cJSON *jprobe = vj ? vj : cJSON_Parse("{}");
                char *jexe = java_resolve_launch(jprobe, prefer, &ctx);
                if (jprobe != vj) cJSON_Delete(jprobe);
                if (vj) cJSON_Delete(vj);
                char **argv = NULL; int argc = 0; char natives[PYMCL_PATH];
                char ip[PYMCL_PATH];
                /* 与 Python 桥一致：工作目录 = 隔离后的游戏目录（可能是
                 * versions/<id>），不是永远的实例根。 */
                pymcl_apply_isolation(inst, ver, ip, sizeof(ip));
                pymcl_launch_prep hooks;
                pymcl_launch_prep_load(inst, ver, &hooks);
                run_launch_hook(t, hooks.pre_launch, ip, hooks.pre_launch_wait);
                if (jexe && build_launch_command(inst, ver, props, jexe, mem, w, h,
                                                 cJSON_GetObjectItem(t->args, "extra_game_args"),
                                                 &argv, &argc, natives, sizeof(natives)) == 0) {
                    ctx_log(t, "正在启动游戏进程…");
                    HANDLE rd = NULL;
                    HANDLE proc = game_spawn((const char **)argv, argc, ip, &rd);
                    pthread_mutex_lock(&g_mu);
                    g_game = proc;
                    snprintf(g_launch_id, sizeof(g_launch_id), "%s", t->id);
                    pthread_mutex_unlock(&g_mu);
                    if (proc) {
                        /* WinUI「启动后隐藏启动器」和 EziApp 的运行状态都靠这对事件；
                         * Python 桥一直在发，这里以前从来不发，功能在 C 桥下静默失效。 */
                        {
                            cJSON *gs = cJSON_CreateObject();
                            emit("game_started", gs);
                            cJSON_Delete(gs);
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
                        /* 游玩时长入账（取消也算，Python 的 tracker 在 finally 里）。 */
                        {
                            long long dur = (long long)((double)time(NULL) - started);
                            pymcl_playtime_record(inst, ver, dur);
                            if (dur > 0) {
                                char fbuf[64], lbuf[128];
                                pymcl_format_playtime(dur, fbuf, sizeof(fbuf));
                                snprintf(lbuf, sizeof(lbuf), "本次游玩 %s", fbuf);
                                ctx_log(t, lbuf);
                            }
                        }
                        /* 与 Python 桥一致：取消也算退出，launcher 必须恢复可见。 */
                        {
                            cJSON *ge = cJSON_CreateObject();
                            cJSON_AddNumberToObject(ge, "code", (double)scode);
                            emit("game_exited", ge);
                            cJSON_Delete(ge);
                        }
                        if (t->cancelled) { ok = 1; snprintf(msg, sizeof(msg), "已停止游戏"); }
                        else {
                            /* 退出后命令：与 Python 一致，取消时不执行。 */
                            run_launch_hook(t, hooks.post_launch, ip, 1);
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
    } else {
        /* 皮肤站/统一通行证登录、自更新、模组更新、装世界、修复、导出整合包：
         * C 侧没有原生实现。任务线程里同步走 py_rpc（py_rpc.py 会等 Python
         * 侧任务真正结束再返回）。进度/日志事件带不回来，但成败是真的。 */
        ctx_log(t, "任务交由 Python 后端执行…");
        /* 传入取消回调：以前这里的任务点「取消」只翻个标志位，
         * Python 子进程照样跑满（最长一小时），任务卡在列表里假活着。 */
        cJSON *r = py_rpc_call_c(t->method, t->args, 3600, ctx_cancel, t);
        ok = r != NULL;
        if (ok) {
            const char *s = cJSON_GetStringValue(r);
            snprintf(msg, sizeof(msg), "%s", (s && s[0]) ? s : "任务完成");
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

/* save_settings 的局部更新辅助：键不在（或类型不对）就完全不动配置。 */
static void cfg_patch_bool(cJSON *d, const char *from, const char *key) {
    cJSON *v = cJSON_GetObjectItem(d, from);
    if (cJSON_IsBool(v)) config_set_bool(key, cJSON_IsTrue(v));
}
static void cfg_patch_int(cJSON *d, const char *from, const char *key) {
    cJSON *v = cJSON_GetObjectItem(d, from);
    if (cJSON_IsNumber(v)) config_set_int(key, (int)v->valuedouble);
}
static void cfg_patch_str(cJSON *d, const char *from, const char *key) {
    cJSON *v = cJSON_GetObjectItem(d, from);
    if (cJSON_IsString(v)) config_set_str(key, v->valuestring);
}

cJSON *backend_call(const char *method, cJSON *params) {
    if (!method) return NULL;
    if (strcmp(method, "get_settings") == 0) {
        /* WinUI 的 SettingsDto 读的键必须都带出去：以前只回 8 个，
         * AI / 隔离 / 下载源 / 动画等控件在 C 桥下永远显示默认值。 */
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
        cJSON_AddStringToObject(o, "ai_mode", config_str("ai_mode", "public"));
        cJSON_AddStringToObject(o, "ai_gateway_url", config_str("ai_gateway_url", ""));
        cJSON_AddStringToObject(o, "ai_base_url", config_str("ai_base_url", ""));
        cJSON_AddStringToObject(o, "ai_api_key", config_str("ai_api_key", ""));
        cJSON_AddStringToObject(o, "ai_model", config_str("ai_model", "deepseek-v4-flash"));
        cJSON_AddStringToObject(o, "default_isolation", config_str("default_isolation", "none"));
        cJSON_AddStringToObject(o, "default_jvm_args", config_str("default_jvm_args", ""));
        cJSON_AddStringToObject(o, "update_url", config_str("update_url", ""));
        cJSON_AddStringToObject(o, "download_source", config_str("download_source", "auto"));
        cJSON_AddStringToObject(o, "launcher_visibility", config_str("launcher_visibility", "keep"));
        cJSON_AddStringToObject(o, "gc_preset", config_str("gc_preset", "auto"));
        cJSON_AddNumberToObject(o, "download_limit_kbps", config_int("download_limit_kbps", 0));
        cJSON_AddBoolToObject(o, "auto_check_update", config_bool("auto_check_update", 1));
        cJSON_AddStringToObject(o, "custom_homepage", config_str("custom_homepage", ""));
        cJSON_AddStringToObject(o, "homepage_mode", config_str("homepage_mode", "news"));
        cJSON_AddStringToObject(o, "window_mode", config_str("window_mode", "window"));
        cJSON_AddBoolToObject(o, "ui_fly_animation", config_bool("ui_fly_animation", 1));
        cJSON_AddNumberToObject(o, "ui_fly_duration_ms", config_int("ui_fly_duration_ms", 620));
        cJSON_AddBoolToObject(o, "ui_motion", config_bool("ui_motion", 1));
        cJSON_AddBoolToObject(o, "skip_assets", config_bool("skip_assets", 0));
        /* EziApp 主题开关 / Java 页「设为默认」/ 离线皮肤读的键 */
        cJSON_AddBoolToObject(o, "ui_dark", config_bool("ui_dark", 0));
        cJSON_AddStringToObject(o, "default_java", config_str("default_java", ""));
        cJSON_AddStringToObject(o, "offline_skin", config_str("offline_skin", "default"));
        {
            char gd[PYMCL_PATH];
            pymcl_path_join(gd, sizeof(gd), g_root, config_str("instances_dir", ".minecraft"));
            cJSON_AddStringToObject(o, "game_dir", gd);
        }
        cJSON_AddStringToObject(o, "root", g_root);
        return o;
    }
    if (strcmp(method, "save_settings") == 0 || strcmp(method, "update_settings") == 0) {
        cJSON *d = params;
        cJSON *inner = cJSON_GetObjectItem(d, "data");
        if (!cJSON_IsObject(inner)) inner = cJSON_GetObjectItem(d, "settings");
        if (cJSON_IsObject(inner)) d = inner;
        /* 局部更新：没提交的键必须原样保留。以前 share_* 不看键在不在、
         * 直接按 IsTrue(NULL)=false 落盘，前端只保存 AI 三键（测试连接）
         * 就会把共享库/共享资源静默关掉。 */
        cfg_patch_bool(d, "share_libraries", "shared_libraries");
        cfg_patch_bool(d, "share_assets", "shared_assets");
        cfg_patch_int(d, "download_threads", "download_threads");
        cfg_patch_int(d, "default_memory_mb", "memory_mb");
        cJSON *res = cJSON_GetObjectItem(d, "default_resolution");
        if (cJSON_IsArray(res) && cJSON_GetArraySize(res) >= 2) {
            config_set_int("width", (int)cJSON_GetArrayItem(res, 0)->valuedouble);
            config_set_int("height", (int)cJSON_GetArrayItem(res, 1)->valuedouble);
        }
        cfg_patch_str(d, "ms_client_id", "microsoft_client_id");
        cfg_patch_str(d, "curseforge_api_key", "curseforge_api_key");
        /* WinUI 设置页提交、以前被静默丢弃的键 */
        cfg_patch_str(d, "ai_mode", "ai_mode");
        cfg_patch_str(d, "ai_gateway_url", "ai_gateway_url");
        cfg_patch_str(d, "ai_base_url", "ai_base_url");
        cfg_patch_str(d, "ai_api_key", "ai_api_key");
        cfg_patch_str(d, "ai_model", "ai_model");
        cfg_patch_str(d, "default_isolation", "default_isolation");
        cfg_patch_str(d, "default_jvm_args", "default_jvm_args");
        cfg_patch_str(d, "launcher_visibility", "launcher_visibility");
        cfg_patch_str(d, "gc_preset", "gc_preset");
        cfg_patch_str(d, "download_source", "download_source");
        cfg_patch_int(d, "download_limit_kbps", "download_limit_kbps");
        cfg_patch_str(d, "homepage_mode", "homepage_mode");
        cfg_patch_str(d, "custom_homepage", "custom_homepage");
        cfg_patch_bool(d, "auto_check_update", "auto_check_update");
        cfg_patch_str(d, "window_mode", "window_mode");
        cfg_patch_bool(d, "ui_fly_animation", "ui_fly_animation");
        cfg_patch_int(d, "ui_fly_duration_ms", "ui_fly_duration_ms");
        cfg_patch_bool(d, "ui_motion", "ui_motion");
        cfg_patch_bool(d, "skip_assets", "skip_assets");
        /* 以前被静默丢弃：EziApp 的主题开关、Java 页「设为默认」、离线皮肤 */
        cfg_patch_bool(d, "ui_dark", "ui_dark");
        cfg_patch_str(d, "default_java", "default_java");
        cfg_patch_str(d, "offline_skin", "offline_skin");
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
            /* 与 bridge/api.py 对齐：默认过滤 hidden 版本。以前 Qt 里隐藏的
             * 版本在 C 桥前端（WinUI/EziApp 启动页）照样全部列出来。 */
            if (cJSON_IsTrue(cJSON_GetObjectItem(params, "include_hidden"))
                || config_bool("show_hidden_versions", 0))
                return ids;
            cJSON *vis = cJSON_CreateArray();
            cJSON *v;
            cJSON_ArrayForEach(v, ids) {
                if (v->valuestring && !pymcl_version_hidden(inst, v->valuestring))
                    cJSON_AddItemToArray(vis, cJSON_CreateString(v->valuestring));
            }
            cJSON_Delete(ids);
            return vis;
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
    if (strncmp(method, "search_", 7) == 0 &&
        (strcmp(method, "search_mods") == 0 || strcmp(method, "search_modpacks") == 0
         || strcmp(method, "search_shaders") == 0 || strcmp(method, "search_resourcepacks") == 0
         || strcmp(method, "search_datapacks") == 0)) {
        /* 下载页的筛选框都塞在 extra 里；以前整个 extra 被丢掉，
         * 「游戏版本」和「分类」选了等于没选。 */
        cJSON *extra = cJSON_GetObjectItem(params, "extra");
        if (!cJSON_IsObject(extra)) extra = params;
        const char *gv = pstr(extra, "game_version", "");
        if (!gv[0]) gv = pstr(extra, "version", "");
        if (strncmp(gv, "全部", strlen("全部")) == 0) gv = "";
        const char *cat = pstr(extra, "category", "");
        if (!cat[0]) cat = pstr(extra, "type", "");
        if (strncmp(cat, "全部", strlen("全部")) == 0 || pymcl_ieq(cat, "all")) cat = "";
        if (cat[0]) {
            /* 分类→平台 facet 的映射表在 Python 侧（catalog_files.category_facets），
             * C 里不复制一份：有分类过滤时把整个搜索交给 Python 桥做。
             * Python 不可用再退回原生搜索（只按版本过滤，分类忽略）。 */
            cJSON *r = py_rpc_call(method, params);
            if (r) return r;
        }
        const char *q = pstr(params, "query", "");
        const char *src = pstr(params, "source", "");
        if (strcmp(method, "search_mods") == 0) return search_mods(q, src, gv);
        if (strcmp(method, "search_modpacks") == 0) return search_modpacks(q, src, gv);
        if (strcmp(method, "search_shaders") == 0) return search_content("shader", q, src, gv);
        if (strcmp(method, "search_resourcepacks") == 0) return search_content("resourcepack", q, src, gv);
        return search_content("datapack", q, src, gv);
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
        /* 返回值以前被无视：非法路径 / 文件不存在照样报 true，前端假成功 */
        if (delete_instance_file(pstr(params, "instance", "default"), "mods",
                                 pstr(params, "filename", "")) != 0)
            return NULL;
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
        /* 任务活在本进程里，绝不能落到 py_rpc：一次性 Python 进程永远看不到它们。 */
        cJSON *arr = cJSON_CreateArray();
        pthread_mutex_lock(&g_mu);
        for (int i = 0; i < g_ntasks; i++) {
            task_t *t = g_tasks[i];
            cJSON *o;
            if (!t) continue;
            o = cJSON_CreateObject();
            cJSON_AddStringToObject(o, "id", t->id);
            cJSON_AddStringToObject(o, "title", t->title);
            cJSON_AddStringToObject(o, "status", t->cancelled ? "cancelling" : "running");
            cJSON_AddStringToObject(o, "message", "");
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
    /* 下面这批以前直接丢给一次性 py_rpc：Python 侧 start_task 起线程就返回，
     * 子进程一退出任务就死——UI 拿到 task id 却永远等不到结果。
     * 现在包成 C 侧原生任务，在任务线程里同步等 Python 跑完。 */
    if (strcmp(method, "start_authlib_login") == 0)
        return start_task("皮肤站登录", method, params);
    if (strcmp(method, "start_nide8_login") == 0)
        return start_task("统一通行证登录", method, params);
    if (strcmp(method, "start_self_update") == 0)
        return start_task("更新启动器", method, params);
    if (strcmp(method, "start_mod_updates") == 0)
        return start_task("检查模组更新", method, params);
    if (strcmp(method, "install_world") == 0)
        return start_task("安装世界", method, params);
    if (strcmp(method, "repair_version") == 0)
        return start_task("修复版本", method, params);
    if (strcmp(method, "export_modpack") == 0)
        return start_task("导出整合包", method, params);

    /* Align remaining RPC with Python bridge/api.py (native first, then py_rpc). */
    {
        /* rpc_align_call 用「设错误 + 返回 NULL」表达显式拒绝（ai_send /
         * terracotta_host 这类一次性 py_rpc 下必假成功的方法）。以前这里
         * 把 NULL 一律当「没处理」，继续丢给通用 py_rpc 兜底，等于把上面
         * 特意立的挡板拆了：AI 页在 C 桥下又会永远卡在「正在想…」。
         * 先清错误缓冲，NULL + 有错误 = 已处理的拒绝，原样上抛。 */
        pymcl_set_error("");
        cJSON *aligned = rpc_align_call(method, params, emit);
        if (aligned) return aligned;
        if (pymcl_error()[0]) return NULL;
    }
    {
        cJSON *via_py = py_rpc_call(method, params);
        if (via_py) return via_py;
    }
    pymcl_set_error("unknown method: %s", method);
    return NULL;
}
