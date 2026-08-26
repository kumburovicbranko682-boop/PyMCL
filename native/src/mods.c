#include "pymcl.h"

#define CF_MCIM_BASE MCIM_MIRROR "/curseforge/v1"

static void cf_hdr(char *out, size_t n) {
    const char *key = config_str("curseforge_api_key", "");
    if (key && key[0]) snprintf(out, n, "Accept: application/json\nx-api-key: %s", key);
    else snprintf(out, n, "Accept: application/json");
}

/* CurseForge API 基址顺序，对齐 mclauncher/source.py cf_api_bases()：
 * auto=官方在前、MCIM 兜底；mcim=镜像在前、官方垫底；official=仅官方。
 * 以前固定官方→MCIM→BMCLAPI：选「仅官方」照样打镜像，「MCIM 优先」
 * 却每次先等官方超时；BMCLAPI /curseforge/v1 已 404（Python 侧已移除）。 */
static int cf_bases_ordered(const char **bases) {
    if (config_community_official_only()) {
        bases[0] = CF_OFFICIAL;
        return 1;
    }
    if (config_community_mirror_first()) {
        bases[0] = CF_MCIM_BASE;
        bases[1] = CF_OFFICIAL;
    } else {
        bases[0] = CF_OFFICIAL;
        bases[1] = CF_MCIM_BASE;
    }
    return 2;
}

cJSON *cf_api_get(const char *path, const char *query) {
    char hdr[512]; cf_hdr(hdr, sizeof(hdr));
    const char *bases[2];
    int nb = cf_bases_ordered(bases);
    for (int i = 0; i < nb; i++) {
        char url[1024];
        if (query && query[0])
            snprintf(url, sizeof(url), "%s%s?%s", bases[i], path, query);
        else
            snprintf(url, sizeof(url), "%s%s", bases[i], path);
        cJSON *j = http_get_json_hdr(url, hdr, 45);
        if (j) return j;
    }
    return NULL;
}

/* Modrinth v2 API GET，候选链对齐 mclauncher/source.py modrinth_api_bases()。
 * 以前除全文搜索外全部只打官方 API：Modrinth 不可达时搜索靠镜像兜底
 * 还能出结果，点「安装」却必失败——纯 C 桥下装模组/光影/整合包全挂。 */
cJSON *mr_api_get(const char *path_query, int timeout) {
    char off[1280], mir[1280];
    snprintf(off, sizeof(off), MODRINTH_API "%s", path_query);
    snprintf(mir, sizeof(mir), MCIM_MIRROR "/modrinth/v2%s", path_query);
    if (config_community_official_only())
        return http_get_json(off, timeout);
    const char *first = config_community_mirror_first() ? mir : off;
    const char *second = config_community_mirror_first() ? off : mir;
    cJSON *j = http_get_json(first, timeout);
    if (!j) j = http_get_json(second, timeout);
    return j;
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

/* percent-encode a query-string value (unreserved chars pass through) */
static void urlenc(const char *s, char *enc, size_t n) {
    size_t o = 0;
    enc[0] = 0;
    for (s = s ? s : ""; *s && o + 4 < n; s++) {
        unsigned char c = (unsigned char)*s;
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~')
            enc[o++] = (char)c;
        else { snprintf(enc + o, n - o, "%%%02X", c); o = strlen(enc); }
    }
    enc[o] = 0;
}

/* Modrinth 文件下载顺序对齐 mclauncher/source.py：auto=官方优先、MCIM 兜底；
 * mcim=镜像优先（官方垫底）；official=仅官方。以前 C 桥固定镜像优先，
 * Qt 设置页选「仅官方」后换到桥接 UI 照旧先走 MCIM。 */
static int mr_download(const char *url, const char *dest, pymcl_ctx *ctx,
                       const char *sha1, const char *sha512) {
    char mir[1024];
    mirror_mr(url, mir, sizeof(mir));
    if (config_community_official_only() || strcmp(mir, url) == 0)
        return download_file(url, NULL, 0, dest, ctx, sha1, -1, sha512);
    if (config_community_mirror_first()) {
        const char *ex[] = { url };
        return download_file(mir, ex, 1, dest, ctx, sha1, -1, sha512);
    }
    const char *ex[] = { mir };
    return download_file(url, ex, 1, dest, ctx, sha1, -1, sha512);
}

static cJSON *mr_search(const char *query, const char *ptype, int limit) {
    char enc[512];
    urlenc(query, enc, sizeof(enc));
    /* encode facets brackets */
    char pq[1024];
    snprintf(pq, sizeof(pq), "/search?query=%s&limit=%d&index=relevance&facets=%%5B%%5B%%22project_type%%3A%s%%22%%5D%%5D",
             enc[0] ? enc : "%20", limit, ptype);
    return mr_api_get(pq, 45);
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
    /* 别名只有 title（如 OptiFine 不在 Modrinth，catalog.json 故意不给 slug）：
     * 对齐 mclauncher/mods.py 的 fallback_q——拿 title 当全文搜索关键词。
     * 以前 C 桥继续用中文原词全文搜索，"高清修复/考古/经验书" 一律空结果。 */
    const char *ftq = (title[0] && !slug[0] && !cf) ? title : q;
    if (slug[0] && want_mr) {
        char pq[256]; snprintf(pq, sizeof(pq), "/project/%s", slug);
        cJSON *p = mr_api_get(pq, 30);
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
        cJSON *d = cf_api_get(path, NULL);
        cJSON *m = d ? cJSON_GetObjectItem(d, "data") : NULL;
        if (cJSON_IsObject(m)) cJSON_AddItemToArray(out, row_from_cf(m));
        cJSON_Delete(d);
    }
    if (cJSON_GetArraySize(out) > 0) return out;
    if (want_mr) {
        cJSON *j = mr_search(ftq, "mod", 30);
        cJSON *hits = j ? cJSON_GetObjectItem(j, "hits") : NULL;
        cJSON *h;
        cJSON_ArrayForEach(h, hits) cJSON_AddItemToArray(out, row_from_mr_hit(h));
        cJSON_Delete(j);
    }
    if (want_cf) {
        /* searchFilter 必须编码：裸空格/中文会让请求行非法，CF 全文直接失败 */
        char enc[512], queryp[1024];
        urlenc(ftq, enc, sizeof(enc));
        snprintf(queryp, sizeof(queryp), "gameId=432&classId=%d&sortField=2&pageSize=30&index=0&searchFilter=%s",
                 CF_CLASS_MOD, enc);
        cJSON *d = cf_api_get("/mods/search", queryp);
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
    /* slug/cf 都缺的别名：title 顶上做全文搜索（对齐 mclauncher/modpack.py）。 */
    const char *ftq = (title[0] && !slug[0] && !cf) ? title : q;
    if (cf && want_cf) {
        char path[64]; snprintf(path, sizeof(path), "/mods/%lld", cf);
        cJSON *d = cf_api_get(path, NULL);
        cJSON *m = d ? cJSON_GetObjectItem(d, "data") : NULL;
        if (cJSON_IsObject(m)) cJSON_AddItemToArray(out, row_from_cf(m));
        cJSON_Delete(d);
    }
    if (slug[0] && want_mr) {
        char pq[256]; snprintf(pq, sizeof(pq), "/project/%s", slug);
        cJSON *p = mr_api_get(pq, 30);
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
        cJSON *j = mr_search(ftq, "modpack", 25);
        cJSON *hits = j ? cJSON_GetObjectItem(j, "hits") : NULL;
        cJSON *h;
        cJSON_ArrayForEach(h, hits) cJSON_AddItemToArray(out, row_from_mr_hit(h));
        cJSON_Delete(j);
    }
    if (want_cf) {
        char enc[512], queryp[1024];
        urlenc(ftq, enc, sizeof(enc));
        snprintf(queryp, sizeof(queryp), "gameId=432&classId=%d&sortField=2&pageSize=25&index=0&searchFilter=%s",
                 CF_CLASS_MODPACK, enc);
        cJSON *d = cf_api_get("/mods/search", queryp);
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
        char enc[512], queryp[1024];
        urlenc(query, enc, sizeof(enc));
        snprintf(queryp, sizeof(queryp), "gameId=432&classId=%d&sortField=2&pageSize=30&index=0%s%s",
                 cf, enc[0] ? "&searchFilter=" : "", enc);
        cJSON *d = cf_api_get("/mods/search", queryp);
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

static int install_modrinth_mod(const char *inst, const char *slug, pymcl_ctx *ctx) {
    char pq[256];
    snprintf(pq, sizeof(pq), "/project/%s/version", slug);
    cJSON *vers = mr_api_get(pq, 45);
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
    int r = mr_download(u, dest, ctx,
                        cJSON_GetStringValue(cJSON_GetObjectItem(cJSON_GetObjectItem(file, "hashes"), "sha1")),
                        cJSON_GetStringValue(cJSON_GetObjectItem(cJSON_GetObjectItem(file, "hashes"), "sha512")));
    cJSON_Delete(vers);
    return r;
}

/* 文件名段编码，对齐 Python quote(filename, safe="._-+()[]")：
 * CDN 路径里的空格/中文不编码会让请求行非法，整条候选直接失败。 */
static void cf_enc_name(const char *s, char *enc, size_t n) {
    size_t o = 0;
    enc[0] = 0;
    for (s = s ? s : ""; *s && o + 4 < n; s++) {
        unsigned char c = (unsigned char)*s;
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')
            || strchr("._-+()[]", (char)c))
            enc[o++] = (char)c;
        else { snprintf(enc + o, n - o, "%%%02X", c); o = strlen(enc); }
    }
    enc[o] = 0;
}

static void add_url_once(cJSON *arr, const char *u) {
    if (!u || !u[0]) return;
    cJSON *it;
    cJSON_ArrayForEach(it, arr)
        if (cJSON_IsString(it) && strcmp(it->valuestring, u) == 0) return;
    cJSON_AddItemToArray(arr, cJSON_CreateString(u));
}

/* CurseForge 文件下载候选，对齐 mclauncher/mods.py cf_mod_download_urls：
 * downloadUrl → CDN 直链（含 MCIM 镜像）→ 官方 API download → MCIM download
 * → 官网内部 API。以前 C 桥没有任何 MCIM 兜底：forgecdn 被墙时下载必败，
 * 而同一台机器换 Python 桥就能装上。 */
cJSON *cf_file_urls(long long addon_id, long long file_id,
                    const char *filename, const char *download_url) {
    cJSON *urls = cJSON_CreateArray();
    add_url_once(urls, download_url);
    if (filename && filename[0]) {
        char fn[512];
        cf_enc_name(filename, fn, sizeof(fn));
        const char *hosts[] = { "mediafilez.forgecdn.net", "edge.forgecdn.net" };
        for (int i = 0; i < 2; i++) {
            char cdn[1024], mir[1024];
            snprintf(cdn, sizeof(cdn), "https://%s/files/%lld/%lld/%s",
                     hosts[i], file_id / 1000, file_id % 1000, fn);
            add_url_once(urls, cdn);
            snprintf(mir, sizeof(mir), MCIM_MIRROR "/files/%lld/%lld/%s",
                     file_id / 1000, file_id % 1000, fn);
            add_url_once(urls, mir);
        }
    }
    char api[256];
    snprintf(api, sizeof(api), CF_OFFICIAL "/mods/%lld/files/%lld/download", addon_id, file_id);
    add_url_once(urls, api);
    snprintf(api, sizeof(api), CF_MCIM_BASE "/mods/%lld/files/%lld/download", addon_id, file_id);
    add_url_once(urls, api);
    snprintf(api, sizeof(api), "https://www.curseforge.com/api/v1/mods/%lld/files/%lld/download",
             addon_id, file_id);
    add_url_once(urls, api);
    return urls;
}

/* 批量查文件元数据 POST /v1/mods/files，返回 {"<fileId>": file}。
 * 对齐 mclauncher/mods.py cf_files_by_ids（每批 50 个）。 */
cJSON *cf_files_by_ids(cJSON *file_ids) {
    cJSON *out = cJSON_CreateObject();
    int n = cJSON_IsArray(file_ids) ? cJSON_GetArraySize(file_ids) : 0;
    if (n == 0) return out;
    char hdr[512]; cf_hdr(hdr, sizeof(hdr));
    const char *bases[2];
    int nb = cf_bases_ordered(bases);
    for (int i = 0; i < n; i += 50) {
        cJSON *body = cJSON_CreateObject();
        cJSON *ids = cJSON_CreateArray();
        for (int k = i; k < n && k < i + 50; k++)
            cJSON_AddItemToArray(ids, cJSON_Duplicate(cJSON_GetArrayItem(file_ids, k), 1));
        cJSON_AddItemToObject(body, "fileIds", ids);
        char *payload = cJSON_PrintUnformatted(body);
        cJSON_Delete(body);
        if (!payload) continue;
        cJSON *data = NULL;
        for (int b = 0; b < nb && !data; b++) {
            char url[256]; snprintf(url, sizeof(url), "%s/mods/files", bases[b]);
            http_resp r;
            if (http_post_json(url, payload, &r, hdr, 45) == 0 && r.body)
                data = cJSON_Parse(r.body);
            http_resp_free(&r);
        }
        free(payload);
        cJSON *f;
        cJSON_ArrayForEach(f, cf_items(data)) {
            cJSON *fid = cJSON_GetObjectItem(f, "id");
            if (!cJSON_IsNumber(fid)) continue;
            char key[32]; snprintf(key, sizeof(key), "%lld", (long long)fid->valuedouble);
            cJSON_DeleteItemFromObject(out, key);
            cJSON_AddItemToObject(out, key, cJSON_Duplicate(f, 1));
        }
        cJSON_Delete(data);
    }
    return out;
}

static int install_cf_mod(const char *inst, long long addon, pymcl_ctx *ctx) {
    char path[64]; snprintf(path, sizeof(path), "/mods/%lld", addon);
    cJSON *d = cf_api_get(path, NULL);
    cJSON *mod = d ? cJSON_GetObjectItem(d, "data") : NULL;
    if (!cJSON_IsObject(mod)) { cJSON_Delete(d); pymcl_set_error("获取 CurseForge 详情失败"); return -1; }
    cJSON *files = cJSON_GetObjectItem(mod, "latestFiles");
    if (!cJSON_IsArray(files) || cJSON_GetArraySize(files) == 0) {
        char p2[80]; snprintf(p2, sizeof(p2), "/mods/%lld/files", addon);
        cJSON *fl = cf_api_get(p2, "pageSize=50");
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
    cJSON *urls = cf_file_urls(addon, fid, fn, du);
    int r = download_url_list(urls, dest, ctx, NULL, -1, NULL);
    cJSON_Delete(urls);
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
        long long id = (long long)cJSON_GetObjectItem(extra, "id")->valuedouble;
        char pth[64]; snprintf(pth, sizeof(pth), "/mods/%lld", id);
        cJSON *d = cf_api_get(pth, NULL);
        cJSON *mod = d ? cJSON_GetObjectItem(d, "data") : NULL;
        cJSON *files = mod ? cJSON_GetObjectItem(mod, "latestFiles") : NULL;
        cJSON *f = files && cJSON_GetArraySize(files) ? cJSON_GetArrayItem(files, 0) : NULL;
        if (!f) { cJSON_Delete(d); pymcl_set_error("没有可下载文件"); return -1; }
        long long fid = (long long)cJSON_GetNumberValue(cJSON_GetObjectItem(f, "id"));
        const char *fn = cJSON_GetStringValue(cJSON_GetObjectItem(f, "fileName")) ?: "pack.zip";
        const char *du = cJSON_GetStringValue(cJSON_GetObjectItem(f, "downloadUrl"));
        pymcl_path_join3(dest, sizeof(dest), ip, subdir, fn);
        /* 以前只认 downloadUrl：CF 该字段经常为 null，这里稳定报
         * 「HTTP 失败」，同一资源在 Python 桥能装（CDN/镜像候选兜底）。 */
        cJSON *urls = cf_file_urls(id, fid, fn, du);
        int r = download_url_list(urls, dest, ctx, NULL, -1, NULL);
        cJSON_Delete(urls);
        cJSON_Delete(d);
        return r;
    }
    const char *slug = extra ? cJSON_GetStringValue(cJSON_GetObjectItem(extra, "slug")) : NULL;
    if (!slug) slug = name;
    char pq[256];
    snprintf(pq, sizeof(pq), "/project/%s/version", slug);
    cJSON *vers = mr_api_get(pq, 45);
    if (!cJSON_IsArray(vers) || cJSON_GetArraySize(vers) == 0) {
        cJSON_Delete(vers); pymcl_set_error("%s 没有可下载版本", slug); return -1;
    }
    cJSON *files = cJSON_GetObjectItem(cJSON_GetArrayItem(vers, 0), "files");
    cJSON *file = cJSON_GetArraySize(files) ? cJSON_GetArrayItem(files, 0) : NULL;
    const char *fn = file ? cJSON_GetStringValue(cJSON_GetObjectItem(file, "filename")) : "pack.zip";
    const char *du = file ? cJSON_GetStringValue(cJSON_GetObjectItem(file, "url")) : NULL;
    pymcl_path_join3(dest, sizeof(dest), ip, subdir, fn);
    int r = du ? mr_download(du, dest, ctx, NULL, NULL) : -1;
    if (!du) pymcl_set_error("%s 没有可下载文件", slug);
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
