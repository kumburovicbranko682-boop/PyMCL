#include "pymcl.h"
#include <curl/curl.h>
#include <pthread.h>

static pthread_mutex_t g_path_mu = PTHREAD_MUTEX_INITIALIZER;
static char g_locked_paths[64][PYMCL_PATH];
static int g_nlocked;

typedef struct { char *buf; size_t len, cap; } mem_buf;
static size_t mem_write(char *ptr, size_t sz, size_t nm, void *ud) {
    mem_buf *m = (mem_buf *)ud;
    size_t n = sz * nm;
    if (m->len + n + 1 > m->cap) {
        size_t nc = (m->len + n + 1) * 2 + 4096;
        char *nb = (char *)realloc(m->buf, nc);
        if (!nb) return 0;
        m->buf = nb; m->cap = nc;
    }
    memcpy(m->buf + m->len, ptr, n);
    m->len += n;
    m->buf[m->len] = 0;
    return n;
}
static size_t file_write(char *ptr, size_t sz, size_t nm, void *ud) {
    return fwrite(ptr, sz, nm, (FILE *)ud);
}

void http_resp_free(http_resp *r) {
    if (!r) return;
    free(r->body);
    r->body = NULL; r->len = 0;
}

static char g_ca[PYMCL_PATH];

static void locate_ca(void) {
    wchar_t wexe[PYMCL_PATH];
    if (GetModuleFileNameW(NULL, wexe, PYMCL_PATH)) {
        char *exe = pymcl_wide_to_u8(wexe);
        if (exe) {
            char dir[PYMCL_PATH];
            pymcl_parent(exe, dir, sizeof(dir));
            free(exe);
            pymcl_path_join(g_ca, sizeof(g_ca), dir, "curl-ca-bundle.crt");
            if (pymcl_file_exists(g_ca)) return;
            pymcl_path_join(g_ca, sizeof(g_ca), dir, "ca-bundle.crt");
            if (pymcl_file_exists(g_ca)) return;
        }
    }
    const char *cands[] = {
        "C:\\msys64\\mingw64\\etc\\ssl\\certs\\ca-bundle.crt",
        "C:\\msys64\\usr\\ssl\\certs\\ca-bundle.crt",
        NULL
    };
    for (int i = 0; cands[i]; i++) {
        if (pymcl_file_exists(cands[i])) { snprintf(g_ca, sizeof(g_ca), "%s", cands[i]); return; }
    }
    g_ca[0] = 0;
}

int http_init(void) {
    if (curl_global_init(CURL_GLOBAL_DEFAULT) != 0) return -1;
    locate_ca();
    return 0;
}
void http_shutdown(void) { curl_global_cleanup(); }

static void apply_common(CURL *c, const char *url, const char *extra_hdr, int timeout) {
    curl_easy_setopt(c, CURLOPT_URL, url);
    curl_easy_setopt(c, CURLOPT_USERAGENT, PYMCL_UA);
    curl_easy_setopt(c, CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(c, CURLOPT_MAXREDIRS, 8L);
    curl_easy_setopt(c, CURLOPT_TIMEOUT, (long)(timeout > 0 ? timeout : 60));
    curl_easy_setopt(c, CURLOPT_CONNECTTIMEOUT, 20L);
    curl_easy_setopt(c, CURLOPT_SSL_VERIFYPEER, 1L);
    curl_easy_setopt(c, CURLOPT_SSL_VERIFYHOST, 2L);
    if (g_ca[0]) curl_easy_setopt(c, CURLOPT_CAINFO, g_ca);
    curl_easy_setopt(c, CURLOPT_ACCEPT_ENCODING, "identity");
    if (extra_hdr && extra_hdr[0]) {
        struct curl_slist *h = NULL;
        const char *p = extra_hdr;
        while (*p) {
            const char *nl = strchr(p, '\n');
            char line[1024];
            size_t n = nl ? (size_t)(nl - p) : strlen(p);
            if (n >= sizeof(line)) n = sizeof(line) - 1;
            memcpy(line, p, n); line[n] = 0;
            if (line[0]) h = curl_slist_append(h, line);
            p = nl ? nl + 1 : p + n;
        }
        curl_easy_setopt(c, CURLOPT_HTTPHEADER, h);
        /* leaked slist on easy cleanup is ok for short-lived easy handles if we store it... */
        curl_easy_setopt(c, CURLOPT_PRIVATE, h);
    }
}

static void free_priv_hdr(CURL *c) {
    char *priv = NULL;
    curl_easy_getinfo(c, CURLINFO_PRIVATE, &priv);
    if (priv) curl_slist_free_all((struct curl_slist *)priv);
}

static int do_get(const char *url, http_resp *r, const char *extra, int timeout) {
    memset(r, 0, sizeof(*r));
    CURL *c = curl_easy_init();
    if (!c) return -1;
    mem_buf m = {0};
    apply_common(c, url, extra, timeout);
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, mem_write);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, &m);
    CURLcode rc = curl_easy_perform(c);
    long code = 0;
    curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &code);
    curl_off_t cl = 0;
    curl_easy_getinfo(c, CURLINFO_CONTENT_LENGTH_DOWNLOAD_T, &cl);
    free_priv_hdr(c);
    curl_easy_cleanup(c);
    r->status = (int)code;
    r->body = m.buf;
    r->len = m.len;
    r->content_length = (long long)cl;
    if (rc != CURLE_OK) {
        pymcl_set_error("HTTP 失败 %s: %s", url, curl_easy_strerror(rc));
        return -1;
    }
    if (code >= 400) {
        pymcl_set_error("HTTP %ld: %s", code, url);
        return -1;
    }
    return 0;
}

int http_get(const char *url, http_resp *r, const char *extra_hdr, int timeout) {
    return do_get(url, r, extra_hdr, timeout);
}

static char *url_encode_component(CURL *c, const char *s) {
    char *e = curl_easy_escape(c, s, 0);
    return e;
}

int http_get_query(const char *url, const char *query, http_resp *r, const char *extra_hdr, int timeout) {
    if (!query || !query[0]) return http_get(url, r, extra_hdr, timeout);
    char full[4096];
    snprintf(full, sizeof(full), "%s%s%s", url, strchr(url, '?') ? "&" : "?", query);
    return http_get(full, r, extra_hdr, timeout);
}

int http_post_form(const char *url, const char *form, http_resp *r, int timeout) {
    memset(r, 0, sizeof(*r));
    CURL *c = curl_easy_init();
    if (!c) return -1;
    mem_buf m = {0};
    apply_common(c, url, NULL, timeout);
    curl_easy_setopt(c, CURLOPT_POSTFIELDS, form);
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, mem_write);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, &m);
    CURLcode rc = curl_easy_perform(c);
    long code = 0;
    curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &code);
    free_priv_hdr(c);
    curl_easy_cleanup(c);
    r->status = (int)code;
    r->body = m.buf;
    r->len = m.len;
    if (rc != CURLE_OK) { pymcl_set_error("%s", curl_easy_strerror(rc)); return -1; }
    return 0;
}

int http_post_json(const char *url, const char *json, http_resp *r, const char *extra_hdr, int timeout) {
    memset(r, 0, sizeof(*r));
    CURL *c = curl_easy_init();
    if (!c) return -1;
    mem_buf m = {0};
    char hdrs[2048];
    snprintf(hdrs, sizeof(hdrs), "Content-Type: application/json\n%s", extra_hdr ? extra_hdr : "");
    apply_common(c, url, hdrs, timeout);
    curl_easy_setopt(c, CURLOPT_POSTFIELDS, json);
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, mem_write);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, &m);
    CURLcode rc = curl_easy_perform(c);
    long code = 0;
    curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &code);
    free_priv_hdr(c);
    curl_easy_cleanup(c);
    r->status = (int)code;
    r->body = m.buf;
    r->len = m.len;
    if (rc != CURLE_OK) { pymcl_set_error("%s", curl_easy_strerror(rc)); return -1; }
    if (code >= 400) { pymcl_set_error("HTTP %ld: %s", code, url); return -1; }
    return 0;
}

cJSON *http_get_json(const char *url, int timeout) {
    return http_get_json_hdr(url, NULL, timeout);
}
cJSON *http_get_json_hdr(const char *url, const char *extra_hdr, int timeout) {
    http_resp r;
    if (http_get(url, &r, extra_hdr, timeout) != 0) { http_resp_free(&r); return NULL; }
    cJSON *j = cJSON_Parse(r.body ? r.body : "{}");
    http_resp_free(&r);
    return j;
}

static int is_github(const char *u) {
    return u && (strstr(u, "github.com") || strstr(u, "githubusercontent.com"));
}

int expand_urls(const char *url, char ***out, int *n) {
    *out = NULL; *n = 0;
    if (!url || !url[0]) return 0;
    char tmp[16][PYMCL_PATH];
    int c = 0;
    if (is_github(url)) {
        static const char *px[] = {
            "https://ghfast.top/", "https://gh.llkk.cc/", "https://ghproxy.vip/",
            "https://gh-proxy.com/", "https://v6.gh-proxy.org/", "https://cdn.gh-proxy.com/",
        };
        for (int i = 0; i < 6; i++) {
            snprintf(tmp[c], sizeof(tmp[c]), "%s%s", px[i], url);
            c++;
        }
        snprintf(tmp[c++], sizeof(tmp[0]), "%s", url);
    } else {
        struct { const char *off, *mir; } mv[] = {
            {"https://maven.minecraftforge.net/", BMCLAPI "/maven/"},
            {"https://files.minecraftforge.net/maven/", BMCLAPI "/maven/"},
            {"https://maven.neoforged.net/releases/", BMCLAPI "/maven/"},
            {"https://libraries.minecraft.net/", BMCLAPI "/maven/"},
        };
        int hit = 0;
        for (int i = 0; i < 4; i++) {
            size_t L = strlen(mv[i].off);
            if (strncmp(url, mv[i].off, L) == 0) {
                snprintf(tmp[c++], sizeof(tmp[0]), "%s%s", mv[i].mir, url + L);
                snprintf(tmp[c++], sizeof(tmp[0]), "%s", url);
                hit = 1;
                break;
            }
        }
        if (!hit) snprintf(tmp[c++], sizeof(tmp[0]), "%s", url);
    }
    *out = (char **)calloc((size_t)c, sizeof(char *));
    for (int i = 0; i < c; i++) (*out)[i] = pymcl_strdup(tmp[i]);
    *n = c;
    return 0;
}
void free_urls(char **u, int n) {
    if (!u) return;
    for (int i = 0; i < n; i++) free(u[i]);
    free(u);
}

static void cf_headers(const char *url, char *out, size_t n) {
    out[0] = 0;
    if (url && (strstr(url, "api.curseforge.com") || strstr(url, "/curseforge/v1/"))) {
        const char *key = config_str("curseforge_api_key", "");
        if (key && key[0]) snprintf(out, n, "x-api-key: %s", key);
    }
}

typedef struct {
    FILE *f;
    long long got, expected;
    pymcl_ctx *ctx;
    const char *name;
    DWORD last;
} dl_state;

static size_t dl_write(char *ptr, size_t sz, size_t nm, void *ud) {
    dl_state *s = (dl_state *)ud;
    size_t n = fwrite(ptr, sz, nm, s->f);
    s->got += (long long)(n * sz);
    if (s->ctx && s->ctx->on_progress) {
        DWORD now = GetTickCount();
        if (now - s->last > 150) {
            s->last = now;
            char msg[256];
            snprintf(msg, sizeof(msg), "下载中 %s", s->name ? s->name : "");
            s->ctx->on_progress(s->ctx->ud, msg, s->got, s->expected);
        }
    }
    if (s->ctx && s->ctx->cancel && s->ctx->cancel(s->ctx->ud)) return 0;
    return n;
}

int http_download_one(const char *url, const char *dest, pymcl_ctx *ctx,
                      const char *sha1, long long size, const char *sha512, int timeout) {
    char parent[PYMCL_PATH];
    pymcl_parent(dest, parent, sizeof(parent));
    pymcl_ensure_dir(parent);
    char part[PYMCL_PATH];
    snprintf(part, sizeof(part), "%s.part", dest);
    wchar_t *w = pymcl_u8_to_wide(part);
    FILE *f = w ? _wfopen(w, L"wb") : NULL;
    free(w);
    if (!f) { pymcl_set_error("无法写入 %s", part); return -1; }
    CURL *c = curl_easy_init();
    if (!c) { fclose(f); return -1; }
    char extra[512];
    cf_headers(url, extra, sizeof(extra));
    apply_common(c, url, extra[0] ? extra : NULL, timeout > 0 ? timeout : 300);
    dl_state st = { f, 0, size, ctx, pymcl_basename(dest), 0 };
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, dl_write);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, &st);
    CURLcode rc = curl_easy_perform(c);
    long code = 0;
    curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &code);
    curl_off_t cl = 0;
    curl_easy_getinfo(c, CURLINFO_CONTENT_LENGTH_DOWNLOAD_T, &cl);
    free_priv_hdr(c);
    curl_easy_cleanup(c);
    fclose(f);
    if (rc != CURLE_OK || code >= 400) {
        pymcl_remove_tree(part);
        pymcl_set_error("HTTP %ld: %s", code, url);
        return -1;
    }
    if (cl > 0 && st.got != (long long)cl) {
        pymcl_remove_tree(part);
        pymcl_set_error("下载不完整 %s (%lld/%lld)", url, st.got, (long long)cl);
        return -1;
    }
    if (!pymcl_file_matches(part, sha1, size >= 0 ? size : -1)) {
        pymcl_remove_tree(part);
        pymcl_set_error("校验失败: %s", url);
        return -1;
    }
    if (sha512 && sha512[0]) {
        char hex[129];
        if (pymcl_sha512_file(part, hex) != 0 || _stricmp(hex, sha512) != 0) {
            pymcl_remove_tree(part);
            pymcl_set_error("sha512 校验失败: %s", url);
            return -1;
        }
    }
    wchar_t *wp = pymcl_u8_to_wide(part);
    wchar_t *wd = pymcl_u8_to_wide(dest);
    DeleteFileW(wd);
    BOOL ok = MoveFileW(wp, wd);
    free(wp); free(wd);
    if (!ok) { pymcl_set_error("无法重命名 %s", dest); return -1; }
    return 0;
}

int download_file(const char *url, const char **extra, int nextra, const char *dest,
                  pymcl_ctx *ctx, const char *sha1, long long size, const char *sha512) {
    if (pymcl_file_matches(dest, sha1, size >= 0 ? size : -1)) {
        if (!sha512 || !sha512[0]) return 0;
        char hex[129];
        if (pymcl_sha512_file(dest, hex) == 0 && _stricmp(hex, sha512) == 0) return 0;
    }
    char **cands = NULL; int nc = 0;
    expand_urls(url, &cands, &nc);
    for (int i = 0; i < nextra; i++) {
        char **more = NULL; int nm = 0;
        expand_urls(extra[i], &more, &nm);
        cands = (char **)realloc(cands, sizeof(char *) * (size_t)(nc + nm));
        for (int j = 0; j < nm; j++) cands[nc++] = more[j];
        free(more);
    }
    int last = -1;
    for (int i = 0; i < nc; i++) {
        if (ctx && ctx->cancel && ctx->cancel(ctx->ud)) {
            pymcl_set_error("用户取消");
            free_urls(cands, nc);
            return -1;
        }
        if (http_download_one(cands[i], dest, ctx, sha1, size, sha512, 300) == 0) {
            free_urls(cands, nc);
            return 0;
        }
        last = -1;
    }
    free_urls(cands, nc);
    return last;
}

int download_url_list(cJSON *urls, const char *dest, pymcl_ctx *ctx,
                      const char *sha1, long long size, const char *sha512) {
    int n = cJSON_IsArray(urls) ? cJSON_GetArraySize(urls) : 0;
    const char *first = NULL;
    const char *extras[32]; int ne = 0;
    for (int i = 0; i < n; i++) {
        const char *u = cJSON_GetStringValue(cJSON_GetArrayItem(urls, i));
        if (!u || !u[0]) continue;
        if (!first) first = u;
        else if (ne < 32) extras[ne++] = u;
    }
    if (!first) { pymcl_set_error("没有可下载文件"); return -1; }
    return download_file(first, extras, ne, dest, ctx, sha1, size, sha512);
}

typedef struct {
    cJSON *task;
    pymcl_ctx *ctx;
    int ok;
    char err[256];
} dl_job;

static void *dl_worker(void *p) {
    dl_job *j = (dl_job *)p;
    cJSON *t = j->task;
    const char *dest = cJSON_GetStringValue(cJSON_GetObjectItem(t, "dest"));
    const char *sha1 = cJSON_GetStringValue(cJSON_GetObjectItem(t, "sha1"));
    const char *sha512 = cJSON_GetStringValue(cJSON_GetObjectItem(t, "sha512"));
    long long size = -1;
    cJSON *sz = cJSON_GetObjectItem(t, "size");
    if (cJSON_IsNumber(sz)) size = (long long)sz->valuedouble;
    cJSON *urls = cJSON_GetObjectItem(t, "urls");
    const char *first = NULL;
    const char *extras[32]; int ne = 0;
    if (cJSON_IsArray(urls) && cJSON_GetArraySize(urls) > 0) {
        first = cJSON_GetArrayItem(urls, 0)->valuestring;
        for (int i = 1; i < cJSON_GetArraySize(urls) && ne < 32; i++)
            extras[ne++] = cJSON_GetArrayItem(urls, i)->valuestring;
    } else {
        first = cJSON_GetStringValue(cJSON_GetObjectItem(t, "url"));
    }
    if (download_file(first, extras, ne, dest, j->ctx, sha1, size, sha512) != 0) {
        j->ok = 0;
        snprintf(j->err, sizeof(j->err), "%s: %s", pymcl_basename(dest), pymcl_error());
    } else j->ok = 1;
    return NULL;
}

int download_all(cJSON *tasks, const char *message, pymcl_ctx *ctx) {
    if (!cJSON_IsArray(tasks)) return 0;
    int n = cJSON_GetArraySize(tasks);
    if (n == 0) return 0;
    int threads = ctx && ctx->threads > 0 ? ctx->threads : config_int("download_threads", 8);
    if (threads > 16) threads = 16;
    int done = 0, fail = 0;
    char first_err[256] = {0};
    for (int i = 0; i < n; ) {
        int batch = n - i; if (batch > threads) batch = threads;
        pthread_t th[16];
        dl_job jobs[16];
        for (int k = 0; k < batch; k++) {
            memset(&jobs[k], 0, sizeof(jobs[k]));
            jobs[k].task = cJSON_GetArrayItem(tasks, i + k);
            jobs[k].ctx = ctx;
            pthread_create(&th[k], NULL, dl_worker, &jobs[k]);
        }
        for (int k = 0; k < batch; k++) {
            pthread_join(th[k], NULL);
            if (!jobs[k].ok) {
                fail++;
                if (!first_err[0]) snprintf(first_err, sizeof(first_err), "%s", jobs[k].err);
            }
            done++;
            if (ctx && ctx->on_progress)
                ctx->on_progress(ctx->ud, message ? message : "下载中", done, n);
        }
        i += batch;
        if (ctx && ctx->cancel && ctx->cancel(ctx->ud)) {
            pymcl_set_error("用户取消");
            return -1;
        }
    }
    if (fail) {
        pymcl_set_error("%s失败（%d/%d 个文件）: %s", message ? message : "下载", fail, n, first_err);
        return -1;
    }
    return 0;
}

cJSON *fetch_json_mirrors(const char **urls, int n, int timeout) {
    for (int i = 0; i < n; i++) {
        char **exp = NULL; int ne = 0;
        expand_urls(urls[i], &exp, &ne);
        for (int j = 0; j < ne; j++) {
            cJSON *o = http_get_json(exp[j], timeout);
            if (o) { free_urls(exp, ne); return o; }
        }
        free_urls(exp, ne);
    }
    return NULL;
}
char *fetch_text_mirrors(const char **urls, int n, int timeout) {
    for (int i = 0; i < n; i++) {
        char **exp = NULL; int ne = 0;
        expand_urls(urls[i], &exp, &ne);
        for (int j = 0; j < ne; j++) {
            http_resp r;
            if (http_get(exp[j], &r, NULL, timeout) == 0 && r.body) {
                free_urls(exp, ne);
                return r.body; /* caller frees */
            }
            http_resp_free(&r);
        }
        free_urls(exp, ne);
    }
    return NULL;
}
