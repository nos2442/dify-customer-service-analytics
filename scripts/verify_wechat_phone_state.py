"""Read-only SQLite -> insert_id -> MySQL check; never sends a WeChat message.

Use a timezone-qualified --since and the exact --question agreed with the user.
Multiple fresh messages require --msg-id; no implicit choice of an old sender.
Even server_verified does not establish phone receipt; obtain that from the user.
"""

import argparse
from contextlib import closing
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET

from dotenv import dotenv_values
import pymysql

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data/private/wechat_messages.sqlite3"
COLUMNS = ("msg_id", "created_at", "generation_state", "callback_state", "callback_elapsed",
           "workflow_run_id", "insert_id", "workflow_elapsed", "send_state", "send_attempts",
           "send_http_status", "send_errcode", "send_error_type", "send_started_at", "send_finished_at")


def select_messages(path, since, msg_id=None):
    # mode=ro refuses both writes and accidental creation of a missing database.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        available = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
        if not set(COLUMNS).issubset(available):
            raise RuntimeError("Async audit columns are not available")
        query = "SELECT " + ",".join((*COLUMNS, "reply_xml")) + " FROM messages WHERE created_at>=? AND generation_state!='fixed'"
        values = [since]
        if msg_id:
            query += " AND msg_id=?"
            values.append(msg_id)
        return [dict(row) for row in db.execute(query + " ORDER BY created_at", values)]


def verify(row, question, lookup):
    report = {key: row[key] for key in COLUMNS}
    report.update(status="pending", phone_receipt_confirmed=False, wechat_send_called=False,
                  openid_logged=False, question_logged=False, reply_logged=False)
    if row["generation_state"] != "succeeded" or row["send_state"] != "accepted":
        if (row["generation_state"] in ("failed", "unknown") or row["send_state"] in ("token_error", "rejected")
                or (row["send_state"] == "unknown" and row["send_finished_at"] is not None)):
            report["status"] = "needs_review"
        return report
    if (row["callback_state"] != "ack_only" or row["send_attempts"] != 1
            or row["send_http_status"] != 200 or row["send_errcode"] != 0
            or not row["workflow_run_id"] or type(row["insert_id"]) is not int or row["insert_id"] <= 0):
        report["status"] = "inconsistent"
        return report
    cached = ET.fromstring(row["reply_xml"]).findtext("Content")
    found = lookup(row["insert_id"])
    matched = bool(found and len(found) == 3 and found[0] == row["insert_id"]
                   and found[1] == question and found[2] == cached)
    report.update(mysql_row_matches_expected_question_and_cached_reply=matched,
                  question_sha256=sha256(question.encode()).hexdigest(),
                  reply_sha256=sha256((cached or "").encode()).hexdigest(),
                  reply_characters=len(cached or ""),
                  status="server_verified" if matched else "mismatch")
    return report


def main(args):
    since = datetime.fromisoformat(args.since)
    if since.tzinfo is None:
        raise ValueError("--since requires an explicit timezone offset")
    report = {"status": "not_found", "since": since.isoformat(), "phone_receipt_confirmed": False,
              "scope": "read-only server evidence; human phone confirmation remains separate"}
    try:
        rows = select_messages(STATE, since.timestamp(), args.msg_id)
        if len(rows) > 1:
            report.update(status="ambiguous", candidates=[{key: row[key] for key in COLUMNS} for row in rows])
        elif rows:
            def lookup(insert_id):
                env = dotenv_values(ROOT / ".env", interpolate=False)
                with closing(pymysql.connect(
                    host=env["MYSQL_HOST"], port=int(env.get("MYSQL_PORT") or 3306), user="root",
                    password=env["MYSQL_ROOT_PASSWORD"], database="wechat", charset="utf8mb4",
                    connect_timeout=10, read_timeout=10, autocommit=True,
                )) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT chathistoryid,user_input,response FROM chathistory WHERE chathistoryid=%s",
                                       (insert_id,))
                        return cursor.fetchone()
            report.update(verify(rows[0], args.question, lookup))
    except Exception as error:
        # Exception strings/row XML can contain private information; emit neither.
        report.update(status="error", error_type=type(error).__name__)
    now = datetime.now().astimezone()
    report["checked_at"] = now.isoformat()
    target = ROOT / "evidence" / now.strftime("%Y%m%d") / ("p05_phone_state_" + now.strftime("%H%M%S%f") + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("Evidence: " + str(target))
    return 0 if report["status"] == "server_verified" else 2


def self_check():
    row = {key: None for key in COLUMNS}
    row.update(msg_id="1", created_at=1, generation_state="succeeded", callback_state="ack_only",
               workflow_run_id="synthetic-run", insert_id=42, send_state="accepted", send_attempts=1,
               send_http_status=200, send_errcode=0,
               reply_xml="<xml><ToUserName>private-synthetic-openid</ToUserName><Content>合成答复</Content></xml>")
    result = verify(row, "合成问题", lambda value: (value, "合成问题", "合成答复"))
    assert result["status"] == "server_verified" and result["phone_receipt_confirmed"] is False
    assert "private-synthetic-openid" not in json.dumps(result, ensure_ascii=False)
    row["send_state"] = "unknown"
    assert verify(row, "合成问题", lambda value: (_ for _ in ()).throw(AssertionError()))["status"] == "pending"
    row["send_finished_at"] = 1
    assert verify(row, "合成问题", lambda value: (_ for _ in ()).throw(AssertionError()))["status"] == "needs_review"
    with TemporaryDirectory() as folder:
        absent = Path(folder) / "absent.sqlite3"
        try:
            select_messages(absent, 0)
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("Read-only selector created a missing database")
        assert not absent.exists()
    print("Offline server-state/redaction/read-only checks passed; no real SQLite, .env, MySQL or WeChat accessed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--since", help="Example: 2026-10-21T22:00:00+08:00")
    parser.add_argument("--question", help="Exact agreed test question; its text is never included in the report")
    parser.add_argument("--msg-id", help="Explicit selection if multiple fresh messages exist")
    args = parser.parse_args()
    if args.self_check:
        self_check()
    elif not args.since or not args.question:
        parser.error("Use --since with timezone and the exact --question, or --self-check")
    else:
        sys.exit(main(args))
