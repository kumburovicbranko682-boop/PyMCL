/* 服务器列表导入/导出与游玩时长汇总的纯逻辑部分。
 *
 * 语义逐条对齐 mclauncher/servers.py 与 mclauncher/playtime.py：
 * EziApp 的「导入/导出服务器」「总游玩时长」在纯 C 桥下以前直接报
 * unknown method，页面整个挂掉。文件 I/O 留在 rpc_extra.c，这里只做
 * 可移植的解析/格式化，方便在任何平台上做与 Python 参考实现的对拍。
 */
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"

static char *dup_stripped(const char *s, size_t len) {
    while (len && isspace((unsigned char)*s)) { s++; len--; }
    while (len && isspace((unsigned char)s[len - 1])) len--;
    char *out = (char *)malloc(len + 1);
    if (!out) return NULL;
    memcpy(out, s, len);
    out[len] = 0;
    return out;
}

/* Python int() 语义：允许首尾空白与符号，其余必须全是数字。 */
static int parse_int_strict(const char *s, long *out) {
    if (!s) return 0;
    while (isspace((unsigned char)*s)) s++;
    if (!*s) return 0;
    char *end = NULL;
    long v = strtol(s, &end, 10);
    if (end == s) return 0;
    while (*end && isspace((unsigned char)*end)) end++;
    if (*end) return 0;
    *out = v;
    return 1;
}

static int servers_has_addr(cJSON *servers, const char *ip, long port) {
    cJSON *it;
    cJSON_ArrayForEach(it, servers) {
        if (!cJSON_IsObject(it)) continue;
        const char *eip = cJSON_GetStringValue(cJSON_GetObjectItem(it, "ip"));
        long eport = 25565;
        cJSON *pj = cJSON_GetObjectItem(it, "port");
        if (cJSON_IsNumber(pj)) eport = (long)pj->valuedouble;
        if (eport == port && strcmp(eip ? eip : "", ip) == 0) return 1;
    }
    return 0;
}

static void servers_append(cJSON *servers, const char *name, const char *ip, long port) {
    cJSON *e = cJSON_CreateObject();
    cJSON_AddStringToObject(e, "name", (name && name[0]) ? name : ip);
    cJSON_AddStringToObject(e, "ip", ip);
    cJSON_AddNumberToObject(e, "port", (double)port);
    cJSON_AddItemToArray(servers, e);
}

/* 对齐 servers.import_servers_txt：每行「名字\t地址:端口」「地址:端口」
 * 或「地址」；空行和 # 注释跳过；按 ip:port 去重；返回新增条数。 */
int pymcl_servers_import_text(cJSON *servers, const char *text) {
    if (!cJSON_IsArray(servers) || !text) return 0;
    int imported = 0;
    const char *p = text;
    while (*p) {
        const char *eol = p;
        while (*eol && *eol != '\n' && *eol != '\r') eol++;
        char *line = dup_stripped(p, (size_t)(eol - p));
        p = eol;
        if (*p == '\r') p++;
        if (*p == '\n') p++;
        if (!line) return imported;
        if (!line[0] || line[0] == '#') { free(line); continue; }

        char *name = NULL;
        char *addr = NULL;
        char *tab = strchr(line, '\t');
        if (tab) {
            name = dup_stripped(line, (size_t)(tab - line));
            addr = dup_stripped(tab + 1, strlen(tab + 1));
        } else {
            name = dup_stripped("", 0);
            addr = dup_stripped(line, strlen(line));
        }
        free(line);
        if (!name || !addr) { free(name); free(addr); return imported; }

        char *ip = NULL;
        long port = 25565;
        char *colon = strrchr(addr, ':');
        if (colon) {
            if (!parse_int_strict(colon + 1, &port)) port = 25565;
            ip = dup_stripped(addr, (size_t)(colon - addr));
        } else {
            ip = dup_stripped(addr, strlen(addr));
        }
        free(addr);
        if (!ip) { free(name); return imported; }

        if (ip[0] && !servers_has_addr(servers, ip, port)) {
            servers_append(servers, name, ip, port);
            imported++;
        }
        free(name);
        free(ip);
    }
    return imported;
}

/* 对齐 servers.import_servers_json：条目取 ip/address，端口可为数字或
 * 字符串；「host:25566」形式的内嵌端口拆出来；只落盘 name/ip/port。 */
int pymcl_servers_import_json(cJSON *servers, cJSON *data) {
    if (!cJSON_IsArray(servers) || !cJSON_IsArray(data)) return 0;
    int imported = 0;
    cJSON *e;
    cJSON_ArrayForEach(e, data) {
        if (!cJSON_IsObject(e)) continue;
        const char *raw = cJSON_GetStringValue(cJSON_GetObjectItem(e, "ip"));
        if (!raw || !raw[0]) raw = cJSON_GetStringValue(cJSON_GetObjectItem(e, "address"));
        char *ip = dup_stripped(raw ? raw : "", raw ? strlen(raw) : 0);
        if (!ip) return imported;
        if (!ip[0]) { free(ip); continue; }

        /* port：数字非 0 直接用；非空字符串按 int() 解析（失败按 25565，
         * 且不再被内嵌端口覆盖——对齐 Python 的 truthy 判断）。 */
        long port = 25565;
        int port_falsy = 1;
        cJSON *pj = cJSON_GetObjectItem(e, "port");
        if (cJSON_IsNumber(pj)) {
            if (pj->valuedouble != 0) { port = (long)pj->valuedouble; port_falsy = 0; }
        } else if (cJSON_IsString(pj) && pj->valuestring && pj->valuestring[0]) {
            port_falsy = 0;
            if (!parse_int_strict(pj->valuestring, &port)) port = 25565;
        }

        char *colon = strrchr(ip, ':');
        if (colon) {
            long embedded;
            if (parse_int_strict(colon + 1, &embedded)) {
                char *host = dup_stripped(ip, (size_t)(colon - ip));
                if (!host) { free(ip); return imported; }
                if (host[0]) {
                    free(ip);
                    ip = host;
                    if (port_falsy) { port = embedded; port_falsy = 0; }
                } else {
                    free(host);
                }
            }
        }

        if (!servers_has_addr(servers, ip, port)) {
            const char *rawname = cJSON_GetStringValue(cJSON_GetObjectItem(e, "name"));
            char *name = dup_stripped(rawname ? rawname : "", rawname ? strlen(rawname) : 0);
            if (!name) { free(ip); return imported; }
            servers_append(servers, name, ip, port);
            free(name);
            imported++;
        }
        free(ip);
    }
    return imported;
}

static int sb_append(char **buf, size_t *len, size_t *cap, const char *s) {
    size_t sl = strlen(s);
    if (*len + sl + 1 > *cap) {
        size_t nc = *cap ? *cap : 256;
        while (nc < *len + sl + 1) nc *= 2;
        char *nb = (char *)realloc(*buf, nc);
        if (!nb) return 0;
        *buf = nb;
        *cap = nc;
    }
    memcpy(*buf + *len, s, sl + 1);
    *len += sl;
    return 1;
}

/* 对齐 servers.export_servers_txt：头两行注释 + 空行，然后每台服务器
 * 「名字\tip:port」（名字缺省或与 ip 相同则只写「ip:port」）。 */
char *pymcl_servers_export_text(cJSON *servers) {
    int count = 0;
    cJSON *it;
    if (cJSON_IsArray(servers)) {
        cJSON_ArrayForEach(it, servers) if (cJSON_IsObject(it)) count++;
    }
    char *buf = NULL;
    size_t len = 0, cap = 0;
    char head[96];
    snprintf(head, sizeof(head), "# PyMCL 服务器列表导出\n# 共 %d 个服务器\n", count);
    if (!sb_append(&buf, &len, &cap, head)) { free(buf); return NULL; }
    int index = 0;
    if (cJSON_IsArray(servers)) {
        cJSON_ArrayForEach(it, servers) {
            int i = index++;
            if (!cJSON_IsObject(it)) continue;
            const char *ip = cJSON_GetStringValue(cJSON_GetObjectItem(it, "ip"));
            const char *name = cJSON_GetStringValue(cJSON_GetObjectItem(it, "name"));
            char fallback[48];
            if (!name || !name[0]) {
                if (ip) {
                    name = ip;
                } else {
                    snprintf(fallback, sizeof(fallback), "服务器 #%d", i + 1);
                    name = fallback;
                }
            }
            if (!ip) ip = "";
            long port = 25565;
            cJSON *pj = cJSON_GetObjectItem(it, "port");
            if (cJSON_IsNumber(pj) && pj->valuedouble != 0) port = (long)pj->valuedouble;
            else if (cJSON_IsString(pj) && pj->valuestring && pj->valuestring[0]) {
                if (!parse_int_strict(pj->valuestring, &port)) port = 25565;
            }
            char tail[64];
            snprintf(tail, sizeof(tail), ":%ld", port);
            /* "\n".join(...) 语义：换行只作分隔符写在每行前面，末尾没有。 */
            int ok = sb_append(&buf, &len, &cap, "\n");
            if (ok && name[0] && strcmp(name, ip) != 0) {
                ok = sb_append(&buf, &len, &cap, name) && sb_append(&buf, &len, &cap, "\t");
            }
            ok = ok && sb_append(&buf, &len, &cap, ip) && sb_append(&buf, &len, &cap, tail);
            if (!ok) { free(buf); return NULL; }
        }
    }
    return buf;
}

/* 对齐 playtime.get_total_playtime：instances 里每个实例 total 求和。 */
double pymcl_playtime_total(cJSON *playtime_root) {
    double total = 0;
    cJSON *insts = cJSON_GetObjectItem(playtime_root, "instances");
    if (!cJSON_IsObject(insts)) return 0;
    cJSON *inst;
    cJSON_ArrayForEach(inst, insts) {
        cJSON *t = cJSON_GetObjectItem(inst, "total");
        if (cJSON_IsNumber(t)) total += t->valuedouble;
    }
    return total;
}
