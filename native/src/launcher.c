#include "pymcl.h"
#include <ctype.h>

static cJSON *load_parent_ud(const char *pid, void *ud) {
    const char *inst = (const char *)ud;
    return instance_version_json(inst, pid);
}

static void expand_args(cJSON *raw, cJSON *ph, int custom_res, char ***out, int *n) {
    *out = NULL; *n = 0;
    if (!cJSON_IsArray(raw)) return;
    cJSON *e;
    cJSON_ArrayForEach(e, raw) {
        if (cJSON_IsString(e)) {
            char buf[8192];
            pymcl_replace_placeholders(e->valuestring, ph, buf, sizeof(buf));
            if (!pymcl_has_placeholder(buf)) {
                *out = (char **)realloc(*out, sizeof(char *) * (size_t)(*n + 1));
                (*out)[(*n)++] = pymcl_strdup(buf);
            }
        } else if (cJSON_IsObject(e)) {
            if (!pymcl_check_rules(cJSON_GetObjectItem(e, "rules"), custom_res)) continue;
            cJSON *val = cJSON_GetObjectItem(e, "value");
            if (cJSON_IsString(val)) {
                char buf[8192];
                pymcl_replace_placeholders(val->valuestring, ph, buf, sizeof(buf));
                if (!pymcl_has_placeholder(buf)) {
                    *out = (char **)realloc(*out, sizeof(char *) * (size_t)(*n + 1));
                    (*out)[(*n)++] = pymcl_strdup(buf);
                }
            } else if (cJSON_IsArray(val)) {
                cJSON *x;
                cJSON_ArrayForEach(x, val) {
                    if (!cJSON_IsString(x)) continue;
                    char buf[8192];
                    pymcl_replace_placeholders(x->valuestring, ph, buf, sizeof(buf));
                    if (!pymcl_has_placeholder(buf)) {
                        *out = (char **)realloc(*out, sizeof(char *) * (size_t)(*n + 1));
                        (*out)[(*n)++] = pymcl_strdup(buf);
                    }
                }
            }
        }
    }
}

static const char *jvm_val_flags[] = {
    "-p", "-cp", "-classpath", "--class-path", "--module-path",
    "--add-modules", "--add-opens", "--add-exports", "--add-reads", NULL
};
static int is_val_flag(const char *a) {
    for (int i = 0; jvm_val_flags[i]; i++) if (strcmp(a, jvm_val_flags[i]) == 0) return 1;
    return 0;
}
static void drop_orphan(char ***args, int *n) {
    char **in = *args; int nin = *n;
    char **out = (char **)calloc((size_t)nin, sizeof(char *));
    int no = 0;
    for (int i = 0; i < nin; i++) {
        if (is_val_flag(in[i])) {
            const char *nxt = (i + 1 < nin) ? in[i + 1] : "";
            if (!nxt[0] || nxt[0] == '-') { free(in[i]); continue; }
            out[no++] = in[i];
            out[no++] = in[++i];
            continue;
        }
        out[no++] = in[i];
    }
    free(in);
    *args = out; *n = no;
}

static void apply_memory(char ***args, int *n, int memory_mb) {
    if (memory_mb < 512) memory_mb = 512;
    int xms = memory_mb / 2; if (xms > 1024) xms = 1024;
    char **in = *args; int nin = *n;
    char **out = (char **)calloc((size_t)nin + 4, sizeof(char *));
    int no = 0;
    for (int i = 0; i < nin; i++) {
        if (pymcl_startswith(in[i], "-Xmx") || pymcl_startswith(in[i], "-Xms")) { free(in[i]); continue; }
        out[no++] = in[i];
    }
    char a[32], b[32];
    snprintf(a, sizeof(a), "-Xmx%dM", memory_mb);
    snprintf(b, sizeof(b), "-Xms%dM", xms);
    out[no++] = pymcl_strdup(a);
    out[no++] = pymcl_strdup(b);
    free(in);
    *args = out; *n = no;
}

/* 拆用户参数串追加进数组（引号感知；反斜杠按字面处理，Windows 路径友好）。
 * 对齐 mclauncher/argsplit.split_args 的引号语义。 */
static void split_args_append(const char *text, char ***out, int *n) {
    if (!text) return;
    const char *p = text;
    char tok[2048];
    while (*p) {
        while (*p && isspace((unsigned char)*p)) p++;
        if (!*p) break;
        size_t L = 0;
        char q = 0;
        while (*p && (q || !isspace((unsigned char)*p))) {
            if (!q && (*p == '"' || *p == '\'')) { q = *p++; continue; }
            if (q && *p == q) { q = 0; p++; continue; }
            if (L + 1 < sizeof(tok)) tok[L++] = *p;
            p++;
        }
        tok[L] = 0;
        if (L) {
            *out = (char **)realloc(*out, sizeof(char *) * (size_t)(*n + 1));
            (*out)[(*n)++] = pymcl_strdup(tok);
        }
    }
}

/* GC 预设参数表（对齐 mclauncher/gc.py 的 ARGS，未知键回落 auto）。 */
static const char *gc_preset_args(const char *key) {
    static const struct { const char *k; const char *a; } tbl[] = {
        { "auto", "-XX:+UseG1GC -XX:+UnlockExperimentalVMOptions -XX:G1NewSizePercent=20 -XX:G1ReservePercent=20 -XX:MaxGCPauseMillis=50 -XX:G1HeapRegionSize=32M" },
        { "g1", "-XX:+UseG1GC" },
        { "g1_tuned", "-XX:+UseG1GC -XX:+UnlockExperimentalVMOptions -XX:G1NewSizePercent=20 "
                      "-XX:G1ReservePercent=20 -XX:MaxGCPauseMillis=50 -XX:G1HeapRegionSize=32M "
                      "-XX:+DisableExplicitGC -XX:+AlwaysPreTouch -XX:+ParallelRefProcEnabled" },
        { "zgc", "-XX:+UseZGC -XX:+UnlockExperimentalVMOptions" },
        { "none", "" },
    };
    if (!key || !key[0]) key = "auto";
    for (size_t i = 0; i < sizeof(tbl) / sizeof(tbl[0]); i++)
        if (pymcl_ieq(key, tbl[i].k)) return tbl[i].a;
    return tbl[0].a;
}

/* GC 预设接到版本 jvm_args 前面；用户已写 GC 旗标时不叠加（对齐 gc.apply）。 */
static void gc_merge_jvm(const char *preset, const char *existing, char *out, size_t n) {
    char **bits = NULL; int nb = 0;
    split_args_append(existing, &bits, &nb);
    int has_gc = 0;
    for (int i = 0; i < nb; i++) {
        if (pymcl_startswith(bits[i], "-XX:+Use") && strstr(bits[i], "GC")) has_gc = 1;
        free(bits[i]);
    }
    free(bits);
    const char *extra = gc_preset_args(preset);
    if (has_gc || !extra[0]) snprintf(out, n, "%s", existing ? existing : "");
    else if (!existing || !existing[0]) snprintf(out, n, "%s", extra);
    else snprintf(out, n, "%s %s", extra, existing);
}

static void patch_ignore(char **args, int n, const char **names, int nn) {
    for (int i = 0; i < n; i++) {
        if (!pymcl_startswith(args[i], "-DignoreList=")) continue;
        char buf[2048];
        snprintf(buf, sizeof(buf), "%s", args[i]);
        for (int k = 0; k < nn; k++) {
            if (!names[k] || !names[k][0]) continue;
            if (!strstr(buf, names[k])) {
                size_t L = strlen(buf);
                snprintf(buf + L, sizeof(buf) - L, ",%s", names[k]);
            }
        }
        free(args[i]);
        args[i] = pymcl_strdup(buf);
    }
}

int build_launch_command(const char *instance, const char *version, cJSON *account_props,
                         const char *java_exe, int memory_mb, int width, int height,
                         cJSON *extra_game_args,
                         char ***argv, int *argc, char *natives_out, size_t nn) {
    /* 版本设置以前在 C 桥启动时完全不生效：内存/JVM/GC/游戏参数/直连/
     * 全屏/窗口尺寸保存了也白存。取值次序对齐 launch_flow.prepare。 */
    pymcl_launch_prep prep;
    pymcl_launch_prep_load(instance, version, &prep);
    if (prep.memory_mb > 0) memory_mb = prep.memory_mb;
    if (prep.window_width > 0) width = prep.window_width;
    if (prep.window_height > 0) height = prep.window_height;
    if (prep.fullscreen) {
        /* 全屏兜底 1280x720（对齐 launch_flow.resolve_resolution）。 */
        if (width < 1280) width = 1280;
        if (height < 720) height = 720;
    }
    cJSON *vjson = instance_version_json(instance, version);
    if (!vjson) { pymcl_set_error("版本 %s 未安装，请先安装。", version); return -1; }
    cJSON *resolved = manifest_resolve_inherits(vjson, load_parent_ud, (void *)instance);
    cJSON_Delete(vjson);
    if (!resolved) return -1;

    char *jexe = pymcl_strdup(java_exe);
    if (!java_usable_for(resolved, jexe)) {
        free(jexe);
        jexe = java_pick(resolved, NULL);
    }
    if (!jexe || !java_usable_for(resolved, jexe)) {
        cJSON_Delete(resolved);
        pymcl_set_error("Java 无法启动此版本。请到 Java 页下载 Java 17。");
        free(jexe);
        return -1;
    }

    char vdir[PYMCL_PATH], jar[PYMCL_PATH];
    instance_versions_dir(instance, vdir, sizeof(vdir));
    char jn[256]; snprintf(jn, sizeof(jn), "%s.jar", version);
    pymcl_path_join3(jar, sizeof(jar), vdir, version, jn);
    if (!pymcl_file_exists(jar)) {
        const char *alts[] = {
            cJSON_GetStringValue(cJSON_GetObjectItem(resolved, "jar")),
            cJSON_GetStringValue(cJSON_GetObjectItem(resolved, "inheritsFrom")),
            NULL
        };
        int found = 0;
        for (int i = 0; alts[i]; i++) {
            char alt[PYMCL_PATH], an[256];
            snprintf(an, sizeof(an), "%s.jar", alts[i]);
            pymcl_path_join3(alt, sizeof(alt), vdir, alts[i], an);
            if (pymcl_file_exists(alt)) { snprintf(jar, sizeof(jar), "%s", alt); found = 1; break; }
        }
        if (!found) {
            pymcl_set_error("客户端 jar 缺失: %s", jar);
            cJSON_Delete(resolved); free(jexe); return -1;
        }
    }

    char natives[PYMCL_PATH];
    if (extract_natives(instance, resolved, version, natives, sizeof(natives)) != 0) {
        /* continue; extract_natives still fills path */
    }
    if (natives_out) snprintf(natives_out, nn, "%s", natives);

    char libs[PYMCL_PATH], assets[PYMCL_PATH], ip[PYMCL_PATH];
    instance_libraries_dir(instance, libs, sizeof(libs));
    instance_assets_dir(instance, assets, sizeof(assets));
    /* 版本隔离：游戏目录可能是 versions/<id> 而非实例根。
     * 以前这里恒用实例根，「隔离全部」在 C 桥下形同虚设。 */
    pymcl_apply_isolation(instance, version, ip, sizeof(ip));

    char **cp = NULL; int ncp = 0;
    cJSON *seen = cJSON_CreateObject();
    cJSON *lib;
    cJSON_ArrayForEach(lib, cJSON_GetObjectItem(resolved, "libraries")) {
        if (cJSON_IsFalse(cJSON_GetObjectItem(lib, "clientreq"))) continue;
        if (!pymcl_check_rules(cJSON_GetObjectItem(lib, "rules"), 0)) continue;
        const char *name = cJSON_GetStringValue(cJSON_GetObjectItem(lib, "name"));
        if (!name) continue;
        cJSON *art = cJSON_GetObjectItem(cJSON_GetObjectItem(lib, "downloads"), "artifact");
        char rel[512] = {0};
        if (art && cJSON_GetStringValue(cJSON_GetObjectItem(art, "path")))
            snprintf(rel, sizeof(rel), "%s", cJSON_GetStringValue(cJSON_GetObjectItem(art, "path")));
        else if (!cJSON_GetObjectItem(lib, "natives"))
            pymcl_maven_path(name, "jar", rel, sizeof(rel));
        else continue;
        pymcl_replace_char(rel, '/', '\\');
        char path[PYMCL_PATH];
        pymcl_path_join(path, sizeof(path), libs, rel);
        char key[256];
        library_identity(lib, key, sizeof(key));
        if (cJSON_GetObjectItem(seen, key)) {
            /* replace path kept at first position — skip extra append */
            continue;
        }
        cJSON_AddTrueToObject(seen, key);
        cp = (char **)realloc(cp, sizeof(char *) * (size_t)(ncp + 1));
        cp[ncp++] = pymcl_strdup(path);
    }
    cJSON_Delete(seen);
    cp = (char **)realloc(cp, sizeof(char *) * (size_t)(ncp + 1));
    cp[ncp++] = pymcl_strdup(jar);
    char classpath[32768] = {0};
    for (int i = 0; i < ncp; i++) {
        if (i) strncat(classpath, ";", sizeof(classpath) - strlen(classpath) - 1);
        strncat(classpath, cp[i], sizeof(classpath) - strlen(classpath) - 1);
    }

    cJSON *ph = cJSON_CreateObject();
    const char *pname = cJSON_GetStringValue(cJSON_GetObjectItem(account_props, "name")) ?: "Player";
    const char *puuid = cJSON_GetStringValue(cJSON_GetObjectItem(account_props, "uuid")) ?: "00000000-0000-0000-0000-000000000000";
    const char *ptok = cJSON_GetStringValue(cJSON_GetObjectItem(account_props, "token")) ?: "0";
    char sess[256]; snprintf(sess, sizeof(sess), "token:%s:%s", ptok, puuid);
    cJSON_AddStringToObject(ph, "auth_player_name", pname);
    cJSON_AddStringToObject(ph, "auth_uuid", puuid);
    cJSON_AddStringToObject(ph, "auth_access_token", ptok);
    cJSON_AddStringToObject(ph, "auth_session", sess);
    cJSON_AddStringToObject(ph, "user_type", cJSON_GetStringValue(cJSON_GetObjectItem(account_props, "user_type")) ?: "legacy");
    cJSON_AddStringToObject(ph, "user_properties", "{}");
    cJSON_AddStringToObject(ph, "auth_xuid", cJSON_GetStringValue(cJSON_GetObjectItem(account_props, "xuid")) ?: "");
    cJSON_AddStringToObject(ph, "clientid", config_str("microsoft_client_id", PYMCL_MS_CLIENT_DEFAULT));
    cJSON_AddStringToObject(ph, "version_name", version);
    cJSON_AddStringToObject(ph, "version_type", cJSON_GetStringValue(cJSON_GetObjectItem(resolved, "type")) ?: "release");
    cJSON_AddStringToObject(ph, "game_directory", ip);
    cJSON_AddStringToObject(ph, "assets_root", assets);
    cJSON *idx = cJSON_GetObjectItem(resolved, "assetIndex");
    cJSON_AddStringToObject(ph, "assets_index_name", cJSON_GetStringValue(cJSON_GetObjectItem(idx, "id")) ?: "legacy");
    char ga[PYMCL_PATH];
    pymcl_path_join3(ga, sizeof(ga), assets, "virtual", cJSON_GetStringValue(cJSON_GetObjectItem(idx, "id")) ?: "legacy");
    cJSON_AddStringToObject(ph, "game_assets", ga);
    cJSON_AddStringToObject(ph, "natives_directory", natives);
    cJSON_AddStringToObject(ph, "classpath", classpath);
    cJSON_AddStringToObject(ph, "library_directory", libs);
    cJSON_AddStringToObject(ph, "classpath_separator", ";");
    cJSON_AddStringToObject(ph, "launcher_name", PYMCL_LAUNCHER_NAME);
    cJSON_AddStringToObject(ph, "launcher_version", PYMCL_LAUNCHER_VERSION);
    char ws[16], hs[16];
    snprintf(ws, sizeof(ws), "%d", width > 0 ? width : 854);
    snprintf(hs, sizeof(hs), "%d", height > 0 ? height : 480);
    cJSON_AddStringToObject(ph, "resolution_width", ws);
    cJSON_AddStringToObject(ph, "resolution_height", hs);

    char **jvm = NULL, **game = NULL; int nj = 0, ng = 0;
    cJSON *args = cJSON_GetObjectItem(resolved, "arguments");
    const char *mine_args = cJSON_GetStringValue(cJSON_GetObjectItem(resolved, "minecraftArguments"));
    int custom = width || height;
    if (cJSON_IsObject(args)) {
        expand_args(cJSON_GetObjectItem(args, "jvm"), ph, custom, &jvm, &nj);
        expand_args(cJSON_GetObjectItem(args, "game"), ph, custom, &game, &ng);
        int has_lp = 0, has_cp = 0;
        for (int i = 0; i < nj; i++) {
            if (pymcl_startswith(jvm[i], "-Djava.library.path")) has_lp = 1;
            if (strcmp(jvm[i], "-cp") == 0 || strcmp(jvm[i], "--class-path") == 0) has_cp = 1;
        }
        if (!has_lp) {
            char lp[PYMCL_PATH + 32];
            snprintf(lp, sizeof(lp), "-Djava.library.path=%s", natives);
            jvm = (char **)realloc(jvm, sizeof(char *) * (size_t)(nj + 1));
            memmove(jvm + 1, jvm, sizeof(char *) * (size_t)nj);
            jvm[0] = pymcl_strdup(lp); nj++;
        }
        if (!has_cp) {
            jvm = (char **)realloc(jvm, sizeof(char *) * (size_t)(nj + 2));
            jvm[nj++] = pymcl_strdup("-cp");
            jvm[nj++] = pymcl_strdup(classpath);
        }
    } else {
        if (mine_args) {
            char buf[4096];
            pymcl_replace_placeholders(mine_args, ph, buf, sizeof(buf));
            char *tok = strtok(buf, " ");
            while (tok) {
                if (!pymcl_has_placeholder(tok)) {
                    game = (char **)realloc(game, sizeof(char *) * (size_t)(ng + 1));
                    game[ng++] = pymcl_strdup(tok);
                }
                tok = strtok(NULL, " ");
            }
        }
        char lp[PYMCL_PATH + 32];
        snprintf(lp, sizeof(lp), "-Djava.library.path=%s", natives);
        jvm = (char **)realloc(jvm, sizeof(char *) * 4);
        jvm[nj++] = pymcl_strdup(lp);
        jvm[nj++] = pymcl_strdup("-cp");
        jvm[nj++] = pymcl_strdup(classpath);
    }
    drop_orphan(&jvm, &nj);
    const char *ign[8]; int ni = 0;
    ign[ni++] = pymcl_basename(jar);
    const char *parent = cJSON_GetStringValue(cJSON_GetObjectItem(resolved, "inheritsFrom"));
    char pjar[128];
    if (parent) { snprintf(pjar, sizeof(pjar), "%s.jar", parent); ign[ni++] = pjar; }
    for (int i = 0; i < ncp; i++) if (pymcl_endswith(cp[i], "-extra.jar")) ign[ni++] = pymcl_basename(cp[i]);
    patch_ignore(jvm, nj, ign, ni);
    if (mine_args && strstr(mine_args, "tweakClass")) {
        jvm = (char **)realloc(jvm, sizeof(char *) * (size_t)(nj + 2));
        jvm[nj++] = pymcl_strdup("-Dfml.ignoreInvalidMinecraftCertificates=true");
        jvm[nj++] = pymcl_strdup("-Dfml.ignorePatchDiscrepancies=true");
    }
    /* 全局 default_jvm_args + GC 预设 + 版本 jvm_args 插在清单参数前
     * （对齐 mclauncher/launcher.py: default_jvm + extra_jvm + jvm_args）；
     * 用户写的 -Xmx/-Xms 随后被 apply_memory 统一收编，与 Python 相同。 */
    {
        char merged[4096];
        gc_merge_jvm(prep.gc, prep.jvm_args, merged, sizeof(merged));
        char **pre = NULL; int np = 0;
        split_args_append(config_str("default_jvm_args", ""), &pre, &np);
        split_args_append(merged, &pre, &np);
        if (np) {
            jvm = (char **)realloc(jvm, sizeof(char *) * (size_t)(nj + np));
            memmove(jvm + np, jvm, sizeof(char *) * (size_t)nj);
            for (int i = 0; i < np; i++) jvm[i] = pre[i];
            nj += np;
            free(pre);
        }
    }
    if (manifest_is_legacy(resolved) && memory_mb > 1024) memory_mb = 1024;
    for (int i = 0; i < nj; i++) if (strcmp(jvm[i], "-p") == 0) { free(jvm[i]); jvm[i] = pymcl_strdup("--module-path"); }
    apply_memory(&jvm, &nj, memory_mb);
    int has_brand = 0, has_ver = 0;
    for (int i = 0; i < nj; i++) {
        if (strstr(jvm[i], "-Dminecraft.launcher.brand")) has_brand = 1;
        if (strstr(jvm[i], "-Dminecraft.launcher.version")) has_ver = 1;
    }
    if (!has_brand) {
        char b[128]; snprintf(b, sizeof(b), "-Dminecraft.launcher.brand=%s", PYMCL_LAUNCHER_NAME);
        jvm = (char **)realloc(jvm, sizeof(char *) * (size_t)(nj + 1));
        jvm[nj++] = pymcl_strdup(b);
    }
    if (!has_ver) {
        char b[128]; snprintf(b, sizeof(b), "-Dminecraft.launcher.version=%s", PYMCL_LAUNCHER_VERSION);
        jvm = (char **)realloc(jvm, sizeof(char *) * (size_t)(nj + 1));
        jvm[nj++] = pymcl_strdup(b);
    }
    cJSON *logc = cJSON_GetObjectItem(cJSON_GetObjectItem(resolved, "logging"), "client");
    const char *lid = cJSON_GetStringValue(cJSON_GetObjectItem(cJSON_GetObjectItem(logc, "file"), "id"));
    if (lid) {
        char lp[PYMCL_PATH];
        pymcl_path_join3(lp, sizeof(lp), assets, "log_configs", lid);
        if (pymcl_file_exists(lp)) {
            char a[PYMCL_PATH + 40];
            snprintf(a, sizeof(a), "-Dlog4j.configurationFile=%s", lp);
            jvm = (char **)realloc(jvm, sizeof(char *) * (size_t)(nj + 1));
            jvm[nj++] = pymcl_strdup(a);
        }
    }
    /* 附加游戏参数：RPC extra_game_args（WinUI 启动页直连服务器以前被
     * 整个丢弃）+ 版本设置 game_args + 直连 --server/--port + 全屏，
     * 追加在清单游戏参数之后（对齐 launch_flow.prepare 的 extras）。 */
    {
        char **ex = NULL; int ne = 0;
        if (cJSON_IsArray(extra_game_args)) {
            cJSON *e;
            cJSON_ArrayForEach(e, extra_game_args) {
                char buf[512] = {0};
                if (cJSON_IsString(e) && e->valuestring[0])
                    snprintf(buf, sizeof(buf), "%s", e->valuestring);
                else if (cJSON_IsNumber(e))
                    snprintf(buf, sizeof(buf), "%d", e->valueint);
                if (!buf[0]) continue;
                ex = (char **)realloc(ex, sizeof(char *) * (size_t)(ne + 1));
                ex[ne++] = pymcl_strdup(buf);
            }
        }
        split_args_append(prep.game_args, &ex, &ne);
        int has_server = 0, has_fs = 0;
        for (int i = 0; i < ne; i++) {
            if (strcmp(ex[i], "--server") == 0) has_server = 1;
            if (strcmp(ex[i], "--fullscreen") == 0) has_fs = 1;
        }
        if (prep.server[0] && !has_server) {
            ex = (char **)realloc(ex, sizeof(char *) * (size_t)(ne + 4));
            ex[ne++] = pymcl_strdup("--server");
            ex[ne++] = pymcl_strdup(prep.server);
            ex[ne++] = pymcl_strdup("--port");
            ex[ne++] = pymcl_strdup(prep.port[0] ? prep.port : "25565");
        }
        if (prep.fullscreen && !has_fs) {
            ex = (char **)realloc(ex, sizeof(char *) * (size_t)(ne + 1));
            ex[ne++] = pymcl_strdup("--fullscreen");
        }
        if (ne) {
            game = (char **)realloc(game, sizeof(char *) * (size_t)(ng + ne));
            for (int i = 0; i < ne; i++) game[ng++] = ex[i];
            free(ex);
        }
    }
    const char *mainc = cJSON_GetStringValue(cJSON_GetObjectItem(resolved, "mainClass")) ?: "net.minecraft.client.main.Main";
    int total = 1 + nj + 1 + ng;
    char **cmd = (char **)calloc((size_t)total, sizeof(char *));
    int k = 0;
    cmd[k++] = pymcl_strdup(jexe);
    for (int i = 0; i < nj; i++) cmd[k++] = jvm[i];
    cmd[k++] = pymcl_strdup(mainc);
    for (int i = 0; i < ng; i++) cmd[k++] = game[i];
    free(jvm); free(game);
    for (int i = 0; i < ncp; i++) free(cp[i]);
    free(cp);
    free(jexe);
    cJSON_Delete(ph);
    cJSON_Delete(resolved);
    *argv = cmd;
    *argc = k;
    return 0;
}

HANDLE game_spawn(const char **argv, int argc, const char *cwd, HANDLE *pipe) {
    return pymcl_spawn_process(argv, argc, cwd, pipe);
}
void game_kill(HANDLE proc) {
    if (proc) TerminateProcess(proc, 1);
}
