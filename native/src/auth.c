#include "pymcl.h"
#include <wincrypt.h>

#pragma comment(lib, "crypt32.lib")

#define MS_DEVICE "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
#define MS_TOKEN "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
#define XBL_AUTH "https://user.auth.xboxlive.com/user/authenticate"
#define XSTS_AUTH "https://xsts.auth.xboxlive.com/xsts/authorize"
#define MC_LOGIN "https://api.minecraftservices.com/authentication/login_with_xbox"
#define MC_PROFILE "https://api.minecraftservices.com/minecraft/profile"

static void accounts_path(char *out, size_t n) {
    pymcl_path_join(out, n, g_root, "accounts.json");
}

cJSON *accounts_load(void) {
    char p[PYMCL_PATH];
    accounts_path(p, sizeof(p));
    cJSON *j = pymcl_read_json(p);
    return j ? j : cJSON_Parse("{\"accounts\":[],\"active\":null}");
}
void accounts_save(cJSON *root) {
    char p[PYMCL_PATH];
    accounts_path(p, sizeof(p));
    pymcl_write_json(p, root);
}

cJSON *account_offline(const char *username) {
    const char *n = (username && username[0]) ? username : "Player";
    char uuid[40];
    pymcl_offline_uuid(n, uuid);
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "type", "offline");
    cJSON_AddStringToObject(o, "name", n);
    cJSON_AddStringToObject(o, "uuid", uuid);
    return o;
}

/* 离线皮肤靠 UUID 奇偶生效：Steve/Alex 必须用固定 UUID，
 * 与 Python AccountManager.offline_account 一致。"default"/空 = 按名字哈希。 */
void account_apply_offline_skin(cJSON *acc, const char *skin) {
    if (!acc || !skin || !skin[0] || pymcl_ieq(skin, "default")) return;
    if (cJSON_GetObjectItem(acc, "skin"))
        cJSON_ReplaceItemInObject(acc, "skin", cJSON_CreateString(skin));
    else
        cJSON_AddStringToObject(acc, "skin", skin);
    if (pymcl_ieq(skin, "steve"))
        cJSON_ReplaceItemInObject(acc, "uuid",
            cJSON_CreateString("8667ba71-b85a-4004-af54-457a9734eed7"));
    else if (pymcl_ieq(skin, "alex"))
        cJSON_ReplaceItemInObject(acc, "uuid",
            cJSON_CreateString("ec561538-f3fd-461d-a7c9-7aa354f5bba9"));
}

cJSON *account_launch_props(cJSON *acc) {
    cJSON *o = cJSON_CreateObject();
    const char *type = cJSON_GetStringValue(cJSON_GetObjectItem(acc, "type"));
    const char *name = cJSON_GetStringValue(cJSON_GetObjectItem(acc, "name")) ?: "Player";
    if (type && strcmp(type, "microsoft") == 0) {
        char uuid[40];
        pymcl_dashed_uuid(cJSON_GetStringValue(cJSON_GetObjectItem(acc, "uuid")) ?: "", uuid);
        cJSON_AddStringToObject(o, "name", name);
        cJSON_AddStringToObject(o, "uuid", uuid);
        cJSON_AddStringToObject(o, "token", cJSON_GetStringValue(cJSON_GetObjectItem(acc, "access_token")) ?: "0");
        cJSON_AddStringToObject(o, "user_type", "msa");
        cJSON_AddStringToObject(o, "xuid", cJSON_GetStringValue(cJSON_GetObjectItem(acc, "xuid")) ?: "");
    } else {
        char uuid[40];
        const char *u = cJSON_GetStringValue(cJSON_GetObjectItem(acc, "uuid"));
        if (u && u[0]) pymcl_dashed_uuid(u, uuid);
        else pymcl_offline_uuid(name, uuid);
        cJSON_AddStringToObject(o, "name", name);
        cJSON_AddStringToObject(o, "uuid", uuid);
        cJSON_AddStringToObject(o, "token", "0");
        cJSON_AddStringToObject(o, "user_type", "legacy");
        cJSON_AddStringToObject(o, "xuid", "");
    }
    return o;
}

static int ms_refresh(cJSON *acc) {
    const char *rt = cJSON_GetStringValue(cJSON_GetObjectItem(acc, "refresh_token"));
    if (!rt) { pymcl_set_error("缺少刷新令牌，需要重新登录。"); return -1; }
    const char *cid = config_str("microsoft_client_id", PYMCL_MS_CLIENT_DEFAULT);
    char form[2048];
    snprintf(form, sizeof(form),
        "grant_type=refresh_token&client_id=%s&refresh_token=%s&scope=XboxLive.signin%%20offline_access",
        cid, rt);
    http_resp r;
    if (http_post_form(MS_TOKEN, form, &r, 20) != 0 || r.status != 200) {
        http_resp_free(&r);
        pymcl_set_error("刷新令牌失败，需要重新登录。");
        return -1;
    }
    cJSON *tok = cJSON_Parse(r.body);
    http_resp_free(&r);
    const char *ms = cJSON_GetStringValue(cJSON_GetObjectItem(tok, "access_token"));
    const char *nrt = cJSON_GetStringValue(cJSON_GetObjectItem(tok, "refresh_token"));
    /* XBL */
    char body[2048];
    snprintf(body, sizeof(body),
        "{\"Properties\":{\"AuthMethod\":\"RPS\",\"SiteName\":\"user.auth.xboxlive.com\",\"RpsTicket\":\"d=%s\"},"
        "\"RelyingParty\":\"http://auth.xboxlive.com\",\"TokenType\":\"JWT\"}", ms ? ms : "");
    http_resp xr;
    if (http_post_json(XBL_AUTH, body, &xr, NULL, 20) != 0) { cJSON_Delete(tok); http_resp_free(&xr); return -1; }
    cJSON *xj = cJSON_Parse(xr.body); http_resp_free(&xr);
    const char *xbl = cJSON_GetStringValue(cJSON_GetObjectItem(xj, "Token"));
    snprintf(body, sizeof(body),
        "{\"Properties\":{\"SandboxId\":\"RETAIL\",\"UserTokens\":[\"%s\"]},"
        "\"RelyingParty\":\"rp://api.minecraftservices.com/\",\"TokenType\":\"JWT\"}", xbl ? xbl : "");
    http_resp sr;
    if (http_post_json(XSTS_AUTH, body, &sr, NULL, 20) != 0) {
        cJSON_Delete(tok); cJSON_Delete(xj); http_resp_free(&sr); return -1;
    }
    cJSON *sj = cJSON_Parse(sr.body); http_resp_free(&sr);
    const char *xsts = cJSON_GetStringValue(cJSON_GetObjectItem(sj, "Token"));
    const char *uhs = NULL;
    cJSON *xui = cJSON_GetObjectItem(cJSON_GetObjectItem(cJSON_GetObjectItem(sj, "DisplayClaims"), "xui"), "0");
    /* DisplayClaims.xui is array */
    cJSON *claims = cJSON_GetObjectItem(sj, "DisplayClaims");
    cJSON *arr = claims ? cJSON_GetObjectItem(claims, "xui") : NULL;
    if (cJSON_IsArray(arr) && cJSON_GetArraySize(arr) > 0)
        uhs = cJSON_GetStringValue(cJSON_GetObjectItem(cJSON_GetArrayItem(arr, 0), "uhs"));
    snprintf(body, sizeof(body), "{\"identityToken\":\"XBL3.0 x=%s;%s\"}", uhs ? uhs : "", xsts ? xsts : "");
    http_resp mr;
    if (http_post_json(MC_LOGIN, body, &mr, NULL, 20) != 0) {
        cJSON_Delete(tok); cJSON_Delete(xj); cJSON_Delete(sj); http_resp_free(&mr); return -1;
    }
    cJSON *mj = cJSON_Parse(mr.body); http_resp_free(&mr);
    const char *mct = cJSON_GetStringValue(cJSON_GetObjectItem(mj, "access_token"));
    char hdr[1024];
    snprintf(hdr, sizeof(hdr), "Authorization: Bearer %s", mct ? mct : "");
    cJSON *prof = http_get_json_hdr(MC_PROFILE, hdr, 20);
    if (mct) {
        cJSON_DeleteItemFromObject(acc, "access_token");
        cJSON_AddStringToObject(acc, "access_token", mct);
    }
    if (nrt) {
        cJSON_DeleteItemFromObject(acc, "refresh_token");
        cJSON_AddStringToObject(acc, "refresh_token", nrt);
    }
    if (uhs) {
        cJSON_DeleteItemFromObject(acc, "xuid");
        cJSON_AddStringToObject(acc, "xuid", uhs);
    }
    if (prof) {
        const char *nm = cJSON_GetStringValue(cJSON_GetObjectItem(prof, "name"));
        const char *id = cJSON_GetStringValue(cJSON_GetObjectItem(prof, "id"));
        if (nm) { cJSON_DeleteItemFromObject(acc, "name"); cJSON_AddStringToObject(acc, "name", nm); }
        if (id) {
            char uuid[40]; pymcl_dashed_uuid(id, uuid);
            cJSON_DeleteItemFromObject(acc, "uuid");
            cJSON_AddStringToObject(acc, "uuid", uuid);
        }
        cJSON_Delete(prof);
    }
    cJSON_DeleteItemFromObject(acc, "expires_at");
    cJSON_AddNumberToObject(acc, "expires_at", (double)time(NULL) + 20 * 3600);
    cJSON_Delete(tok); cJSON_Delete(xj); cJSON_Delete(sj); cJSON_Delete(mj);
    (void)xui;
    return 0;
}

cJSON *account_ensure_valid(cJSON *acc) {
    if (!acc) return NULL;
    const char *type = cJSON_GetStringValue(cJSON_GetObjectItem(acc, "type"));
    if (!type || strcmp(type, "microsoft") != 0) return cJSON_Duplicate(acc, 1);
    double exp = cJSON_GetNumberValue(cJSON_GetObjectItem(acc, "expires_at"));
    const char *tok = cJSON_GetStringValue(cJSON_GetObjectItem(acc, "access_token"));
    if (time(NULL) < exp && tok && tok[0]) return cJSON_Duplicate(acc, 1);
    cJSON *copy = cJSON_Duplicate(acc, 1);
    if (ms_refresh(copy) != 0) { cJSON_Delete(copy); return NULL; }
    cJSON *root = accounts_load();
    cJSON *arr = cJSON_GetObjectItem(root, "accounts");
    const char *nm = cJSON_GetStringValue(cJSON_GetObjectItem(copy, "name"));
    if (cJSON_IsArray(arr)) {
        int i = 0;
        cJSON *it;
        cJSON_ArrayForEach(it, arr) {
            if (nm && strcmp(cJSON_GetStringValue(cJSON_GetObjectItem(it, "name")) ?: "", nm) == 0) {
                cJSON_ReplaceItemInArray(arr, i, cJSON_Duplicate(copy, 1));
                break;
            }
            i++;
        }
    }
    accounts_save(root);
    cJSON_Delete(root);
    return copy;
}

int ms_login(pymcl_ctx *ctx, void (*on_code)(void *, const char *, const char *), void *ud, cJSON **out_acc) {
    const char *cid = config_str("microsoft_client_id", PYMCL_MS_CLIENT_DEFAULT);
    char form[512];
    snprintf(form, sizeof(form), "client_id=%s&scope=XboxLive.signin%%20offline_access", cid);
    http_resp r;
    if (http_post_form(MS_DEVICE, form, &r, 15) != 0 || r.status != 200) {
        http_resp_free(&r);
        pymcl_set_error("获取设备码失败");
        return -1;
    }
    cJSON *dc = cJSON_Parse(r.body);
    http_resp_free(&r);
    const char *user = cJSON_GetStringValue(cJSON_GetObjectItem(dc, "user_code"));
    const char *uri = cJSON_GetStringValue(cJSON_GetObjectItem(dc, "verification_uri"));
    const char *dcode = cJSON_GetStringValue(cJSON_GetObjectItem(dc, "device_code"));
    int interval = (int)cJSON_GetNumberValue(cJSON_GetObjectItem(dc, "interval"));
    int expires = (int)cJSON_GetNumberValue(cJSON_GetObjectItem(dc, "expires_in"));
    if (interval <= 0) interval = 5;
    if (on_code) on_code(ud, user ? user : "", uri ? uri : "");
    if (uri) ShellExecuteA(NULL, "open", uri, NULL, NULL, SW_SHOWNORMAL);
    if (ctx && ctx->on_log) {
        char msg[256];
        snprintf(msg, sizeof(msg), "请打开 %s 并输入代码 %s", uri ? uri : "", user ? user : "");
        ctx->on_log(ctx->ud, msg);
    }
    time_t deadline = time(NULL) + (expires > 0 ? expires : 900);
    cJSON *tokens = NULL;
    while (time(NULL) < deadline) {
        if (ctx && ctx->cancel && ctx->cancel(ctx->ud)) { cJSON_Delete(dc); pymcl_set_error("用户取消"); return -1; }
        Sleep((DWORD)interval * 1000);
        char pf[1024];
        snprintf(pf, sizeof(pf),
            "grant_type=urn:ietf:params:oauth:grant-type:device_code&client_id=%s&device_code=%s",
            cid, dcode ? dcode : "");
        http_resp tr;
        http_post_form(MS_TOKEN, pf, &tr, 15);
        cJSON *tj = tr.body ? cJSON_Parse(tr.body) : NULL;
        if (tr.status == 200 && tj) {
            tokens = tj;
            http_resp_free(&tr);
            break;
        }
        const char *err = tj ? cJSON_GetStringValue(cJSON_GetObjectItem(tj, "error")) : "";
        if (err && (strcmp(err, "expired_token") == 0 || strcmp(err, "authorization_declined") == 0)) {
            cJSON_Delete(tj); http_resp_free(&tr); cJSON_Delete(dc);
            pymcl_set_error("授权已过期或被拒绝，请重试。");
            return -1;
        }
        if (err && strcmp(err, "slow_down") == 0) interval += 5;
        if (ctx && ctx->on_log) ctx->on_log(ctx->ud, "等待授权中…");
        cJSON_Delete(tj);
        http_resp_free(&tr);
    }
    cJSON_Delete(dc);
    if (!tokens) { pymcl_set_error("授权超时。"); return -1; }
    /* reuse refresh path by stuffing tokens into a temp account */
    cJSON *acc = cJSON_CreateObject();
    cJSON_AddStringToObject(acc, "type", "microsoft");
    cJSON_AddStringToObject(acc, "refresh_token",
        cJSON_GetStringValue(cJSON_GetObjectItem(tokens, "refresh_token")) ?: "");
    cJSON_AddStringToObject(acc, "access_token",
        cJSON_GetStringValue(cJSON_GetObjectItem(tokens, "access_token")) ?: "");
    cJSON_Delete(tokens);
    if (ms_refresh(acc) != 0) { cJSON_Delete(acc); return -1; }
    cJSON *root = accounts_load();
    cJSON *arr = cJSON_GetObjectItem(root, "accounts");
    if (!cJSON_IsArray(arr)) {
        arr = cJSON_CreateArray();
        cJSON_AddItemToObject(root, "accounts", arr);
    }
    const char *nm = cJSON_GetStringValue(cJSON_GetObjectItem(acc, "name"));
    int i = 0, replaced = 0;
    cJSON *it;
    cJSON_ArrayForEach(it, arr) {
        if (nm && strcmp(cJSON_GetStringValue(cJSON_GetObjectItem(it, "name")) ?: "", nm) == 0) {
            cJSON_ReplaceItemInArray(arr, i, cJSON_Duplicate(acc, 1));
            replaced = 1;
            break;
        }
        i++;
    }
    if (!replaced) cJSON_AddItemToArray(arr, cJSON_Duplicate(acc, 1));
    cJSON_DeleteItemFromObject(root, "active");
    cJSON_AddStringToObject(root, "active", nm ? nm : "");
    accounts_save(root);
    cJSON_Delete(root);
    *out_acc = acc;
    return 0;
}
