#include "pymcl.h"

static void emit(pymcl_ctx *ctx, const char *msg) {
    if (ctx && ctx->on_progress) ctx->on_progress(ctx->ud, msg, 0, 1);
    if (ctx && ctx->on_log) ctx->on_log(ctx->ud, msg);
    pymcl_log("%s", msg);
}

static int install_mrpack_file(const char *pack, const char *instance, pymcl_ctx *ctx) {
    char tmp[MAX_PATH], tdir[PYMCL_PATH];
    GetTempPathA(MAX_PATH, tmp);
    snprintf(tdir, sizeof(tdir), "%spymcl_mrpack_%lu", tmp, (unsigned long)GetTickCount());
    pymcl_ensure_dir(tdir);
    if (pymcl_extract_zip(pack, tdir) != 0) {
        pymcl_remove_tree(tdir);
        pymcl_set_error("不是有效的 mrpack 文件");
        return -1;
    }
    char idxp[PYMCL_PATH];
    pymcl_path_join(idxp, sizeof(idxp), tdir, "modrinth.index.json");
    cJSON *idx = pymcl_read_json(idxp);
    if (!idx) {
        pymcl_remove_tree(tdir);
        pymcl_set_error("整合包缺少 modrinth.index.json");
        return -1;
    }
    instance_ensure_dirs(instance);
    cJSON *deps = cJSON_GetObjectItem(idx, "dependencies");
    const char *mc = cJSON_GetStringValue(cJSON_GetObjectItem(deps, "minecraft"));
    char *play = mc ? manifest_resolve_playable(mc) : NULL;
    if (!play && mc) play = pymcl_strdup(mc);
    if (!play) { cJSON_Delete(idx); pymcl_remove_tree(tdir); pymcl_set_error("整合包没有声明可用的 Minecraft 版本"); return -1; }
    emit(ctx, "安装 Minecraft");
    if (install_version(instance, play, ctx) != 0) { free(play); cJSON_Delete(idx); pymcl_remove_tree(tdir); return -1; }
    char loader_vid[256] = {0};
    const char *keys[] = { "fabric-loader", "quilt-loader", "forge", "neoforge", NULL };
    for (int i = 0; keys[i]; i++) {
        const char *lv = cJSON_GetStringValue(cJSON_GetObjectItem(deps, keys[i]));
        if (!lv) continue;
        char msg[128]; snprintf(msg, sizeof(msg), "安装加载器 %s %s", keys[i], lv);
        emit(ctx, msg);
        if (install_loader(instance, keys[i], lv, play, ctx, loader_vid, sizeof(loader_vid)) != 0) {
            /* try latest */
            if (install_loader(instance, keys[i], NULL, play, ctx, loader_vid, sizeof(loader_vid)) != 0) {
                free(play); cJSON_Delete(idx); pymcl_remove_tree(tdir); return -1;
            }
        }
        break;
    }
    cJSON *tasks = cJSON_CreateArray();
    cJSON *f;
    char ip[PYMCL_PATH];
    instance_path(instance, ip, sizeof(ip));
    cJSON_ArrayForEach(f, cJSON_GetObjectItem(idx, "files")) {
        const char *envc = cJSON_GetStringValue(cJSON_GetObjectItem(cJSON_GetObjectItem(f, "env"), "client"));
        if (envc && (strcmp(envc, "unsupported") == 0 || strcmp(envc, "server") == 0)) continue;
        const char *rel = cJSON_GetStringValue(cJSON_GetObjectItem(f, "path"));
        cJSON *dls = cJSON_GetObjectItem(f, "downloads");
        if (!rel || !cJSON_IsArray(dls) || cJSON_GetArraySize(dls) == 0) continue;
        if (strstr(rel, "..")) continue;
        char dest[PYMCL_PATH];
        pymcl_path_join(dest, sizeof(dest), ip, rel);
        pymcl_replace_char(dest, '/', '\\');
        cJSON *t = cJSON_CreateObject();
        cJSON *urls = cJSON_CreateArray();
        cJSON *u;
        cJSON_ArrayForEach(u, dls) {
            if (!cJSON_IsString(u)) continue;
            char mir[1024];
            if (strstr(u->valuestring, MODRINTH_CDN)) {
                snprintf(mir, sizeof(mir), "%s%s", MCIM_MIRROR, u->valuestring + strlen(MODRINTH_CDN));
                cJSON_AddItemToArray(urls, cJSON_CreateString(mir));
            }
            cJSON_AddItemToArray(urls, cJSON_CreateString(u->valuestring));
        }
        cJSON_AddItemToObject(t, "urls", urls);
        cJSON_AddStringToObject(t, "dest", dest);
        const char *sha1 = cJSON_GetStringValue(cJSON_GetObjectItem(cJSON_GetObjectItem(f, "hashes"), "sha1"));
        if (sha1) cJSON_AddStringToObject(t, "sha1", sha1);
        if (cJSON_IsNumber(cJSON_GetObjectItem(f, "size")))
            cJSON_AddNumberToObject(t, "size", cJSON_GetObjectItem(f, "size")->valuedouble);
        cJSON_AddItemToArray(tasks, t);
    }
    if (cJSON_GetArraySize(tasks) > 0) {
        emit(ctx, "下载整合包文件");
        if (download_all(tasks, "下载整合包文件", ctx) != 0) {
            cJSON_Delete(tasks); free(play); cJSON_Delete(idx); pymcl_remove_tree(tdir); return -1;
        }
    }
    cJSON_Delete(tasks);
    char ov[PYMCL_PATH];
    pymcl_path_join(ov, sizeof(ov), tdir, "overrides");
    if (pymcl_dir_exists(ov)) pymcl_copy_tree(ov, ip);
    else {
        pymcl_path_join(ov, sizeof(ov), tdir, "client-overrides");
        if (pymcl_dir_exists(ov)) pymcl_copy_tree(ov, ip);
    }
    cJSON *meta = cJSON_CreateObject();
    cJSON_AddStringToObject(meta, "name", cJSON_GetStringValue(cJSON_GetObjectItem(idx, "name")) ?: "modpack");
    cJSON_AddStringToObject(meta, "version", cJSON_GetStringValue(cJSON_GetObjectItem(idx, "versionId")) ?: "?");
    cJSON_AddStringToObject(meta, "mc_version", play);
    cJSON_AddStringToObject(meta, "source", "modrinth");
    cJSON_AddStringToObject(meta, "instance", instance);
    instance_set_meta(instance, "modpack", meta);
    cJSON *mv = cJSON_CreateString(loader_vid[0] ? loader_vid : play);
    instance_set_meta(instance, "mc_version", mv);
    cJSON_Delete(mv);
    cJSON_Delete(meta);
    free(play);
    cJSON_Delete(idx);
    pymcl_remove_tree(tdir);
    emit(ctx, "整合包安装完成");
    return 0;
}

static int install_cf_zip_file(const char *pack, const char *instance, pymcl_ctx *ctx) {
    char tmp[MAX_PATH], tdir[PYMCL_PATH];
    GetTempPathA(MAX_PATH, tmp);
    snprintf(tdir, sizeof(tdir), "%spymcl_cfpack_%lu", tmp, (unsigned long)GetTickCount());
    pymcl_ensure_dir(tdir);
    if (pymcl_extract_zip(pack, tdir) != 0) {
        pymcl_remove_tree(tdir);
        pymcl_set_error("不是有效的整合包 zip");
        return -1;
    }
    char mf[PYMCL_PATH];
    pymcl_path_join(mf, sizeof(mf), tdir, "manifest.json");
    cJSON *man = pymcl_read_json(mf);
    if (!man) { pymcl_remove_tree(tdir); pymcl_set_error("整合包缺少 manifest.json"); return -1; }
    instance_ensure_dirs(instance);
    const char *mc = cJSON_GetStringValue(cJSON_GetObjectItem(cJSON_GetObjectItem(man, "minecraft"), "version"));
    char *play = mc ? manifest_resolve_playable(mc) : NULL;
    if (!play && mc) play = pymcl_strdup(mc);
    if (!play) { cJSON_Delete(man); pymcl_remove_tree(tdir); pymcl_set_error("整合包没有声明可用的 Minecraft 版本"); return -1; }
    emit(ctx, "安装 Minecraft");
    if (install_version(instance, play, ctx) != 0) { free(play); cJSON_Delete(man); pymcl_remove_tree(tdir); return -1; }
    cJSON *loaders = cJSON_GetObjectItem(cJSON_GetObjectItem(man, "minecraft"), "modLoaders");
    cJSON *primary = NULL, *L;
    cJSON_ArrayForEach(L, loaders) if (cJSON_IsTrue(cJSON_GetObjectItem(L, "primary"))) primary = L;
    if (!primary && cJSON_GetArraySize(loaders) > 0) primary = cJSON_GetArrayItem(loaders, 0);
    const char *lid = primary ? cJSON_GetStringValue(cJSON_GetObjectItem(primary, "id")) : NULL;
    char loader[64] = {0}, lver[64] = {0};
    if (lid) {
        const char *dash = strchr(lid, '-');
        if (dash) {
            snprintf(loader, sizeof(loader), "%.*s", (int)(dash - lid), lid);
            snprintf(lver, sizeof(lver), "%s", dash + 1);
        }
    }
    if (pymcl_ieq(loader, "fabric")) snprintf(loader, sizeof(loader), "fabric-loader");
    if (pymcl_ieq(loader, "quilt")) snprintf(loader, sizeof(loader), "quilt-loader");
    char loader_vid[256] = {0};
    if (loader[0]) {
        char msg[128]; snprintf(msg, sizeof(msg), "安装加载器 %s %s", loader, lver);
        emit(ctx, msg);
        if (install_loader(instance, loader, lver, play, ctx, loader_vid, sizeof(loader_vid)) != 0) {
            free(play); cJSON_Delete(man); pymcl_remove_tree(tdir); return -1;
        }
    }
    /* mods */
    cJSON *tasks = cJSON_CreateArray();
    char ip[PYMCL_PATH];
    instance_path(instance, ip, sizeof(ip));
    cJSON *file;
    cJSON_ArrayForEach(file, cJSON_GetObjectItem(man, "files")) {
        long long pid = (long long)cJSON_GetNumberValue(cJSON_GetObjectItem(file, "projectID"));
        long long fid = (long long)cJSON_GetNumberValue(cJSON_GetObjectItem(file, "fileID"));
        if (!pid || !fid) continue;
        char dest[PYMCL_PATH], fn[64];
        snprintf(fn, sizeof(fn), "mod-%lld-%lld.jar", pid, fid);
        pymcl_path_join3(dest, sizeof(dest), ip, "mods", fn);
        char u1[256], u2[256], u3[256];
        snprintf(u1, sizeof(u1), "https://mediafilez.forgecdn.net/files/%lld/%lld/%s", fid / 1000, fid % 1000, fn);
        snprintf(u2, sizeof(u2), "https://edge.forgecdn.net/files/%lld/%lld/%s", fid / 1000, fid % 1000, fn);
        snprintf(u3, sizeof(u3), CF_OFFICIAL "/mods/%lld/files/%lld/download", pid, fid);
        cJSON *t = cJSON_CreateObject();
        cJSON *urls = cJSON_CreateArray();
        cJSON_AddItemToArray(urls, cJSON_CreateString(u1));
        cJSON_AddItemToArray(urls, cJSON_CreateString(u2));
        cJSON_AddItemToArray(urls, cJSON_CreateString(u3));
        cJSON_AddItemToObject(t, "urls", urls);
        cJSON_AddStringToObject(t, "dest", dest);
        cJSON_AddItemToArray(tasks, t);
    }
    if (cJSON_GetArraySize(tasks) > 0) {
        emit(ctx, "下载整合包 Mod");
        download_all(tasks, "下载整合包 Mod", ctx);
    }
    cJSON_Delete(tasks);
    const char *ovn = cJSON_GetStringValue(cJSON_GetObjectItem(man, "overrides"));
    if (ovn) {
        char ov[PYMCL_PATH];
        pymcl_path_join(ov, sizeof(ov), tdir, ovn);
        if (pymcl_dir_exists(ov)) pymcl_copy_tree(ov, ip);
    }
    cJSON *meta = cJSON_CreateObject();
    cJSON_AddStringToObject(meta, "name", cJSON_GetStringValue(cJSON_GetObjectItem(man, "name")) ?: "modpack");
    cJSON_AddStringToObject(meta, "version", cJSON_GetStringValue(cJSON_GetObjectItem(man, "version")) ?: "?");
    cJSON_AddStringToObject(meta, "mc_version", play);
    cJSON_AddStringToObject(meta, "source", "curseforge");
    cJSON_AddStringToObject(meta, "instance", instance);
    instance_set_meta(instance, "modpack", meta);
    cJSON *mv = cJSON_CreateString(loader_vid[0] ? loader_vid : play);
    instance_set_meta(instance, "mc_version", mv);
    cJSON_Delete(mv); cJSON_Delete(meta);
    free(play); cJSON_Delete(man);
    pymcl_remove_tree(tdir);
    emit(ctx, "整合包安装完成");
    return 0;
}

static int install_cf_modpack_id(long long addon, const char *instance, const char *slug, pymcl_ctx *ctx) {
    char path[64]; snprintf(path, sizeof(path), "/mods/%lld", addon);
    /* reuse search via HTTP */
    char url[256];
    snprintf(url, sizeof(url), CF_OFFICIAL "/mods/%lld", addon);
    char hdr[512];
    const char *key = config_str("curseforge_api_key", "");
    snprintf(hdr, sizeof(hdr), "Accept: application/json\nx-api-key: %s", key ? key : "");
    cJSON *d = http_get_json_hdr(url, hdr, 45);
    if (!d) {
        snprintf(url, sizeof(url), "https://mod.mcimirror.top/curseforge/v1/mods/%lld", addon);
        d = http_get_json_hdr(url, hdr, 45);
    }
    cJSON *mod = d ? cJSON_GetObjectItem(d, "data") : NULL;
    cJSON *files = mod ? cJSON_GetObjectItem(mod, "latestFiles") : NULL;
    cJSON *f = files && cJSON_GetArraySize(files) ? cJSON_GetArrayItem(files, 0) : NULL;
    if (!f) { cJSON_Delete(d); pymcl_set_error("获取 CurseForge 整合包文件失败"); return -1; }
    long long fid = (long long)cJSON_GetNumberValue(cJSON_GetObjectItem(f, "id"));
    const char *fn = cJSON_GetStringValue(cJSON_GetObjectItem(f, "fileName")) ?: "pack.zip";
    const char *du = cJSON_GetStringValue(cJSON_GetObjectItem(f, "downloadUrl"));
    char tmp[MAX_PATH], dest[PYMCL_PATH];
    GetTempPathA(MAX_PATH, tmp);
    snprintf(dest, sizeof(dest), "%spymcl_cfpack_%lld_%lld.zip", tmp, addon, fid);
    char u1[256], u2[256];
    snprintf(u1, sizeof(u1), "https://mediafilez.forgecdn.net/files/%lld/%lld/%s", fid / 1000, fid % 1000, fn);
    snprintf(u2, sizeof(u2), "https://edge.forgecdn.net/files/%lld/%lld/%s", fid / 1000, fid % 1000, fn);
    const char *first = du && du[0] ? du : u1;
    const char *ex[] = { u1, u2 };
    emit(ctx, "下载整合包");
    int r = download_file(first, ex, 2, dest, ctx, NULL, -1, NULL);
    cJSON_Delete(d);
    if (r != 0) return -1;
    r = install_cf_zip_file(dest, instance, ctx);
    pymcl_remove_tree(dest);
    (void)slug;
    return r;
}

static int install_mr_slug(const char *slug, const char *instance, pymcl_ctx *ctx) {
    char url[256];
    snprintf(url, sizeof(url), MODRINTH_API "/project/%s/version", slug);
    cJSON *vers = http_get_json(url, 45);
    if (!cJSON_IsArray(vers)) { cJSON_Delete(vers); pymcl_set_error("整合包 %s 没有可下载的版本", slug); return -1; }
    cJSON *v;
    cJSON_ArrayForEach(v, vers) {
        cJSON *files = cJSON_GetObjectItem(v, "files");
        cJSON *f;
        cJSON_ArrayForEach(f, files) {
            const char *fn = cJSON_GetStringValue(cJSON_GetObjectItem(f, "filename"));
            const char *u = cJSON_GetStringValue(cJSON_GetObjectItem(f, "url"));
            if (fn && pymcl_endswith(fn, ".mrpack") && u) {
                char tmp[MAX_PATH], dest[PYMCL_PATH];
                GetTempPathA(MAX_PATH, tmp);
                snprintf(dest, sizeof(dest), "%spymcl_%s.mrpack", tmp, slug);
                char mir[1024];
                snprintf(mir, sizeof(mir), "%s", u);
                if (strstr(u, MODRINTH_CDN))
                    snprintf(mir, sizeof(mir), "%s%s", MCIM_MIRROR, u + strlen(MODRINTH_CDN));
                const char *ex[] = { u };
                emit(ctx, "下载整合包");
                int r = download_file(mir, ex, 1, dest, ctx, NULL, -1, NULL);
                if (r == 0) r = install_mrpack_file(dest, instance, ctx);
                pymcl_remove_tree(dest);
                cJSON_Delete(vers);
                return r;
            }
        }
    }
    cJSON_Delete(vers);
    pymcl_set_error("「%s」没有 .mrpack 文件", slug);
    return -1;
}

int install_modpack(const char *name, const char *source, cJSON *extra, pymcl_ctx *ctx) {
    const char *inst = extra ? cJSON_GetStringValue(cJSON_GetObjectItem(extra, "instance")) : NULL;
    if (!inst || !inst[0]) inst = config_str("default_instance", "default");
    const char *path = extra ? cJSON_GetStringValue(cJSON_GetObjectItem(extra, "path")) : NULL;
    if (!path) path = name;
    if (path && pymcl_file_exists(path)) {
        if (pymcl_endswith(path, ".mrpack")) return install_mrpack_file(path, inst, ctx);
        return install_cf_zip_file(path, inst, ctx);
    }
    if (source && (pymcl_startswith(source, "本地") || pymcl_ieq(source, "local"))) {
        pymcl_set_error("找不到整合包文件: %s", path ? path : "");
        return -1;
    }
    if (source && pymcl_istartswith(source, "curse")) {
        long long id = extra && cJSON_IsNumber(cJSON_GetObjectItem(extra, "id"))
            ? (long long)cJSON_GetObjectItem(extra, "id")->valuedouble : 0;
        const char *slug = extra ? cJSON_GetStringValue(cJSON_GetObjectItem(extra, "slug")) : NULL;
        if (!id) {
            char s[128] = {0}; long long cf = 0; char t[128];
            catalog_lookup_pack(name, s, sizeof(s), &cf, t, sizeof(t));
            id = cf; if (!slug) slug = s[0] ? s : name;
        }
        if (!id) { pymcl_set_error("无法解析整合包: %s", name); return -1; }
        return install_cf_modpack_id(id, inst, slug, ctx);
    }
    const char *slug = extra ? cJSON_GetStringValue(cJSON_GetObjectItem(extra, "slug")) : NULL;
    if (!slug) {
        char s[128] = {0}; long long cf = 0; char t[128];
        catalog_lookup_pack(name, s, sizeof(s), &cf, t, sizeof(t));
        slug = s[0] ? s : name;
        if (cf && (!source || !pymcl_ieq(source, "modrinth")))
            return install_cf_modpack_id(cf, inst, slug, ctx);
    }
    return install_mr_slug(slug ? slug : name, inst, ctx);
}
