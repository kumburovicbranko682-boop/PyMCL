#include "pymcl.h"

static cJSON *g_cfg;

static void apply_defaults(cJSON *o) {
    if (!cJSON_GetObjectItem(o, "instances_dir")) cJSON_AddStringToObject(o, "instances_dir", ".minecraft");
    if (!cJSON_GetObjectItem(o, "default_instance")) cJSON_AddStringToObject(o, "default_instance", "default");
    if (!cJSON_GetObjectItem(o, "java_dir")) cJSON_AddStringToObject(o, "java_dir", "java");
    if (!cJSON_GetObjectItem(o, "shared_libraries")) cJSON_AddBoolToObject(o, "shared_libraries", 0);
    if (!cJSON_GetObjectItem(o, "shared_assets")) cJSON_AddBoolToObject(o, "shared_assets", 0);
    if (!cJSON_GetObjectItem(o, "memory_mb")) cJSON_AddNumberToObject(o, "memory_mb", 4096);
    if (!cJSON_GetObjectItem(o, "download_threads")) cJSON_AddNumberToObject(o, "download_threads", 8);
    if (!cJSON_GetObjectItem(o, "width")) cJSON_AddNumberToObject(o, "width", 854);
    if (!cJSON_GetObjectItem(o, "height")) cJSON_AddNumberToObject(o, "height", 480);
    if (!cJSON_GetObjectItem(o, "microsoft_client_id"))
        cJSON_AddStringToObject(o, "microsoft_client_id", PYMCL_MS_CLIENT_DEFAULT);
    if (!cJSON_GetObjectItem(o, "curseforge_api_key"))
        cJSON_AddStringToObject(o, "curseforge_api_key",
            "$2a$10$o8pygPrhvKBHuuh5imL2W.LCNFhB15zBYAExXx/TqTx/Zp5px2lxu");
}

static int migrate_legacy_instances_dir(cJSON *o) {
    cJSON *v = cJSON_GetObjectItem(o, "instances_dir");
    if (!cJSON_IsString(v) || !v->valuestring) return 0;
    if (strcmp(v->valuestring, "instances") != 0) return 0;
    char oldp[PYMCL_PATH], newp[PYMCL_PATH];
    pymcl_path_join(oldp, sizeof(oldp), g_root, "instances");
    pymcl_path_join(newp, sizeof(newp), g_root, ".minecraft");
    if (pymcl_dir_exists(oldp) && !pymcl_dir_exists(newp)) {
        wchar_t *wa = pymcl_u8_to_wide(oldp), *wb = pymcl_u8_to_wide(newp);
        MoveFileW(wa, wb);
        free(wa);
        free(wb);
    }
    cJSON_DeleteItemFromObject(o, "instances_dir");
    cJSON_AddStringToObject(o, "instances_dir", ".minecraft");
    return 1;
}

void config_init(void) {
    char p[PYMCL_PATH];
    pymcl_path_join(p, sizeof(p), g_root, "config.json");
    g_cfg = pymcl_read_json(p);
    if (!g_cfg || !cJSON_IsObject(g_cfg)) {
        if (g_cfg) cJSON_Delete(g_cfg);
        g_cfg = cJSON_CreateObject();
    }
    int migrated = migrate_legacy_instances_dir(g_cfg);
    apply_defaults(g_cfg);
    if (migrated) config_save();
}

cJSON *config_obj(void) { return g_cfg; }

const char *config_str(const char *key, const char *def) {
    cJSON *v = g_cfg ? cJSON_GetObjectItem(g_cfg, key) : NULL;
    return cJSON_IsString(v) ? v->valuestring : def;
}
int config_int(const char *key, int def) {
    cJSON *v = g_cfg ? cJSON_GetObjectItem(g_cfg, key) : NULL;
    return cJSON_IsNumber(v) ? (int)v->valuedouble : def;
}
int config_bool(const char *key, int def) {
    cJSON *v = g_cfg ? cJSON_GetObjectItem(g_cfg, key) : NULL;
    if (cJSON_IsBool(v)) return cJSON_IsTrue(v);
    if (cJSON_IsNumber(v)) return v->valuedouble != 0;
    return def;
}
/* 社区资源源模式（community_source，与 Qt 侧共用同一份 config.json）：
 * 三态对齐 mclauncher/source.py community_mode——auto=官方优先、MCIM 兜底；
 * mcim=镜像优先；official=仅官方。 */
int config_community_official_only(void) {
    return pymcl_ieq(config_str("community_source", "auto"), "official");
}
int config_community_mirror_first(void) {
    return pymcl_ieq(config_str("community_source", "auto"), "mcim");
}
void config_set_str(const char *key, const char *val) {
    if (!g_cfg) return;
    cJSON_DeleteItemFromObject(g_cfg, key);
    cJSON_AddStringToObject(g_cfg, key, val ? val : "");
}
void config_set_int(const char *key, int val) {
    if (!g_cfg) return;
    cJSON_DeleteItemFromObject(g_cfg, key);
    cJSON_AddNumberToObject(g_cfg, key, val);
}
void config_set_bool(const char *key, int v) {
    if (!g_cfg) return;
    cJSON_DeleteItemFromObject(g_cfg, key);
    cJSON_AddBoolToObject(g_cfg, key, v);
}
void config_save(void) {
    char p[PYMCL_PATH];
    pymcl_path_join(p, sizeof(p), g_root, "config.json");
    pymcl_write_json(p, g_cfg);
}
void config_libraries_dir(const char *instance_path, char *out, size_t n) {
    if (config_bool("shared_libraries", 0))
        pymcl_path_join3(out, n, g_root, "shared", "libraries");
    else
        pymcl_path_join(out, n, instance_path, "libraries");
}
void config_assets_dir(const char *instance_path, char *out, size_t n) {
    if (config_bool("shared_assets", 0))
        pymcl_path_join3(out, n, g_root, "shared", "assets");
    else
        pymcl_path_join(out, n, instance_path, "assets");
}
