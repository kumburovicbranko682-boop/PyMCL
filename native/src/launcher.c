#include "pymcl.h"

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
                         char ***argv, int *argc, char *natives_out, size_t nn) {
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
