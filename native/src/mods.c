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

static void url_enc(const char *s, char *out, size_t n) {
    size_t o = 0;
    out[0] = 0;
    for (; s && *s && o + 4 < n; s++) {
        unsigned char c = (unsigned char)*s;
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')
            || c == '-' || c == '_' || c == '.' || c == '~')
            out[o++] = (char)c;
        else { snprintf(out + o, n - o, "%%%02X", c); o = strlen(out); }
    }
    out[o] = 0;
}

/* ---- 搜索筛选：映射表与 mclauncher/catalog_files.py 保持一致，
        改那边的表时这里要跟着改 ---- */

static const char *filter_type_key(const char *label) {
    static const struct { const char *alias; const char *key; } aliases[] = {
        {"优化", "optimization"}, {"科技", "technology"}, {"魔法", "magic"},
        {"冒险", "adventure"}, {"生存", "survival"}, {"装饰", "decoration"},
        {"写实", "realistic"}, {"卡通", "cartoon"}, {"高性能", "performance"},
        {"光追", "path-tracing"}, {"现代风", "modern"}, {"动态效果", "animated"},
        {"空岛", "skyblock"}, {"创造", "creation"},
    };
    if (!label || !label[0]) return "";
    if (pymcl_ieq(label, "全部") || pymcl_ieq(label, "all") || pymcl_ieq(label, "any"))
        return "";
    for (size_t i = 0; i < sizeof(aliases) / sizeof(aliases[0]); i++)
        if (strcmp(label, aliases[i].alias) == 0) return aliases[i].key;
    return label; /* UI 也可能直接传 canonical key */
}

static int filter_mr_cats(const char *key, const char *cats[2]) {
    static const struct { const char *key; const char *a; const char *b; } t[] = {
        {"optimization", "optimization", NULL}, {"technology", "technology", NULL},
        {"magic", "magic", NULL}, {"adventure", "adventure", NULL},
        {"survival", "utility", "food"}, {"decoration", "decoration", NULL},
        {"realistic", "realistic", NULL}, {"cartoon", "cartoon", NULL},
        {"performance", "performance", NULL}, {"path-tracing", "path-tracing", "pbr"},
        {"16x", "16x", NULL}, {"32x", "32x", NULL}, {"64x", "64x", NULL},
        {"modern", "modern", NULL}, {"animated", "animated", NULL},
        {"skyblock", "skyblock", NULL}, {"creation", "creation", NULL},
    };
    cats[0] = cats[1] = NULL;
    if (!key || !key[0]) return 0;
    for (size_t i = 0; i < sizeof(t) / sizeof(t[0]); i++) {
        if (strcmp(key, t[i].key) == 0) {
            cats[0] = t[i].a;
            cats[1] = t[i].b;
            return 1;
        }
    }
    cats[0] = key; /* 未知 key 原样当分类用，与 Python 兜底一致 */
    return 1;
}

static int filter_cf_tokens(const char *key, const char *toks[3]) {
    static const struct { const char *key; const char *a; const char *b; const char *c; } t[] = {
        {"optimization", "performance", "optimization", NULL},
        {"technology", "technology", "tech", NULL},
        {"magic", "magic", NULL, NULL}, {"adventure", "adventure", NULL, NULL},
        {"survival", "survival", "util", "food"}, {"decoration", "decor", NULL, NULL},
        {"realistic", "realistic", NULL, NULL}, {"cartoon", "cartoon", "animated", NULL},
        {"performance", "performance", NULL, NULL},
        {"path-tracing", "path tracing", "shader", NULL},
        {"16x", "16x", NULL, NULL}, {"32x", "32x", NULL, NULL}, {"64x", "64x", NULL, NULL},
        {"modern", "modern", NULL, NULL}, {"animated", "animated", NULL, NULL},
        {"skyblock", "skyblock", NULL, NULL}, {"creation", "creation", NULL, NULL},
    };
    toks[0] = toks[1] = toks[2] = NULL;
    if (!key || !key[0]) return 0;
    for (size_t i = 0; i < sizeof(t) / sizeof(t[0]); i++) {
        if (strcmp(key, t[i].key) == 0) {
            toks[0] = t[i].a;
            toks[1] = t[i].b;
            toks[2] = t[i].c;
            return 1;
        }
    }
    toks[0] = key;
    return 1;
}

/* CF 的 categoryFilter 不稳定（与 Python 端一致），拉回结果后按分类展示名过滤。 */
static int cf_item_matches_cat(cJSON *m, const char *toks[3]) {
    if (!toks[0]) return 1;
    cJSON *c;
    cJSON_ArrayForEach(c, cJSON_GetObjectItem(m, "categories")) {
        const char *nm = cJSON_GetStringValue(cJSON_GetObjectItem(c, "name"));
        if (!nm) continue;
        for (int i = 0; i < 3 && toks[i]; i++)
            if (pymcl_icontains(nm, toks[i])) return 1;
    }
    return 0;
}

static const char *extra_str(cJSON *extra, const char *key, const char *def) {
    const char *s = cJSON_GetStringValue(cJSON_GetObjectItem(extra, key));
    return (s && s[0]) ? s : def;
}

static const char *extra_game_version(cJSON *extra) {
    const char *gv = extra_str(extra, "game_version", extra_str(extra, "version", ""));
    /* “全部版本”这类占位选项等于不过滤；带引号的怪值会破坏 facets JSON，同样丢弃 */
    if (pymcl_startswith(gv, "全部") || strchr(gv, '"') || strchr(gv, '\\'))
        return "";
    return gv;
}

static const char *extra_type_key(cJSON *extra) {
    return filter_type_key(extra_str(extra, "category", extra_str(extra, "type", "")));
}

static cJSON *mr_search(const char *query, const char *ptype, int limit,
                        const char *gv, const char *catkey) {
    char enc[512];
    url_enc(query ? query : "", enc, sizeof(enc));
    char facets[512];
    size_t off = (size_t)snprintf(facets, sizeof(facets), "[[\"project_type:%s\"]", ptype);
    if (gv && gv[0] && off < sizeof(facets))
        off += (size_t)snprintf(facets + off, sizeof(facets) - off, ",[\"versions:%s\"]", gv);
    const char *cats[2];
    if (catkey && catkey[0] && filter_mr_cats(catkey, cats) && off < sizeof(facets)) {
        off += (size_t)snprintf(facets + off, sizeof(facets) - off, ",[\"categories:%s\"", cats[0]);
        if (cats[1] && off < sizeof(facets))
            off += (size_t)snprintf(facets + off, sizeof(facets) - off, ",\"categories:%s\"", cats[1]);
        if (off < sizeof(facets))
            off += (size_t)snprintf(facets + off, sizeof(facets) - off, "]");
    }
    if (off < sizeof(facets))
        snprintf(facets + off, sizeof(facets) - off, "]");
    char fenc[1024];
    url_enc(facets, fenc, sizeof(fenc));
    char url1[2048], url2[2048];
    snprintf(url1, sizeof(url1), MODRINTH_API "/search?query=%s&limit=%d&index=relevance&facets=%s",
             enc[0] ? enc : "%20", limit, fenc);
    snprintf(url2, sizeof(url2), MCIM_MIRROR "/modrinth/v2/search?query=%s&limit=%d&index=relevance&facets=%s",
             enc[0] ? enc : "%20", limit, fenc);
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

/* CF 搜索共用：带 gameVersion 参数与分类客户端过滤（对齐 mclauncher.mods.search_curseforge）。 */
static void cf_search_into(cJSON *out, int class_id, const char *q, int limit,
                           const char *gv, const char *toks[3]) {
    char qenc[512], gvenc[128], queryp[1024];
    url_enc(q ? q : "", qenc, sizeof(qenc));
    url_enc(gv ? gv : "", gvenc, sizeof(gvenc));
    int page = toks[0] ? limit * 2 : limit;
    size_t off = (size_t)snprintf(queryp, sizeof(queryp),
                                  "gameId=432&classId=%d&sortField=2&pageSize=%d&index=0",
                                  class_id, page);
    if (qenc[0] && off < sizeof(queryp))
        off += (size_t)snprintf(queryp + off, sizeof(queryp) - off, "&searchFilter=%s", qenc);
    if (gvenc[0] && off < sizeof(queryp))
        snprintf(queryp + off, sizeof(queryp) - off, "&gameVersion=%s", gvenc);
    cJSON *d = cf_get("/mods/search", queryp);
    cJSON *items = cf_items(d);
    cJSON *m;
    int kept = 0;
    cJSON_ArrayForEach(m, items) {
        if (kept >= limit) break;
        if (!cf_item_matches_cat(m, toks)) continue;
        cJSON_AddItemToArray(out, row_from_cf(m));
        kept++;
    }
    cJSON_Delete(d);
}

cJSON *search_mods(const char *query, const char *source, cJSON *extra) {
    const char *q = query ? query : "";
    const char *gv = extra_game_version(extra);
    const char *catkey = extra_type_key(extra);
    const char *toks[3];
    filter_cf_tokens(catkey, toks);
    int has_filter = (gv[0] || catkey[0]);
    int want_cf = 0, want_mr = 1;
    if (source && (pymcl_ieq(source, "全部") || pymcl_ieq(source, "all") || !source[0])) { want_cf = 1; want_mr = 1; }
    else if (source && pymcl_startswith(source, "curse")) { want_cf = 1; want_mr = 0; }
    else { want_mr = 1; want_cf = 0; }
    if (!q[0]) return catalog_popular_mods(source);
    cJSON *out = cJSON_CreateArray();
    /* 别名直达只在没开筛选时用：直达命中无法应用版本/分类过滤，
       用户点了筛选就得走真搜索，不然筛选框等于摆设。 */
    if (!has_filter) {
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
    }
    if (want_mr) {
        cJSON *j = mr_search(q, "mod", 30, gv, catkey);
        cJSON *hits = j ? cJSON_GetObjectItem(j, "hits") : NULL;
        cJSON *h;
        cJSON_ArrayForEach(h, hits) cJSON_AddItemToArray(out, row_from_mr_hit(h));
        cJSON_Delete(j);
    }
    if (want_cf)
        cf_search_into(out, CF_CLASS_MOD, q, 30, gv, toks);
    return out;
}

cJSON *search_modpacks(const char *query, const char *source, cJSON *extra) {
    const char *q = query ? query : "";
    const char *gv = extra_game_version(extra);
    const char *catkey = extra_type_key(extra);
    const char *toks[3];
    filter_cf_tokens(catkey, toks);
    int has_filter = (gv[0] || catkey[0]);
    if (!q[0]) return catalog_popular_packs(source);
    int want_cf = 1, want_mr = 1;
    if (source && pymcl_startswith(source, "curse")) want_mr = 0;
    if (source && pymcl_ieq(source, "modrinth")) want_cf = 0;
    cJSON *out = cJSON_CreateArray();
    if (!has_filter) {
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
    }
    if (want_mr) {
        cJSON *j = mr_search(q, "modpack", 25, gv, catkey);
        cJSON *hits = j ? cJSON_GetObjectItem(j, "hits") : NULL;
        cJSON *h;
        cJSON_ArrayForEach(h, hits) cJSON_AddItemToArray(out, row_from_mr_hit(h));
        cJSON_Delete(j);
    }
    if (want_cf)
        cf_search_into(out, CF_CLASS_MODPACK, q, 25, gv, toks);
    return out;
}

cJSON *search_content(const char *kind, const char *query, const char *source, cJSON *extra) {
    const char *mr = "mod";
    int cf = CF_CLASS_MOD;
    if (strcmp(kind, "shader") == 0) { mr = "shader"; cf = CF_CLASS_SHADER; }
    else if (strcmp(kind, "resourcepack") == 0) { mr = "resourcepack"; cf = CF_CLASS_RESOURCEPACK; }
    else if (strcmp(kind, "datapack") == 0) { mr = "datapack"; cf = CF_CLASS_DATAPACK; }
    const char *gv = extra_game_version(extra);
    const char *catkey = extra_type_key(extra);
    const char *toks[3];
    filter_cf_tokens(catkey, toks);
    int want_mr = 1, want_cf = 1;
    if (source && pymcl_startswith(source, "curse")) want_mr = 0;
    if (source && pymcl_ieq(source, "modrinth")) want_cf = 0;
    cJSON *out = cJSON_CreateArray();
    if (want_mr) {
        cJSON *j = mr_search(query, mr, 30, gv, catkey);
        cJSON *hits = j ? cJSON_GetObjectItem(j, "hits") : NULL;
        cJSON *h;
        cJSON_ArrayForEach(h, hits) cJSON_AddItemToArray(out, row_from_mr_hit(h));
        cJSON_Delete(j);
    }
    if (want_cf)
        cf_search_into(out, cf, query ? query : "", 30, gv, toks);
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
    cJSON *chosen = cJSON_GetArrayItem(vers, 0);
    cJSON *v;
    cJSON_ArrayForEach(v, vers) {
        int ok_mc = !mc, ok_ld = !loader;
        cJSON *gv, *ld;
        cJSON_ArrayForEach(gv, cJSON_GetObjectItem(v, "game_versions"))
            if (mc && cJSON_IsString(gv) && strcmp(gv->valuestring, mc) == 0) ok_mc = 1;
        cJSON_ArrayForEach(ld, cJSON_GetObjectItem(v, "loaders"))
            if (loader && cJSON_IsString(ld) && pymcl_ieq(ld->valuestring, loader)) ok_ld = 1;
        if (ok_mc && ok_ld) { chosen = v; break; }
    }
    free(mc);
    cJSON *files = cJSON_GetObjectItem(chosen, "files");
    cJSON *file = NULL, *f;
    cJSON_ArrayForEach(f, files) if (cJSON_IsTrue(cJSON_GetObjectItem(f, "primary"))) file = f;
    if (!file && cJSON_GetArraySize(files) > 0) file = cJSON_GetArrayItem(files, 0);
    if (!file) { cJSON_Delete(vers); pymcl_set_error("没有可下载文件"); return -1; }
    const char *fn = cJSON_GetStringValue(cJSON_GetObjectItem(file, "filename"));
    const char *u = cJSON_GetStringValue(cJSON_GetObjectItem(file, "url"));
    char dest[PYMCL_PATH], ip[PYMCL_PATH];
    instance_path(inst, ip, sizeof(ip));
    instance_ensure_dirs(inst);
    pymcl_path_join3(dest, sizeof(dest), ip, "mods", fn ? fn : "mod.jar");
    char mir[1024];
    mirror_mr(u, mir, sizeof(mir));
    const char *ex[] = { u };
    int r = download_file(mir, ex, 1, dest, ctx,
                          cJSON_GetStringValue(cJSON_GetObjectItem(cJSON_GetObjectItem(file, "hashes"), "sha1")),
                          -1,
                          cJSON_GetStringValue(cJSON_GetObjectItem(cJSON_GetObjectItem(file, "hashes"), "sha512")));
    cJSON_Delete(vers);
    return r;
}

static void cf_cdn(long long fid, const char *fn, const char *host, char *out, size_t n) {
    snprintf(out, n, "https://%s/files/%lld/%lld/%s", host, fid / 1000, fid % 1000, fn ? fn : "file.jar");
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
    cJSON *f = cJSON_GetArrayItem(files, 0);
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
    int r = download_file(first, ex, ne, dest, ctx, NULL, -1, NULL);
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
