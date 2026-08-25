#include "pymcl.h"
#include <bcrypt.h>
#include <shlobj.h>
#include <direct.h>
#include <errno.h>
#include <ctype.h>

#pragma comment(lib, "bcrypt.lib")

static char g_err[PYMCL_ERR];
char g_root[PYMCL_PATH];

const char *pymcl_error(void) { return g_err[0] ? g_err : ""; }
void pymcl_set_error(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    vsnprintf(g_err, sizeof(g_err), fmt, ap);
    va_end(ap);
}
void pymcl_log(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    fprintf(stderr, "[pymcl] ");
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    va_end(ap);
}

char *pymcl_strdup(const char *s) {
    if (!s) return NULL;
    size_t n = strlen(s) + 1;
    char *p = (char *)malloc(n);
    if (p) memcpy(p, s, n);
    return p;
}
int pymcl_snprintf(char *buf, size_t n, const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    int r = vsnprintf(buf, n, fmt, ap);
    va_end(ap);
    if (n) buf[n - 1] = 0;
    return r;
}
void pymcl_path_join(char *out, size_t n, const char *a, const char *b) {
    if (!a || !a[0]) { snprintf(out, n, "%s", b ? b : ""); return; }
    if (!b || !b[0]) { snprintf(out, n, "%s", a); return; }
    size_t la = strlen(a);
    if (a[la - 1] == '/' || a[la - 1] == '\\')
        snprintf(out, n, "%s%s", a, b);
    else
        snprintf(out, n, "%s\\%s", a, b);
}
void pymcl_path_join3(char *out, size_t n, const char *a, const char *b, const char *c) {
    char tmp[PYMCL_PATH];
    pymcl_path_join(tmp, sizeof(tmp), a, b);
    pymcl_path_join(out, n, tmp, c);
}
const char *pymcl_basename(const char *p) {
    if (!p) return "";
    const char *s = p, *last = p;
    for (; *s; s++) if (*s == '/' || *s == '\\') last = s + 1;
    return last;
}
void pymcl_parent(const char *p, char *out, size_t n) {
    snprintf(out, n, "%s", p ? p : "");
    char *s = out + strlen(out);
    while (s > out && (s[-1] == '/' || s[-1] == '\\')) *--s = 0;
    while (s > out && s[-1] != '/' && s[-1] != '\\') *--s = 0;
    if (s > out && (s[-1] == '/' || s[-1] == '\\')) *--s = 0;
}
int pymcl_endswith(const char *s, const char *suf) {
    if (!s || !suf) return 0;
    size_t a = strlen(s), b = strlen(suf);
    return a >= b && _stricmp(s + a - b, suf) == 0;
}
int pymcl_startswith(const char *s, const char *pre) {
    if (!s || !pre) return 0;
    return strncmp(s, pre, strlen(pre)) == 0;
}
int pymcl_istartswith(const char *s, const char *pre) {
    if (!s || !pre) return 0;
    return _strnicmp(s, pre, strlen(pre)) == 0;
}
int pymcl_ieq(const char *a, const char *b) {
    if (!a || !b) return a == b;
    return _stricmp(a, b) == 0;
}
int pymcl_icontains(const char *hay, const char *needle) {
    if (!hay || !needle || !needle[0]) return 0;
    size_t n = strlen(needle);
    for (const char *p = hay; *p; p++) {
        if (_strnicmp(p, needle, n) == 0) return 1;
    }
    return 0;
}
void pymcl_replace_char(char *s, char a, char b) {
    if (!s) return;
    for (; *s; s++) if (*s == a) *s = b;
}
wchar_t *pymcl_u8_to_wide(const char *s) {
    if (!s) return NULL;
    int n = MultiByteToWideChar(CP_UTF8, 0, s, -1, NULL, 0);
    wchar_t *w = (wchar_t *)malloc((size_t)n * sizeof(wchar_t));
    if (!w) return NULL;
    MultiByteToWideChar(CP_UTF8, 0, s, -1, w, n);
    return w;
}
char *pymcl_wide_to_u8(const wchar_t *w) {
    if (!w) return NULL;
    int n = WideCharToMultiByte(CP_UTF8, 0, w, -1, NULL, 0, NULL, NULL);
    char *s = (char *)malloc((size_t)n);
    if (!s) return NULL;
    WideCharToMultiByte(CP_UTF8, 0, w, -1, s, n, NULL, NULL);
    return s;
}

static int mkdir_one(const char *p) {
    wchar_t *w = pymcl_u8_to_wide(p);
    if (!w) return -1;
    BOOL ok = CreateDirectoryW(w, NULL);
    DWORD e = GetLastError();
    free(w);
    return (ok || e == ERROR_ALREADY_EXISTS) ? 0 : -1;
}
int pymcl_ensure_dir(const char *path) {
    if (!path || !path[0]) return -1;
    char buf[PYMCL_PATH];
    snprintf(buf, sizeof(buf), "%s", path);
    pymcl_replace_char(buf, '/', '\\');
    size_t n = strlen(buf);
    if (n && (buf[n - 1] == '\\')) buf[n - 1] = 0;
    for (char *p = buf; *p; p++) {
        if (*p == '\\' && p > buf + 2) {
            char c = *p; *p = 0;
            mkdir_one(buf);
            *p = c;
        }
    }
    return mkdir_one(buf);
}
int pymcl_file_exists(const char *path) {
    wchar_t *w = pymcl_u8_to_wide(path);
    if (!w) return 0;
    DWORD a = GetFileAttributesW(w);
    free(w);
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}
int pymcl_dir_exists(const char *path) {
    wchar_t *w = pymcl_u8_to_wide(path);
    if (!w) return 0;
    DWORD a = GetFileAttributesW(w);
    free(w);
    return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY);
}
long long pymcl_file_size(const char *path) {
    wchar_t *w = pymcl_u8_to_wide(path);
    if (!w) return -1;
    WIN32_FILE_ATTRIBUTE_DATA d;
    BOOL ok = GetFileAttributesExW(w, GetFileExInfoStandard, &d);
    free(w);
    if (!ok) return -1;
    ULARGE_INTEGER u;
    u.LowPart = d.nFileSizeLow;
    u.HighPart = d.nFileSizeHigh;
    return (long long)u.QuadPart;
}
int pymcl_read_file(const char *path, char **out, size_t *len) {
    wchar_t *w = pymcl_u8_to_wide(path);
    if (!w) return -1;
    FILE *f = _wfopen(w, L"rb");
    free(w);
    if (!f) { pymcl_set_error("无法读取 %s", path); return -1; }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (n < 0) { fclose(f); return -1; }
    char *buf = (char *)malloc((size_t)n + 1);
    if (!buf) { fclose(f); return -1; }
    size_t got = fread(buf, 1, (size_t)n, f);
    fclose(f);
    buf[got] = 0;
    *out = buf;
    if (len) *len = got;
    return 0;
}
int pymcl_write_file(const char *path, const void *data, size_t len) {
    char parent[PYMCL_PATH];
    pymcl_parent(path, parent, sizeof(parent));
    if (parent[0]) pymcl_ensure_dir(parent);
    wchar_t *w = pymcl_u8_to_wide(path);
    if (!w) return -1;
    FILE *f = _wfopen(w, L"wb");
    free(w);
    if (!f) { pymcl_set_error("无法写入 %s", path); return -1; }
    size_t wro = fwrite(data, 1, len, f);
    fclose(f);
    return wro == len ? 0 : -1;
}
int pymcl_copy_file(const char *src, const char *dst) {
    char parent[PYMCL_PATH];
    pymcl_parent(dst, parent, sizeof(parent));
    if (parent[0]) pymcl_ensure_dir(parent);
    wchar_t *ws = pymcl_u8_to_wide(src);
    wchar_t *wd = pymcl_u8_to_wide(dst);
    if (!ws || !wd) { free(ws); free(wd); return -1; }
    BOOL ok = CopyFileW(ws, wd, FALSE);
    free(ws); free(wd);
    return ok ? 0 : -1;
}
static void remove_tree_w(const wchar_t *dir) {
    wchar_t pat[PYMCL_PATH];
    _snwprintf(pat, PYMCL_PATH, L"%s\\*", dir);
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pat, &fd);
    if (h == INVALID_HANDLE_VALUE) { RemoveDirectoryW(dir); return; }
    do {
        if (wcscmp(fd.cFileName, L".") == 0 || wcscmp(fd.cFileName, L"..") == 0) continue;
        wchar_t child[PYMCL_PATH];
        _snwprintf(child, PYMCL_PATH, L"%s\\%s", dir, fd.cFileName);
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)
            remove_tree_w(child);
        else {
            SetFileAttributesW(child, FILE_ATTRIBUTE_NORMAL);
            DeleteFileW(child);
        }
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    RemoveDirectoryW(dir);
}
void pymcl_remove_tree(const char *path) {
    if (!path || !path[0]) return;
    wchar_t *w = pymcl_u8_to_wide(path);
    if (!w) return;
    DWORD a = GetFileAttributesW(w);
    if (a == INVALID_FILE_ATTRIBUTES) { free(w); return; }
    if (a & FILE_ATTRIBUTE_DIRECTORY) remove_tree_w(w);
    else { SetFileAttributesW(w, FILE_ATTRIBUTE_NORMAL); DeleteFileW(w); }
    free(w);
}
void pymcl_copy_tree(const char *src, const char *dst) {
    wchar_t *ws = pymcl_u8_to_wide(src);
    if (!ws) return;
    WIN32_FIND_DATAW fd;
    wchar_t pat[PYMCL_PATH];
    _snwprintf(pat, PYMCL_PATH, L"%s\\*", ws);
    HANDLE h = FindFirstFileW(pat, &fd);
    pymcl_ensure_dir(dst);
    if (h == INVALID_HANDLE_VALUE) { free(ws); return; }
    do {
        if (wcscmp(fd.cFileName, L".") == 0 || wcscmp(fd.cFileName, L"..") == 0) continue;
        char *name = pymcl_wide_to_u8(fd.cFileName);
        char csrc[PYMCL_PATH], cdst[PYMCL_PATH];
        pymcl_path_join(csrc, sizeof(csrc), src, name);
        pymcl_path_join(cdst, sizeof(cdst), dst, name);
        free(name);
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)
            pymcl_copy_tree(csrc, cdst);
        else
            pymcl_copy_file(csrc, cdst);
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    free(ws);
}
cJSON *pymcl_read_json(const char *path) {
    char *buf = NULL; size_t n = 0;
    if (pymcl_read_file(path, &buf, &n) != 0) return NULL;
    cJSON *j = cJSON_Parse(buf);
    free(buf);
    return j;
}
int pymcl_write_json(const char *path, cJSON *obj) {
    char *s = cJSON_Print(obj);
    if (!s) return -1;
    int r = pymcl_write_file(path, s, strlen(s));
    cJSON_free(s);
    return r;
}

static int hash_file(const char *path, LPCWSTR alg, char *hex, size_t hexn, int bytes) {
    wchar_t *w = pymcl_u8_to_wide(path);
    if (!w) return -1;
    FILE *f = _wfopen(w, L"rb");
    free(w);
    if (!f) return -1;
    BCRYPT_ALG_HANDLE hA = NULL;
    BCRYPT_HASH_HANDLE hH = NULL;
    if (BCryptOpenAlgorithmProvider(&hA, alg, NULL, 0) != 0) { fclose(f); return -1; }
    if (BCryptCreateHash(hA, &hH, NULL, 0, NULL, 0, 0) != 0) { BCryptCloseAlgorithmProvider(hA, 0); fclose(f); return -1; }
    unsigned char buf[1 << 16];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), f)) > 0)
        BCryptHashData(hH, buf, (ULONG)n, 0);
    fclose(f);
    unsigned char dig[64];
    BCryptFinishHash(hH, dig, (ULONG)bytes, 0);
    BCryptDestroyHash(hH);
    BCryptCloseAlgorithmProvider(hA, 0);
    static const char *x = "0123456789abcdef";
    for (int i = 0; i < bytes && (size_t)(i * 2 + 1) < hexn; i++) {
        hex[i * 2] = x[dig[i] >> 4];
        hex[i * 2 + 1] = x[dig[i] & 15];
    }
    hex[bytes * 2] = 0;
    return 0;
}
int pymcl_sha1_file(const char *path, char hex[41]) {
    return hash_file(path, BCRYPT_SHA1_ALGORITHM, hex, 41, 20);
}
int pymcl_sha512_file(const char *path, char hex[129]) {
    return hash_file(path, BCRYPT_SHA512_ALGORITHM, hex, 129, 64);
}
void pymcl_sha1_bytes(const void *data, size_t n, char hex[41]) {
    BCRYPT_ALG_HANDLE hA = NULL;
    BCRYPT_HASH_HANDLE hH = NULL;
    BCryptOpenAlgorithmProvider(&hA, BCRYPT_SHA1_ALGORITHM, NULL, 0);
    BCryptCreateHash(hA, &hH, NULL, 0, NULL, 0, 0);
    BCryptHashData(hH, (PUCHAR)data, (ULONG)n, 0);
    unsigned char d[20];
    BCryptFinishHash(hH, d, 20, 0);
    BCryptDestroyHash(hH);
    BCryptCloseAlgorithmProvider(hA, 0);
    static const char *x = "0123456789abcdef";
    for (int i = 0; i < 20; i++) { hex[i * 2] = x[d[i] >> 4]; hex[i * 2 + 1] = x[d[i] & 15]; }
    hex[40] = 0;
}
void pymcl_md5_bytes(const void *data, size_t n, unsigned char out[16]) {
    BCRYPT_ALG_HANDLE hA = NULL;
    BCRYPT_HASH_HANDLE hH = NULL;
    BCryptOpenAlgorithmProvider(&hA, BCRYPT_MD5_ALGORITHM, NULL, 0);
    BCryptCreateHash(hA, &hH, NULL, 0, NULL, 0, 0);
    BCryptHashData(hH, (PUCHAR)data, (ULONG)n, 0);
    BCryptFinishHash(hH, out, 16, 0);
    BCryptDestroyHash(hH);
    BCryptCloseAlgorithmProvider(hA, 0);
}
int pymcl_file_matches(const char *path, const char *sha1, long long size) {
    if (!pymcl_file_exists(path)) return 0;
    if (size >= 0 && pymcl_file_size(path) != size) return 0;
    if (sha1 && sha1[0]) {
        char hex[41];
        if (pymcl_sha1_file(path, hex) != 0) return 0;
        return _stricmp(hex, sha1) == 0;
    }
    return size >= 0;
}

int pymcl_open_folder(const char *path) {
    wchar_t *w = pymcl_u8_to_wide(path);
    if (!w) return -1;
    ShellExecuteW(NULL, L"open", w, NULL, NULL, SW_SHOWNORMAL);
    free(w);
    return 0;
}

static char *quote_arg(const char *a) {
    size_t n = strlen(a);
    int need = 0;
    for (size_t i = 0; i < n; i++) if (a[i] == ' ' || a[i] == '"') need = 1;
    if (!need) return pymcl_strdup(a);
    char *o = (char *)malloc(n * 2 + 3);
    char *p = o;
    *p++ = '"';
    for (size_t i = 0; i < n; i++) {
        if (a[i] == '"') *p++ = '\\';
        *p++ = a[i];
    }
    *p++ = '"'; *p = 0;
    return o;
}
static char *join_cmdline(const char **argv, int argc) {
    size_t cap = 16;
    char *s = (char *)malloc(cap);
    s[0] = 0;
    size_t len = 0;
    for (int i = 0; i < argc; i++) {
        char *q = quote_arg(argv[i]);
        size_t n = strlen(q);
        if (len + n + 2 > cap) { cap = (len + n + 2) * 2; s = (char *)realloc(s, cap); }
        if (len) s[len++] = ' ';
        memcpy(s + len, q, n + 1);
        len += n;
        free(q);
    }
    return s;
}
int pymcl_run_process_cancelable(const char **argv, int argc, const char *cwd,
                                 void (*on_line)(void *, const char *), void *ud,
                                 int timeout_sec, int (*cancel)(void *), void *cud) {
    HANDLE rd = NULL;
    HANDLE proc = pymcl_spawn_process(argv, argc, cwd, &rd);
    if (!proc) return -1;
    DWORD deadline = timeout_sec > 0 ? GetTickCount() + (DWORD)timeout_sec * 1000 : 0;
    char buf[4096]; char acc[8192]; size_t al = 0; acc[0] = 0;
    for (;;) {
        DWORD got = 0, avail = 0;
        if (PeekNamedPipe(rd, NULL, 0, NULL, &avail, NULL) && avail) {
            if (avail > sizeof(buf)) avail = sizeof(buf);
            if (ReadFile(rd, buf, avail, &got, NULL) && got) {
                for (DWORD i = 0; i < got; i++) {
                    if (buf[i] == '\n' || al >= sizeof(acc) - 2) {
                        acc[al] = 0;
                        if (al && acc[al - 1] == '\r') acc[al - 1] = 0;
                        if (on_line) on_line(ud, acc);
                        al = 0;
                    } else acc[al++] = buf[i];
                }
            }
        }
        DWORD st = WaitForSingleObject(proc, 50);
        if (st == WAIT_OBJECT_0) break;
        if (cancel && cancel(cud)) {
            /* 用户点了「取消」：真正杀掉子进程，而不是等它自己跑完 */
            TerminateProcess(proc, 1);
            CloseHandle(rd); CloseHandle(proc);
            return PYMCL_PROC_CANCELLED;
        }
        if (deadline && GetTickCount() > deadline) {
            TerminateProcess(proc, 1);
            CloseHandle(rd); CloseHandle(proc);
            return -1;
        }
    }
    if (al && on_line) { acc[al] = 0; on_line(ud, acc); }
    DWORD code = 1;
    GetExitCodeProcess(proc, &code);
    CloseHandle(rd); CloseHandle(proc);
    return (int)code;
}

int pymcl_run_process(const char **argv, int argc, const char *cwd,
                      void (*on_line)(void *, const char *), void *ud, int timeout_sec) {
    return pymcl_run_process_cancelable(argv, argc, cwd, on_line, ud, timeout_sec, NULL, NULL);
}
HANDLE pymcl_spawn_process(const char **argv, int argc, const char *cwd, HANDLE *out_read) {
    SECURITY_ATTRIBUTES sa = { sizeof(sa), NULL, TRUE };
    HANDLE rd, wr;
    if (!CreatePipe(&rd, &wr, &sa, 0)) return NULL;
    SetHandleInformation(rd, HANDLE_FLAG_INHERIT, 0);
    STARTUPINFOW si; PROCESS_INFORMATION pi;
    memset(&si, 0, sizeof(si)); memset(&pi, 0, sizeof(pi));
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;
    si.hStdOutput = wr;
    si.hStdError = wr;
    si.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    char *cmd = join_cmdline(argv, argc);
    wchar_t *wcmd = pymcl_u8_to_wide(cmd);
    wchar_t *wcwd = cwd ? pymcl_u8_to_wide(cwd) : NULL;
    free(cmd);
    BOOL ok = CreateProcessW(NULL, wcmd, NULL, NULL, TRUE, CREATE_NO_WINDOW, NULL, wcwd, &si, &pi);
    free(wcmd); free(wcwd);
    CloseHandle(wr);
    if (!ok) {
        CloseHandle(rd);
        pymcl_set_error("无法启动进程");
        return NULL;
    }
    CloseHandle(pi.hThread);
    if (out_read) *out_read = rd;
    else CloseHandle(rd);
    return pi.hProcess;
}

void pymcl_dashed_uuid(const char *in, char out[40]) {
    char hex[33] = {0};
    int j = 0;
    for (const char *p = in ? in : ""; *p && j < 32; p++) {
        if ((*p >= '0' && *p <= '9') || (*p >= 'a' && *p <= 'f') || (*p >= 'A' && *p <= 'F'))
            hex[j++] = (char)tolower((unsigned char)*p);
    }
    if (j != 32) { snprintf(out, 40, "%s", in ? in : ""); return; }
    snprintf(out, 40, "%.8s-%.4s-%.4s-%.4s-%.12s", hex, hex + 8, hex + 12, hex + 16, hex + 20);
}
void pymcl_offline_uuid(const char *name, char out[40]) {
    char key[512];
    snprintf(key, sizeof(key), "OfflinePlayer:%s", name ? name : "Player");
    unsigned char d[16];
    pymcl_md5_bytes(key, strlen(key), d);
    d[6] = (unsigned char)((d[6] & 0x0F) | 0x30);
    d[8] = (unsigned char)((d[8] & 0x3F) | 0x80);
    char hex[33];
    static const char *x = "0123456789abcdef";
    for (int i = 0; i < 16; i++) { hex[i * 2] = x[d[i] >> 4]; hex[i * 2 + 1] = x[d[i] & 15]; }
    hex[32] = 0;
    pymcl_dashed_uuid(hex, out);
}
void pymcl_format_size(double n, char *out, size_t cap) {
    const char *u[] = {"B", "KB", "MB", "GB", "TB"};
    int i = 0;
    while (n >= 1024 && i < 4) { n /= 1024; i++; }
    if (i == 0) snprintf(out, cap, "%d B", (int)n);
    else snprintf(out, cap, "%.1f %s", n, u[i]);
}
int pymcl_maven_path(const char *name, const char *suffix, char *out, size_t n) {
    char buf[512];
    snprintf(buf, sizeof(buf), "%s", name ? name : "");
    if (buf[0] == '[' && buf[strlen(buf) - 1] == ']') {
        buf[strlen(buf) - 1] = 0;
        memmove(buf, buf + 1, strlen(buf));
    }
    const char *ext = suffix ? suffix : "jar";
    char *at = strrchr(buf, '@');
    if (at) { *at = 0; ext = at + 1; }
    char *p1 = strchr(buf, ':');
    if (!p1) return -1;
    *p1 = 0;
    char *p2 = strchr(p1 + 1, ':');
    if (!p2) return -1;
    *p2 = 0;
    char *p3 = strchr(p2 + 1, ':');
    const char *group = buf, *art = p1 + 1, *ver = p2 + 1, *cls = NULL;
    if (p3) { *p3 = 0; cls = p3 + 1; }
    char g[256]; snprintf(g, sizeof(g), "%s", group);
    pymcl_replace_char(g, '.', '/');
    if (cls && cls[0])
        snprintf(out, n, "%s/%s/%s/%s-%s-%s.%s", g, art, ver, art, ver, cls, ext);
    else
        snprintf(out, n, "%s/%s/%s/%s-%s.%s", g, art, ver, art, ver, ext);
    return 0;
}
int pymcl_check_rules(cJSON *rules, int has_custom_res) {
    if (!cJSON_IsArray(rules) || cJSON_GetArraySize(rules) == 0) return 1;
    int allow = 0;
    cJSON *rule;
    cJSON_ArrayForEach(rule, rules) {
        int matched = 1;
        cJSON *os = cJSON_GetObjectItem(rule, "os");
        if (cJSON_IsObject(os)) {
            cJSON *name = cJSON_GetObjectItem(os, "name");
            if (cJSON_IsString(name) && strcmp(name->valuestring, pymcl_os_name()) != 0) matched = 0;
            cJSON *arch = cJSON_GetObjectItem(os, "arch");
            if (cJSON_IsString(arch) && strcmp(arch->valuestring, pymcl_arch()) != 0) matched = 0;
        }
        cJSON *feat = cJSON_GetObjectItem(rule, "features");
        if (cJSON_IsObject(feat) && matched) {
            cJSON *f;
            cJSON_ArrayForEach(f, feat) {
                int want = cJSON_IsTrue(f);
                int have = 0;
                if (strcmp(f->string, "has_custom_resolution") == 0) have = has_custom_res;
                if (want != have) matched = 0;
            }
        }
        if (matched) {
            cJSON *act = cJSON_GetObjectItem(rule, "action");
            allow = !act || !cJSON_IsString(act) || strcmp(act->valuestring, "allow") == 0;
        }
    }
    return allow;
}
void pymcl_replace_placeholders(const char *text, cJSON *map, char *out, size_t n) {
    if (!text) { out[0] = 0; return; }
    size_t o = 0;
    for (const char *p = text; *p && o + 1 < n;) {
        if (p[0] == '$' && p[1] == '{') {
            const char *e = strchr(p + 2, '}');
            if (e) {
                char key[128];
                size_t kn = (size_t)(e - (p + 2));
                if (kn > sizeof(key) - 1) kn = sizeof(key) - 1;
                memcpy(key, p + 2, kn); key[kn] = 0;
                cJSON *v = cJSON_GetObjectItem(map, key);
                if (cJSON_IsString(v)) {
                    size_t vl = strlen(v->valuestring);
                    if (o + vl >= n) vl = n - o - 1;
                    memcpy(out + o, v->valuestring, vl);
                    o += vl;
                    p = e + 1;
                    continue;
                }
            }
        }
        out[o++] = *p++;
    }
    out[o] = 0;
}
int pymcl_has_placeholder(const char *text) {
    return text && strstr(text, "${") != NULL;
}
const char *pymcl_os_name(void) { return "windows"; }
const char *pymcl_arch(void) {
#if defined(_M_ARM64) || defined(__aarch64__)
    return "arm64";
#elif defined(_M_IX86) || defined(__i386__)
    return "x86";
#else
    return "x64";
#endif
}
int pymcl_is_windows(void) { return 1; }
void pymcl_native_arch_token(char *out, size_t n) {
    snprintf(out, n, "%s", strcmp(pymcl_arch(), "x86") == 0 ? "32" : "64");
}

void pymcl_set_root(const char *root) {
    wchar_t *win = pymcl_u8_to_wide(root && root[0] ? root : ".");
    wchar_t full[PYMCL_PATH];
    DWORD n = win ? GetFullPathNameW(win, PYMCL_PATH, full, NULL) : 0;
    free(win);
    char *u8 = (n && n < PYMCL_PATH) ? pymcl_wide_to_u8(full) : pymcl_strdup(root ? root : ".");
    snprintf(g_root, sizeof(g_root), "%s", u8 ? u8 : ".");
    free(u8);
    wchar_t *w = pymcl_u8_to_wide(g_root);
    if (w) {
        SetEnvironmentVariableW(L"PYMCL_HOME", w);
        SetCurrentDirectoryW(w);
        free(w);
    }
}
void pymcl_instances_dir(char *out, size_t n) {
    pymcl_path_join(out, n, g_root, config_str("instances_dir", ".minecraft"));
}
void pymcl_java_dir(char *out, size_t n) {
    pymcl_path_join(out, n, g_root, config_str("java_dir", "java"));
}
void pymcl_cache_dir(char *out, size_t n) {
    pymcl_path_join(out, n, g_root, "cache");
}
