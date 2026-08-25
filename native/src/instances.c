#include "pymcl.h"
#include <ctype.h>

static const char *k_dirs[] = {
    "mods", "config", "saves", "resourcepacks", "shaderpacks", "datapacks",
    "screenshots", "crash-reports", "logs", "options", "servers",
    "texturepacks", "versions", "libraries", NULL
};

static int reserved_name(const char *s) {
    static const char *r[] = {
        "CON","PRN","AUX","NUL","COM1","COM2","COM3","COM4","COM5","COM6","COM7","COM8","COM9",
        "LPT1","LPT2","LPT3","LPT4","LPT5","LPT6","LPT7","LPT8","LPT9", NULL
    };
    for (int i = 0; r[i]; i++) if (pymcl_ieq(s, r[i])) return 1;
    return 0;
}

void sanitize_instance_name(const char *raw, char *out, size_t n) {
    const char *s = raw ? raw : "";
    size_t o = 0;
    for (; *s && o + 1 < n; s++) {
        unsigned char c = (unsigned char)*s;
        if (c < 32 || strchr("\\/:*?\"<>|", c)) {
            if (o && out[o - 1] != '-') out[o++] = '-';
        } else out[o++] = (char)c;
    }
    while (o && (out[o - 1] == ' ' || out[o - 1] == '.')) o--;
    out[o] = 0;
    if (!out[0]) snprintf(out, n, "游戏");
    if (reserved_name(out)) {
        char tmp[128]; snprintf(tmp, sizeof(tmp), "%s-游戏", out);
        snprintf(out, n, "%s", tmp);
    }
    if (strlen(out) > 48) out[48] = 0;
}

/* 外部游戏目录注册表（config.json 的 external_instances，与 Python 侧共享）。 */
static cJSON *external_registry(void) {
    cJSON *v = cJSON_GetObjectItem(config_obj(), "external_instances");
    return cJSON_IsObject(v) ? v : NULL;
}

const char *instance_external_path(const char *name) {
    cJSON *reg = external_registry();
    if (!reg || !name || !name[0]) return NULL;
    cJSON *v = cJSON_GetObjectItem(reg, name);
    return (cJSON_IsString(v) && v->valuestring[0]) ? v->valuestring : NULL;
}

int instance_path(const char *name, char *out, size_t n) {
    if (!name || !name[0] || strcmp(name, ".") == 0 || strcmp(name, "..") == 0) {
        pymcl_set_error("非法实例名: %s", name ? name : "");
        return -1;
    }
    if (strpbrk(name, "\\/:*?\"<>|")) {
        pymcl_set_error("非法实例名: %s", name);
        return -1;
    }
    const char *ext = instance_external_path(name);
    if (ext) {
        snprintf(out, n, "%s", ext);
        return 0;
    }
    char root[PYMCL_PATH];
    pymcl_instances_dir(root, sizeof(root));
    pymcl_path_join(out, n, root, name);
    return 0;
}

void unique_instance_name(const char *raw, char *out, size_t n) {
    sanitize_instance_name(raw, out, n);
    char path[PYMCL_PATH];
    int i = 2;
    char base[128];
    snprintf(base, sizeof(base), "%s", out);
    while (instance_path(out, path, sizeof(path)) == 0 && pymcl_dir_exists(path)) {
        snprintf(out, n, "%s-%d", base, i++);
    }
}

static int list_contains(cJSON *arr, const char *name) {
    cJSON *it;
    cJSON_ArrayForEach(it, arr) {
        if (cJSON_IsString(it) && strcmp(it->valuestring, name) == 0) return 1;
    }
    return 0;
}

int instance_list(cJSON **out) {
    *out = cJSON_CreateArray();
    char root[PYMCL_PATH];
    pymcl_instances_dir(root, sizeof(root));
    if (pymcl_dir_exists(root)) {
        wchar_t *w = pymcl_u8_to_wide(root);
        wchar_t pat[PYMCL_PATH];
        _snwprintf(pat, PYMCL_PATH, L"%s\\*", w);
        WIN32_FIND_DATAW fd;
        HANDLE h = FindFirstFileW(pat, &fd);
        free(w);
        if (h != INVALID_HANDLE_VALUE) {
            do {
                if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) continue;
                if (wcscmp(fd.cFileName, L".") == 0 || wcscmp(fd.cFileName, L"..") == 0) continue;
                char *name = pymcl_wide_to_u8(fd.cFileName);
                char meta[PYMCL_PATH], ip[PYMCL_PATH];
                pymcl_path_join(ip, sizeof(ip), root, name);
                pymcl_path_join(meta, sizeof(meta), ip, ".instance.json");
                if (pymcl_file_exists(meta)) cJSON_AddItemToArray(*out, cJSON_CreateString(name));
                free(name);
            } while (FindNextFileW(h, &fd));
            FindClose(h);
        }
    }
    /* 外部游戏目录：注册过且目录仍存在的也算实例（与 Python 侧一致） */
    cJSON *reg = external_registry();
    if (reg) {
        cJSON *it;
        cJSON_ArrayForEach(it, reg) {
            if (!cJSON_IsString(it) || !it->valuestring[0]) continue;
            if (list_contains(*out, it->string)) continue;
            if (pymcl_dir_exists(it->valuestring))
                cJSON_AddItemToArray(*out, cJSON_CreateString(it->string));
        }
    }
    return 0;
}

void instance_ensure_dirs(const char *name) {
    char ip[PYMCL_PATH];
    if (instance_path(name, ip, sizeof(ip)) != 0) return;
    pymcl_ensure_dir(ip);
    for (int i = 0; k_dirs[i]; i++) {
        char d[PYMCL_PATH];
        pymcl_path_join(d, sizeof(d), ip, k_dirs[i]);
        pymcl_ensure_dir(d);
    }
}

int instance_create(const char *name, cJSON *meta) {
    char ip[PYMCL_PATH];
    if (instance_path(name, ip, sizeof(ip)) != 0) return -1;
    if (instance_external_path(name)) {
        /* 外部目录是用户自己的 .minecraft，绝不往里面铺标准目录结构 */
        pymcl_set_error("实例 %s 已存在。", name);
        return -1;
    }
    char existing_meta[PYMCL_PATH];
    pymcl_path_join(existing_meta, sizeof(existing_meta), ip, ".instance.json");
    if (pymcl_dir_exists(ip) && pymcl_file_exists(existing_meta)) {
        pymcl_set_error("实例 %s 已存在。", name);
        return -1;
    }
    instance_ensure_dirs(name);
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "name", name);
    cJSON_AddNullToObject(o, "mc_version");
    cJSON_AddNullToObject(o, "modpack");
    cJSON_AddStringToObject(o, "java", PYMCL_JAVA_AUTO);
    if (cJSON_IsObject(meta)) {
        cJSON *c = NULL;
        cJSON_ArrayForEach(c, meta) {
            cJSON_DeleteItemFromObject(o, c->string);
            cJSON_AddItemToObject(o, c->string, cJSON_Duplicate(c, 1));
        }
    }
    char mf[PYMCL_PATH];
    pymcl_path_join(mf, sizeof(mf), ip, ".instance.json");
    int r = pymcl_write_json(mf, o);
    cJSON_Delete(o);
    return r;
}

static void reset_default_if(const char *name) {
    if (strcmp(config_str("default_instance", "default"), name) != 0) return;
    cJSON *list = NULL;
    instance_list(&list);
    const char *next = "default";
    if (cJSON_GetArraySize(list) > 0)
        next = cJSON_GetArrayItem(list, 0)->valuestring;
    config_set_str("default_instance", next);
    config_save();
    cJSON_Delete(list);
}

int instance_delete(const char *name) {
    if (instance_external_path(name)) {
        /* 外部目录只解除注册，绝不删用户文件 */
        cJSON *reg = external_registry();
        if (reg) cJSON_DeleteItemFromObject(reg, name);
        config_save();
        reset_default_if(name);
        return 0;
    }
    char ip[PYMCL_PATH];
    if (instance_path(name, ip, sizeof(ip)) != 0) return -1;
    if (!pymcl_dir_exists(ip)) { pymcl_set_error("实例 %s 不存在。", name); return -1; }
    pymcl_remove_tree(ip);
    reset_default_if(name);
    return 0;
}

int instance_rename(const char *name, const char *new_name) {
    if (instance_external_path(name)) {
        /* 外部目录只改注册名，不移动文件夹、不写 .instance.json */
        if (!new_name || !new_name[0] || strpbrk(new_name, "\\/:*?\"<>|")) {
            pymcl_set_error("非法实例名: %s", new_name ? new_name : "");
            return -1;
        }
        char np[PYMCL_PATH];
        if (instance_external_path(new_name)
            || (instance_path(new_name, np, sizeof(np)) == 0 && pymcl_dir_exists(np))) {
            pymcl_set_error("实例 %s 已存在。", new_name);
            return -1;
        }
        cJSON *reg = external_registry();
        cJSON *v = reg ? cJSON_DetachItemFromObject(reg, name) : NULL;
        if (!v) { pymcl_set_error("外部实例不存在: %s", name); return -1; }
        cJSON_AddItemToObject(reg, new_name, v);
        config_save();
        if (strcmp(config_str("default_instance", ""), name) == 0) {
            config_set_str("default_instance", new_name);
            config_save();
        }
        return 0;
    }
    char a[PYMCL_PATH], b[PYMCL_PATH];
    if (instance_path(name, a, sizeof(a)) != 0) return -1;
    if (instance_path(new_name, b, sizeof(b)) != 0) return -1;
    if (pymcl_dir_exists(b)) { pymcl_set_error("实例 %s 已存在。", new_name); return -1; }
    wchar_t *wa = pymcl_u8_to_wide(a), *wb = pymcl_u8_to_wide(b);
    BOOL ok = MoveFileW(wa, wb);
    free(wa); free(wb);
    if (!ok) { pymcl_set_error("重命名失败"); return -1; }
    if (strcmp(config_str("default_instance", ""), name) == 0) {
        config_set_str("default_instance", new_name);
        config_save();
    }
    cJSON *s = cJSON_CreateString(new_name);
    instance_set_meta(new_name, "name", s);
    cJSON_Delete(s);
    return 0;
}

cJSON *instance_meta(const char *name) {
    char ip[PYMCL_PATH], mf[PYMCL_PATH];
    if (instance_path(name, ip, sizeof(ip)) != 0) return cJSON_CreateObject();
    pymcl_path_join(mf, sizeof(mf), ip, ".instance.json");
    cJSON *j = pymcl_read_json(mf);
    return j ? j : cJSON_CreateObject();
}

int instance_set_meta(const char *name, const char *key, cJSON *val) {
    char ip[PYMCL_PATH], mf[PYMCL_PATH];
    if (instance_path(name, ip, sizeof(ip)) != 0) return -1;
    pymcl_path_join(mf, sizeof(mf), ip, ".instance.json");
    cJSON *o = pymcl_read_json(mf);
    if (!o) o = cJSON_CreateObject();
    cJSON_DeleteItemFromObject(o, key);
    cJSON_AddItemToObject(o, key, cJSON_Duplicate(val, 1));
    int r = pymcl_write_json(mf, o);
    cJSON_Delete(o);
    return r;
}

void instance_java_pref(const char *name, char *out, size_t n) {
    cJSON *m = instance_meta(name);
    cJSON *j = cJSON_GetObjectItem(m, "java");
    const char *v = cJSON_IsString(j) ? j->valuestring : PYMCL_JAVA_AUTO;
    if (!v[0] || pymcl_ieq(v, "auto") || pymcl_ieq(v, "default") || pymcl_ieq(v, PYMCL_JAVA_AUTO))
        snprintf(out, n, "%s", PYMCL_JAVA_AUTO);
    else
        snprintf(out, n, "%s", v);
    cJSON_Delete(m);
}
void instance_set_java_pref(const char *name, const char *java) {
    const char *v = (!java || !java[0] || pymcl_ieq(java, "auto") || pymcl_ieq(java, "default"))
        ? PYMCL_JAVA_AUTO : java;
    cJSON *s = cJSON_CreateString(v);
    instance_set_meta(name, "java", s);
    cJSON_Delete(s);
}

void instance_versions_dir(const char *name, char *out, size_t n) {
    char ip[PYMCL_PATH];
    instance_path(name, ip, sizeof(ip));
    pymcl_path_join(out, n, ip, "versions");
}
void instance_libraries_dir(const char *name, char *out, size_t n) {
    char ip[PYMCL_PATH];
    instance_path(name, ip, sizeof(ip));
    config_libraries_dir(ip, out, n);
}
void instance_assets_dir(const char *name, char *out, size_t n) {
    char ip[PYMCL_PATH];
    instance_path(name, ip, sizeof(ip));
    config_assets_dir(ip, out, n);
}
void instance_natives_dir(const char *name, const char *vid, cJSON *vjson, char *out, size_t n) {
    if (vjson && manifest_is_legacy(vjson)) {
        char ip[PYMCL_PATH];
        instance_path(name, ip, sizeof(ip));
        pymcl_path_join3(out, n, ip, "bin", "natives");
        return;
    }
    char vd[PYMCL_PATH], nn[256];
    instance_versions_dir(name, vd, sizeof(vd));
    snprintf(nn, sizeof(nn), "%s-natives", vid);
    pymcl_path_join3(out, n, vd, vid, nn);
}

int instance_installed_ids(const char *name, cJSON **out) {
    *out = cJSON_CreateArray();
    char vd[PYMCL_PATH];
    instance_versions_dir(name, vd, sizeof(vd));
    if (!pymcl_dir_exists(vd)) return 0;
    wchar_t *w = pymcl_u8_to_wide(vd);
    wchar_t pat[PYMCL_PATH];
    _snwprintf(pat, PYMCL_PATH, L"%s\\*", w);
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pat, &fd);
    free(w);
    if (h == INVALID_HANDLE_VALUE) return 0;
    do {
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) continue;
        if (fd.cFileName[0] == L'.') continue;
        char *vid = pymcl_wide_to_u8(fd.cFileName);
        char jf[PYMCL_PATH], jn[256];
        snprintf(jn, sizeof(jn), "%s.json", vid);
        pymcl_path_join3(jf, sizeof(jf), vd, vid, jn);
        if (pymcl_file_exists(jf)) cJSON_AddItemToArray(*out, cJSON_CreateString(vid));
        free(vid);
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return 0;
}

cJSON *instance_version_json(const char *name, const char *vid) {
    char vd[PYMCL_PATH], jf[PYMCL_PATH], jn[256];
    instance_versions_dir(name, vd, sizeof(vd));
    snprintf(jn, sizeof(jn), "%s.json", vid);
    pymcl_path_join3(jf, sizeof(jf), vd, vid, jn);
    return pymcl_read_json(jf);
}

int instance_has_version(const char *name, const char *vid) {
    char vd[PYMCL_PATH], jf[PYMCL_PATH], jn[256];
    instance_versions_dir(name, vd, sizeof(vd));
    snprintf(jn, sizeof(jn), "%s.json", vid);
    pymcl_path_join3(jf, sizeof(jf), vd, vid, jn);
    return pymcl_file_exists(jf);
}
