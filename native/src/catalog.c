#include "pymcl.h"

static cJSON *g_cat;

static void try_load(const char *p) {
    if (g_cat) return;
    if (pymcl_file_exists(p)) g_cat = pymcl_read_json(p);
}

void catalog_init(void) {
    if (g_cat) return;
    char p[PYMCL_PATH];
    pymcl_path_join3(p, sizeof(p), g_root, "native", "data");
    pymcl_path_join(p, sizeof(p), p, "catalog.json");
    try_load(p);
    pymcl_path_join3(p, sizeof(p), g_root, "data", "catalog.json");
    try_load(p);
    wchar_t wexe[PYMCL_PATH];
    if (GetModuleFileNameW(NULL, wexe, PYMCL_PATH)) {
        char *exe = pymcl_wide_to_u8(wexe);
        if (exe) {
            char dir[PYMCL_PATH], parent[PYMCL_PATH];
            pymcl_parent(exe, dir, sizeof(dir));
            free(exe);
            pymcl_path_join(p, sizeof(p), dir, "catalog.json");
            try_load(p);
            pymcl_parent(dir, parent, sizeof(parent));
            pymcl_path_join3(p, sizeof(p), parent, "data", "catalog.json");
            try_load(p);
        }
    }
    if (!g_cat) g_cat = cJSON_CreateObject();
}

static int lookup_map(cJSON *map, const char *q, char *slug, size_t ns, long long *cf, char *title, size_t nt) {
    if (slug) slug[0] = 0;
    if (cf) *cf = 0;
    if (title) title[0] = 0;
    if (!cJSON_IsObject(map) || !q) return 0;
    cJSON *it = NULL;
    cJSON_ArrayForEach(it, map) {
        if (pymcl_ieq(it->string, q)) {
            cJSON *s = cJSON_GetObjectItem(it, "slug");
            cJSON *c = cJSON_GetObjectItem(it, "cf");
            cJSON *t = cJSON_GetObjectItem(it, "title");
            if (slug && cJSON_IsString(s)) snprintf(slug, ns, "%s", s->valuestring);
            if (cf && cJSON_IsNumber(c)) *cf = (long long)c->valuedouble;
            if (title && cJSON_IsString(t)) snprintf(title, nt, "%s", t->valuestring);
            return 1;
        }
    }
    /* fuzzy */
    cJSON_ArrayForEach(it, map) {
        if (pymcl_icontains(it->string, q) || pymcl_icontains(q, it->string)) {
            cJSON *s = cJSON_GetObjectItem(it, "slug");
            cJSON *c = cJSON_GetObjectItem(it, "cf");
            cJSON *t = cJSON_GetObjectItem(it, "title");
            if (slug && cJSON_IsString(s)) snprintf(slug, ns, "%s", s->valuestring);
            if (cf && cJSON_IsNumber(c)) *cf = (long long)c->valuedouble;
            if (title && cJSON_IsString(t)) snprintf(title, nt, "%s", t->valuestring);
            return 1;
        }
    }
    return 0;
}

int catalog_lookup_mod(const char *q, char *slug, size_t ns, long long *cf, char *title, size_t nt) {
    catalog_init();
    return lookup_map(cJSON_GetObjectItem(g_cat, "mod_aliases"), q, slug, ns, cf, title, nt);
}
int catalog_lookup_pack(const char *q, char *slug, size_t ns, long long *cf, char *title, size_t nt) {
    catalog_init();
    return lookup_map(cJSON_GetObjectItem(g_cat, "pack_aliases"), q, slug, ns, cf, title, nt);
}

cJSON *catalog_popular_mods(const char *source) {
    catalog_init();
    cJSON *src = cJSON_GetObjectItem(g_cat, "popular_mods");
    cJSON *out = cJSON_CreateArray();
    if (!cJSON_IsArray(src)) return out;
    int want_cf = source && (pymcl_ieq(source, "curseforge") || pymcl_istartswith(source, "curse"));
    int want_mr = !source || !source[0] || pymcl_ieq(source, "全部") || pymcl_ieq(source, "all") || pymcl_ieq(source, "modrinth");
    if (want_cf) want_mr = source && pymcl_ieq(source, "全部") ? 1 : (pymcl_istartswith(source, "curse") ? 0 : want_mr);
    if (source && pymcl_istartswith(source, "curse")) { want_cf = 1; want_mr = 0; }
    if (source && pymcl_ieq(source, "modrinth")) { want_mr = 1; want_cf = 0; }
    cJSON *it;
    cJSON_ArrayForEach(it, src) {
        const char *s = cJSON_GetStringValue(cJSON_GetObjectItem(it, "source"));
        if (s && pymcl_ieq(s, "curseforge") && !want_cf) continue;
        if (s && pymcl_ieq(s, "modrinth") && !want_mr) continue;
        cJSON *row = cJSON_CreateObject();
        cJSON_AddStringToObject(row, "name", cJSON_GetStringValue(cJSON_GetObjectItem(it, "name")) ?: "?");
        cJSON_AddStringToObject(row, "author", pymcl_ieq(s, "curseforge") ? "CurseForge" : "Modrinth");
        cJSON_AddNumberToObject(row, "downloads", 0);
        cJSON *key = cJSON_GetObjectItem(it, "key");
        if (cJSON_IsNumber(key)) cJSON_AddNumberToObject(row, "id", key->valuedouble);
        else if (cJSON_IsString(key) && pymcl_ieq(s, "modrinth")) cJSON_AddStringToObject(row, "slug", key->valuestring);
        if (cJSON_IsString(key) && pymcl_ieq(s, "curseforge")) {
            /* skip */
        }
        cJSON_AddStringToObject(row, "source", s ? s : "modrinth");
        cJSON_AddItemToArray(out, row);
    }
    return out;
}

cJSON *catalog_popular_packs(const char *source) {
    catalog_init();
    cJSON *src = cJSON_GetObjectItem(g_cat, "popular_modpacks");
    cJSON *out = cJSON_CreateArray();
    if (!cJSON_IsArray(src)) return out;
    int want_cf = 1, want_mr = 1;
    if (source && pymcl_istartswith(source, "curse")) { want_mr = 0; want_cf = 1; }
    if (source && pymcl_ieq(source, "modrinth")) { want_mr = 1; want_cf = 0; }
    cJSON *it;
    cJSON_ArrayForEach(it, src) {
        const char *s = cJSON_GetStringValue(cJSON_GetObjectItem(it, "source"));
        if (s && pymcl_ieq(s, "curseforge") && !want_cf) continue;
        if (s && pymcl_ieq(s, "modrinth") && !want_mr) continue;
        cJSON *row = cJSON_CreateObject();
        cJSON_AddStringToObject(row, "name", cJSON_GetStringValue(cJSON_GetObjectItem(it, "name")) ?: "?");
        cJSON_AddStringToObject(row, "author", pymcl_ieq(s, "curseforge") ? "CurseForge" : "Modrinth");
        cJSON_AddNumberToObject(row, "downloads", 0);
        cJSON *key = cJSON_GetObjectItem(it, "key");
        if (cJSON_IsNumber(key)) cJSON_AddNumberToObject(row, "id", key->valuedouble);
        const char *slug = cJSON_GetStringValue(cJSON_GetObjectItem(it, "slug"));
        if (cJSON_IsString(key) && !slug) slug = key->valuestring;
        if (slug) cJSON_AddStringToObject(row, "slug", slug);
        cJSON_AddStringToObject(row, "source", s ? s : "modrinth");
        cJSON_AddItemToArray(out, row);
    }
    return out;
}
