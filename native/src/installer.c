#include "pymcl.h"

static void note(pymcl_ctx *ctx, const char *msg) {
    pymcl_log("%s", msg);
    if (ctx && ctx->on_progress) ctx->on_progress(ctx->ud, msg, 0, 1);
    if (ctx && ctx->on_log) ctx->on_log(ctx->ud, msg);
}
static int cancelled(pymcl_ctx *ctx) {
    return ctx && ctx->cancel && ctx->cancel(ctx->ud);
}
static cJSON *load_parent_inst(const char *pid, void *ud) {
    const char *inst = (const char *)ud;
    cJSON *local = instance_version_json(inst, pid);
    if (local) return local;
    return manifest_get_version(pid, 0);
}

cJSON *instance_resolved_version(const char *name, const char *vid) {
    cJSON *vj = instance_version_json(name, vid);
    if (!vj) return NULL;
    cJSON *r = manifest_resolve_inherits(vj, load_parent_inst, (void *)name);
    cJSON_Delete(vj);
    return r;
}

static void subst_arch(char *key) {
    char tok[8]; pymcl_native_arch_token(tok, sizeof(tok));
    char *p = strstr(key, "${arch}");
    if (!p) return;
    char tmp[256];
    snprintf(tmp, sizeof(tmp), "%.*s%s%s", (int)(p - key), key, tok, p + 7);
    snprintf(key, 256, "%s", tmp);
}

char *select_native_classifier(cJSON *lib) {
    cJSON *cls = cJSON_GetObjectItem(cJSON_GetObjectItem(lib, "downloads"), "classifiers");
    cJSON *nmap = cJSON_GetObjectItem(lib, "natives");
    const char *wanted = cJSON_GetStringValue(cJSON_GetObjectItem(nmap, pymcl_os_name()));
    char want[256] = {0};
    if (wanted) { snprintf(want, sizeof(want), "%s", wanted); subst_arch(want); }
    if (want[0] && cls && cJSON_GetObjectItem(cls, want)) return pymcl_strdup(want);
    if (cJSON_IsObject(cls)) {
        cJSON *k;
        cJSON_ArrayForEach(k, cls) {
            char sk[256]; snprintf(sk, sizeof(sk), "%s", k->string); subst_arch(sk);
            if (strcmp(sk, want) == 0) return pymcl_strdup(sk);
        }
    }
    if (want[0] && (!cls || !cJSON_IsObject(cls))) return pymcl_strdup(want);
    /* score natives-* */
    char best[256] = {0}; int bests = -99;
    if (cJSON_IsObject(cls)) {
        cJSON *k;
        cJSON_ArrayForEach(k, cls) {
            char sk[256]; snprintf(sk, sizeof(sk), "%s", k->string); subst_arch(sk);
            if (!pymcl_startswith(sk, "natives-")) continue;
            const char *rest = sk + 8;
            int s = 0;
            if (pymcl_startswith(rest, "windows")) s += 2;
            char tok[8]; pymcl_native_arch_token(tok, sizeof(tok));
            if (pymcl_endswith(rest, tok) || strstr(rest, pymcl_arch())) s += 3;
            else if (pymcl_endswith(rest, "-32") || pymcl_endswith(rest, "-64") || pymcl_endswith(rest, "-x86")) s -= 2;
            if (s > bests) { bests = s; snprintf(best, sizeof(best), "%s", sk); }
        }
    }
    if (best[0] && bests > 0) return pymcl_strdup(best);
    return want[0] ? pymcl_strdup(want) : NULL;
}

int natives_present(const char *dir) {
    if (!pymcl_dir_exists(dir)) return 0;
    wchar_t *w = pymcl_u8_to_wide(dir);
    wchar_t pat[PYMCL_PATH];
    _snwprintf(pat, PYMCL_PATH, L"%s\\*", w);
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pat, &fd);
    free(w);
    if (h == INVALID_HANDLE_VALUE) return 0;
    int ok = 0;
    do {
        char *n = pymcl_wide_to_u8(fd.cFileName);
        if (pymcl_startswith(n, "lwjgl") || pymcl_startswith(n, "liblwjgl")) ok = 1;
        free(n);
    } while (!ok && FindNextFileW(h, &fd));
    FindClose(h);
    return ok;
}

static const char *lib_base_url(cJSON *lib) {
    const char *u = cJSON_GetStringValue(cJSON_GetObjectItem(lib, "url"));
    if (u && pymcl_startswith(u, "http://files.minecraftforge.net/maven"))
        return "https://maven.minecraftforge.net/";
    return u && u[0] ? u : MOJANG_LIBS;
}

static int install_libraries(const char *inst, cJSON *resolved, const char *vid, pymcl_ctx *ctx) {
    char libs[PYMCL_PATH], nat[PYMCL_PATH];
    instance_libraries_dir(inst, libs, sizeof(libs));
    instance_natives_dir(inst, vid, resolved, nat, sizeof(nat));
    pymcl_ensure_dir(nat);
    cJSON *tasks = cJSON_CreateArray();
    typedef struct { char jar[PYMCL_PATH]; cJSON *ex; } nat_t;
    nat_t nats[256]; int nn = 0;
    cJSON *lib;
    cJSON_ArrayForEach(lib, cJSON_GetObjectItem(resolved, "libraries")) {
        if (cJSON_IsFalse(cJSON_GetObjectItem(lib, "clientreq"))) continue;
        if (!pymcl_check_rules(cJSON_GetObjectItem(lib, "rules"), 0)) continue;
        const char *name = cJSON_GetStringValue(cJSON_GetObjectItem(lib, "name"));
        if (!name) continue;
        cJSON *dl = cJSON_GetObjectItem(lib, "downloads");
        cJSON *art = cJSON_GetObjectItem(dl, "artifact");
        if (art && cJSON_GetStringValue(cJSON_GetObjectItem(art, "url"))) {
            const char *path = cJSON_GetStringValue(cJSON_GetObjectItem(art, "path"));
            char rel[512];
            if (path) snprintf(rel, sizeof(rel), "%s", path);
            else pymcl_maven_path(name, "jar", rel, sizeof(rel));
            pymcl_replace_char(rel, '/', '\\');
            char dest[PYMCL_PATH];
            pymcl_path_join(dest, sizeof(dest), libs, rel);
            cJSON *t = cJSON_CreateObject();
            cJSON *urls = cJSON_CreateArray();
            cJSON_AddItemToArray(urls, cJSON_CreateString(cJSON_GetStringValue(cJSON_GetObjectItem(art, "url"))));
            cJSON_AddItemToObject(t, "urls", urls);
            cJSON_AddStringToObject(t, "dest", dest);
            if (cJSON_GetStringValue(cJSON_GetObjectItem(art, "sha1")))
                cJSON_AddStringToObject(t, "sha1", cJSON_GetStringValue(cJSON_GetObjectItem(art, "sha1")));
            if (cJSON_IsNumber(cJSON_GetObjectItem(art, "size")))
                cJSON_AddNumberToObject(t, "size", cJSON_GetObjectItem(art, "size")->valuedouble);
            cJSON_AddItemToArray(tasks, t);
        } else if (!dl && !cJSON_GetObjectItem(lib, "natives")) {
            char rel[512]; pymcl_maven_path(name, "jar", rel, sizeof(rel));
            char dest[PYMCL_PATH];
            char relw[512]; snprintf(relw, sizeof(relw), "%s", rel); pymcl_replace_char(relw, '/', '\\');
            pymcl_path_join(dest, sizeof(dest), libs, relw);
            char url[1024];
            snprintf(url, sizeof(url), "%s%s", lib_base_url(lib), rel);
            cJSON *t = cJSON_CreateObject();
            cJSON *urls = cJSON_CreateArray();
            cJSON_AddItemToArray(urls, cJSON_CreateString(url));
            cJSON_AddItemToObject(t, "urls", urls);
            cJSON_AddStringToObject(t, "dest", dest);
            cJSON_AddItemToArray(tasks, t);
        }
        char *nkey = select_native_classifier(lib);
        if (nkey) {
            cJSON *classifiers = cJSON_GetObjectItem(dl, "classifiers");
            cJSON *entry = classifiers ? cJSON_GetObjectItem(classifiers, nkey) : NULL;
            char rel[512];
            const char *path = entry ? cJSON_GetStringValue(cJSON_GetObjectItem(entry, "path")) : NULL;
            if (path) snprintf(rel, sizeof(rel), "%s", path);
            else {
                char spec[512]; snprintf(spec, sizeof(spec), "%s:%s", name, nkey);
                pymcl_maven_path(spec, "jar", rel, sizeof(rel));
            }
            char relw[512]; snprintf(relw, sizeof(relw), "%s", rel); pymcl_replace_char(relw, '/', '\\');
            char dest[PYMCL_PATH];
            pymcl_path_join(dest, sizeof(dest), libs, relw);
            const char *url = entry ? cJSON_GetStringValue(cJSON_GetObjectItem(entry, "url")) : NULL;
            char built[1024];
            if (!url) { snprintf(built, sizeof(built), "%s%s", lib_base_url(lib), rel); url = built; }
            cJSON *t = cJSON_CreateObject();
            cJSON *urls = cJSON_CreateArray();
            cJSON_AddItemToArray(urls, cJSON_CreateString(url));
            cJSON_AddItemToObject(t, "urls", urls);
            cJSON_AddStringToObject(t, "dest", dest);
            if (entry && cJSON_GetStringValue(cJSON_GetObjectItem(entry, "sha1")))
                cJSON_AddStringToObject(t, "sha1", cJSON_GetStringValue(cJSON_GetObjectItem(entry, "sha1")));
            cJSON_AddItemToArray(tasks, t);
            if (nn < 256) {
                snprintf(nats[nn].jar, sizeof(nats[nn].jar), "%s", dest);
                nats[nn].ex = cJSON_GetObjectItem(cJSON_GetObjectItem(lib, "extract"), "exclude");
                nn++;
            }
            free(nkey);
        }
    }
    int r = 0;
    if (cJSON_GetArraySize(tasks) > 0)
        r = download_all(tasks, "下载依赖库", ctx);
    cJSON_Delete(tasks);
    if (r != 0) return r;
    for (int i = 0; i < nn; i++) {
        if (cancelled(ctx)) { pymcl_set_error("用户取消"); return -1; }
        pymcl_extract_jar_natives(nats[i].jar, nat, nats[i].ex);
    }
    return 0;
}

static void copy_objects(cJSON *objects, const char *assets, const char *base) {
    pymcl_ensure_dir(base);
    cJSON *it;
    cJSON_ArrayForEach(it, objects) {
        const char *h = cJSON_GetStringValue(cJSON_GetObjectItem(it, "hash"));
        if (!h) continue;
        char src[PYMCL_PATH];
        char sub[4] = { h[0], h[1], 0 };
        pymcl_path_join3(src, sizeof(src), assets, "objects", sub);
        pymcl_path_join(src, sizeof(src), src, h);
        if (!pymcl_file_exists(src)) continue;
        char dst[PYMCL_PATH];
        pymcl_path_join(dst, sizeof(dst), base, it->string);
        pymcl_replace_char(dst, '/', '\\');
        pymcl_copy_file(src, dst);
    }
}

static int install_assets(const char *inst, cJSON *resolved, pymcl_ctx *ctx) {
    cJSON *idx = cJSON_GetObjectItem(resolved, "assetIndex");
    const char *url = cJSON_GetStringValue(cJSON_GetObjectItem(idx, "url"));
    const char *id = cJSON_GetStringValue(cJSON_GetObjectItem(idx, "id"));
    if (!url || !id) return 0;
    char assets[PYMCL_PATH], indexf[PYMCL_PATH];
    instance_assets_dir(inst, assets, sizeof(assets));
    char iname[64]; snprintf(iname, sizeof(iname), "%s.json", id);
    pymcl_path_join3(indexf, sizeof(indexf), assets, "indexes", iname);
    if (!pymcl_file_exists(indexf)) {
        note(ctx, "下载资源索引");
        if (download_file(url, NULL, 0, indexf, ctx,
                          cJSON_GetStringValue(cJSON_GetObjectItem(idx, "sha1")), -1, NULL) != 0)
            return -1;
    }
    cJSON *index = pymcl_read_json(indexf);
    cJSON *objects = cJSON_GetObjectItem(index, "objects");
    cJSON *tasks = cJSON_CreateArray();
    cJSON *obj;
    cJSON_ArrayForEach(obj, objects) {
        const char *h = cJSON_GetStringValue(cJSON_GetObjectItem(obj, "hash"));
        if (!h) continue;
        char dest[PYMCL_PATH], sub[4] = { h[0], h[1], 0 };
        pymcl_path_join3(dest, sizeof(dest), assets, "objects", sub);
        pymcl_path_join(dest, sizeof(dest), dest, h);
        long long sz = cJSON_IsNumber(cJSON_GetObjectItem(obj, "size"))
            ? (long long)cJSON_GetObjectItem(obj, "size")->valuedouble : -1;
        if (pymcl_file_matches(dest, h, sz)) continue;
        char u1[256], u2[256];
        snprintf(u1, sizeof(u1), BMCLAPI "/assets/%s/%s", sub, h);
        snprintf(u2, sizeof(u2), "https://resources.download.minecraft.net/%s/%s", sub, h);
        cJSON *t = cJSON_CreateObject();
        cJSON *urls = cJSON_CreateArray();
        cJSON_AddItemToArray(urls, cJSON_CreateString(u1));
        cJSON_AddItemToArray(urls, cJSON_CreateString(u2));
        cJSON_AddItemToObject(t, "urls", urls);
        cJSON_AddStringToObject(t, "dest", dest);
        cJSON_AddStringToObject(t, "sha1", h);
        if (sz >= 0) cJSON_AddNumberToObject(t, "size", (double)sz);
        cJSON_AddItemToArray(tasks, t);
    }
    int r = 0;
    if (cJSON_GetArraySize(tasks) > 0) {
        char msg[64]; snprintf(msg, sizeof(msg), "下载资源文件 %s", id);
        r = download_all(tasks, msg, ctx);
    }
    cJSON_Delete(tasks);
    if (r == 0) {
        if (cJSON_IsTrue(cJSON_GetObjectItem(index, "virtual"))) {
            char vb[PYMCL_PATH];
            pymcl_path_join3(vb, sizeof(vb), assets, "virtual", id);
            copy_objects(objects, assets, vb);
        }
        if (cJSON_IsTrue(cJSON_GetObjectItem(index, "map_to_resources"))) {
            char ip[PYMCL_PATH], rb[PYMCL_PATH];
            instance_path(inst, ip, sizeof(ip));
            pymcl_path_join(rb, sizeof(rb), ip, "resources");
            copy_objects(objects, assets, rb);
        }
    }
    cJSON_Delete(index);
    return r;
}

static int install_logging(const char *inst, cJSON *resolved, pymcl_ctx *ctx) {
    cJSON *f = cJSON_GetObjectItem(cJSON_GetObjectItem(cJSON_GetObjectItem(resolved, "logging"), "client"), "file");
    const char *url = cJSON_GetStringValue(cJSON_GetObjectItem(f, "url"));
    if (!url) return 0;
    const char *id = cJSON_GetStringValue(cJSON_GetObjectItem(f, "id")) ?: "client";
    char assets[PYMCL_PATH], dest[PYMCL_PATH];
    instance_assets_dir(inst, assets, sizeof(assets));
    pymcl_path_join3(dest, sizeof(dest), assets, "log_configs", id);
    download_file(url, NULL, 0, dest, ctx, cJSON_GetStringValue(cJSON_GetObjectItem(f, "sha1")), -1, NULL);
    return 0;
}

static char *jar_main_class(const char *jar) {
    size_t n = 0;
    char *mf = pymcl_zip_read(jar, "META-INF/MANIFEST.MF", &n);
    if (!mf) return NULL;
    char *line = mf;
    char *found = NULL;
    while (line && *line) {
        char *nl = strstr(line, "\n");
        if (nl) *nl = 0;
        if (_strnicmp(line, "Main-Class:", 11) == 0) {
            char *p = line + 11;
            while (*p == ' ') p++;
            found = pymcl_strdup(p);
            break;
        }
        line = nl ? nl + 1 : NULL;
    }
    free(mf);
    return found;
}

static int install_json(const char *inst, const char *vid, cJSON *vjson, pymcl_ctx *ctx);

static int install_json(const char *inst, const char *vid, cJSON *vjson, pymcl_ctx *ctx) {
    instance_ensure_dirs(inst);
    char vdir[PYMCL_PATH], jf[PYMCL_PATH], jn[256];
    instance_versions_dir(inst, vdir, sizeof(vdir));
    pymcl_path_join(vdir, sizeof(vdir), vdir, vid);
    pymcl_ensure_dir(vdir);
    cJSON *clean = cJSON_Duplicate(vjson, 1);
    cJSON_DeleteItemFromObject(clean, "__pymcl_cached_at");
    snprintf(jn, sizeof(jn), "%s.json", vid);
    pymcl_path_join(jf, sizeof(jf), vdir, jn);
    pymcl_write_json(jf, clean);
    cJSON_Delete(clean);
    const char *pid = cJSON_GetStringValue(cJSON_GetObjectItem(vjson, "inheritsFrom"));
    if (pid && !instance_has_version(inst, pid)) {
        char msg[128]; snprintf(msg, sizeof(msg), "安装依赖版本 %s", pid);
        note(ctx, msg);
        cJSON *pj = load_parent_inst(pid, (void *)inst);
        if (!pj) { pymcl_set_error("缺少父版本 %s", pid); return -1; }
        int r = install_json(inst, pid, pj, ctx);
        cJSON_Delete(pj);
        if (r != 0) return r;
    }
    cJSON *resolved = manifest_resolve_inherits(vjson, load_parent_inst, (void *)inst);
    if (!resolved) return -1;
    cJSON *client = cJSON_GetObjectItem(cJSON_GetObjectItem(resolved, "downloads"), "client");
    const char *curl = cJSON_GetStringValue(cJSON_GetObjectItem(client, "url"));
    if (curl) {
        char jar[PYMCL_PATH], jarn[256];
        snprintf(jarn, sizeof(jarn), "%s.jar", vid);
        pymcl_path_join(jar, sizeof(jar), vdir, jarn);
        note(ctx, "下载客户端 jar");
        if (download_file(curl, NULL, 0, jar, ctx,
                          cJSON_GetStringValue(cJSON_GetObjectItem(client, "sha1")),
                          cJSON_IsNumber(cJSON_GetObjectItem(client, "size"))
                            ? (long long)cJSON_GetObjectItem(client, "size")->valuedouble : -1,
                          NULL) != 0) {
            cJSON_Delete(resolved);
            return -1;
        }
    } else if (!cJSON_GetObjectItem(vjson, "inheritsFrom")) {
        pymcl_set_error("版本 %s 缺少客户端 jar 下载信息", vid);
        cJSON_Delete(resolved);
        return -1;
    }
    if (install_libraries(inst, resolved, vid, ctx) != 0) { cJSON_Delete(resolved); return -1; }
    if (ctx && ctx->skip_assets)
        note(ctx, "已按设置跳过资源文件校验");
    else if (install_assets(inst, resolved, ctx) != 0) { cJSON_Delete(resolved); return -1; }
    install_logging(inst, resolved, ctx);
    cJSON *mv = cJSON_CreateString(vid);
    instance_set_meta(inst, "mc_version", mv);
    cJSON_Delete(mv);
    cJSON_Delete(resolved);
    return 0;
}

int install_version(const char *instance, const char *version_id, pymcl_ctx *ctx) {
    instance_ensure_dirs(instance);
    cJSON *vjson = manifest_get_version(version_id, 0);
    if (!vjson) {
        vjson = instance_version_json(instance, version_id);
        if (!vjson) {
            char *alt = manifest_resolve_playable(version_id);
            if (alt && strcmp(alt, version_id) != 0) {
                char msg[128]; snprintf(msg, sizeof(msg), "版本 %s 不存在，自动改用 %s", version_id, alt);
                note(ctx, msg);
                int r = install_version(instance, alt, ctx);
                free(alt);
                return r;
            }
            free(alt);
            pymcl_set_error("找不到版本 %s", version_id);
            return -1;
        }
    }
    int r = install_json(instance, version_id, vjson, ctx);
    cJSON_Delete(vjson);
    return r;
}

int extract_natives(const char *instance, cJSON *resolved, const char *vid, char *out, size_t n) {
    instance_natives_dir(instance, vid, resolved, out, n);
    pymcl_ensure_dir(out);
    char libs[PYMCL_PATH];
    instance_libraries_dir(instance, libs, sizeof(libs));
    int extracted = 0;
    cJSON *lib;
    cJSON_ArrayForEach(lib, cJSON_GetObjectItem(resolved, "libraries")) {
        if (cJSON_IsFalse(cJSON_GetObjectItem(lib, "clientreq"))) continue;
        if (!pymcl_check_rules(cJSON_GetObjectItem(lib, "rules"), 0)) continue;
        char *nkey = select_native_classifier(lib);
        if (!nkey) continue;
        cJSON *entry = cJSON_GetObjectItem(cJSON_GetObjectItem(cJSON_GetObjectItem(lib, "downloads"), "classifiers"), nkey);
        char rel[512];
        const char *path = entry ? cJSON_GetStringValue(cJSON_GetObjectItem(entry, "path")) : NULL;
        if (path) snprintf(rel, sizeof(rel), "%s", path);
        else {
            char spec[512];
            snprintf(spec, sizeof(spec), "%s:%s", cJSON_GetStringValue(cJSON_GetObjectItem(lib, "name")) ?: "", nkey);
            pymcl_maven_path(spec, "jar", rel, sizeof(rel));
        }
        pymcl_replace_char(rel, '/', '\\');
        char jar[PYMCL_PATH];
        pymcl_path_join(jar, sizeof(jar), libs, rel);
        if (pymcl_file_exists(jar)) {
            pymcl_extract_jar_natives(jar, out, cJSON_GetObjectItem(cJSON_GetObjectItem(lib, "extract"), "exclude"));
            extracted++;
        }
        free(nkey);
    }
    if (extracted && natives_present(out)) return 0;
    const char *parent = cJSON_GetStringValue(cJSON_GetObjectItem(resolved, "inheritsFrom"));
    if (parent && strcmp(parent, vid) != 0) {
        cJSON *pj = instance_version_json(instance, parent);
        if (pj) {
            char src[PYMCL_PATH];
            instance_natives_dir(instance, parent, pj, src, sizeof(src));
            if (natives_present(src) && _stricmp(src, out) != 0)
                pymcl_copy_tree(src, out);
            cJSON_Delete(pj);
        }
    }
    return 0;
}

char *install_fabric(const char *instance, const char *mc, const char *loader, pymcl_ctx *ctx) {
    char url[512];
    snprintf(url, sizeof(url), FABRIC_META "/versions/loader/%s", mc);
    cJSON *data = http_get_json(url, 45);
    if (!cJSON_IsArray(data) || cJSON_GetArraySize(data) == 0) {
        cJSON_Delete(data);
        pymcl_set_error("Fabric 不支持 Minecraft %s", mc);
        return NULL;
    }
    cJSON *chosen = NULL;
    if (loader && loader[0]) {
        cJSON *d;
        cJSON_ArrayForEach(d, data) {
            const char *v = cJSON_GetStringValue(cJSON_GetObjectItem(cJSON_GetObjectItem(d, "loader"), "version"));
            if (v && strcmp(v, loader) == 0) { chosen = d; break; }
        }
    } else {
        cJSON *d;
        cJSON_ArrayForEach(d, data) {
            if (cJSON_IsTrue(cJSON_GetObjectItem(cJSON_GetObjectItem(d, "loader"), "stable"))) { chosen = d; break; }
        }
        if (!chosen) chosen = cJSON_GetArrayItem(data, 0);
    }
    if (!chosen) { cJSON_Delete(data); pymcl_set_error("找不到 Fabric Loader"); return NULL; }
    const char *lv = cJSON_GetStringValue(cJSON_GetObjectItem(cJSON_GetObjectItem(chosen, "loader"), "version"));
    char purl[512];
    snprintf(purl, sizeof(purl), FABRIC_META "/versions/loader/%s/%s/profile/json", mc, lv);
    cJSON *profile = http_get_json(purl, 45);
    cJSON_Delete(data);
    if (!profile) return NULL;
    const char *id = cJSON_GetStringValue(cJSON_GetObjectItem(profile, "id"));
    if (!id) { cJSON_Delete(profile); return NULL; }
    char *ret = pymcl_strdup(id);
    int r = install_json(instance, id, profile, ctx);
    cJSON_Delete(profile);
    if (r != 0) { free(ret); return NULL; }
    return ret;
}

char *install_quilt(const char *instance, const char *mc, const char *loader, pymcl_ctx *ctx) {
    char url[512];
    snprintf(url, sizeof(url), QUILT_META "/versions/loader/%s", mc);
    cJSON *data = http_get_json(url, 45);
    if (!cJSON_IsArray(data) || cJSON_GetArraySize(data) == 0) {
        cJSON_Delete(data);
        pymcl_set_error("Quilt 不支持 Minecraft %s", mc);
        return NULL;
    }
    cJSON *chosen = cJSON_GetArrayItem(data, 0);
    if (loader && loader[0]) {
        cJSON *d;
        cJSON_ArrayForEach(d, data) {
            const char *v = cJSON_GetStringValue(cJSON_GetObjectItem(cJSON_GetObjectItem(d, "loader"), "version"));
            if (v && strcmp(v, loader) == 0) { chosen = d; break; }
        }
    }
    const char *lv = cJSON_GetStringValue(cJSON_GetObjectItem(cJSON_GetObjectItem(chosen, "loader"), "version"));
    char purl[512];
    snprintf(purl, sizeof(purl), QUILT_META "/versions/loader/%s/%s/profile/json", mc, lv);
    cJSON *profile = http_get_json(purl, 45);
    cJSON_Delete(data);
    if (!profile) return NULL;
    const char *id = cJSON_GetStringValue(cJSON_GetObjectItem(profile, "id"));
    char *ret = pymcl_strdup(id ? id : "");
    int r = install_json(instance, id, profile, ctx);
    cJSON_Delete(profile);
    if (r != 0) { free(ret); return NULL; }
    return ret;
}

static int parse_maven_versions(const char *xml, char ***out, int *n) {
    *out = NULL; *n = 0;
    if (!xml) return 0;
    const char *p = xml;
    while ((p = strstr(p, "<version>"))) {
        p += 9;
        const char *e = strstr(p, "</version>");
        if (!e) break;
        char v[128];
        size_t L = (size_t)(e - p); if (L > 127) L = 127;
        memcpy(v, p, L); v[L] = 0;
        *out = (char **)realloc(*out, sizeof(char *) * (size_t)(*n + 1));
        (*out)[(*n)++] = pymcl_strdup(v);
        p = e + 10;
    }
    return 0;
}

static int is_forge_installer(const char *p) {
    return pymcl_file_exists(p) && pymcl_file_size(p) > 1024 &&
        (pymcl_zip_has(p, "install_profile.json") || pymcl_zip_has(p, "version.json"));
}

static char *forge_guess(const char *mc, const char *fv, int idx) {
    if (!mc || !fv) return NULL;
    if (idx == 0) {
        char *s = (char *)malloc(128);
        snprintf(s, 128, "%s-%s", mc, fv);
        return s;
    }
    if (idx == 1) {
        char *s = (char *)malloc(160);
        snprintf(s, 160, "%s-%s-%s", mc, fv, mc);
        return s;
    }
    return NULL;
}

static int download_forge_installer(const char *full, const char *mc, const char *dest, pymcl_ctx *ctx) {
    char u1[512], u2[512], u3[512];
    snprintf(u1, sizeof(u1), "%s/%s/forge-%s-installer.jar", FORGE_MAVEN, full, full);
    snprintf(u2, sizeof(u2), BMCLAPI "/maven/net/minecraftforge/forge/%s/forge-%s-installer.jar", full, full);
    snprintf(u3, sizeof(u3), BMCLAPI "/forge/download?mcversion=%s&version=%s&category=installer&format=jar", mc, full);
    const char *ex[] = { u2, u3 };
    if (download_file(u1, ex, 2, dest, ctx, NULL, -1, NULL) != 0) return -1;
    if (!is_forge_installer(dest)) { pymcl_remove_tree(dest); pymcl_set_error("Forge 安装器无效"); return -1; }
    return 0;
}

static char *install_forge_legacy(const char *inst, const char *jar, cJSON *profile, const char *mc, pymcl_ctx *ctx) {
    cJSON *install = cJSON_GetObjectItem(profile, "install");
    cJSON *vinfo = cJSON_GetObjectItem(profile, "versionInfo");
    const char *vid = cJSON_GetStringValue(cJSON_GetObjectItem(vinfo, "id"));
    if (!vid) vid = cJSON_GetStringValue(cJSON_GetObjectItem(install, "target"));
    if (!vid) { pymcl_set_error("旧版 Forge 安装器缺少版本 id"); return NULL; }
    const char *vanilla = cJSON_GetStringValue(cJSON_GetObjectItem(install, "minecraft")) ?: mc;
    if (!instance_has_version(inst, vanilla)) {
        if (install_version(inst, vanilla, ctx) != 0) return NULL;
    }
    char vdir[PYMCL_PATH];
    instance_versions_dir(inst, vdir, sizeof(vdir));
    pymcl_path_join(vdir, sizeof(vdir), vdir, vid);
    pymcl_ensure_dir(vdir);
    char jf[PYMCL_PATH], jn[256];
    snprintf(jn, sizeof(jn), "%s.json", vid);
    pymcl_path_join(jf, sizeof(jf), vdir, jn);
    pymcl_write_json(jf, vinfo);
    char src[PYMCL_PATH], dst[PYMCL_PATH], vd0[PYMCL_PATH];
    instance_versions_dir(inst, vd0, sizeof(vd0));
    snprintf(jn, sizeof(jn), "%s.jar", vanilla);
    pymcl_path_join3(src, sizeof(src), vd0, vanilla, jn);
    snprintf(jn, sizeof(jn), "%s.jar", vid);
    pymcl_path_join(dst, sizeof(dst), vdir, jn);
    if (pymcl_file_exists(src)) pymcl_copy_file(src, dst);
    const char *maven = cJSON_GetStringValue(cJSON_GetObjectItem(install, "path"));
    const char *fp = cJSON_GetStringValue(cJSON_GetObjectItem(install, "filePath"));
    if (maven && fp) {
        char rel[512], dest[PYMCL_PATH], libs[PYMCL_PATH];
        pymcl_maven_path(maven, "jar", rel, sizeof(rel));
        pymcl_replace_char(rel, '/', '\\');
        instance_libraries_dir(inst, libs, sizeof(libs));
        pymcl_path_join(dest, sizeof(dest), libs, rel);
        pymcl_zip_extract_one(jar, fp, dest);
    }
    cJSON *resolved = manifest_resolve_inherits(vinfo, load_parent_inst, (void *)inst);
    if (resolved) {
        install_libraries(inst, resolved, vid, ctx);
        cJSON_Delete(resolved);
    }
    cJSON *mv = cJSON_CreateString(vid);
    instance_set_meta(inst, "mc_version", mv);
    cJSON_Delete(mv);
    return pymcl_strdup(vid);
}

static void sub_brace(const char *in, cJSON *data, char *out, size_t n) {
    size_t o = 0;
    for (const char *p = in; *p && o + 1 < n;) {
        if (*p == '{') {
            const char *e = strchr(p + 1, '}');
            if (e) {
                char key[128];
                size_t kn = (size_t)(e - (p + 1));
                if (kn > 127) kn = 127;
                memcpy(key, p + 1, kn); key[kn] = 0;
                cJSON *v = cJSON_GetObjectItem(data, key);
                if (cJSON_IsString(v)) {
                    size_t vl = strlen(v->valuestring);
                    if (o + vl >= n) vl = n - o - 1;
                    memcpy(out + o, v->valuestring, vl);
                    o += vl; p = e + 1; continue;
                }
            }
        }
        out[o++] = *p++;
    }
    out[o] = 0;
}

static int run_processors(const char *inst, cJSON *profile, cJSON *data, const char *java, pymcl_ctx *ctx) {
    cJSON *procs = cJSON_GetObjectItem(profile, "processors");
    if (!cJSON_IsArray(procs)) return 0;
    char libs[PYMCL_PATH];
    instance_libraries_dir(inst, libs, sizeof(libs));
    int total = cJSON_GetArraySize(procs);
    int i = 0;
    cJSON *proc;
    cJSON_ArrayForEach(proc, procs) {
        i++;
        cJSON *sides = cJSON_GetObjectItem(proc, "sides");
        if (cJSON_IsArray(sides)) {
            int ok = 0; cJSON *s;
            cJSON_ArrayForEach(s, sides) if (cJSON_IsString(s) && strcmp(s->valuestring, "client") == 0) ok = 1;
            if (!ok) continue;
        }
        const char *jarn = cJSON_GetStringValue(cJSON_GetObjectItem(proc, "jar"));
        if (!jarn) continue;
        char rel[512], jp[PYMCL_PATH];
        pymcl_maven_path(jarn, "jar", rel, sizeof(rel));
        pymcl_replace_char(rel, '/', '\\');
        pymcl_path_join(jp, sizeof(jp), libs, rel);
        char *mainc = jar_main_class(jp);
        if (!mainc) { pymcl_set_error("处理器 jar 没有 Main-Class: %s", jarn); return -1; }
        char cp[8192]; snprintf(cp, sizeof(cp), "%s", jp);
        cJSON *cpl = cJSON_GetObjectItem(proc, "classpath");
        cJSON *n;
        cJSON_ArrayForEach(n, cpl) {
            char r2[512], p2[PYMCL_PATH];
            pymcl_maven_path(n->valuestring, "jar", r2, sizeof(r2));
            pymcl_replace_char(r2, '/', '\\');
            pymcl_path_join(p2, sizeof(p2), libs, r2);
            strncat(cp, ";", sizeof(cp) - strlen(cp) - 1);
            strncat(cp, p2, sizeof(cp) - strlen(cp) - 1);
        }
        const char *argv[64]; int ac = 0;
        argv[ac++] = java;
        argv[ac++] = "-cp";
        argv[ac++] = cp;
        argv[ac++] = mainc;
        char argbuf[32][PYMCL_PATH]; int na = 0;
        cJSON *a;
        cJSON_ArrayForEach(a, cJSON_GetObjectItem(proc, "args")) {
            if (!cJSON_IsString(a) || na >= 32) continue;
            sub_brace(a->valuestring, data, argbuf[na], sizeof(argbuf[na]));
            if (argbuf[na][0] == '[' && argbuf[na][strlen(argbuf[na]) - 1] == ']') {
                char r3[512], p3[PYMCL_PATH];
                pymcl_maven_path(argbuf[na], "jar", r3, sizeof(r3));
                pymcl_replace_char(r3, '/', '\\');
                pymcl_path_join(p3, sizeof(p3), libs, r3);
                snprintf(argbuf[na], sizeof(argbuf[na]), "%s", p3);
            }
            argv[ac++] = argbuf[na++];
        }
        char msg[256];
        snprintf(msg, sizeof(msg), "运行 Forge 处理器 %d/%d: %s", i, total, mainc);
        note(ctx, msg);
        char ip[PYMCL_PATH];
        instance_path(inst, ip, sizeof(ip));
        int code = pymcl_run_process(argv, ac, ip, NULL, NULL, 1800);
        free(mainc);
        if (code != 0) {
            /* allow if outputs exist */
            cJSON *outs = cJSON_GetObjectItem(proc, "outputs");
            int all = outs && cJSON_IsObject(outs);
            cJSON *o;
            cJSON_ArrayForEach(o, outs) {
                char p[PYMCL_PATH];
                sub_brace(o->string, data, p, sizeof(p));
                if (!pymcl_file_exists(p)) all = 0;
            }
            if (!all) { pymcl_set_error("Forge 处理器失败 (退出码 %d): %s", code, jarn); return -1; }
        }
    }
    return 0;
}

static char *install_forge_modern(const char *inst, const char *jar, cJSON *profile, const char *mc, pymcl_ctx *ctx) {
    const char *json_entry = cJSON_GetStringValue(cJSON_GetObjectItem(profile, "json"));
    if (!json_entry) json_entry = "/version.json";
    while (*json_entry == '/') json_entry++;
    size_t n = 0;
    char *raw = pymcl_zip_read(jar, json_entry, &n);
    if (!raw) { pymcl_set_error("Forge 安装器缺少 version json"); return NULL; }
    cJSON *vjson = cJSON_Parse(raw);
    free(raw);
    const char *vid = cJSON_GetStringValue(cJSON_GetObjectItem(vjson, "id"));
    if (!vid) vid = cJSON_GetStringValue(cJSON_GetObjectItem(profile, "version"));
    if (!vid) { cJSON_Delete(vjson); pymcl_set_error("Forge 安装器缺少 version id"); return NULL; }
    const char *vanilla = cJSON_GetStringValue(cJSON_GetObjectItem(profile, "minecraft"));
    if (!vanilla) vanilla = cJSON_GetStringValue(cJSON_GetObjectItem(vjson, "inheritsFrom"));
    if (!vanilla) vanilla = mc;
    if (!instance_has_version(inst, vanilla)) {
        if (install_version(inst, vanilla, ctx) != 0) { cJSON_Delete(vjson); return NULL; }
    }
    cJSON *libs = cJSON_CreateObject();
    cJSON *arr = cJSON_CreateArray();
    cJSON *x;
    cJSON_ArrayForEach(x, cJSON_GetObjectItem(profile, "libraries"))
        cJSON_AddItemToArray(arr, cJSON_Duplicate(x, 1));
    cJSON_ArrayForEach(x, cJSON_GetObjectItem(vjson, "libraries"))
        cJSON_AddItemToArray(arr, cJSON_Duplicate(x, 1));
    cJSON_AddItemToObject(libs, "libraries", arr);
    install_libraries(inst, libs, vid, ctx);
    cJSON_Delete(libs);
    char tmp[PYMCL_PATH];
    GetTempPathA(sizeof(tmp), tmp);
    char tdir[PYMCL_PATH];
    snprintf(tdir, sizeof(tdir), "%spymcl_fgdata_%u", tmp, GetTickCount());
    pymcl_ensure_dir(tdir);
    cJSON *data = cJSON_CreateObject();
    char ip[PYMCL_PATH], libsdir[PYMCL_PATH], vjar[PYMCL_PATH], vd[PYMCL_PATH];
    instance_path(inst, ip, sizeof(ip));
    instance_libraries_dir(inst, libsdir, sizeof(libsdir));
    instance_versions_dir(inst, vd, sizeof(vd));
    char jn[256]; snprintf(jn, sizeof(jn), "%s.jar", vanilla);
    pymcl_path_join3(vjar, sizeof(vjar), vd, vanilla, jn);
    cJSON_AddStringToObject(data, "SIDE", "client");
    cJSON_AddStringToObject(data, "MINECRAFT_JAR", vjar);
    cJSON_AddStringToObject(data, "ROOT", ip);
    cJSON_AddStringToObject(data, "INSTALLER", jar);
    cJSON_AddStringToObject(data, "LIBRARY_DIR", libsdir);
    cJSON *d;
    cJSON_ArrayForEach(d, cJSON_GetObjectItem(profile, "data")) {
        cJSON *spec = cJSON_IsObject(d) ? cJSON_GetObjectItem(d, "client") : d;
        const char *raws = cJSON_IsString(spec) ? spec->valuestring : "";
        if (raws[0] == '\'' && raws[strlen(raws) - 1] == '\'') {
            char t[512]; snprintf(t, sizeof(t), "%.*s", (int)strlen(raws) - 2, raws + 1);
            cJSON_AddStringToObject(data, d->string, t);
        } else if (raws[0] == '[' && raws[strlen(raws) - 1] == ']') {
            char rel[512], dest[PYMCL_PATH];
            pymcl_maven_path(raws, "jar", rel, sizeof(rel));
            pymcl_replace_char(rel, '/', '\\');
            pymcl_path_join(dest, sizeof(dest), libsdir, rel);
            cJSON_AddStringToObject(data, d->string, dest);
        } else if (raws[0] == '/') {
            char dest[PYMCL_PATH];
            pymcl_path_join(dest, sizeof(dest), tdir, pymcl_basename(raws + 1));
            pymcl_zip_extract_one(jar, raws + 1, dest);
            cJSON_AddStringToObject(data, d->string, dest);
        } else {
            cJSON_AddStringToObject(data, d->string, raws);
        }
    }
    const char *moj = cJSON_GetStringValue(cJSON_GetObjectItem(data, "MOJMAPS"));
    if (moj) {
        cJSON *vj = instance_version_json(inst, vanilla);
        if (!vj) vj = manifest_get_version(vanilla, 0);
        cJSON *cm = cJSON_GetObjectItem(cJSON_GetObjectItem(vj, "downloads"), "client_mappings");
        const char *mu = cJSON_GetStringValue(cJSON_GetObjectItem(cm, "url"));
        if (mu) download_file(mu, NULL, 0, moj, ctx, cJSON_GetStringValue(cJSON_GetObjectItem(cm, "sha1")), -1, NULL);
        cJSON_Delete(vj);
    }
    char *java = java_for_installer("forge", ctx);
    int pr = java ? run_processors(inst, profile, data, java, ctx) : -1;
    free(java);
    cJSON_Delete(data);
    pymcl_remove_tree(tdir);
    if (pr != 0) { cJSON_Delete(vjson); return NULL; }
    char ov[PYMCL_PATH];
    instance_versions_dir(inst, ov, sizeof(ov));
    pymcl_path_join(ov, sizeof(ov), ov, vid);
    pymcl_ensure_dir(ov);
    char oj[PYMCL_PATH]; snprintf(jn, sizeof(jn), "%s.json", vid);
    pymcl_path_join(oj, sizeof(oj), ov, jn);
    pymcl_write_json(oj, vjson);
    char *ret = pymcl_strdup(vid);
    cJSON *mv = cJSON_CreateString(vid);
    instance_set_meta(inst, "mc_version", mv);
    cJSON_Delete(mv);
    cJSON_Delete(vjson);
    return ret;
}

static char *run_forge_installer(const char *inst, const char *jar, const char *mc, const char *full, pymcl_ctx *ctx) {
    size_t n = 0;
    char *raw = pymcl_zip_read(jar, "install_profile.json", &n);
    cJSON *profile = raw ? cJSON_Parse(raw) : NULL;
    if (raw) free(raw);
    if (profile && cJSON_GetObjectItem(profile, "versionInfo") && !cJSON_GetObjectItem(profile, "processors")) {
        char *r = install_forge_legacy(inst, jar, profile, mc, ctx);
        cJSON_Delete(profile);
        return r;
    }
    if (profile && (cJSON_GetObjectItem(profile, "processors") || cJSON_GetObjectItem(profile, "json"))) {
        char *r = install_forge_modern(inst, jar, profile, mc, ctx);
        cJSON_Delete(profile);
        return r;
    }
    cJSON_Delete(profile);
    int a, b, c;
    mc_version_tuple(mc, &a, &b, &c);
    char *java = java_for_installer((a < 1 || (a == 1 && b < 17)) ? "forge-legacy" : "forge", ctx);
    if (!java) return NULL;
    char tmp[MAX_PATH], work[PYMCL_PATH];
    GetTempPathA(MAX_PATH, tmp);
    snprintf(work, sizeof(work), "%spymclfg%u", tmp, GetTickCount());
    pymcl_ensure_dir(work);
    const char *argv[] = { java, "-jar", jar, "--installClient", work };
    int code = pymcl_run_process(argv, 5, work, NULL, NULL, 1800);
    free(java);
    if (code != 0) { pymcl_remove_tree(work); pymcl_set_error("Forge 安装器退出码 %d", code); return NULL; }
    char vsrc[PYMCL_PATH], vdst[PYMCL_PATH];
    pymcl_path_join(vsrc, sizeof(vsrc), work, "versions");
    instance_versions_dir(inst, vdst, sizeof(vdst));
    if (pymcl_dir_exists(vsrc)) pymcl_copy_tree(vsrc, vdst);
    char lsrc[PYMCL_PATH], ldst[PYMCL_PATH];
    pymcl_path_join(lsrc, sizeof(lsrc), work, "libraries");
    instance_libraries_dir(inst, ldst, sizeof(ldst));
    if (pymcl_dir_exists(lsrc)) pymcl_copy_tree(lsrc, ldst);
    pymcl_remove_tree(work);
    cJSON *ids = NULL;
    instance_installed_ids(inst, &ids);
    char *vid = NULL;
    cJSON *it;
    cJSON_ArrayForEach(it, ids) {
        if (pymcl_icontains(it->valuestring, "forge")) { free(vid); vid = pymcl_strdup(it->valuestring); }
    }
    cJSON_Delete(ids);
    if (!vid) {
        char buf[128]; snprintf(buf, sizeof(buf), "%s-forge-%s", mc, full);
        vid = pymcl_strdup(buf);
    }
    return vid;
}

char *install_forge(const char *instance, const char *mc, const char *forge, pymcl_ctx *ctx) {
    char cache[PYMCL_PATH];
    pymcl_cache_dir(cache, sizeof(cache));
    pymcl_ensure_dir(cache);
    if (forge && forge[0]) {
        char want[128];
        snprintf(want, sizeof(want), "%s-forge-%s", mc, forge);
        if (instance_has_version(instance, want)) return pymcl_strdup(want);
    }
    char *full = NULL;
    char dest[PYMCL_PATH] = {0};
    for (int i = 0; i < 2; i++) {
        char *g = forge_guess(mc, forge, i);
        if (!g) continue;
        snprintf(dest, sizeof(dest), "%s\\forge-%s-installer.jar", cache, g);
        if (is_forge_installer(dest) || download_forge_installer(g, mc, dest, ctx) == 0) {
            full = g; break;
        }
        free(g);
        dest[0] = 0;
    }
    if (!full) {
        char url[256];
        snprintf(url, sizeof(url), BMCLAPI "/forge/minecraft/%s", mc);
        cJSON *list = http_get_json(url, 60);
        char best[128] = {0};
        if (cJSON_IsArray(list) && cJSON_GetArraySize(list) > 0) {
            cJSON *last = cJSON_GetArrayItem(list, cJSON_GetArraySize(list) - 1);
            const char *ver = cJSON_GetStringValue(cJSON_GetObjectItem(last, "version"));
            const char *imc = cJSON_GetStringValue(cJSON_GetObjectItem(last, "mcversion")) ?: mc;
            if (ver) snprintf(best, sizeof(best), "%s-%s", imc, ver);
        }
        cJSON_Delete(list);
        if (!best[0]) {
            const char *xmlu[] = { FORGE_MAVEN "/maven-metadata.xml", BMCLAPI "/maven/net/minecraftforge/forge/maven-metadata.xml" };
            char *xml = fetch_text_mirrors(xmlu, 2, 90);
            char **vers = NULL; int nv = 0;
            parse_maven_versions(xml, &vers, &nv);
            free(xml);
            for (int i = 0; i < nv; i++) {
                if (pymcl_startswith(vers[i], mc)) snprintf(best, sizeof(best), "%s", vers[i]);
                free(vers[i]);
            }
            free(vers);
        }
        if (!best[0]) { pymcl_set_error("Forge 没有支持 Minecraft %s 的版本", mc); return NULL; }
        full = pymcl_strdup(best);
        snprintf(dest, sizeof(dest), "%s\\forge-%s-installer.jar", cache, full);
        if (!is_forge_installer(dest) && download_forge_installer(full, mc, dest, ctx) != 0) {
            free(full); return NULL;
        }
    }
    char *vid = run_forge_installer(instance, dest, mc, full, ctx);
    free(full);
    return vid;
}

char *install_neoforge(const char *instance, const char *mc, const char *ver, pymcl_ctx *ctx) {
    char *xml = NULL;
    const char *u[] = { NEOFORGE_MAVEN "/maven-metadata.xml" };
    xml = fetch_text_mirrors(u, 1, 60);
    char **vers = NULL; int nv = 0;
    parse_maven_versions(xml, &vers, &nv);
    free(xml);
    char full[128] = {0};
    if (ver && ver[0]) {
        for (int i = 0; i < nv; i++) if (strcmp(vers[i], ver) == 0) snprintf(full, sizeof(full), "%s", ver);
    }
    if (!full[0]) {
        int a, b, c; mc_version_tuple(mc, &a, &b, &c);
        char prefix[32];
        if (a > 1 || (a == 1 && (b > 20 || (b == 20 && c >= 2))))
            snprintf(prefix, sizeof(prefix), "%d.%d.", b, c);
        else
            snprintf(prefix, sizeof(prefix), "%s-", mc);
        for (int i = 0; i < nv; i++)
            if (pymcl_startswith(vers[i], prefix)) snprintf(full, sizeof(full), "%s", vers[i]);
    }
    for (int i = 0; i < nv; i++) free(vers[i]);
    free(vers);
    if (!full[0]) { pymcl_set_error("NeoForge 没有支持 Minecraft %s 的版本", mc); return NULL; }
    char cache[PYMCL_PATH], dest[PYMCL_PATH], url[512];
    pymcl_cache_dir(cache, sizeof(cache));
    snprintf(dest, sizeof(dest), "%s\\neoforge-%s-installer.jar", cache, full);
    snprintf(url, sizeof(url), "%s/%s/neoforge-%s-installer.jar", NEOFORGE_MAVEN, full, full);
    if (download_file(url, NULL, 0, dest, ctx, NULL, -1, NULL) != 0) return NULL;
    size_t n = 0;
    char *raw = pymcl_zip_read(dest, "install_profile.json", &n);
    cJSON *profile = raw ? cJSON_Parse(raw) : NULL;
    if (raw) free(raw);
    if (profile) {
        char *r = install_forge_modern(instance, dest, profile, mc, ctx);
        cJSON_Delete(profile);
        return r;
    }
    pymcl_set_error("NeoForge 安装器无法解析");
    return NULL;
}

int uninstall_version(const char *instance, const char *vid) {
    char vd[PYMCL_PATH];
    instance_versions_dir(instance, vd, sizeof(vd));
    pymcl_path_join(vd, sizeof(vd), vd, vid);
    if (!pymcl_dir_exists(vd)) { pymcl_set_error("版本 %s 未安装", vid); return -1; }
    pymcl_remove_tree(vd);
    return 0;
}

int install_loader(const char *instance, const char *loader, const char *ver, const char *mc, pymcl_ctx *ctx, char *vid_out, size_t n) {
    char *id = NULL;
    if (pymcl_ieq(loader, "fabric-loader") || pymcl_ieq(loader, "fabric"))
        id = install_fabric(instance, mc, ver, ctx);
    else if (pymcl_ieq(loader, "quilt-loader") || pymcl_ieq(loader, "quilt"))
        id = install_quilt(instance, mc, ver, ctx);
    else if (pymcl_ieq(loader, "forge"))
        id = install_forge(instance, mc, ver, ctx);
    else if (pymcl_ieq(loader, "neoforge"))
        id = install_neoforge(instance, mc, ver, ctx);
    else { pymcl_set_error("不支持的加载器: %s", loader); return -1; }
    if (!id) return -1;
    if (vid_out) snprintf(vid_out, n, "%s", id);
    free(id);
    return 0;
}
