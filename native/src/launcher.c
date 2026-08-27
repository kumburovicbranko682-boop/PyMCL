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

static int is_mp_flag(const char *a) {
    return strcmp(a, "-p") == 0 || strcmp(a, "--module-path") == 0;
}

/* maven 布局 <...>/<artifact>/<version>/<artifact>-<version>.jar 归并成
 * <...>/<artifact>：JPMS 不允许同名模块出现两次，同 artifact 换版本文件名
 * 会变，必须按 artifact 归并；非 maven 布局退回完整路径（只去掉全同项）。 */
static void module_entry_key(const char *entry, char *out, size_t n) {
    const char *fname = entry;
    for (const char *p = entry; *p; p++) if (*p == '/' || *p == '\\') fname = p + 1;
    size_t lf = strlen(fname);
    if (fname > entry && lf > 4
        && (pymcl_ieq(fname + lf - 4, ".jar") || pymcl_ieq(fname + lf - 4, ".zip"))) {
        const char *vend = fname - 1;   /* version 目录后的分隔符 */
        const char *vstart = entry;
        for (const char *p = entry; p < vend; p++) if (*p == '/' || *p == '\\') vstart = p + 1;
        size_t lv = (size_t)(vend - vstart);
        if (vstart > entry && lv > 0 && lf > lv + 5
            && fname[lf - 4 - lv - 1] == '-'
            && strncmp(fname + lf - 4 - lv, vstart, lv) == 0) {
            size_t plen = (size_t)(vstart - entry);  /* 含结尾分隔符 */
            size_t alen = lf - 4 - lv - 1;           /* artifact 名长度 */
            if (plen + alen < n) {
                memcpy(out, entry, plen);
                memcpy(out + plen, fname, alen);
                out[plen + alen] = 0;
                return;
            }
        }
    }
    snprintf(out, n, "%s", entry);
}

/* 对齐 mclauncher/launcher.py::_merge_module_paths：合并多个 -p/--module-path
 * 并按 artifact 去重（后出现的路径生效、位置保持第一次出现处）。inherit 链
 * 上叠了两层 Forge/NeoForge 参数时会出现第二个 -p 或同一 artifact 的两个
 * 版本，JPMS 对同名模块直接 ResolutionException（"reads more than one
 * module named ..."），游戏退出码 1。 */
static void merge_module_paths(char ***args, int *n, char ***entries_out, int *nout) {
    char **in = *args; int nin = *n;
    *entries_out = NULL; *nout = 0;
    int has = 0;
    for (int i = 0; i < nin; i++) if (is_mp_flag(in[i])) { has = 1; break; }
    if (!has) return;
    char **keys = NULL; char **vals = NULL; int ne = 0;
    for (int i = 0; i + 1 < nin; i++) {
        if (!is_mp_flag(in[i])) continue;
        char *dup = pymcl_strdup(in[i + 1]);
        char *tok = strtok(dup, ";");
        while (tok) {
            if (tok[0]) {
                char key[PYMCL_PATH];
                module_entry_key(tok, key, sizeof(key));
                int found = -1;
                for (int k = 0; k < ne; k++) if (strcmp(keys[k], key) == 0) { found = k; break; }
                if (found >= 0) {
                    free(vals[found]);
                    vals[found] = pymcl_strdup(tok);
                } else {
                    keys = (char **)realloc(keys, sizeof(char *) * (size_t)(ne + 1));
                    vals = (char **)realloc(vals, sizeof(char *) * (size_t)(ne + 1));
                    keys[ne] = pymcl_strdup(key);
                    vals[ne] = pymcl_strdup(tok);
                    ne++;
                }
            }
            tok = strtok(NULL, ";");
        }
        free(dup);
        i++;
    }
    char joined[8192]; joined[0] = 0;
    for (int k = 0; k < ne; k++) {
        if (k) strncat(joined, ";", sizeof(joined) - strlen(joined) - 1);
        strncat(joined, vals[k], sizeof(joined) - strlen(joined) - 1);
    }
    char **out = (char **)calloc((size_t)nin + 2, sizeof(char *));
    int no = 0, placed = 0;
    for (int i = 0; i < nin; i++) {
        if (is_mp_flag(in[i]) && i + 1 < nin) {
            if (!placed && ne > 0) {
                out[no++] = pymcl_strdup("--module-path");
                out[no++] = pymcl_strdup(joined);
                placed = 1;
            }
            free(in[i]); free(in[i + 1]);
            i++;
            continue;
        }
        out[no++] = in[i];
    }
    free(in);
    for (int k = 0; k < ne; k++) free(keys[k]);
    free(keys);
    *args = out; *n = no;
    *entries_out = vals; *nout = ne;
}

static void patch_ignore(char **args, int n, const char **names, int nn) {
    for (int i = 0; i < n; i++) {
        if (!pymcl_startswith(args[i], "-DignoreList=")) continue;
        char buf[4096];
        snprintf(buf, sizeof(buf), "%s", args[i]);
        for (int k = 0; k < nn; k++) {
            if (!names[k] || !names[k][0]) continue;
            /* 对齐 mclauncher/launcher.py::_patch_ignore_list 的 startswith
             * 语义：已有前缀能命中该文件名就不追加。 */
            char list[4096];
            snprintf(list, sizeof(list), "%s", buf + strlen("-DignoreList="));
            int covered = 0;
            char *tok = strtok(list, ",");
            while (tok) {
                if (tok[0] && pymcl_startswith(names[k], tok)) { covered = 1; break; }
                tok = strtok(NULL, ",");
            }
            if (!covered) {
                size_t L = strlen(buf);
                snprintf(buf + L, sizeof(buf) - L, ",%s", names[k]);
            }
        }
        free(args[i]);
        args[i] = pymcl_strdup(buf);
    }
}

/* 对齐 mclauncher/gc.py：GC 预设拼在用户 JVM 参数前；已有 GC 旗标就不再加。 */
static const char *gc_preset_flags(const char *key) {
    if (!key || !key[0] || pymcl_ieq(key, "auto"))
        return "-XX:+UseG1GC -XX:+UnlockExperimentalVMOptions -XX:G1NewSizePercent=20 "
               "-XX:G1ReservePercent=20 -XX:MaxGCPauseMillis=50 -XX:G1HeapRegionSize=32M";
    if (pymcl_ieq(key, "g1")) return "-XX:+UseG1GC";
    if (pymcl_ieq(key, "g1_tuned"))
        return "-XX:+UseG1GC -XX:+UnlockExperimentalVMOptions -XX:G1NewSizePercent=20 "
               "-XX:G1ReservePercent=20 -XX:MaxGCPauseMillis=50 -XX:G1HeapRegionSize=32M "
               "-XX:+DisableExplicitGC -XX:+AlwaysPreTouch -XX:+ParallelRefProcEnabled";
    if (pymcl_ieq(key, "zgc")) return "-XX:+UseZGC -XX:+UnlockExperimentalVMOptions";
    if (pymcl_ieq(key, "none")) return "";
    /* 未知键按 Python 语义落到 auto */
    return gc_preset_flags("auto");
}
void gc_preset_apply(const char *preset, const char *existing, char *out, size_t n) {
    const char *ex = existing ? existing : "";
    char **toks = NULL;
    int nt = pymcl_split_args(ex, &toks);
    int has_gc = 0;
    for (int i = 0; i < nt; i++) {
        if (pymcl_startswith(toks[i], "-XX:+Use") && strstr(toks[i], "GC")) has_gc = 1;
    }
    pymcl_free_args(toks, nt);
    const char *flags = gc_preset_flags(preset);
    if (has_gc || !flags[0]) snprintf(out, n, "%s", ex);
    else if (!ex[0]) snprintf(out, n, "%s", flags);
    else snprintf(out, n, "%s %s", flags, ex);
}

int build_launch_command(const char *instance, const char *version, cJSON *account_props,
                         const char *java_exe, int memory_mb, int width, int height,
                         const char *extra_jvm, const char *game_dir,
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
    instance_path(instance, ip, sizeof(ip));

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
    /* 版本隔离时游戏目录是 versions/<ver>，不再固定实例根目录。 */
    cJSON_AddStringToObject(ph, "game_directory", game_dir && game_dir[0] ? game_dir : ip);
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
    char **mp_entries = NULL; int nmp = 0;
    merge_module_paths(&jvm, &nj, &mp_entries, &nmp);
    /* BootstrapLauncher 会把 classpath（legacyClassPath）里未被 -DignoreList
     * 前缀命中的 jar 装进 MC-BOOTSTRAP 模块层；-p 上的 jar 若没被覆盖，
     * classpath 副本会重复加载出第二个同名模块（ResolutionException，
     * 退出码 1）。把 -p 上每个 jar 的文件名都补进 ignoreList，缺失时补一条。 */
    const char **ign = (const char **)calloc((size_t)(2 + ncp + nmp), sizeof(char *));
    int ni = 0;
    ign[ni++] = pymcl_basename(jar);
    const char *parent = cJSON_GetStringValue(cJSON_GetObjectItem(resolved, "inheritsFrom"));
    char pjar[128];
    if (parent) { snprintf(pjar, sizeof(pjar), "%s.jar", parent); ign[ni++] = pjar; }
    for (int i = 0; i < ncp; i++) if (pymcl_endswith(cp[i], "-extra.jar")) ign[ni++] = pymcl_basename(cp[i]);
    for (int i = 0; i < nmp; i++) ign[ni++] = pymcl_basename(mp_entries[i]);
    patch_ignore(jvm, nj, ign, ni);
    if (nmp > 0) {
        int has_ign = 0;
        for (int i = 0; i < nj; i++) if (pymcl_startswith(jvm[i], "-DignoreList=")) { has_ign = 1; break; }
        if (!has_ign) {
            char buf[4096];
            snprintf(buf, sizeof(buf), "-DignoreList=");
            for (int k = 0; k < ni; k++) {
                if (!ign[k] || !ign[k][0] || strstr(buf, ign[k])) continue;
                size_t L = strlen(buf);
                snprintf(buf + L, sizeof(buf) - L, "%s%s", buf[L - 1] == '=' ? "" : ",", ign[k]);
            }
            jvm = (char **)realloc(jvm, sizeof(char *) * (size_t)(nj + 1));
            jvm[nj++] = pymcl_strdup(buf);
        }
    }
    for (int i = 0; i < nmp; i++) free(mp_entries[i]);
    free(mp_entries);
    free((void *)ign);
    if (mine_args && strstr(mine_args, "tweakClass")) {
        jvm = (char **)realloc(jvm, sizeof(char *) * (size_t)(nj + 2));
        jvm[nj++] = pymcl_strdup("-Dfml.ignoreInvalidMinecraftCertificates=true");
        jvm[nj++] = pymcl_strdup("-Dfml.ignorePatchDiscrepancies=true");
    }
    if (manifest_is_legacy(resolved) && memory_mb > 1024) memory_mb = 1024;
    for (int i = 0; i < nj; i++) if (strcmp(jvm[i], "-p") == 0) { free(jvm[i]); jvm[i] = pymcl_strdup("--module-path"); }
    /* 对齐 mclauncher/launcher.py：default_jvm + extra_jvm 排在清单 JVM 参数前，
     * 再统一去重 -Xmx/-Xms。以前全局「默认 JVM 参数」和版本设置的 GC/JVM
     * 参数在 C 桥启动时整个被无视。 */
    {
        char **pre = NULL; int np = 0;
        char **dj = NULL;
        int ndj = pymcl_split_args(config_str("default_jvm_args", ""), &dj);
        for (int i = 0; i < ndj; i++) {
            pre = (char **)realloc(pre, sizeof(char *) * (size_t)(np + 1));
            pre[np++] = dj[i];
        }
        free(dj);
        char **xj = NULL;
        int nxj = pymcl_split_args(extra_jvm ? extra_jvm : "", &xj);
        for (int i = 0; i < nxj; i++) {
            pre = (char **)realloc(pre, sizeof(char *) * (size_t)(np + 1));
            pre[np++] = xj[i];
        }
        free(xj);
        if (np) {
            char **merged = (char **)calloc((size_t)(np + nj), sizeof(char *));
            memcpy(merged, pre, sizeof(char *) * (size_t)np);
            memcpy(merged + np, jvm, sizeof(char *) * (size_t)nj);
            free(pre); free(jvm);
            jvm = merged; nj += np;
        }
    }
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
