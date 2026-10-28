"""Probe WeChat account capabilities without sending anything by default.

Reads WECHAT_APP_ID / WECHAT_APP_SECRET from the private .env only.
Never prints the secret or the access_token itself.
Default mode only calls /cgi-bin/token and /cgi-bin/getcallbackip, which every
account can call; --send additionally posts one customer-service message to
verify the message/custom/send permission that asynchronous replies depend on.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.weixin.qq.com"

# errcode meanings that decide whether asynchronous customer replies are possible
ERR_MEANING = {
    0: "成功，客服消息接口可用（异步方案可行）",
    40001: "access_token 无效",
    40013: "AppID 无效",
    40125: "AppSecret 无效",
    40164: "调用来源 IP 不在白名单里，需要去后台填本机出口公网 IP",
    45015: "距该用户上次互动已超过 48 小时；接口本身可用，说明账号有权限",
    48001: "API 功能未授权，通常意味着账号未认证，客服消息接口不可用",
    45009: "接口调用超过限额",
    40003: "openid 无效",
}


def mask(value):
    if not value:
        return "(空)"
    return f"{value[:4]}…{value[-2:]} (长度{len(value)})"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--send", action="store_true",
                        help="额外发送一条客服消息以实测权限（默认只做只读探测）")
    parser.add_argument("--openid", default="", help="接收探测消息的 openid，缺省取第一个关注者")
    parser.add_argument("--text", default="权限探测：这是一条测试消息，可以放心忽略。",
                        help="探测消息内容")
    args = parser.parse_args()

    cfg = dotenv_values(args.env_file)
    app_id = (cfg.get("WECHAT_APP_ID") or os.environ.get("WECHAT_APP_ID") or "").strip()
    secret = (cfg.get("WECHAT_APP_SECRET") or os.environ.get("WECHAT_APP_SECRET") or "").strip()

    report = {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "app_id": mask(app_id), "app_secret": mask(secret),
              "secret_logged": False, "token_logged": False, "sent": False}
    if not app_id or not secret:
        print(json.dumps({**report, "status": "blocked",
                          "reason": ".env 中 WECHAT_APP_ID 或 WECHAT_APP_SECRET 仍为空，先去后台重置并填入"},
                         ensure_ascii=False, indent=2))
        return 2

    # 1. 取 access_token：只验证凭证，不涉及权限
    try:
        r = requests.get(API + "/cgi-bin/token",
                         params={"grant_type": "client_credential", "appid": app_id, "secret": secret},
                         timeout=(3, 15))
        payload = r.json()
    except requests.RequestException as e:
        print(json.dumps({**report, "status": "failed", "step": "token",
                          "error": type(e).__name__}, ensure_ascii=False, indent=2))
        return 1
    if "access_token" not in payload:
        code = payload.get("errcode")
        print(json.dumps({**report, "status": "failed", "step": "token", "errcode": code,
                          "errcode_meaning": ERR_MEANING.get(code, "未知错误"),
                          "errmsg": payload.get("errmsg", "")}, ensure_ascii=False, indent=2))
        return 1
    token = payload["access_token"]
    report.update({"token_step": "ok", "token_length": len(token),
                   "expires_in": payload.get("expires_in")})

    # 2. 验证 token 可用：该接口所有账号都能调
    r = requests.get(API + "/cgi-bin/getcallbackip", params={"access_token": token}, timeout=(3, 15))
    cb = r.json()
    report["getcallbackip"] = {"errcode": cb.get("errcode", 0),
                               "ip_list": cb.get("ip_list", [])[:3]} if "ip_list" in cb else {
        "errcode": cb.get("errcode"), "errmsg": cb.get("errmsg", "")}

    # 3. 只读判断关注者接口权限，借此推断账号能力级别
    r = requests.get(API + "/cgi-bin/user/get", params={"access_token": token}, timeout=(3, 15))
    users = r.json()
    if "data" in users and users["data"].get("openid"):
        report["user_get"] = {"errcode": 0, "total": users.get("total"),
                              "first_openid": mask(users["data"]["openid"][0])}
        target = args.openid or users["data"]["openid"][0]
    else:
        report["user_get"] = {"errcode": users.get("errcode"),
                              "errcode_meaning": ERR_MEANING.get(users.get("errcode"), ""),
                              "errmsg": users.get("errmsg", "")}
        target = args.openid

    if not args.send:
        report["status"] = "probed"
        report["next"] = ("凭证有效。加 --send 实测客服消息权限；"
                          "建议先用手机给公众号发一条消息再测，避免 45015 干扰判断")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if not target:
        report["status"] = "blocked"
        report["reason"] = "未能取得 openid；请先用手机给公众号发一条消息，或用 --openid 指定"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    r = requests.post(API + "/cgi-bin/message/custom/send", params={"access_token": token},
                      json={"touser": target, "msgtype": "text", "text": {"content": args.text}},
                      timeout=(3, 15))
    result = r.json()
    code = result.get("errcode", 0)
    report.update({"sent": True, "send_errcode": code,
                   "send_meaning": ERR_MEANING.get(code, "未知错误"),
                   "send_errmsg": result.get("errmsg", ""),
                   "status": "ok" if code == 0 else "permission_unknown"})
    if code == 0:
        report["conclusion"] = "客服消息接口可用，异步智能回复方案可以落地"
    elif code == 48001:
        report["conclusion"] = "账号未认证，客服消息接口不可用，异步方案走不通"
    elif code == 45015:
        report["conclusion"] = "接口存在且已授权，只是超过 48 小时互动窗口；异步方案可行"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
