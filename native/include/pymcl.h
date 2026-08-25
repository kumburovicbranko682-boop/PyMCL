#pragma once
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0601
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <shellapi.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdarg.h>
#include <time.h>
#include "cJSON.h"

#ifdef __cplusplus
extern "C" {
#endif

#define PYMCL_APP_NAME "PyMCL"
#define PYMCL_APP_VERSION "1.0.0"
#define PYMCL_LAUNCHER_NAME "PyMCL"
#define PYMCL_LAUNCHER_VERSION "1.0.0"
#define PYMCL_UA "PyMCL/1.0.0 (c; +minecraft launcher)"
#define PYMCL_MS_CLIENT_DEFAULT "00000000402b5328"
#define PYMCL_JAVA_AUTO "自动选择"
#define PYMCL_PATH 4096
#define PYMCL_ERR 1024

#define BMCLAPI "https://bmclapi2.bangbang93.com"
#define MOJANG_LIBS "https://libraries.minecraft.net/"
#define FABRIC_META "https://meta.fabricmc.net/v2"
#define QUILT_META "https://meta.quiltmc.org/v3"
#define FORGE_MAVEN "https://maven.minecraftforge.net/net/minecraftforge/forge"
#define NEOFORGE_MAVEN "https://maven.neoforged.net/releases/net/neoforged/neoforge"
#define MODRINTH_API "https://api.modrinth.com/v2"
#define MCIM_MIRROR "https://mod.mcimirror.top"
#define MODRINTH_CDN "https://cdn.modrinth.com"
#define CF_OFFICIAL "https://api.curseforge.com/v1"
#define CF_CLASS_MOD 6
#define CF_CLASS_MODPACK 4471
#define CF_CLASS_RESOURCEPACK 12
#define CF_CLASS_SHADER 6552
#define CF_CLASS_DATAPACK 6945

typedef void (*pymcl_progress_fn)(void *ud, const char *msg, long long done, long long total);
typedef int (*pymcl_cancel_fn)(void *ud);
typedef void (*pymcl_log_fn)(void *ud, const char *text);

typedef struct {
    pymcl_progress_fn on_progress;
    pymcl_log_fn on_log;
    pymcl_cancel_fn cancel;
    void *ud;
    int threads;
    /* 跳过资源文件校验/下载（UI 勾选 extra.skip_assets）。位置初始化的
     * 旧调用点自动补 0 = 不跳过。 */
    int skip_assets;
} pymcl_ctx;

/* ---------- error / log ---------- */
const char *pymcl_error(void);
void pymcl_set_error(const char *fmt, ...);
void pymcl_log(const char *fmt, ...);

/* ---------- strings / paths ---------- */
char *pymcl_strdup(const char *s);
int pymcl_snprintf(char *buf, size_t n, const char *fmt, ...);
void pymcl_path_join(char *out, size_t n, const char *a, const char *b);
void pymcl_path_join3(char *out, size_t n, const char *a, const char *b, const char *c);
const char *pymcl_basename(const char *p);
void pymcl_parent(const char *p, char *out, size_t n);
int pymcl_endswith(const char *s, const char *suf);
int pymcl_startswith(const char *s, const char *pre);
int pymcl_istartswith(const char *s, const char *pre);
int pymcl_ieq(const char *a, const char *b);
int pymcl_icontains(const char *hay, const char *needle);
void pymcl_replace_char(char *s, char a, char b);
wchar_t *pymcl_u8_to_wide(const char *s);
char *pymcl_wide_to_u8(const wchar_t *w);
int pymcl_ensure_dir(const char *path);
int pymcl_file_exists(const char *path);
int pymcl_dir_exists(const char *path);
long long pymcl_file_size(const char *path);
int pymcl_read_file(const char *path, char **out, size_t *len);
int pymcl_write_file(const char *path, const void *data, size_t len);
int pymcl_copy_file(const char *src, const char *dst);
void pymcl_remove_tree(const char *path);
void pymcl_copy_tree(const char *src, const char *dst);
cJSON *pymcl_read_json(const char *path);
int pymcl_write_json(const char *path, cJSON *obj);
int pymcl_sha1_file(const char *path, char hex[41]);
int pymcl_sha512_file(const char *path, char hex[129]);
void pymcl_sha1_bytes(const void *data, size_t n, char hex[41]);
void pymcl_md5_bytes(const void *data, size_t n, unsigned char out[16]);
int pymcl_file_matches(const char *path, const char *sha1, long long size);
int pymcl_extract_zip(const char *zip_path, const char *dest);
int pymcl_extract_jar_natives(const char *jar, const char *dest, cJSON *exclude);
int pymcl_zip_has(const char *zip_path, const char *inner);
char *pymcl_zip_read(const char *zip_path, const char *inner, size_t *len);
int pymcl_zip_extract_one(const char *zip_path, const char *inner, const char *dest);
int pymcl_open_folder(const char *path);
int pymcl_run_process(const char **argv, int argc, const char *cwd,
                      void (*on_line)(void *, const char *), void *ud, int timeout_sec);
/* pymcl_run_process_cancelable 在取消时的返回值（区别于失败 -1） */
#define PYMCL_PROC_CANCELLED (-2)
int pymcl_run_process_cancelable(const char **argv, int argc, const char *cwd,
                                 void (*on_line)(void *, const char *), void *ud,
                                 int timeout_sec, int (*cancel)(void *), void *cud);
HANDLE pymcl_spawn_process(const char **argv, int argc, const char *cwd, HANDLE *out_read);
void pymcl_dashed_uuid(const char *in, char out[40]);
void pymcl_offline_uuid(const char *name, char out[40]);
void pymcl_format_size(double n, char *out, size_t cap);
int pymcl_maven_path(const char *name, const char *suffix, char *out, size_t n);
int pymcl_check_rules(cJSON *rules, int has_custom_res);
void pymcl_replace_placeholders(const char *text, cJSON *map, char *out, size_t n);
int pymcl_has_placeholder(const char *text);
const char *pymcl_os_name(void);
const char *pymcl_arch(void);
int pymcl_is_windows(void);
void pymcl_native_arch_token(char *out, size_t n);

/* ---------- root / config ---------- */
extern char g_root[PYMCL_PATH];
void pymcl_set_root(const char *root);
void pymcl_instances_dir(char *out, size_t n);
void pymcl_java_dir(char *out, size_t n);
void pymcl_cache_dir(char *out, size_t n);
void config_init(void);
cJSON *config_obj(void);
const char *config_str(const char *key, const char *def);
int config_int(const char *key, int def);
int config_bool(const char *key, int def);
void config_set_str(const char *key, const char *val);
void config_set_int(const char *key, int val);
void config_set_bool(const char *key, int v);
void config_save(void);
void config_libraries_dir(const char *instance_path, char *out, size_t n);
void config_assets_dir(const char *instance_path, char *out, size_t n);

/* ---------- http / download ---------- */
typedef struct {
    int status;
    char *body;
    size_t len;
    long long content_length;
} http_resp;

void http_resp_free(http_resp *r);
int http_init(void);
void http_shutdown(void);
int http_get(const char *url, http_resp *r, const char *extra_hdr, int timeout);
int http_get_query(const char *url, const char *query, http_resp *r, const char *extra_hdr, int timeout);
int http_post_form(const char *url, const char *form, http_resp *r, int timeout);
int http_post_json(const char *url, const char *json, http_resp *r, const char *extra_hdr, int timeout);
cJSON *http_get_json(const char *url, int timeout);
cJSON *http_get_json_hdr(const char *url, const char *extra_hdr, int timeout);
int http_download_one(const char *url, const char *dest, pymcl_ctx *ctx,
                      const char *sha1, long long size, const char *sha512, int timeout);
int expand_urls(const char *url, char ***out, int *n);
void free_urls(char **u, int n);
int download_file(const char *url, const char **extra, int nextra, const char *dest,
                  pymcl_ctx *ctx, const char *sha1, long long size, const char *sha512);
int download_all(cJSON *tasks, const char *message, pymcl_ctx *ctx);
cJSON *fetch_json_mirrors(const char **urls, int n, int timeout);
char *fetch_text_mirrors(const char **urls, int n, int timeout);

/* ---------- instances ---------- */
int instance_list(cJSON **out);
int instance_path(const char *name, char *out, size_t n);
int instance_create(const char *name, cJSON *meta);
int instance_delete(const char *name);
int instance_rename(const char *name, const char *new_name);
cJSON *instance_meta(const char *name);
int instance_set_meta(const char *name, const char *key, cJSON *val);
void instance_ensure_dirs(const char *name);
int instance_installed_ids(const char *name, cJSON **out);
cJSON *instance_version_json(const char *name, const char *vid);
cJSON *instance_resolved_version(const char *name, const char *vid);
int instance_has_version(const char *name, const char *vid);
void instance_java_pref(const char *name, char *out, size_t n);
void instance_set_java_pref(const char *name, const char *java);
void instance_versions_dir(const char *name, char *out, size_t n);
void instance_libraries_dir(const char *name, char *out, size_t n);
void instance_assets_dir(const char *name, char *out, size_t n);
void instance_natives_dir(const char *name, const char *vid, cJSON *vjson, char *out, size_t n);
void sanitize_instance_name(const char *raw, char *out, size_t n);
void unique_instance_name(const char *raw, char *out, size_t n);

/* ---------- manifest ---------- */
cJSON *manifest_get(int force);
cJSON *manifest_list_remote(int force);
cJSON *manifest_get_version(const char *id, int force);
cJSON *manifest_get_version_url(const char *url, const char *id);
cJSON *manifest_resolve_inherits(cJSON *vjson, cJSON *(*load)(const char *, void *), void *ud);
int manifest_is_legacy(cJSON *vjson);
void library_identity(cJSON *lib, char *out, size_t n);
char *manifest_resolve_playable(const char *vid);
int mc_version_tuple(const char *id, int *a, int *b, int *c);

/* ---------- java ---------- */
int java_get_major(const char *exe);
int java_required_major(cJSON *vjson);
int java_usable_for(cJSON *vjson, const char *exe);
cJSON *java_list_installed(void);
cJSON *java_list_system(void);
cJSON *java_all(void);
char *java_pick(cJSON *vjson, const char *prefer);
char *java_resolve_launch(cJSON *vjson, const char *prefer, pymcl_ctx *ctx);
char *java_install_adoptium(int major, const char *arch, pymcl_ctx *ctx);
char *java_install_mojang(const char *component, pymcl_ctx *ctx);
char *java_for_installer(const char *loader, pymcl_ctx *ctx);
char *java_version_output(const char *exe);
char *java_find_exe(const char *root);

/* ---------- installer ---------- */
int install_version(const char *instance, const char *version_id, pymcl_ctx *ctx);
char *install_fabric(const char *instance, const char *mc, const char *loader, pymcl_ctx *ctx);
char *install_quilt(const char *instance, const char *mc, const char *loader, pymcl_ctx *ctx);
char *install_forge(const char *instance, const char *mc, const char *forge, pymcl_ctx *ctx);
char *install_neoforge(const char *instance, const char *mc, const char *ver, pymcl_ctx *ctx);
int uninstall_version(const char *instance, const char *vid);
int extract_natives(const char *instance, cJSON *resolved, const char *vid, char *out, size_t n);
int natives_present(const char *dir);
char *select_native_classifier(cJSON *lib);
int install_loader(const char *instance, const char *loader, const char *ver, const char *mc, pymcl_ctx *ctx, char *vid_out, size_t n);

/* ---------- launcher ---------- */
/* 启动参数侧的版本/全局设置（对齐 mclauncher/launch_flow.prepare）。
 * 0 / 空串表示无覆盖。 */
typedef struct {
    int memory_mb;
    int window_width;
    int window_height;
    int fullscreen;            /* window_mode ∈ {maximize, fullscreen} */
    int pre_launch_wait;       /* 缺省 1（对齐 DEFAULTS pre_launch_wait=True） */
    char gc[32];               /* 生效 GC 预设键：版本 > 全局 gc_preset > auto */
    char jvm_args[2048];       /* 版本设置 jvm_args 原文 */
    char game_args[2048];      /* 版本设置 game_args 原文 */
    char server[256];          /* 直连服务器 */
    char port[16];
    char pre_launch[1024];     /* 启动前命令（shell 原样执行） */
    char post_launch[1024];    /* 退出后命令 */
    char nide8_id[128];        /* 版本设置的统一通行证服务器 ID */
} pymcl_launch_prep;
void pymcl_launch_prep_load(const char *instance, const char *version_id, pymcl_launch_prep *out);
int build_launch_command(const char *instance, const char *version, cJSON *account_props,
                         const char *java_exe, int memory_mb, int width, int height,
                         cJSON *extra_game_args,
                         char ***argv, int *argc, char *natives_out, size_t nn);
HANDLE game_spawn(const char **argv, int argc, const char *cwd, HANDLE *pipe);
void game_kill(HANDLE proc);

/* ---------- auth ---------- */
cJSON *accounts_load(void);
void accounts_save(cJSON *root);
cJSON *account_offline(const char *username);
void account_apply_offline_skin(cJSON *acc, const char *skin);
cJSON *account_launch_props(cJSON *acc);
cJSON *account_ensure_valid(cJSON *acc);
int ms_login(pymcl_ctx *ctx, void (*on_code)(void *, const char *, const char *), void *ud, cJSON **out_acc);

/* ---------- catalog / mods / packs ---------- */
void catalog_init(void);
cJSON *catalog_popular_mods(const char *source);
cJSON *catalog_popular_packs(const char *source);
int catalog_lookup_mod(const char *q, char *slug, size_t ns, long long *cf, char *title, size_t nt);
int catalog_lookup_pack(const char *q, char *slug, size_t ns, long long *cf, char *title, size_t nt);
cJSON *search_mods(const char *query, const char *source, const char *game_version);
cJSON *search_modpacks(const char *query, const char *source, const char *game_version);
cJSON *search_content(const char *kind, const char *query, const char *source, const char *game_version);
int install_mod(const char *instance, const char *name, cJSON *extra, pymcl_ctx *ctx);
int install_content(const char *kind, const char *instance, const char *name, cJSON *extra, pymcl_ctx *ctx);
int install_modpack(const char *name, const char *source, cJSON *extra, pymcl_ctx *ctx);
cJSON *list_instance_files(const char *instance, const char *subdir);
int delete_instance_file(const char *instance, const char *subdir, const char *filename);
int pymcl_version_hidden(const char *instance, const char *version_id);
int pymcl_apply_isolation(const char *instance, const char *version_id, char *gdir_out, size_t n);
void pymcl_playtime_record(const char *instance_name, const char *version_id, long long duration_sec);
void pymcl_format_playtime(long long seconds, char *out, size_t n);
void pymcl_global_mods_root(char *out, size_t n);
int pymcl_global_mods_apply(const char *game_mods_dir);
int pymcl_nide8_sid(const char *raw, char *out, size_t n);
void pymcl_authlib_normalize_api(const char *raw, char *out, size_t n);
int pymcl_ensure_auth_agents(cJSON *account_props, const char *prep_nide8, pymcl_ctx *ctx);

/* ---------- backend / server ---------- */
typedef void (*sse_emit_fn)(const char *event, cJSON *data);
void backend_init(sse_emit_fn emit);
cJSON *backend_call(const char *method, cJSON *params);
void backend_shutdown(void);
int server_run(const char *host, int port, const char *token);
cJSON *py_rpc_call(const char *method, cJSON *params);
cJSON *py_rpc_call_t(const char *method, cJSON *params, int timeout_secs);
/* 带取消回调的变体：cancel(cud) 返回非 0 时立即杀掉 Python 子进程 */
cJSON *py_rpc_call_c(const char *method, cJSON *params, int timeout_secs,
                     int (*cancel)(void *), void *cud);
cJSON *rpc_align_call(const char *method, cJSON *params, sse_emit_fn emit);

#ifdef __cplusplus
}
#endif
