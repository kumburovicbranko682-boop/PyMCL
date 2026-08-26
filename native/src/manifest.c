#include "pymcl.h"

int mc_version_tuple(const char *id, int *a, int *b, int *c) {
    if (!id) return -1;
    char buf[64]; snprintf(buf, sizeof(buf), "%s", id);
    char *dash = strpbrk(buf, "-+");
    if (dash) *dash = 0;
    int x = 0, y = 0, z = 0;
    if (sscanf(buf, "%d.%d.%d", &x, &y, &z) < 2) {
        if (sscanf(buf, "%d.%d", &x, &y) < 2) return -1;
    }
    if (a) *a = x; if (b) *b = y; if (c) *c = z;
    return 0;
}

cJSON *manifest_get(int force) {
    char cache[PYMCL_PATH], meta[PYMCL_PATH];
    pymcl_cache_dir(cache, sizeof(cache));
    pymcl_ensure_dir(cache);
    char mf[PYMCL_PATH];
    pymcl_path_join(mf, sizeof(mf), cache, "version_manifest.json");
    pymcl_path_join(meta, sizeof(meta), cache, "version_manifest.meta");
    if (!force && pymcl_file_exists(mf)) {
        cJSON *m = pymcl_read_json(meta);
        double fetched = m && cJSON_IsNumber(cJSON_GetObjectItem(m, "fetched_at"))
            ? cJSON_GetObjectItem(m, "fetched_at")->valuedouble : 0;
        cJSON_Delete(m);
        if (time(NULL) - fetched < 4 * 3600) {
            cJSON *d = pymcl_read_json(mf);
            if (d) return d;
        }
    }
    /* 顺序对齐 mclauncher/source.py version_manifest_urls()：以前 BMCLAPI
     * 永远排第一，「仅官方」照样先打镜像、海外自动模式也被拖慢。
     * fetch_json_mirrors 会经 expand_urls 按 download_source 补镜像。 */
    const char *urls[] = {
        "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json",
        "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json",
    };
    cJSON *data = fetch_json_mirrors(urls, 2, 60);
    if (data) {
        pymcl_write_json(mf, data);
        cJSON *mm = cJSON_CreateObject();
        cJSON_AddNumberToObject(mm, "fetched_at", (double)time(NULL));
        pymcl_write_json(meta, mm);
        cJSON_Delete(mm);
        return data;
    }
    return pymcl_read_json(mf);
}

cJSON *manifest_list_remote(int force) {
    cJSON *m = manifest_get(force);
    cJSON *out = cJSON_CreateObject();
    if (!m) return out;
    cJSON *vers = cJSON_GetObjectItem(m, "versions");
    cJSON *v;
    if (cJSON_IsArray(vers)) {
        cJSON_ArrayForEach(v, vers) {
            const char *id = cJSON_GetStringValue(cJSON_GetObjectItem(v, "id"));
            if (id) cJSON_AddItemToObject(out, id, cJSON_Duplicate(v, 1));
        }
    }
    cJSON_Delete(m);
    return out;
}

cJSON *manifest_get_version_url(const char *url, const char *id) {
    cJSON *data = http_get_json(url, 60);
    if (!data) return NULL;
    cJSON_AddNumberToObject(data, "__pymcl_cached_at", (double)time(NULL));
    char dir[PYMCL_PATH], file[PYMCL_PATH];
    pymcl_cache_dir(dir, sizeof(dir));
    pymcl_path_join(dir, sizeof(dir), dir, "versions");
    pymcl_ensure_dir(dir);
    char fn[256]; snprintf(fn, sizeof(fn), "%s.json", id);
    pymcl_path_join(file, sizeof(file), dir, fn);
    pymcl_write_json(file, data);
    return data;
}

cJSON *manifest_get_version(const char *id, int force) {
    char dir[PYMCL_PATH], file[PYMCL_PATH], fn[256];
    pymcl_cache_dir(dir, sizeof(dir));
    pymcl_path_join(dir, sizeof(dir), dir, "versions");
    snprintf(fn, sizeof(fn), "%s.json", id);
    pymcl_path_join(file, sizeof(file), dir, fn);
    if (!force && pymcl_file_exists(file)) {
        cJSON *cached = pymcl_read_json(file);
        double at = cached && cJSON_IsNumber(cJSON_GetObjectItem(cached, "__pymcl_cached_at"))
            ? cJSON_GetObjectItem(cached, "__pymcl_cached_at")->valuedouble : 0;
        if (cached && time(NULL) - at < 24 * 3600) return cached;
        cJSON_Delete(cached);
    }
    cJSON *vers = manifest_list_remote(0);
    cJSON *entry = cJSON_GetObjectItem(vers, id);
    const char *url = entry ? cJSON_GetStringValue(cJSON_GetObjectItem(entry, "url")) : NULL;
    cJSON *data = NULL;
    if (url) data = manifest_get_version_url(url, id);
    cJSON_Delete(vers);
    if (!data) pymcl_set_error("找不到版本 %s", id);
    return data;
}

void library_identity(cJSON *lib, char *out, size_t n) {
    const char *name = cJSON_GetStringValue(cJSON_GetObjectItem(lib, "name")) ?: "";
    char buf[512]; snprintf(buf, sizeof(buf), "%s", name);
    char *p1 = strchr(buf, ':');
    char *p2 = p1 ? strchr(p1 + 1, ':') : NULL;
    char *p3 = p2 ? strchr(p2 + 1, ':') : NULL;
    if (p1) *p1 = 0;
    if (p2) *p2 = 0;
    if (p3) *p3 = 0;
    const char *g = buf, *a = p1 ? p1 + 1 : name, *cls = p3 ? p3 + 1 : NULL;
    if (cJSON_GetObjectItem(lib, "natives"))
        snprintf(out, n, "%s:%s:natives", g, a);
    else if (cls && cls[0])
        snprintf(out, n, "%s:%s:%s", g, a, cls);
    else
        snprintf(out, n, "%s:%s", g, a);
}

static cJSON *merge_libs(cJSON *parent, cJSON *child) {
    cJSON *merged = cJSON_CreateArray();
    cJSON *index = cJSON_CreateObject();
    cJSON *lib;
    cJSON_ArrayForEach(lib, parent) {
        char key[256];
        library_identity(lib, key, sizeof(key));
        cJSON_AddNumberToObject(index, key, cJSON_GetArraySize(merged));
        cJSON_AddItemToArray(merged, cJSON_Duplicate(lib, 1));
    }
    cJSON_ArrayForEach(lib, child) {
        char key[256];
        library_identity(lib, key, sizeof(key));
        cJSON *ix = cJSON_GetObjectItem(index, key);
        if (cJSON_IsNumber(ix)) {
            cJSON_ReplaceItemInArray(merged, (int)ix->valuedouble, cJSON_Duplicate(lib, 1));
        } else {
            cJSON_AddNumberToObject(index, key, cJSON_GetArraySize(merged));
            cJSON_AddItemToArray(merged, cJSON_Duplicate(lib, 1));
        }
    }
    cJSON_Delete(index);
    return merged;
}

static cJSON *merge_ver(cJSON *base, cJSON *child) {
    cJSON *merged = cJSON_Duplicate(base, 1);
    cJSON *c;
    cJSON_ArrayForEach(c, child) {
        if (strcmp(c->string, "inheritsFrom") == 0) continue;
        if (strcmp(c->string, "libraries") == 0) continue;
        if (strcmp(c->string, "arguments") == 0) continue;
        if (strcmp(c->string, "downloads") == 0) continue;
        cJSON_DeleteItemFromObject(merged, c->string);
        cJSON_AddItemToObject(merged, c->string, cJSON_Duplicate(c, 1));
    }
    cJSON *libs = merge_libs(cJSON_GetObjectItem(base, "libraries"), cJSON_GetObjectItem(child, "libraries"));
    cJSON_DeleteItemFromObject(merged, "libraries");
    cJSON_AddItemToObject(merged, "libraries", libs);
    cJSON *ba = cJSON_GetObjectItem(base, "arguments");
    cJSON *ca = cJSON_GetObjectItem(child, "arguments");
    if (cJSON_IsObject(ba) && cJSON_IsObject(ca)) {
        cJSON *args = cJSON_CreateObject();
        cJSON *g = cJSON_CreateArray();
        cJSON *jv = cJSON_CreateArray();
        cJSON *x;
        cJSON_ArrayForEach(x, cJSON_GetObjectItem(ba, "game")) cJSON_AddItemToArray(g, cJSON_Duplicate(x, 1));
        cJSON_ArrayForEach(x, cJSON_GetObjectItem(ca, "game")) cJSON_AddItemToArray(g, cJSON_Duplicate(x, 1));
        cJSON_ArrayForEach(x, cJSON_GetObjectItem(ba, "jvm")) cJSON_AddItemToArray(jv, cJSON_Duplicate(x, 1));
        cJSON_ArrayForEach(x, cJSON_GetObjectItem(ca, "jvm")) cJSON_AddItemToArray(jv, cJSON_Duplicate(x, 1));
        cJSON_AddItemToObject(args, "game", g);
        cJSON_AddItemToObject(args, "jvm", jv);
        cJSON_DeleteItemFromObject(merged, "arguments");
        cJSON_AddItemToObject(merged, "arguments", args);
    } else if (cJSON_GetObjectItem(child, "minecraftArguments") && !cJSON_GetObjectItem(child, "arguments")) {
        cJSON_DeleteItemFromObject(merged, "arguments");
    }
    cJSON *dl = cJSON_Duplicate(cJSON_GetObjectItem(base, "downloads"), 1);
    if (!dl) dl = cJSON_CreateObject();
    cJSON *cd = cJSON_GetObjectItem(child, "downloads");
    if (cJSON_IsObject(cd)) {
        cJSON *k;
        cJSON_ArrayForEach(k, cd) {
            cJSON_DeleteItemFromObject(dl, k->string);
            cJSON_AddItemToObject(dl, k->string, cJSON_Duplicate(k, 1));
        }
    }
    cJSON_DeleteItemFromObject(merged, "downloads");
    cJSON_AddItemToObject(merged, "downloads", dl);
    return merged;
}

cJSON *manifest_resolve_inherits(cJSON *vjson, cJSON *(*load)(const char *, void *), void *ud) {
    cJSON *merged = cJSON_Duplicate(vjson, 1);
    char seen[16][128]; int ns = 0;
    while (1) {
        const char *pid = cJSON_GetStringValue(cJSON_GetObjectItem(merged, "inheritsFrom"));
        if (!pid) break;
        for (int i = 0; i < ns; i++) if (strcmp(seen[i], pid) == 0) {
            pymcl_set_error("版本继承链出现循环: %s", pid);
            cJSON_Delete(merged);
            return NULL;
        }
        if (ns < 16) snprintf(seen[ns++], 128, "%s", pid);
        cJSON *parent = load(pid, ud);
        if (!parent) {
            pymcl_set_error("缺少被继承的父版本 %s", pid);
            cJSON_Delete(merged);
            return NULL;
        }
        cJSON *next = merge_ver(parent, merged);
        cJSON_Delete(parent);
        cJSON_Delete(merged);
        merged = next;
    }
    return merged;
}

int manifest_is_legacy(cJSON *vjson) {
    const char *assets = cJSON_GetStringValue(cJSON_GetObjectItem(vjson, "assets"));
    if (assets && strcmp(assets, "pre-1.6") == 0) return 1;
    if (!cJSON_GetObjectItem(vjson, "assetIndex") && !assets) {
        if (!cJSON_GetObjectItem(vjson, "arguments")) return 1;
    }
    return 0;
}

static int looks_mc(const char *id) {
    int a, b, c;
    if (mc_version_tuple(id, &a, &b, &c) != 0) {
        /* snapshot 24w36a */
        int y, w; char ch;
        if (sscanf(id, "%2dw%2d%c", &y, &w, &ch) == 3) return 1;
        return 0;
    }
    if (a >= 3 && a <= 19) return 0;
    return a == 1 || a >= 20;
}

char *manifest_resolve_playable(const char *vid) {
    if (!vid || !vid[0]) return NULL;
    cJSON *vers = manifest_list_remote(0);
    if (cJSON_GetObjectItem(vers, vid)) {
        cJSON_Delete(vers);
        return pymcl_strdup(vid);
    }
    char base[64]; snprintf(base, sizeof(base), "%s", vid);
    char *cut = strpbrk(base, "-_+");
    if (cut) *cut = 0;
    if (cJSON_GetObjectItem(vers, base)) {
        cJSON_Delete(vers);
        return pymcl_strdup(base);
    }
    if (!looks_mc(vid)) { cJSON_Delete(vers); return NULL; }
    int wa, wb, wc;
    if (mc_version_tuple(vid, &wa, &wb, &wc) != 0) { cJSON_Delete(vers); return NULL; }
    char best[64] = {0};
    int ba = -1, bb = -1, bc = -1;
    cJSON *it;
    cJSON_ArrayForEach(it, vers) {
        const char *id = it->string;
        const char *ty = cJSON_GetStringValue(cJSON_GetObjectItem(it, "type"));
        if (ty && strcmp(ty, "release") != 0) continue;
        int a, b, c;
        if (mc_version_tuple(id, &a, &b, &c) != 0) continue;
        if (a != wa || b != wb) continue;
        if (a < wa || (a == wa && (b < wb || (b == wb && c <= wc)))) {
            if (a > ba || (a == ba && (b > bb || (b == bb && c > bc)))) {
                ba = a; bb = b; bc = c;
                snprintf(best, sizeof(best), "%s", id);
            }
        }
    }
    cJSON_Delete(vers);
    return best[0] ? pymcl_strdup(best) : NULL;
}
