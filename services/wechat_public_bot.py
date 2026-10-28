"""Plaintext callback with persistent deduplication and optional async customer replies."""

import argparse
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import closing
from hashlib import sha1, sha256
import hmac
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from dotenv import load_dotenv
from flask import Flask, Response, abort, request
import requests

ROOT = Path(__file__).resolve().parents[1]
CALLBACK_WAIT_SECONDS = 3.5
DIRECT_WAIT_SECONDS = 3.2
DIRECT_TIMEOUT_REPLY = "这次没有及时取得答案，请稍后再试。"
DIRECT_FAILED_REPLY = "这次没能完成回复，请稍后再试。"
DIRECT_SQL = "INSERT INTO wechat.chathistory (user_input, response, create_time) VALUES (%s, %s, NOW())"
FIXED_REPLY = "已收到您的消息，当前正在验证公众号回调。"
POLL_WAIT_REPLY = "正在生成，请稍后发送‘结果’查看。"
POLL_STATUS_TEXT = {
    "not_found": "暂无可查询的新问题，请先发送一个问题。",
    "pending": "尚未完成或生成已中断，请稍后发送‘结果’查询；系统不会自动重复执行。",
    "failed": "本次生成失败，未取得有效答复；系统不会自动重试。",
    "unknown": "本次生成或写库结果未知，系统不会自动重试，请联系维护人员核对。",
}
STABLE_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/stable_token"
CUSTOM_SEND_URL = "https://api.weixin.qq.com/cgi-bin/message/custom/send"
SAFETY_REPLY = "请先停止使用并远离设备、水流和金属部件，仅在干燥安全且不接触故障设备的位置切断供电；不能安全操作就不要靠近。请通过购买渠道联系专业售后检查，不要再次通电或自行拆修。我目前不能代建工单或安排上门。"


def quick_reply(content):
    if any(word in content for word in ("麻手", "冒烟", "焦糊", "烧焦", "漏电了", "一直跳闸", "反复跳闸")):
        return SAFETY_REPLY
    phrase = re.sub(r"[\W_]+", "", content).casefold()
    if phrase in {"你好", "您好", "在吗", "有人吗", "有人在吗", "哈喽", "hello", "hi",
                  "你好你是", "你好你是谁", "您好你是谁", "你是", "您是", "你是谁", "您是谁",
                  "你是什么", "你是什么ai", "你叫什么", "你是机器人吗"}:
        return "您好，我是智能客服小沐，可以帮您了解热水器选购、使用和售后问题。您想先了解哪方面？"
    if phrase in {"谢谢", "谢谢你", "谢谢您", "多谢", "感谢"}:
        return "不客气，有热水器相关问题随时问我。"
    if phrase in {"再见", "拜拜", "bye"}:
        return "再见，有需要随时来问，祝您生活愉快。"
    return ""


class WeChatAPIError(Exception):
    def __init__(self, http_status, errcode=None):
        super().__init__("WeChat token request rejected")
        self.http_status, self.errcode = http_status, errcode


def text_reply(message, text, created_at=None):
    root = ET.Element("xml")
    for name, value in (
        ("ToUserName", message["FromUserName"]),
        ("FromUserName", message["ToUserName"]),
        ("CreateTime", str(int(time.time() if created_at is None else created_at))),
        ("MsgType", "text"), ("Content", text),
    ):
        ET.SubElement(root, name).text = value
    return ET.tostring(root, encoding="unicode")


def workflow_output(payload):
    data = payload["data"]
    if data["status"] != "succeeded":
        raise ValueError("Workflow did not succeed")
    output = data["outputs"]
    text, result = output["text"], output["db_write_result"]
    # Dify 1.8.0 MCP json is a one-item array in the verified course tool.
    if isinstance(result, list) and len(result) == 1:
        result = result[0]
    if (not isinstance(text, str) or not text.strip() or not isinstance(result, dict)
            or type(result.get("affected_rows")) is not int or result["affected_rows"] != 1
            or type(result.get("insert_id")) is not int or result["insert_id"] <= 0):
        raise ValueError("Invalid text/db_write_result contract")
    return text, result["insert_id"]


def create_app(token, state_path, *, reply_mode="fixed", dify_base_url="", dify_api_key="",
               message_mode="plain", encoding_aes_key="", app_id="", app_secret=""):
    if not token:
        raise ValueError("WECHAT_TOKEN is required")
    # Backend forms can record EncodingAESKey in plain mode; ciphertext is still rejected below.
    if message_mode != "plain":
        raise ValueError("Only plaintext XML is implemented; AES/compatible modes are unsupported")
    if reply_mode not in ("fixed", "workflow", "async", "poll", "direct"):
        raise ValueError("WECHAT_REPLY_MODE must be fixed, workflow, async, poll or direct")
    if reply_mode in ("workflow", "async", "poll", "direct"):
        url = urlsplit(dify_base_url)
        if (url.scheme not in ("http", "https") or not url.netloc or url.username
                or url.path not in ("", "/") or url.query or url.fragment or not dify_api_key):
            raise ValueError("Workflow mode requires a Dify origin URL and application API key")
    if reply_mode == "async" and (not app_id or not app_secret):
        raise ValueError("Async mode requires WECHAT_APP_ID and WECHAT_APP_SECRET")

    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(state_path)) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS messages (
            message_key TEXT PRIMARY KEY, msg_id TEXT NOT NULL, created_at REAL NOT NULL,
            generation_state TEXT NOT NULL, callback_state TEXT NOT NULL DEFAULT 'pending',
            callback_attempts INTEGER NOT NULL DEFAULT 0, callback_elapsed REAL,
            workflow_run_id TEXT, insert_id INTEGER, workflow_elapsed REAL,
            dify_http_elapsed REAL, dify_http_status INTEGER, llm_elapsed REAL,
            reply_xml TEXT, error_type TEXT)""")
        existing = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
        for name, definition in {
            "send_state": "TEXT NOT NULL DEFAULT 'not_started'",
            "send_attempts": "INTEGER NOT NULL DEFAULT 0", "send_started_at": "REAL",
            "send_finished_at": "REAL", "send_http_status": "INTEGER",
            "send_errcode": "INTEGER", "send_error_type": "TEXT",
            "user_scope": "TEXT", "message_kind": "TEXT NOT NULL DEFAULT 'question'",
            "reply_text": "TEXT", "poll_reply_text": "TEXT",
            "lookup_state": "TEXT", "lookup_target_key": "TEXT",
            "question_text": "TEXT", "history_text": "TEXT",
            "ready_text": "TEXT", "reply_ready_elapsed": "REAL",
        }.items():
            if name not in existing:
                db.execute("ALTER TABLE messages ADD COLUMN " + name + " " + definition)
        db.commit()

    def read(key):
        with closing(sqlite3.connect(state_path, timeout=0.5)) as db:
            db.row_factory = sqlite3.Row
            return db.execute("SELECT * FROM messages WHERE message_key=?", (key,)).fetchone()

    def update(key, **values):
        with closing(sqlite3.connect(state_path, timeout=0.5)) as db:
            db.execute("UPDATE messages SET " + ",".join(name + "=?" for name in values)
                       + " WHERE message_key=?", (*values.values(), key))
            db.commit()

    def poll_result(row):
        if row is None:
            return "not_found", POLL_STATUS_TEXT["not_found"]
        state = row["generation_state"]
        if state == "succeeded" and row["reply_text"]:
            return state, row["reply_text"]
        state = state if state in POLL_STATUS_TEXT else "unknown"
        return state, POLL_STATUS_TEXT[state]

    # ponytail: two in-process workers; use a durable queue before real traffic.
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wechat-workflow")
    token_cache = {"value": "", "expires_at": 0.0}
    token_lock = threading.Lock()

    def access_token(session):
        with token_lock:
            if token_cache["value"] and time.monotonic() < token_cache["expires_at"]:
                return token_cache["value"]
            response = session.post(STABLE_TOKEN_URL, json={
                "grant_type": "client_credential", "appid": app_id,
                "secret": app_secret, "force_refresh": False,
            }, timeout=(3, 15), allow_redirects=False)
            if response.status_code != 200:
                raise WeChatAPIError(response.status_code)
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Invalid stable_token response")
            code = payload.get("errcode", 0)
            if type(code) is not int:
                raise ValueError("Invalid stable_token errcode")
            if code:
                raise WeChatAPIError(response.status_code, code)
            value, expires = payload.get("access_token"), payload.get("expires_in")
            if not isinstance(value, str) or not value or type(expires) is not int or expires <= 0:
                raise ValueError("Invalid stable_token token/expiry")
            token_cache.update(value=value, expires_at=time.monotonic() + max(0, expires - 60))
            return value

    def send_customer_reply(key, message, text):
        with requests.Session() as session:
            session.trust_env = False
            try:
                credential = access_token(session)
            except (requests.RequestException, WeChatAPIError, ValueError, TypeError) as error:
                update(key, send_state="token_error", send_error_type=type(error).__name__,
                       send_http_status=getattr(error, "http_status", None),
                       send_errcode=getattr(error, "errcode", None), send_finished_at=time.time())
                return
            # Persist ambiguity before the HTTP side effect. A crash must never enable a resend.
            update(key, send_state="unknown", send_attempts=1, send_started_at=time.time())
            try:
                response = session.post(CUSTOM_SEND_URL, params={"access_token": credential}, json={
                    "touser": message["FromUserName"], "msgtype": "text", "text": {"content": text},
                    "aimsgcontext": {"is_ai_msg": 1},
                }, timeout=(3, 15), allow_redirects=False)
                update(key, send_http_status=response.status_code)
                if response.status_code != 200:
                    raise ValueError("Uncertain customer-message HTTP result")
                payload = response.json()
                if not isinstance(payload, dict) or type(payload.get("errcode")) is not int:
                    raise ValueError("Invalid customer-message response")
                code = payload["errcode"]
                update(key, send_state="accepted" if code == 0 else "rejected",
                       send_errcode=code, send_finished_at=time.time())
                if code in (40001, 40014, 42001):
                    with token_lock:
                        token_cache.update(value="", expires_at=0.0)
            except (requests.RequestException, ValueError, TypeError) as error:
                update(key, send_state="unknown", send_error_type=type(error).__name__,
                       send_finished_at=time.time())

    def direct_workflow(response, key, message, started):
        run_id = None
        for line in response.iter_lines(chunk_size=128):
            # iter_lines reconstructs fragmented HTTP chunks; SSE comments/heartbeats carry no answer.
            if not line.startswith(b"data:"):
                continue
            event = json.loads(line[5:].decode("utf-8").strip())
            if not isinstance(event, dict) or not isinstance(event.get("event"), str):
                raise ValueError("Invalid workflow stream event")
            kind, current_id = event["event"], event.get("workflow_run_id")
            if kind in ("workflow_started", "node_finished", "workflow_finished") and not isinstance(event.get("data"), dict):
                raise ValueError("Invalid workflow stream data")
            if kind == "workflow_started":
                if (run_id is not None or not isinstance(current_id, str) or not current_id
                        or event["data"]["id"] != current_id):
                    raise ValueError("Invalid workflow stream start")
                run_id = current_id
                update(key, workflow_run_id=run_id)
            elif kind in ("node_finished", "workflow_finished") or current_id is not None:
                if run_id is None or current_id != run_id:
                    raise ValueError("Workflow stream identity changed")
            if kind == "node_finished" and event["data"].get("node_id") == "sql":
                data, output = event["data"], event["data"]["outputs"]
                if not isinstance(output, dict):
                    raise ValueError("Invalid early answer SQL outputs")
                text = output.get("safe_text")
                if (data.get("node_type") != "code" or data.get("status") != "succeeded"
                        or not isinstance(text, str) or not text.strip()
                        or output.get("sql_statement") != DIRECT_SQL
                        or not isinstance(output.get("parameters_json"), str)
                        or json.loads(output["parameters_json"]) != [message["Content"], text]
                        or read(key)["ready_text"] is not None):
                    raise ValueError("Invalid early answer SQL contract")
                update(key, ready_text=text, reply_ready_elapsed=time.monotonic() - started)
            if kind == "workflow_finished":
                if event["data"]["id"] != run_id:
                    raise ValueError("Invalid workflow stream finish")
                return event
            if kind == "error":
                raise ValueError("Workflow stream reported an error")
        raise ValueError("Workflow stream ended without a terminal result")

    def generate(key, message, user):
        started = time.monotonic()
        try:
            inputs = {"input": message["Content"]}
            if reply_mode in ("poll", "async", "direct"):
                inputs["history"] = read(key)["history_text"] or ""
            with requests.Session() as session:
                session.trust_env = False
                response = session.post(
                    dify_base_url.rstrip("/") + "/v1/workflows/run",
                    headers={"Authorization": "Bearer " + dify_api_key},
                    json={"inputs": inputs,
                          "response_mode": "streaming" if reply_mode == "direct" else "blocking", "user": user},
                    **({"stream": True} if reply_mode == "direct" else {}),
                    timeout=(1, 120), allow_redirects=False,
                )
                update(key, dify_http_status=response.status_code)
                if response.status_code != 200:
                    if reply_mode == "direct":
                        response.close()
                    raise ValueError("Dify HTTP request was unsuccessful")
                if reply_mode == "direct":
                    try:
                        payload = direct_workflow(response, key, message, started)
                    finally:
                        response.close()
                else:
                    payload = response.json()
            data = payload["data"]
            if not isinstance(data, dict):
                raise ValueError("Invalid workflow response")
            run_id = payload.get("workflow_run_id") or data.get("id")
            if not isinstance(run_id, str) or not run_id:
                raise ValueError("Missing workflow_run_id")
            update(key, workflow_run_id=run_id, workflow_elapsed=data.get("elapsed_time"))
            if data.get("status") in ("failed", "stopped"):
                update(key, generation_state="failed", error_type="WorkflowFailed",
                       dify_http_elapsed=time.monotonic() - started)
                return ""
            text, insert_id = workflow_output(payload)
            if reply_mode == "direct" and read(key)["ready_text"] != text:
                raise ValueError("Terminal answer differs from validated SQL answer")
            xml = text_reply(message, text)
            update(key, generation_state="succeeded", insert_id=insert_id,
                   **({"reply_text": text} if reply_mode in ("poll", "async", "direct") else {}),
                   **({"reply_xml": xml} if reply_mode not in ("poll", "direct") else {}),
                   dify_http_elapsed=time.monotonic() - started)
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            # HTTP/transport/contract failures may occur after INSERT; never retry automatically.
            update(key, generation_state="unknown", error_type=type(error).__name__,
                   dify_http_elapsed=time.monotonic() - started)
            return ""
        if reply_mode == "async":
            send_customer_reply(key, message, text)
        return xml

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

    @app.route("/wechat", methods=["GET", "POST"])
    def callback():
        started = time.monotonic()
        if request.args.get("encrypt_type") or request.args.get("msg_signature"):
            abort(400, "Encrypted/compatible messages are unsupported")
        signature = request.args.get("signature", "")
        timestamp, nonce = request.args.get("timestamp"), request.args.get("nonce")
        if (not timestamp or not nonce or not re.fullmatch(r"[0-9a-f]{40}", signature)
                or not hmac.compare_digest(signature, sha1(
                    "".join(sorted((token, timestamp, nonce))).encode("utf-8")).hexdigest())):
            abort(403)
        if request.method == "GET":
            if "echostr" not in request.args:
                abort(400)
            return Response(request.args["echostr"], content_type="text/plain; charset=utf-8")
        try:
            raw = request.get_data().decode("utf-8")
            if "<!DOCTYPE" in raw.upper() or "<!ENTITY" in raw.upper():
                abort(400)
            root = ET.fromstring(raw)
            if root.tag != "xml" or root.find("Encrypt") is not None:
                abort(400)
            message = {child.tag: child.text or "" for child in root}
            if message.get("MsgType") != "text":
                return Response("success", content_type="text/plain")
            if (any(not message.get(name) for name in
                    ("ToUserName", "FromUserName", "CreateTime", "Content", "MsgId"))
                    or not re.fullmatch(r"[0-9]{1,20}", message["MsgId"])
                    or int(message["MsgId"]) > 2**64 - 1):
                abort(400)
        except (ET.ParseError, UnicodeDecodeError):
            abort(400)
        user = "wechat-" + sha256((message["ToUserName"] + "\0"
                                   + message["FromUserName"]).encode()).hexdigest()
        key = sha256((user + "\0" + message["MsgId"]).encode()).hexdigest()
        lookup = reply_mode == "poll" and message["Content"] == "结果"
        immediate = quick_reply(message["Content"]) if reply_mode in ("poll", "async", "direct") else ""
        kind = "quick" if immediate else ("lookup" if lookup else (
            "poll_question" if reply_mode == "poll" else ("async_question" if reply_mode == "async" else (
                "direct_question" if reply_mode == "direct" else "question"))))
        with closing(sqlite3.connect(state_path, timeout=0.5)) as db:
            db.row_factory = sqlite3.Row
            history = ""
            if kind in ("poll_question", "async_question", "direct_question"):
                recent = db.execute(
                    "SELECT question_text,COALESCE(ready_text,reply_text) AS reply_text FROM messages WHERE user_scope=? "
                    "AND ((generation_state='succeeded' AND message_kind='poll_question') "
                    "OR (generation_state='succeeded' AND message_kind='async_question' AND send_state='accepted') "
                    "OR (message_kind='direct_question' AND poll_reply_text=COALESCE(ready_text,reply_text) "
                    "AND callback_state='passive_reply_returned')) "
                    "AND question_text IS NOT NULL AND COALESCE(ready_text,reply_text) IS NOT NULL AND created_at>=? "
                    "ORDER BY created_at DESC,message_key DESC LIMIT 3", (user, time.time() - 1800)).fetchall()
                question_limit, answer_limit = (150, 160) if reply_mode == "direct" else (300, 600)
                history = json.dumps([{"user": row["question_text"][:question_limit], "assistant": row["reply_text"][:answer_limit]}
                                      for row in reversed(recent)], ensure_ascii=False) if recent else ""
            claimed = db.execute(
                "INSERT OR IGNORE INTO messages(message_key,msg_id,created_at,generation_state,user_scope,message_kind,"
                "question_text,history_text,poll_reply_text,reply_text) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (key, message["MsgId"], time.time(), "quick" if immediate else ("not_requested" if lookup else "pending"),
                 user, kind, message["Content"] if kind in ("poll_question", "async_question", "direct_question") else None, history,
                 immediate or None, immediate or None),
            ).rowcount == 1
            if claimed and lookup:
                target = db.execute("SELECT * FROM messages WHERE user_scope=? AND message_kind='poll_question' "
                                    "ORDER BY created_at DESC,message_key DESC LIMIT 1", (user,)).fetchone()
                state, text = poll_result(target)
                db.execute("UPDATE messages SET lookup_state=?,lookup_target_key=?,poll_reply_text=? "
                           "WHERE message_key=?", (state, target["message_key"] if target else None, text, key))
            db.commit()
        xml = ""
        if reply_mode in ("poll", "async", "direct") and read(key)["message_kind"] == "quick":
            row = read(key)
            xml = text_reply(message, row["poll_reply_text"], row["created_at"])
        elif reply_mode == "direct" and read(key)["message_kind"] == "direct_question":
            if claimed:
                pool.submit(generate, key, message, user)
            row = read(key)
            while (not row["poll_reply_text"] and not row["ready_text"] and row["generation_state"] == "pending"
                   and time.monotonic() - started < DIRECT_WAIT_SECONDS):
                time.sleep(min(0.02, max(0, DIRECT_WAIT_SECONDS - (time.monotonic() - started))))
                row = read(key)
            if not row["poll_reply_text"]:
                text = row["ready_text"] or (
                    row["reply_text"] if row["generation_state"] == "succeeded" and row["reply_text"] else
                    DIRECT_TIMEOUT_REPLY if row["generation_state"] == "pending" else DIRECT_FAILED_REPLY)
                with closing(sqlite3.connect(state_path, timeout=0.5)) as db:
                    db.execute("UPDATE messages SET poll_reply_text=COALESCE(poll_reply_text,?) WHERE message_key=?", (text, key))
                    db.commit()
                row = read(key)
            xml = text_reply(message, row["poll_reply_text"], row["created_at"])
        elif reply_mode == "direct":
            pass  # Never adopt or replay a message first processed in another mode.
        elif (reply_mode == "poll" and not claimed
                and read(key)["message_kind"] not in ("poll_question", "lookup", "quick")):
            pass  # A mode switch must not adopt or resend an old message.
        elif reply_mode == "poll":
            current_kind = read(key)["message_kind"]
            if claimed and current_kind == "poll_question":
                pool.submit(generate, key, message, user)
            if current_kind == "poll_question":
                # Freeze the first callback text; concurrent retries must not change it later.
                with closing(sqlite3.connect(state_path, timeout=0.5)) as db:
                    db.execute("UPDATE messages SET poll_reply_text=COALESCE(poll_reply_text,?) "
                               "WHERE message_key=?", (POLL_WAIT_REPLY, key))
                    db.commit()
            row = read(key)
            xml = text_reply(message, row["poll_reply_text"], row["created_at"])
        elif claimed and reply_mode == "fixed":
            xml = text_reply(message, FIXED_REPLY)
            update(key, generation_state="fixed", reply_xml=xml)
        elif claimed:
            future = pool.submit(generate, key, message, user)
            if reply_mode == "workflow":
                try:
                    xml = future.result(timeout=max(0, CALLBACK_WAIT_SECONDS
                                                    - (time.monotonic() - started)))
                except TimeoutError:
                    pass  # Only async mode can send a customer message after this acknowledgment.
        elif reply_mode != "async":
            xml = read(key)["reply_xml"] or ""
        with closing(sqlite3.connect(state_path, timeout=0.5)) as db:
            db.execute("UPDATE messages SET callback_state=?,callback_elapsed=?,"
                       "callback_attempts=callback_attempts+1 WHERE message_key=?",
                       ("passive_reply_returned" if xml else "ack_only",
                        time.monotonic() - started, key))
            db.commit()
        return Response(xml or "success", content_type=(
            "application/xml; charset=utf-8" if xml else "text/plain; charset=utf-8"))

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Explicit private configuration file; never auto-loaded")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if args.env_file:
        load_dotenv(args.env_file)
    app = create_app(
        os.environ.get("WECHAT_TOKEN", ""), ROOT / "data/private/wechat_messages.sqlite3",
        reply_mode=os.environ.get("WECHAT_REPLY_MODE", "fixed"),
        dify_base_url=os.environ.get("DIFY_BASE_URL", ""),
        dify_api_key=os.environ.get("DIFY_WORKFLOW_API_KEY", ""),
        message_mode=os.environ.get("WECHAT_MESSAGE_MODE", "plain"),
        encoding_aes_key=os.environ.get("WECHAT_ENCODING_AES_KEY", ""),
        app_id=os.environ.get("WECHAT_APP_ID", ""),
        app_secret=os.environ.get("WECHAT_APP_SECRET", ""),
    )
    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # Do not access-log callback query strings.
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
