#include "pymcl.h"

static const char *cf_bases[] = {
    CF_OFFICIAL,
    "https://mod.mcimirror.top/curseforge/v1",
    BMCLAPI "/curseforge/v1",
};

static void cf_hdr(char *out, size_t n) {
    const char *key = config_str("curseforge_api_key", "");
    if (key && key[0]) snprintf(out, n, "Accept: application/json\nx-api-key: %s", key);
    else snprintf(out, n, "Accept: application/json");
}

static cJSON *cf_get(const char *path, const char *query) {
    char hdr[512]; cf_hdr(hdr, sizeof(hdr));
    for (int i = 0; i < 3; i++) {
        char url[1024];
        if (query && query[0])
            snprintf(url, sizeof(url), "%s%s?%s", cf_bases[i], path, query);
        else
            snprintf(url, sizeof(url), "%s%s", cf_bases[i], path);
        cJSON *j = http_get_json_hdr(url, hdr, 45);
        if (j) return j;
    }
    return NULL;
}

static cJSON *cf_items(cJSON *data) {
    if (cJSON_IsArray(data)) return data;
    cJSON *d = cJSON_GetObjectItem(data, "data");
    return cJSON_IsArray(d) ? d : NULL;
}

static void mirror_mr(const char *url, char *out, size_t n) {
    if (strstr(url, "api.modrinth.com")) {
        snprintf(out, n, "%s", url);
        char *p = strstr(out, "https://api.modrinth.com");
        if (p) {
            char tmp[1024];
            snprintf(tmp, sizeof(tmp), "%s/modrinth%s", MCIM_MIRROR, p + strlen("https://api.modrinth.com"));
            snprintf(out, n, "%s", tmp);
        }
        return;
    }
    if (strstr(url, MODRINTH_CDN)) {
        snprintf(out, n, "%s", url);
        char *p = strstr(out, MODRINTH_CDN);
        if (p) {
            char tmp[1024];
            snprintf(tmp, sizeof(tmp), "%s%s", MCIM_MIRROR, p + strlen(MODRINTH_CDN));
            snprintf(out, n, "%s", tmp);
        }
        return;
    }
    snprintf(out, n, "%s", url);
}

static cJSON *mr_search(const char *query, const char *ptype, int limit) {
    char q[1024];
    char facets[128];
    snprintf(facets, sizeof(facets), "[[\"project_type:%s\"]]", ptype);
    /* curl-escape roughly */
    char enc[512] = {0};
    const char *s = query ? query : "";
    size_t o = 0;
    for (; *s && o + 4 < sizeof(enc); s++) {
        unsigned char c = (unsigned char)*s;
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '_')
            enc[o++] = (char)c;
        else { snprintf(enc + o, sizeof(enc) - o, "%%%02X", c); o = strlen(enc); }
    }
    snprintf(q, sizeof(q), "query=%s&facets=%s&limit=%d&index=relevance", enc[0] ? enc : "%20", facets, limit);
    /* encode facets brackets */
    char url1[1024], url2[1024];
    snprintf(url1, sizeof(url1), MODRINTH_API "/search?query=%s&limit=%d&index=relevance&facets=%%5B%%5B%%22project_type%%3A%s%%22%%5D%%5D",
             enc[0] ? enc : "%20", limit, ptype);
    snprintf(url2, sizeof(url2), MCIM_MIRROR "/modrinth/v2/search?query=%s&limit=%d&index=relevance&facets=%%5B%%5B%%22project_type%%3A%s%%22%%5D%%5D",
             enc[0] ? enc : "%20", limit, ptype);
    cJSON *j = http_get_json(url1, 45);
    if (!j) j = http_get_json(url2, 45);
    return j;
}

static cJSON *row_from_mr_hit(cJSON *h) {
    cJSON *row = cJSON_CreateObject();
    cJSON_AddStringToObject(row, "name", cJSON_GetStringValue(cJSON_GetObjectItem(h, "title")) ?: "?");
    cJSON_AddStringToObject(row, "author", cJSON_GetStringValue(cJSON_GetObjectItem(h, "author")) ?: "?");
    cJSON_AddNumberToObject(row, "downloads", cJSON_GetNumberValue(cJSON_GetObjectItem(h, "downloads")));
    if (cJSON_GetStringValue(cJSON_GetObjectItem(h, "slug")))
        cJSON_AddStringToObject(row, "slug", cJSON_GetStringValue(cJSON_GetObjectItem(h, "slug")));
    cJSON_AddStringToObject(row, "source", "modrinth");
    const char *desc = cJSON_GetStringValue(cJSON_GetObjectItem(h, "description"));
    if (desc) {
        char d[161]; snprintf(d, sizeof(d), "%s", desc);
        cJSON_AddStringToObject(row, "description", d);
    }
    return row;
}

static cJSON *row_from_cf(cJSON *m) {
    cJSON *row = cJSON_CreateObject();
    cJSON_AddStringToObject(row, "name", cJSON_GetStringValue(cJSON_GetObjectItem(m, "name")) ?: "?");
    char author[256] = {0};
    cJSON *a; int first = 1;
    cJSON_ArrayForEach(a, cJSON_GetObjectItem(m, "authors")) {
        const char *nm = cJSON_GetStringValue(cJSON_GetObjectItem(a, "name"));
        if (!nm) continue;
        if (!first) strncat(author, ", ", sizeof(author) - strlen(author) - 1);
        strncat(author, nm, sizeof(author) - strlen(author) - 1);
        first = 0;
    }
    cJSON_AddStringToObject(row, "author", author[0] ? author : "?");
    cJSON_AddNumberToObject(row, "downloads", cJSON_GetNumberValue(cJSON_GetObjectItem(m, "downloadCount")));
    if (cJSON_IsNumber(cJSON_GetObjectItem(m, "id")))
        cJSON_AddNumberToObject(row, "id", cJSON_GetObjectItem(m, "id")->valuedouble);
    if (cJSON_GetStringValue(cJSON_GetObjectItem(m, "slug")))
        cJSON_AddStringToObject(row, "slug", cJSON_GetStringValue(cJSON_GetObjectItem(m, "slug")));
    cJSON_AddStringToObject(row, "source", "curseforge");
    const char *sum = cJSON_GetStringValue(cJSON_GetObjectItem(m, "summary"));
    if (sum) {
        char d[161]; snprintf(d, sizeof(d), "%s", sum);
        cJSON_AddStringToObject(row, "description", d);
    }
    return row;
}

cJSON *search_mods(const char *query, const char *source) {
    const char *q = query ? query : "";
    int want_cf = 0, want_mr = 1;
    if (source && (pymcl_ieq(source, "全部") || pymcl_ieq(source, "all") || !source[0])) { want_cf = 1; want_mr = 1; }
    else if (source && pymcl_startswith(source, "curse")) { want_cf = 1; want_mr = 0; }
    else { want_mr = 1; want_cf = 0; }
    if (!q[0]) return catalog_popular_mods(source);
    cJSON *out = cJSON_CreateArray();
    char slug[128] = {0}; long long cf = 0; char title[128] = {0};
    catalog_lookup_mod(q, slug, sizeof(slug), &cf, title, sizeof(title));
    if (slug[0] && want_mr) {
        char url[256]; snprintf(url, sizeof(url), MODRINTH_API "/project/%s", slug);
        cJSON *p = http_get_json(url, 30);
        if (p) {
            cJSON *row = cJSON_CreateObject();
            cJSON_AddStringToObject(row, "name", cJSON_GetStringValue(cJSON_GetObjectItem(p, "title")) ?: title);
            cJSON_AddStringToObject(row, "author", "?");
            cJSON_AddNumberToObject(row, "downloads", cJSON_GetNumberValue(cJSON_GetObjectItem(p, "downloads")));
            cJSON_AddStringToObject(row, "slug", cJSON_GetStringValue(cJSON_GetObjectItem(p, "slug")) ?: slug);
            cJSON_AddStringToObject(row, "source", "modrinth");
            cJSON_AddItemToArray(out, row);
            cJSON_Delete(p);
        }
    }
    if (cf && want_cf) {
        char path[64]; snprintf(path, sizeof(path), "/mods/%lld", cf);
        cJSON *d = cf_get(path, NULL);
        cJSON *m = d ? cJSON_GetObjectItem(d, "data") : NULL;
        if (cJSON_IsObject(m)) cJSON_AddItemToArray(out, row_from_cf(m));
        cJSON_Delete(d);
    }
    if (cJSON_GetArraySize(out) > 0) return out;
    if (want_mr) {
        cJSON *j = mr_search(q, "mod", 30);
        cJSON *hits = j ? cJSON_GetObjectItem(j, "hits") : NULL;
        cJSON *h;
        cJSON_ArrayForEach(h, hits) cJSON_AddItemToArray(out, row_from_mr_hit(h));
        cJSON_Delete(j);
    }
    if (want_cf) {
        char queryp[512];
        snprintf(queryp, sizeof(queryp), "gameId=432&classId=%d&sortField=2&pageSize=30&index=0&searchFilter=%s",
                 CF_CLASS_MOD, q);
        cJSON *d = cf_get("/mods/search", queryp);
        cJSON *items = cf_items(d);
        cJSON *m;
        cJSON_ArrayForEach(m, items) cJSON_AddItemToArray(out, row_from_cf(m));
        cJSON_Delete(d);
    }
    return out;
}

cJSON *search_modpacks(const char *query, const char *source) {
    const char *q = query ? query : "";
    if (!q[0]) return catalog_popular_packs(source);
    int want_cf = 1, want_mr = 1;
    if (source && pymcl_startswith(source, "curse")) want_mr = 0;
    if (source && pymcl_ieq(source, "modrinth")) want_cf = 0;
    cJSON *out = cJSON_CreateArray();
    char slug[128] = {0}; long long cf = 0; char title[128] = {0};
    catalog_lookup_pack(q, slug, sizeof(slug), &cf, title, sizeof(title));
    if (cf && want_cf) {
        char path[64]; snprintf(path, sizeof(path), "/mods/%lld", cf);
        cJSON *d = cf_get(path, NULL);
        cJSON *m = d ? cJSON_GetObjectItem(d, "data") : NULL;
        if (cJSON_IsObject(m)) cJSON_AddItemToArray(out, row_from_cf(m));
        cJSON_Delete(d);
    }
    if (slug[0] && want_mr) {
        char url[256]; snprintf(url, sizeof(url), MODRINTH_API "/project/%s", slug);
        cJSON *p = http_get_json(url, 30);
        if (p) {
            cJSON *row = cJSON_CreateObject();
            cJSON_AddStringToObject(row, "name", cJSON_GetStringValue(cJSON_GetObjectItem(p, "title")) ?: title);
            cJSON_AddStringToObject(row, "slug", slug);
            cJSON_AddStringToObject(row, "source", "modrinth");
            cJSON_AddNumberToObject(row, "downloads", cJSON_GetNumberValue(cJSON_GetObjectItem(p, "downloads")));
            cJSON_AddItemToArray(out, row);
            cJSON_Delete(p);
        }
    }
    if (cJSON_GetArraySize(out) > 0) return out;
    if (want_mr) {
        cJSON *j = mr_search(q, "modpack", 25);
        cJSON *hits = j ? cJSON_GetObjectItem(j, "hits") : NULL;
        cJSON *h;
        cJSON_ArrayForEach(h, hits) cJSON_AddItemToArray(out, row_from_mr_hit(h));
        cJSON_Delete(j);
    }
    if (want_cf) {
        char queryp[512];
        snprintf(queryp, sizeof(queryp), "gameId=432&classId=%d&sortField=2&pageSize=25&index=0&searchFilter=%s",
                 CF_CLASS_MODPACK, q);
        cJSON *d = cf_get("/mods/search", queryp);
        cJSON *items = cf_items(d);
        cJSON *m;
        cJSON_ArrayForEach(m, items) cJSON_AddItemToArray(out, row_from_cf(m));
        cJSON_Delete(d);
    }
    return out;
}

cJSON *search_content(const char *kind, const char *query, const char *source) {
    const char *mr = "mod";
    int cf = CF_CLASS_MOD;
    if (strcmp(kind, "shader") == 0) { mr = "shader"; cf = CF_CLASS_SHADER; }
    else if (strcmp(kind, "resourcepack") == 0) { mr = "resourcepack"; cf = CF_CLASS_RESOURCEPACK; }
    else if (strcmp(kind, "datapack") == 0) { mr = "datapack"; cf = CF_CLASS_DATAPACK; }
    int want_mr = 1, want_cf = 1;
    if (source && pymcl_startswith(source, "curse")) want_mr = 0;
    if (source && pymcl_ieq(source, "modrinth")) want_cf = 0;
    cJSON *out = cJSON_CreateArray();
    if (want_mr) {
        cJSON *j = mr_search(query, mr, 30);
        cJSON *hits = j ? cJSON_GetObjectItem(j, "hits") : NULL;
        cJSON *h;
        cJSON_ArrayForEach(h, hits) cJSON_AddItemToArray(out, row_from_mr_hit(h));
        cJSON_Delete(j);
    }
    if (want_cf) {
        char queryp[512];
        snprintf(queryp, sizeof(queryp), "gameId=432&classId=%d&sortField=2&pageSize=30&index=0%s%s",
                 cf, (query && query[0]) ? "&searchFilter=" : "", query ? query : "");
        cJSON *d = cf_get("/mods/search", queryp);
        cJSON *items = cf_items(d);
        cJSON *m;
        cJSON_ArrayForEach(m, items) cJSON_AddItemToArray(out, row_from_cf(m));
        cJSON_Delete(d);
    }
    return out;
}

static const char *detect_loader(const char *inst) {
    cJSON *ids = NULL;
    instance_installed_ids(inst, &ids);
    const char *r = NULL;
    cJSON *it;
    cJSON_ArrayForEach(it, ids) {
        const char *v = it->valuestring;
        if (pymcl_icontains(v, "fabric")) r = "fabric";
        else if (pymcl_icontains(v, "quilt")) r = "quilt";
        else if (pymcl_icontains(v, "neoforge")) r = "neoforge";
        else if (pymcl_icontains(v, "forge")) r = "forge";
    }
    cJSON_Delete(ids);
    return r;
}

static char *detect_mc(const char *inst) {
    cJSON *m = instance_meta(inst);
    const char *mc = cJSON_GetStringValue(cJSON_GetObjectItem(m, "mc_version"));
    char *r = NULL;
    if (mc && mc[0]) {
        /* strip loader suffix */
        char buf[64]; snprintf(buf, sizeof(buf), "%s", mc);
        char *d = strchr(buf, '-'); if (d) *d = 0;
        r = pymcl_strdup(buf);
    }
    cJSON_Delete(m);
    if (r) return r;
    cJSON *ids = NULL;
    instance_installed_ids(inst, &ids);
    if (cJSON_GetArraySize(ids) > 0) {
        const char *v = cJSON_GetArrayItem(ids, 0)->valuestring;
        char buf[64]; snprintf(buf, sizeof(buf), "%s", v);
        char *d = strchr(buf, '-'); if (d) *d = 0;
        r = pymcl_strdup(buf);
    }
    cJSON_Delete(ids);
    return r;
}

/* ============================================================
 * 必需前置自动安装（对齐 PCL2「自动下载前置」/ HMCL，与 mclauncher/mods.py 同步）
 * ============================================================ */
#define CF_DEP_REQUIRED 3
#define DEP_SEEN_MAX 32

typedef struct {
    long long num[DEP_SEEN_MAX]; int nn;      /* CurseForge 项目 id */
    char str[DEP_SEEN_MAX][64];  int ns;      /* Modrinth 项目 / 版本 id */
} dep_seen;

/* 返回 1 = 已见过；否则记录并返回 0 */
static int seen_num(dep_seen *s, long long id) {
    for (int i = 0; i < s->nn; i++) if (s->num[i] == id) return 1;
    if (s->nn < DEP_SEEN_MAX) s->num[s->nn++] = id;
    return 0;
}

static int seen_str(dep_seen *s, const char *id) {
    if (!id || !id[0]) return 0;
    for (int i = 0; i < s->ns; i++) if (strcmp(s->str[i], id) == 0) return 1;
    if (s->ns < DEP_SEEN_MAX) snprintf(s->str[s->ns++], 64, "%s", id);
    return 0;
}

static void dep_log(pymcl_ctx *ctx, const char *fmt, const char *arg) {
    if (!ctx || !ctx->on_log) return;
    char m[320];
    snprintf(m, sizeof(m), fmt, arg ? arg : "?");
    ctx->on_log(ctx->ud, m);
}

static cJSON *mr_pick_version(cJSON *vers, const char *mc, const char *loader) {
    if (!cJSON_IsArray(vers) || cJSON_GetArraySize(vers) == 0) return NULL;
    cJSON *v;
    cJSON_ArrayForEach(v, vers) {
        int ok_mc = !mc, ok_ld = !loader;
        cJSON *gv, *ld;
        cJSON_ArrayForEach(gv, cJSON_GetObjectItem(v, "game_versions"))
            if (mc && cJSON_IsString(gv) && strcmp(gv->valuestring, mc) == 0) ok_mc = 1;
        cJSON_ArrayForEach(ld, cJSON_GetObjectItem(v, "loaders"))
            if (loader && cJSON_IsString(ld) && pymcl_ieq(ld->valuestring, loader)) ok_ld = 1;
        if (ok_mc && ok_ld) return v;
    }
    return cJSON_GetArrayItem(vers, 0);
}

static cJSON *mr_primary_file(cJSON *ver) {
    cJSON *files = cJSON_GetObjectItem(ver, "files");
    cJSON *file = NULL, *f;
    cJSON_ArrayForEach(f, files) if (cJSON_IsTrue(cJSON_GetObjectItem(f, "primary"))) file = f;
    if (!file && cJSON_GetArraySize(files) > 0) file = cJSON_GetArrayItem(files, 0);
    return file;
}

static int mr_download_version(const char *inst, cJSON *ver, pymcl_ctx *ctx) {
    cJSON *file = mr_primary_file(ver);
    if (!file) { pymcl_set_error("没有可下载文件"); return -1; }
    const char *fn = cJSON_GetStringValue(cJSON_GetObjectItem(file, "filename"));
    const char *u = cJSON_GetStringValue(cJSON_GetObjectItem(file, "url"));
    char dest[PYMCL_PATH], ip[PYMCL_PATH];
    instance_path(inst, ip, sizeof(ip));
    instance_ensure_dirs(inst);
    pymcl_path_join3(dest, sizeof(dest), ip, "mods", fn ? fn : "mod.jar");
    char mir[1024];
    mirror_mr(u, mir, sizeof(mir));
    const char *ex[] = { u };
    return download_file(mir, ex, 1, dest, ctx,
                         cJSON_GetStringValue(cJSON_GetObjectItem(cJSON_GetObjectItem(file, "hashes"), "sha1")),
                         -1,
                         cJSON_GetStringValue(cJSON_GetObjectItem(cJSON_GetObjectItem(file, "hashes"), "sha512")));
}

/* 递归安装 Modrinth 必需前置；单个失败只记日志，不打断主模组 */
static void mr_install_deps(const char *inst, cJSON *ver, const char *mc, const char *loader,
                            dep_seen *seen, int depth, pymcl_ctx *ctx) {
    if (depth > 3) return;
    cJSON *dep;
    cJSON_ArrayForEach(dep, cJSON_GetObjectItem(ver, "dependencies")) {
        const char *type = cJSON_GetStringValue(cJSON_GetObjectItem(dep, "dependency_type"));
        if (!type || strcmp(type, "required") != 0) continue;
        const char *dvid = cJSON_GetStringValue(cJSON_GetObjectItem(dep, "version_id"));
        const char *dpid = cJSON_GetStringValue(cJSON_GetObjectItem(dep, "project_id"));
        if ((!dvid || !dvid[0]) && (!dpid || !dpid[0])) continue;
        if (dpid && dpid[0] && seen_str(seen, dpid)) continue;
        char url[256];
        cJSON *owner = NULL, *dv = NULL;
        if (dvid && dvid[0]) {
            snprintf(url, sizeof(url), MODRINTH_API "/version/%s", dvid);
            owner = http_get_json(url, 45);
            dv = cJSON_IsObject(owner) ? owner : NULL;
        } else {
            /* 只声明了项目：按实例 MC 版本与加载器挑兼容版本 */
            snprintf(url, sizeof(url), MODRINTH_API "/project/%s/version", dpid);
            owner = http_get_json(url, 45);
            dv = mr_pick_version(owner, mc, loader);
        }
        if (!dv) {
            dep_log(ctx, "必需前置 %s 获取失败，需手动安装", dpid ? dpid : dvid);
            cJSON_Delete(owner);
            continue;
        }
        const char *vpid = cJSON_GetStringValue(cJSON_GetObjectItem(dv, "project_id"));
        if (vpid && (!dpid || strcmp(vpid, dpid) != 0) && seen_str(seen, vpid)) {
            cJSON_Delete(owner);
            continue;
        }
        cJSON *pfile = mr_primary_file(dv);
        const char *fn = pfile ? cJSON_GetStringValue(cJSON_GetObjectItem(pfile, "filename")) : NULL;
        char dest[PYMCL_PATH], ip[PYMCL_PATH];
        instance_path(inst, ip, sizeof(ip));
        pymcl_path_join3(dest, sizeof(dest), ip, "mods", fn ? fn : "");
        if (fn && pymcl_file_exists(dest)) {   /* 已装过同名文件，跳过 */
            cJSON_Delete(owner);
            continue;
        }
        dep_log(ctx, "自动安装必需前置: %s", fn ? fn : (dpid ? dpid : dvid));
        if (mr_download_version(inst, dv, ctx) == 0)
            mr_install_deps(inst, dv, mc, loader, seen, depth + 1, ctx);
        else
            dep_log(ctx, "必需前置 %s 下载失败，需手动安装", fn ? fn : dpid);
        cJSON_Delete(owner);
    }
}

static int install_modrinth_mod(const char *inst, const char *slug, pymcl_ctx *ctx) {
    char url[256];
    snprintf(url, sizeof(url), MODRINTH_API "/project/%s/version", slug);
    cJSON *vers = http_get_json(url, 45);
    if (!cJSON_IsArray(vers) || cJSON_GetArraySize(vers) == 0) {
        cJSON_Delete(vers);
        pymcl_set_error("模组 %s 没有可下载版本", slug);
        return -1;
    }
    char *mc = detect_mc(inst);
    const char *loader = detect_loader(inst);
    cJSON *chosen = mr_pick_version(vers, mc, loader);
    int r = mr_download_version(inst, chosen, ctx);
    if (r == 0) {
        dep_seen seen = {0};
        const char *pid = cJSON_GetStringValue(cJSON_GetObjectItem(chosen, "project_id"));
        seen_str(&seen, pid ? pid : slug);
        mr_install_deps(inst, chosen, mc, loader, &seen, 0, ctx);
    }
    free(mc);
    cJSON_Delete(vers);
    return r;
}

static void cf_cdn(long long fid, const char *fn, const char *host, char *out, size_t n) {
    snprintf(out, n, "https://%s/files/%lld/%lld/%s", host, fid / 1000, fid % 1000, fn ? fn : "file.jar");
}

/* 挑最匹配 MC 版本与加载器的文件；退而求其次只匹配 MC，再不行取第一个 */
static cJSON *cf_pick_file(cJSON *files, const char *mc, const char *loader) {
    if (!cJSON_IsArray(files) || cJSON_GetArraySize(files) == 0) return NULL;
    cJSON *f, *best_mc = NULL;
    cJSON_ArrayForEach(f, files) {
        int ok_mc = !mc || !mc[0], ok_ld = !loader || !loader[0];
        cJSON *gv;
        cJSON_ArrayForEach(gv, cJSON_GetObjectItem(f, "gameVersions")) {
            if (!cJSON_IsString(gv)) continue;
            if (mc && mc[0] && strcmp(gv->valuestring, mc) == 0) ok_mc = 1;
            if (loader && loader[0] && pymcl_ieq(gv->valuestring, loader)) ok_ld = 1;
        }
        if (ok_mc && ok_ld) return f;
        if (ok_mc && !best_mc) best_mc = f;
    }
    return best_mc ? best_mc : cJSON_GetArrayItem(files, 0);
}

static int cf_download_picked(const char *inst, long long addon, cJSON *f, pymcl_ctx *ctx) {
    long long fid = (long long)cJSON_GetNumberValue(cJSON_GetObjectItem(f, "id"));
    const char *fn = cJSON_GetStringValue(cJSON_GetObjectItem(f, "fileName")) ?: "mod.jar";
    const char *du = cJSON_GetStringValue(cJSON_GetObjectItem(f, "downloadUrl"));
    char dest[PYMCL_PATH], ip[PYMCL_PATH];
    instance_path(inst, ip, sizeof(ip));
    instance_ensure_dirs(inst);
    pymcl_path_join3(dest, sizeof(dest), ip, "mods", fn);
    char u1[512], u2[512], u3[512];
    cf_cdn(fid, fn, "mediafilez.forgecdn.net", u1, sizeof(u1));
    cf_cdn(fid, fn, "edge.forgecdn.net", u2, sizeof(u2));
    snprintf(u3, sizeof(u3), CF_OFFICIAL "/mods/%lld/files/%lld/download", addon, fid);
    const char *first = du && du[0] ? du : u1;
    const char *ex[4]; int ne = 0;
    if (first != u1) ex[ne++] = u1;
    ex[ne++] = u2; ex[ne++] = u3;
    return download_file(first, ex, ne, dest, ctx, NULL, -1, NULL);
}

/* 递归安装 CurseForge 必需前置（relationType=3）；失败只记日志 */
static void cf_install_deps(const char *inst, cJSON *file, const char *mc, const char *loader,
                            dep_seen *seen, int depth, pymcl_ctx *ctx) {
    if (depth > 3) return;
    cJSON *dep;
    cJSON_ArrayForEach(dep, cJSON_GetObjectItem(file, "dependencies")) {
        cJSON *rel = cJSON_GetObjectItem(dep, "relationType");
        cJSON *mid = cJSON_GetObjectItem(dep, "modId");
        if (!cJSON_IsNumber(rel) || (int)cJSON_GetNumberValue(rel) != CF_DEP_REQUIRED) continue;
        if (!cJSON_IsNumber(mid)) continue;
        long long id = (long long)cJSON_GetNumberValue(mid);
        if (id <= 0 || seen_num(seen, id)) continue;
        char path[64]; snprintf(path, sizeof(path), "/mods/%lld", id);
        cJSON *d = cf_get(path, NULL);
        cJSON *mod = d ? cJSON_GetObjectItem(d, "data") : NULL;
        const char *nm = mod ? cJSON_GetStringValue(cJSON_GetObjectItem(mod, "name")) : NULL;
        cJSON *pf = cJSON_IsObject(mod)
            ? cf_pick_file(cJSON_GetObjectItem(mod, "latestFiles"), mc, loader) : NULL;
        if (!pf) {
            dep_log(ctx, "必需前置 %s 没有可用文件，需手动安装", nm ? nm : path);
            cJSON_Delete(d);
            continue;
        }
        const char *fn = cJSON_GetStringValue(cJSON_GetObjectItem(pf, "fileName"));
        char dest[PYMCL_PATH], ip[PYMCL_PATH];
        instance_path(inst, ip, sizeof(ip));
        pymcl_path_join3(dest, sizeof(dest), ip, "mods", fn ? fn : "");
        if (fn && pymcl_file_exists(dest)) {   /* 已装过同名文件，跳过 */
            cJSON_Delete(d);
            continue;
        }
        dep_log(ctx, "自动安装必需前置: %s", nm ? nm : (fn ? fn : path));
        if (cf_download_picked(inst, id, pf, ctx) == 0)
            cf_install_deps(inst, pf, mc, loader, seen, depth + 1, ctx);
        else
            dep_log(ctx, "必需前置 %s 下载失败，需手动安装", nm ? nm : path);
        cJSON_Delete(d);
    }
}

static int install_cf_mod(const char *inst, long long addon, pymcl_ctx *ctx) {
    char path[64]; snprintf(path, sizeof(path), "/mods/%lld", addon);
    cJSON *d = cf_get(path, NULL);
    cJSON *mod = d ? cJSON_GetObjectItem(d, "data") : NULL;
    if (!cJSON_IsObject(mod)) { cJSON_Delete(d); pymcl_set_error("获取 CurseForge 详情失败"); return -1; }
    cJSON *files = cJSON_GetObjectItem(mod, "latestFiles");
    if (!cJSON_IsArray(files) || cJSON_GetArraySize(files) == 0) {
        char p2[80]; snprintf(p2, sizeof(p2), "/mods/%lld/files", addon);
        cJSON *fl = cf_get(p2, "pageSize=50");
        files = cf_items(fl);
        /* leak fl with d - keep both */
        if (!files) { cJSON_Delete(d); cJSON_Delete(fl); pymcl_set_error("没有可下载文件"); return -1; }
        /* use fl as owner via attaching */
        cJSON_AddItemToObject(d, "_files", fl);
        files = cf_items(fl);
    }
    char *mc = detect_mc(inst);
    const char *loader = detect_loader(inst);
    cJSON *f = cf_pick_file(files, mc, loader);
    int r = f ? cf_download_picked(inst, addon, f, ctx) : -1;
    if (!f) pymcl_set_error("没有可下载文件");
    if (r == 0) {
        dep_seen seen = {0};
        seen_num(&seen, addon);
        cf_install_deps(inst, f, mc, loader, &seen, 0, ctx);
    }
    free(mc);
    cJSON_Delete(d);
    return r;
}

int install_mod(const char *instance, const char *name, cJSON *extra, pymcl_ctx *ctx) {
    instance_ensure_dirs(instance);
    const char *path = extra ? cJSON_GetStringValue(cJSON_GetObjectItem(extra, "path")) : NULL;
    const char *url = extra ? cJSON_GetStringValue(cJSON_GetObjectItem(extra, "url")) : NULL;
    const char *src = extra ? cJSON_GetStringValue(cJSON_GetObjectItem(extra, "source")) : NULL;
    if (path && pymcl_file_exists(path)) {
        char dest[PYMCL_PATH], ip[PYMCL_PATH];
        instance_path(instance, ip, sizeof(ip));
        pymcl_path_join3(dest, sizeof(dest), ip, "mods", pymcl_basename(path));
        return pymcl_copy_file(path, dest);
    }
    if (url && pymcl_startswith(url, "http")) {
        if (strstr(url, "modrinth.com/mod")) {
            const char *p = strstr(url, "/mod/");
            char slug[128] = {0};
            if (p) {
                p += 5; int i = 0;
                while (*p && *p != '/' && *p != '?' && i < 127) slug[i++] = *p++;
            }
            return install_modrinth_mod(instance, slug, ctx);
        }
        if (strstr(url, "curseforge.com")) {
            /* treat as direct if .jar */
        }
        if (pymcl_endswith(url, ".jar")) {
            char dest[PYMCL_PATH], ip[PYMCL_PATH];
            instance_path(instance, ip, sizeof(ip));
            pymcl_path_join3(dest, sizeof(dest), ip, "mods", pymcl_basename(url));
            return download_file(url, NULL, 0, dest, ctx, NULL, -1, NULL);
        }
    }
    if (src && pymcl_startswith(src, "curse") && extra && cJSON_IsNumber(cJSON_GetObjectItem(extra, "id")))
        return install_cf_mod(instance, (long long)cJSON_GetObjectItem(extra, "id")->valuedouble, ctx);
    const char *slug = extra ? cJSON_GetStringValue(cJSON_GetObjectItem(extra, "slug")) : NULL;
    if (!slug) slug = name;
    if (slug && slug[0]) return install_modrinth_mod(instance, slug, ctx);
    pymcl_set_error("无法解析模组: %s", name ? name : "");
    return -1;
}

static const char *kind_subdir(const char *kind) {
    if (strcmp(kind, "shader") == 0) return "shaderpacks";
    if (strcmp(kind, "resourcepack") == 0) return "resourcepacks";
    if (strcmp(kind, "datapack") == 0) return "datapacks";
    return "mods";
}

int install_content(const char *kind, const char *instance, const char *name, cJSON *extra, pymcl_ctx *ctx) {
    const char *subdir = kind_subdir(kind);
    instance_ensure_dirs(instance);
    const char *path = extra ? cJSON_GetStringValue(cJSON_GetObjectItem(extra, "path")) : NULL;
    const char *url = extra ? cJSON_GetStringValue(cJSON_GetObjectItem(extra, "url")) : NULL;
    char ip[PYMCL_PATH], dest[PYMCL_PATH];
    instance_path(instance, ip, sizeof(ip));
    if (path && pymcl_file_exists(path)) {
        pymcl_path_join3(dest, sizeof(dest), ip, subdir, pymcl_basename(path));
        return pymcl_copy_file(path, dest);
    }
    if (url && pymcl_startswith(url, "http")) {
        pymcl_path_join3(dest, sizeof(dest), ip, subdir, pymcl_basename(url));
        return download_file(url, NULL, 0, dest, ctx, NULL, -1, NULL);
    }
    const char *src = extra ? cJSON_GetStringValue(cJSON_GetObjectItem(extra, "source")) : NULL;
    if (src && pymcl_startswith(src, "curse") && extra && cJSON_IsNumber(cJSON_GetObjectItem(extra, "id"))) {
        /* reuse cf download into subdir */
        char tmpname[64];
        snprintf(tmpname, sizeof(tmpname), "%s", name ? name : "pack");
        /* install to mods then move — simpler: download via cf into subdir */
        long long id = (long long)cJSON_GetObjectItem(extra, "id")->valuedouble;
        char pth[64]; snprintf(pth, sizeof(pth), "/mods/%lld", id);
        cJSON *d = cf_get(pth, NULL);
        cJSON *mod = d ? cJSON_GetObjectItem(d, "data") : NULL;
        cJSON *files = mod ? cJSON_GetObjectItem(mod, "latestFiles") : NULL;
        cJSON *f = files && cJSON_GetArraySize(files) ? cJSON_GetArrayItem(files, 0) : NULL;
        if (!f) { cJSON_Delete(d); pymcl_set_error("没有可下载文件"); return -1; }
        const char *fn = cJSON_GetStringValue(cJSON_GetObjectItem(f, "fileName")) ?: "pack.zip";
        const char *du = cJSON_GetStringValue(cJSON_GetObjectItem(f, "downloadUrl"));
        pymcl_path_join3(dest, sizeof(dest), ip, subdir, fn);
        int r = download_file(du ? du : "", NULL, 0, dest, ctx, NULL, -1, NULL);
        cJSON_Delete(d);
        return r;
    }
    const char *slug = extra ? cJSON_GetStringValue(cJSON_GetObjectItem(extra, "slug")) : NULL;
    if (!slug) slug = name;
    char u[256];
    snprintf(u, sizeof(u), MODRINTH_API "/project/%s/version", slug);
    cJSON *vers = http_get_json(u, 45);
    if (!cJSON_IsArray(vers) || cJSON_GetArraySize(vers) == 0) {
        cJSON_Delete(vers); pymcl_set_error("%s 没有可下载版本", slug); return -1;
    }
    cJSON *files = cJSON_GetObjectItem(cJSON_GetArrayItem(vers, 0), "files");
    cJSON *file = cJSON_GetArraySize(files) ? cJSON_GetArrayItem(files, 0) : NULL;
    const char *fn = file ? cJSON_GetStringValue(cJSON_GetObjectItem(file, "filename")) : "pack.zip";
    const char *du = file ? cJSON_GetStringValue(cJSON_GetObjectItem(file, "url")) : NULL;
    pymcl_path_join3(dest, sizeof(dest), ip, subdir, fn);
    char mir[1024];
    if (du) mirror_mr(du, mir, sizeof(mir));
    const char *ex[] = { du };
    int r = du ? download_file(mir, ex, 1, dest, ctx, NULL, -1, NULL) : -1;
    cJSON_Delete(vers);
    return r;
}

cJSON *list_instance_files(const char *instance, const char *subdir) {
    cJSON *out = cJSON_CreateArray();
    char ip[PYMCL_PATH], dir[PYMCL_PATH];
    instance_path(instance, ip, sizeof(ip));
    pymcl_path_join(dir, sizeof(dir), ip, subdir);
    if (!pymcl_dir_exists(dir)) return out;
    wchar_t *w = pymcl_u8_to_wide(dir);
    wchar_t pat[PYMCL_PATH];
    _snwprintf(pat, PYMCL_PATH, L"%s\\*", w);
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pat, &fd);
    free(w);
    if (h == INVALID_HANDLE_VALUE) return out;
    do {
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
        char *n = pymcl_wide_to_u8(fd.cFileName);
        if (pymcl_endswith(n, ".jar") || pymcl_endswith(n, ".zip")
            || pymcl_endswith(n, ".jar.disabled") || pymcl_endswith(n, ".zip.disabled"))
            cJSON_AddItemToArray(out, cJSON_CreateString(n));
        free(n);
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return out;
}

int delete_instance_file(const char *instance, const char *subdir, const char *filename) {
    char ip[PYMCL_PATH], dir[PYMCL_PATH], p[PYMCL_PATH];
    instance_path(instance, ip, sizeof(ip));
    pymcl_path_join(dir, sizeof(dir), ip, subdir);
    pymcl_path_join(p, sizeof(p), dir, filename);
    if (strstr(filename, "..") || strchr(filename, '/') || strchr(filename, '\\')) {
        pymcl_set_error("非法路径: %s", filename);
        return -1;
    }
    pymcl_remove_tree(p);
    return 0;
}
