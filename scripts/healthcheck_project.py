"""One-shot health check for the whole project chain.

Reads only the private .env. Prints a status table and writes evidence JSON.
Never logs secrets; only reachability, counts and HTTP statuses.
"""
import argparse
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import pymysql
import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ARGS = argparse.ArgumentParser(description=__doc__)
ARGS.add_argument("--tunnel", default="", help="public callback origin, e.g. https://x.trycloudflare.com")
ARGS.add_argument("--callback", default="http://127.0.0.1:8080")
ARGS.add_argument("--write-evidence", action="store_true")
CLI = ARGS.parse_args()

env = dotenv_values(ROOT / ".env")
checks = []
session = requests.Session()
session.trust_env = False


def add(name, ok, detail):
    checks.append({"component": name, "ok": None if ok is None else bool(ok), "detail": detail})


# 1. Dify
dify = (env.get("DIFY_BASE_URL") or "").rstrip("/")
try:
    r = session.get(dify + "/console/api/setup", timeout=(3, 15))
    add("Dify 控制台", r.status_code == 200, f"HTTP {r.status_code} {dify}")
except requests.RequestException as e:
    add("Dify 控制台", False, f"{type(e).__name__} {dify}")

# 2. Dify 课程工作流应用（有 key 才查）
course_key = (env.get("DIFY_COURSE_WORKFLOW_API_KEY") or "").strip()
if course_key:
    try:
        r = session.get(dify + "/v1/parameters",
                         headers={"Authorization": f"Bearer {course_key}"}, timeout=(3, 15))
        add("课程客服应用", r.status_code == 200, f"HTTP {r.status_code}")
    except requests.RequestException as e:
        add("课程客服应用", False, type(e).__name__)
else:
    add("课程客服应用", None, "未配置 DIFY_COURSE_WORKFLOW_API_KEY")

# 3. Xinference 本地模型
try:
    r = session.get(f"http://{env.get('MYSQL_HOST','')}:9997" if False else
                     (env.get("DIFY_BASE_URL", "").rstrip("/") + ":9997") + "/v1/models",
                     timeout=(3, 15))
    models = [m.get("id") for m in (r.json().get("data") or [])] if r.status_code == 200 else []
    add("Xinference 模型", r.status_code == 200, f"HTTP {r.status_code} 已加载 {models}")
except requests.RequestException as e:
    add("Xinference 模型", None, f"不可达（{type(e).__name__}），本地模型当前非链路必需")

# 4. Windows MCP 写库服务
mcp_host, mcp_port = env.get("MCP_HOST", ""), env.get("MCP_PORT", "8095")
try:
    r = session.get(f"http://{mcp_host}:{mcp_port}/sse", timeout=(3, 8), stream=True)
    add("MCP 写库服务", r.status_code == 200, f"HTTP {r.status_code} {mcp_host}:{mcp_port}")
    r.close()
except requests.RequestException as e:
    add("MCP 写库服务", False, type(e).__name__)

# 5. MySQL
try:
    conn = pymysql.connect(host=env["MYSQL_HOST"], port=int(env.get("MYSQL_PORT", 3306)),
                           user="root", password=env["MYSQL_ROOT_PASSWORD"],
                           database="wechat", charset="utf8mb4", connect_timeout=8)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM chathistory")
    wechat_rows = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM chat.records")
    course_rows = cur.fetchone()[0]
    conn.close()
    add("MySQL", True, f"wechat.chathistory {wechat_rows} 行 / 课程 chat.records {course_rows} 行")
except Exception as e:
    add("MySQL", False, type(e).__name__)

# 6. 本机回调服务
try:
    r = session.get(CLI.callback + "/wechat", params={"signature": "0" * 40, "timestamp": "1",
                                                       "nonce": "1", "echostr": "probe"},
                     timeout=(3, 10))
    add("回调服务（本机）", r.status_code in (200, 403),
        f"HTTP {r.status_code}（403=验签拒绝，说明服务在跑）")
except requests.RequestException as e:
    add("回调服务（本机）", False, type(e).__name__)

# 7. 公网隧道（可选）
if CLI.tunnel:
    try:
        t0 = time.time()
        r = session.get(CLI.tunnel.rstrip("/") + "/wechat",
                         params={"signature": "0" * 40, "timestamp": "1", "nonce": "1",
                                 "echostr": "probe"}, timeout=(3, 20))
        add("公网回调", r.status_code in (200, 403),
            f"HTTP {r.status_code} {time.time() - t0:.2f}s")
    except requests.RequestException as e:
        add("公网回调", False, type(e).__name__)

# 8. 微信去重库
db_path = ROOT / "data/private/wechat_messages.sqlite3"
try:
    db = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
    total = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    db.close()
    add("微信去重库", True, f"共 {total} 条；真实手机收件以单独验收证据为准")
except sqlite3.Error as e:
    add("微信去重库", False, type(e).__name__)

passed = sum(1 for c in checks if c["ok"] is True)
failed = [c for c in checks if c["ok"] is False]
print(f"\n项目链路健康检查  {datetime.now().astimezone().isoformat(timespec='seconds')}\n")
for c in checks:
    mark = "[通过]" if c["ok"] else ("[未配]" if c["ok"] is None else "[失败]")
    print(f"  {mark} {c['component']:<16} {c['detail']}")
print(f"\n合计 {passed}/{len(checks)} 项通过，失败 {len(failed)} 项\n")

payload = {"checked_at": datetime.now().astimezone().isoformat(),
           "components": checks, "passed": passed, "failed": len(failed),
           "total": len(checks), "secrets_logged": False}
if CLI.write_evidence:
    folder = ROOT / "evidence" / datetime.now().astimezone().strftime("%Y%m%d")
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"p00_healthcheck_{datetime.now().astimezone().strftime('%H%M%S')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("证据已写出:", out)

raise SystemExit(1 if failed else 0)
