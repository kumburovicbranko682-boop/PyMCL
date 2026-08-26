#include "pymcl.h"
#include <ctype.h>

static int parse_major(const char *out) {
    const char *p = strstr(out, "version \"");
    if (!p) return -1;
    p += 9;
    char ver[64]; int i = 0;
    while (*p && *p != '"' && i < 63) ver[i++] = *p++;
    ver[i] = 0;
    int a = 0, b = 0;
    if (ver[0] == '1' && ver[1] == '.') { sscanf(ver, "1.%d", &b); return b; }
    sscanf(ver, "%d", &a);
    return a > 0 ? a : -1;
}

char *java_version_output(const char *exe) {
    if (!exe || !pymcl_file_exists(exe)) return pymcl_strdup("");
    const char *argv[] = { exe, "-version" };
    HANDLE rd = NULL;
    HANDLE proc = pymcl_spawn_process(argv, 2, NULL, &rd);
    if (!proc) return pymcl_strdup("");
    char buf[4096]; size_t n = 0;
    DWORD got;
    while (ReadFile(rd, buf + n, (DWORD)(sizeof(buf) - 1 - n), &got, NULL) && got) n += got;
    buf[n] = 0;
    WaitForSingleObject(proc, 8000);
    CloseHandle(rd); CloseHandle(proc);
    return pymcl_strdup(buf);
}

int java_get_major(const char *exe) {
    char *o = java_version_output(exe);
    int m = parse_major(o);
    free(o);
    return m;
}

int java_required_major(cJSON *vjson) {
    cJSON *jv = cJSON_GetObjectItem(vjson, "javaVersion");
    cJSON *maj = jv ? cJSON_GetObjectItem(jv, "majorVersion") : NULL;
    if (cJSON_IsNumber(maj)) return (int)maj->valuedouble;
    const char *main = cJSON_GetStringValue(cJSON_GetObjectItem(vjson, "mainClass")) ?: "";
    if (pymcl_icontains(main, "bootstraplauncher")) return 17;
    cJSON *jvm = cJSON_GetObjectItem(cJSON_GetObjectItem(vjson, "arguments"), "jvm");
    cJSON *e;
    cJSON_ArrayForEach(e, jvm) {
        const char *s = cJSON_IsString(e) ? e->valuestring : NULL;
        if (!s && cJSON_IsObject(e)) {
            cJSON *val = cJSON_GetObjectItem(e, "value");
            if (cJSON_IsString(val)) s = val->valuestring;
            else if (cJSON_IsArray(val)) {
                cJSON *x; cJSON_ArrayForEach(x, val) {
                    if (cJSON_IsString(x) && (strcmp(x->valuestring, "-p") == 0 ||
                        strcmp(x->valuestring, "--module-path") == 0 ||
                        strcmp(x->valuestring, "--add-modules") == 0))
                        return 17;
                }
            }
        }
        if (s && (strcmp(s, "-p") == 0 || strcmp(s, "--module-path") == 0 || strcmp(s, "--add-modules") == 0))
            return 17;
    }
    return 8;
}

int java_usable_for(cJSON *vjson, const char *exe) {
    if (!exe || !pymcl_file_exists(exe)) return 0;
    int need = java_required_major(vjson);
    int got = java_get_major(exe);
    return got >= need;
}

char *java_find_exe(const char *root) {
    static const char *names[] = { "java.exe", "javaw.exe", "java", NULL };
    static const char *subs[] = { "bin", "jre\\bin", "jre/bin", "", NULL };
    for (int i = 0; subs[i]; i++) {
        char base[PYMCL_PATH];
        if (subs[i][0]) pymcl_path_join(base, sizeof(base), root, subs[i]);
        else snprintf(base, sizeof(base), "%s", root);
        for (int j = 0; names[j]; j++) {
            char p[PYMCL_PATH];
            pymcl_path_join(p, sizeof(p), base, names[j]);
            if (pymcl_file_exists(p)) return pymcl_strdup(p);
        }
    }
    /* shallow walk */
    wchar_t *w = pymcl_u8_to_wide(root);
    if (!w) return NULL;
    wchar_t pat[PYMCL_PATH];
    _snwprintf(pat, PYMCL_PATH, L"%s\\*\\bin\\java.exe", w);
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pat, &fd);
    free(w);
    if (h == INVALID_HANDLE_VALUE) return NULL;
    /* reconstruct path is hard from glob; fallback */
    FindClose(h);
    return NULL;
}

static void add_java_row(cJSON *arr, const char *name, const char *exe, int major, const char *dir) {
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "name", name ? name : exe);
    cJSON_AddStringToObject(o, "exe", exe ? exe : "");
    cJSON_AddStringToObject(o, "path", exe ? exe : "");
    if (major > 0) cJSON_AddNumberToObject(o, "major", major);
    else cJSON_AddStringToObject(o, "major", "?");
    if (dir) cJSON_AddStringToObject(o, "dir", dir);
    cJSON_AddItemToArray(arr, o);
}

cJSON *java_list_installed(void) {
    cJSON *arr = cJSON_CreateArray();
    char jd[PYMCL_PATH];
    pymcl_java_dir(jd, sizeof(jd));
    if (!pymcl_dir_exists(jd)) return arr;
    wchar_t *w = pymcl_u8_to_wide(jd);
    wchar_t pat[PYMCL_PATH];
    _snwprintf(pat, PYMCL_PATH, L"%s\\*", w);
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pat, &fd);
    free(w);
    if (h == INVALID_HANDLE_VALUE) return arr;
    do {
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || fd.cFileName[0] == L'.') continue;
        char *name = pymcl_wide_to_u8(fd.cFileName);
        char child[PYMCL_PATH], meta[PYMCL_PATH];
        pymcl_path_join(child, sizeof(child), jd, name);
        pymcl_path_join(meta, sizeof(meta), child, "runtime.meta.json");
        char *exe = java_find_exe(child);
        if (exe) {
            cJSON *m = pymcl_read_json(meta);
            const char *disp = cJSON_GetStringValue(cJSON_GetObjectItem(m, "name"));
            int maj = java_get_major(exe);
            add_java_row(arr, disp ? disp : name, exe, maj, child);
            cJSON_Delete(m);
            free(exe);
        }
        free(name);
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return arr;
}

static void walk_java(const char *root, cJSON *arr, int depth) {
    if (depth > 4 || !pymcl_dir_exists(root)) return;
    char bin[PYMCL_PATH];
    pymcl_path_join3(bin, sizeof(bin), root, "bin", "java.exe");
    if (pymcl_file_exists(bin)) {
        int maj = java_get_major(bin);
        char name[256];
        snprintf(name, sizeof(name), "Java %d (%s)", maj > 0 ? maj : 0, bin);
        add_java_row(arr, name, bin, maj, root);
        return;
    }
    wchar_t *w = pymcl_u8_to_wide(root);
    wchar_t pat[PYMCL_PATH];
    _snwprintf(pat, PYMCL_PATH, L"%s\\*", w);
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pat, &fd);
    free(w);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || fd.cFileName[0] == L'.') continue;
        char *n = pymcl_wide_to_u8(fd.cFileName);
        if (pymcl_ieq(n, "Windows") || pymcl_ieq(n, "WinSxS") || pymcl_ieq(n, "node_modules")) { free(n); continue; }
        int hint = pymcl_icontains(n, "java") || pymcl_icontains(n, "jdk") || pymcl_icontains(n, "jre")
            || pymcl_icontains(n, "zulu") || pymcl_icontains(n, "temurin") || pymcl_icontains(n, "adoptium");
        char child[PYMCL_PATH];
        pymcl_path_join(child, sizeof(child), root, n);
        free(n);
        if (hint || depth == 0) walk_java(child, arr, depth + 1);
    } while (FindNextFileW(h, &fd));
    FindClose(h);
}

cJSON *java_list_system(void) {
    cJSON *arr = cJSON_CreateArray();
    const char *jh = getenv("JAVA_HOME");
    if (jh) walk_java(jh, arr, 0);
    walk_java("C:\\Program Files\\Java", arr, 0);
    walk_java("C:\\Program Files\\Eclipse Adoptium", arr, 0);
    walk_java("C:\\Program Files\\Microsoft", arr, 0);
    walk_java("C:\\Program Files\\Zulu", arr, 0);
    walk_java("C:\\Program Files (x86)\\Java", arr, 0);
    walk_java("C:\\Program Files\\Eclipse Foundation", arr, 0);
    char own[PYMCL_PATH];
    pymcl_java_dir(own, sizeof(own));
    walk_java(own, arr, 0);
    return arr;
}

cJSON *java_all(void) {
    cJSON *a = java_list_installed();
    cJSON *b = java_list_system();
    cJSON *it;
    cJSON_ArrayForEach(it, b) {
        const char *exe = cJSON_GetStringValue(cJSON_GetObjectItem(it, "exe"));
        int dup = 0;
        cJSON *x;
        cJSON_ArrayForEach(x, a) {
            const char *e2 = cJSON_GetStringValue(cJSON_GetObjectItem(x, "exe"));
            if (exe && e2 && pymcl_ieq(exe, e2)) dup = 1;
        }
        if (!dup) cJSON_AddItemToArray(a, cJSON_Duplicate(it, 1));
    }
    cJSON_Delete(b);
    return a;
}

static int adoptium_major(int m) {
    if (m <= 8) return 8;
    if (m <= 11) return 11;
    if (m <= 17) return 17;
    if (m <= 21) return 21;
    return 25; /* MC 26.1+ 需要 Java 25 */
}

char *java_pick(cJSON *vjson, const char *prefer) {
    int need = java_required_major(vjson);
    if (prefer && prefer[0] && !pymcl_ieq(prefer, PYMCL_JAVA_AUTO)) {
        const char *exe = prefer;
        if (pymcl_file_exists(exe) && java_get_major(exe) >= need) return pymcl_strdup(exe);
        cJSON *all = java_all();
        cJSON *j;
        cJSON_ArrayForEach(j, all) {
            const char *e = cJSON_GetStringValue(cJSON_GetObjectItem(j, "exe"));
            const char *nm = cJSON_GetStringValue(cJSON_GetObjectItem(j, "name"));
            if ((e && strcmp(e, prefer) == 0) || (nm && strcmp(nm, prefer) == 0)) {
                if (e && java_get_major(e) >= need) {
                    char *r = pymcl_strdup(e);
                    cJSON_Delete(all);
                    return r;
                }
            }
        }
        cJSON_Delete(all);
    }
    cJSON *all = java_all();
    char *exact = NULL, *newer = NULL;
    int newer_maj = 999;
    cJSON *j;
    cJSON_ArrayForEach(j, all) {
        int maj = 0;
        cJSON *m = cJSON_GetObjectItem(j, "major");
        if (cJSON_IsNumber(m)) maj = (int)m->valuedouble;
        const char *e = cJSON_GetStringValue(cJSON_GetObjectItem(j, "exe"));
        if (!e) continue;
        if (maj == need && !exact) exact = pymcl_strdup(e);
        if (maj > need && need >= 17 && maj < newer_maj) { free(newer); newer = pymcl_strdup(e); newer_maj = maj; }
    }
    cJSON_Delete(all);
    if (exact) { free(newer); return exact; }
    return newer;
}

char *java_install_adoptium(int major, const char *arch, pymcl_ctx *ctx) {
    major = adoptium_major(major);
    if (!arch) arch = pymcl_arch();
    const char *api_arch = pymcl_ieq(arch, "arm64") ? "aarch64" : arch;
    char jd[PYMCL_PATH], td[PYMCL_PATH];
    pymcl_java_dir(jd, sizeof(jd));
    char dn[64]; snprintf(dn, sizeof(dn), "adoptium-%d-%s", major, arch);
    pymcl_path_join(td, sizeof(td), jd, dn);
    char *exist = java_find_exe(td);
    if (exist) return exist;
    char url[512];
    snprintf(url, sizeof(url),
        "https://api.adoptium.net/v3/binary/latest/%d/ga/windows/%s/jre/hotspot/normal/eclipse",
        major, api_arch);
    pymcl_ensure_dir(jd);
    char archv[PYMCL_PATH];
    snprintf(archv, sizeof(archv), "%s\\adoptium-%d-%s.zip", jd, major, arch);
    if (ctx && ctx->on_progress) {
        char m[128]; snprintf(m, sizeof(m), "下载 Adoptium Java %d (%s) 运行时", major, arch);
        ctx->on_progress(ctx->ud, m, 0, 1);
    }
    if (download_file(url, NULL, 0, archv, ctx, NULL, -1, NULL) != 0) return NULL;
    pymcl_remove_tree(td);
    pymcl_ensure_dir(td);
    if (pymcl_extract_zip(archv, td) != 0) { pymcl_remove_tree(archv); return NULL; }
    pymcl_remove_tree(archv);
    char *exe = java_find_exe(td);
    if (!exe) { pymcl_set_error("Adoptium Java %d 解压后未找到 java", major); return NULL; }
    cJSON *meta = cJSON_CreateObject();
    char nm[64]; snprintf(nm, sizeof(nm), "Adoptium Java %d (%s)", major, arch);
    cJSON_AddStringToObject(meta, "kind", "adoptium");
    cJSON_AddStringToObject(meta, "name", nm);
    cJSON_AddNumberToObject(meta, "major", java_get_major(exe));
    char mf[PYMCL_PATH];
    pymcl_path_join(mf, sizeof(mf), td, "runtime.meta.json");
    pymcl_write_json(mf, meta);
    cJSON_Delete(meta);
    return exe;
}

char *java_install_mojang(const char *component, pymcl_ctx *ctx) {
    const char *urls[] = {
        "https://piston-meta.mojang.com/v1/products/java-runtime/2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json",
        "https://launchermeta.mojang.com/v1/products/java-runtime/2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json",
    };
    cJSON *man = fetch_json_mirrors(urls, 2, 60);
    if (!man) return NULL;
    char pk[64];
    snprintf(pk, sizeof(pk), "windows-%s", pymcl_arch());
    cJSON *plat = cJSON_GetObjectItem(man, pk);
    cJSON *ents = plat ? cJSON_GetObjectItem(plat, component) : NULL;
    if (!cJSON_IsArray(ents) || cJSON_GetArraySize(ents) == 0) { cJSON_Delete(man); return NULL; }
    cJSON *entry = cJSON_GetArrayItem(ents, cJSON_GetArraySize(ents) - 1);
    cJSON *info = cJSON_GetObjectItem(entry, "manifest");
    const char *url = cJSON_GetStringValue(cJSON_GetObjectItem(info, "url"));
    const char *sha1 = cJSON_GetStringValue(cJSON_GetObjectItem(info, "sha1"));
    if (!url) { cJSON_Delete(man); return NULL; }
    char jd[PYMCL_PATH], td[PYMCL_PATH];
    pymcl_java_dir(jd, sizeof(jd));
    char dn[128]; snprintf(dn, sizeof(dn), "%s-%s", component, pk);
    pymcl_path_join(td, sizeof(td), jd, dn);
    char *exist = java_find_exe(td);
    if (exist) { cJSON_Delete(man); return exist; }
    char zip[PYMCL_PATH];
    snprintf(zip, sizeof(zip), "%s\\%s.zip", jd, dn);
    if (download_file(url, NULL, 0, zip, ctx, sha1, -1, NULL) != 0) { cJSON_Delete(man); return NULL; }
    pymcl_remove_tree(td); pymcl_ensure_dir(td);
    pymcl_extract_zip(zip, td);
    pymcl_remove_tree(zip);
    char *exe = java_find_exe(td);
    cJSON_Delete(man);
    return exe;
}

char *java_resolve_launch(cJSON *vjson, const char *prefer, pymcl_ctx *ctx) {
    char *exe = java_pick(vjson, prefer);
    if (exe && java_usable_for(vjson, exe)) return exe;
    free(exe);
    exe = java_pick(vjson, NULL);
    if (exe && java_usable_for(vjson, exe)) return exe;
    free(exe);
    int need = java_required_major(vjson);
    cJSON *jv = cJSON_GetObjectItem(vjson, "javaVersion");
    const char *comp = cJSON_GetStringValue(cJSON_GetObjectItem(jv, "component"));
    if (comp && strcmp(comp, "jre-legacy") != 0) {
        exe = java_install_mojang(comp, ctx);
        if (exe) return exe;
    }
    return java_install_adoptium(need, NULL, ctx);
}

char *java_for_installer(const char *loader, pymcl_ctx *ctx) {
    int need8 = loader && strcmp(loader, "forge-legacy") == 0;
    cJSON *all = java_all();
    cJSON *j;
    cJSON_ArrayForEach(j, all) {
        int maj = 0;
        cJSON *m = cJSON_GetObjectItem(j, "major");
        if (cJSON_IsNumber(m)) maj = (int)m->valuedouble;
        const char *e = cJSON_GetStringValue(cJSON_GetObjectItem(j, "exe"));
        if (!e) continue;
        if (need8 && maj == 8) { char *r = pymcl_strdup(e); cJSON_Delete(all); return r; }
        if (!need8 && maj >= 17) { char *r = pymcl_strdup(e); cJSON_Delete(all); return r; }
    }
    cJSON_Delete(all);
    return java_install_adoptium(need8 ? 8 : 17, NULL, ctx);
}
