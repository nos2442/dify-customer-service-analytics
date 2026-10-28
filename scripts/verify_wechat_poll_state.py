"""Read-only MySQL -> poll question -> scoped lookup check; never sends messages.

Use the exact new test question and --since ending in +08:00. A verified server
record does not establish phone receipt; human confirmation remains separate.
"""

import argparse
from contextlib import closing
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory

from dotenv import dotenv_values
import pymysql

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data/private/wechat_messages.sqlite3"
COLUMNS = ("message_key", "user_scope", "message_kind", "created_at", "generation_state",
           "callback_state", "callback_attempts", "workflow_run_id", "insert_id",
           "dify_http_status", "reply_text", "poll_reply_text", "lookup_state",
           "lookup_target_key", "send_state", "send_attempts")


def parse_since(value):
    since = datetime.fromisoformat(value)
    if not value.endswith("+08:00") or since.utcoffset() != timedelta(hours=8):
        raise ValueError("--since requires an explicit +08:00 offset")
    return since


def verify(question, since, mysql_rows, state_path):
    report = {"status": "mysql_not_found", "since": since.isoformat(),
              "phone_receipt_confirmed": False, "wechat_api_called_by_verifier": False,
              "question_logged": False, "reply_logged": False, "openid_logged": False,
              "question_sha256": sha256(question.encode()).hexdigest(),
              "mysql_exact_question_count": len(mysql_rows)}
    if len(mysql_rows) != 1:
        if mysql_rows:
            report["status"] = "ambiguous_mysql_rows"
        return report
    insert_id, stored_question, response = mysql_rows[0]
    if (type(insert_id) is not int or insert_id <= 0 or stored_question != question
            or not isinstance(response, str) or not response.strip()):
        report["status"] = "invalid_mysql_contract"
        return report
    report.update(insert_id=insert_id, reply_sha256=sha256(response.encode()).hexdigest(),
                  reply_characters=len(response))
    # mode=ro refuses writes and accidental creation of a missing database.
    with closing(sqlite3.connect(state_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        available = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
        if not set(COLUMNS).issubset(available):
            raise RuntimeError("Poll audit columns are unavailable")
        select = "SELECT " + ",".join(COLUMNS) + " FROM messages "
        questions = db.execute(select + "WHERE insert_id=? AND message_kind='poll_question'",
                               (insert_id,)).fetchall()
        report["sqlite_question_count"] = len(questions)
        if len(questions) != 1:
            report["status"] = "ambiguous_sqlite_questions" if questions else "question_not_found"
            return report
        row = questions[0]
        report.update(question_message_key=row["message_key"], workflow_run_id=row["workflow_run_id"],
                      question_generation_state=row["generation_state"],
                      question_callback_state=row["callback_state"])
        if (row["created_at"] < since.timestamp() or not row["user_scope"]
                or row["generation_state"] != "succeeded" or not row["workflow_run_id"]
                or row["dify_http_status"] != 200 or row["reply_text"] != response
                or row["callback_state"] != "passive_reply_returned"
                or row["send_state"] != "not_started" or row["send_attempts"] != 0):
            report["status"] = "question_mismatch"
            return report
        lookups = db.execute(select + "WHERE user_scope=? AND lookup_target_key=? "
                             "AND message_kind='lookup' AND created_at>=? ORDER BY created_at,message_key",
                             (row["user_scope"], row["message_key"], since.timestamp())).fetchall()
    report["scoped_target_lookup_count"] = len(lookups)
    delivered = [item for item in lookups if item["lookup_state"] == "succeeded"
                 and item["callback_state"] == "passive_reply_returned"]
    report["successful_passive_lookup_count"] = len(delivered)
    if not delivered:
        report["status"] = "lookup_pending"
        return report
    report["lookups"] = [{"message_key": item["message_key"], "lookup_state": item["lookup_state"],
                          "callback_state": item["callback_state"],
                          "callback_attempts": item["callback_attempts"]} for item in delivered]
    cached_matches = all(item["poll_reply_text"] == row["reply_text"] == response for item in delivered)
    no_generation_or_send = all(item["generation_state"] == "not_requested" and item["workflow_run_id"] is None
                               and item["insert_id"] is None and item["dify_http_status"] is None
                               and item["send_state"] == "not_started" and item["send_attempts"] == 0
                               for item in delivered)
    matched = cached_matches and no_generation_or_send and all(
        item["callback_attempts"] >= 1 and item["created_at"] >= row["created_at"] for item in delivered)
    report.update(status="server_verified" if matched else "lookup_mismatch",
                  lookup_cached_answer_matches_question_and_mysql=cached_matches,
                  lookup_has_no_generation_or_customer_send_record=no_generation_or_send)
    return report


def main(args):
    since = parse_since(args.since)
    report = {"status": "error", "since": since.isoformat(), "phone_receipt_confirmed": False}
    try:
        env = dotenv_values(ROOT / ".env", interpolate=False)
        with closing(pymysql.connect(host=env["MYSQL_HOST"], port=int(env.get("MYSQL_PORT") or 3306),
                     user="root", password=env["MYSQL_ROOT_PASSWORD"], database="wechat",
                     charset="utf8mb4", connect_timeout=10, read_timeout=10, autocommit=True)) as connection:
            with connection.cursor() as cursor:
                # BINARY also rejects case/trailing-space matches from a permissive collation.
                cursor.execute("SELECT chathistoryid,user_input,response FROM chathistory "
                               "WHERE BINARY user_input=BINARY %s", (args.question,))
                rows = cursor.fetchall()
        report = verify(args.question, since, rows, STATE)
    except Exception as error:
        # Error strings and row contents may contain private data; emit neither.
        report.update(status="error", error_type=type(error).__name__)
    now = datetime.now().astimezone()
    report.update(checked_at=now.isoformat(), scope="read-only server poll evidence; phone receipt is separate")
    target = ROOT / "evidence" / now.strftime("%Y%m%d") / ("p05_poll_state_" + now.strftime("%H%M%S%f") + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("Evidence: " + str(target))
    return 0 if report["status"] == "server_verified" else 2


def self_check():
    since = parse_since("2026-10-22T10:00:00+08:00")
    for value in ("2026-10-22T10:00:00", "2026-10-22T02:00:00+00:00"):
        try:
            parse_since(value)
        except ValueError:
            pass
        else:
            raise AssertionError("Missing or wrong timezone accepted")
    with TemporaryDirectory() as folder:
        state = Path(folder) / "state.sqlite3"
        assert verify("合成问题", since, [], state)["status"] == "mysql_not_found"
        assert not state.exists()
        with closing(sqlite3.connect(state)) as db, db:
            db.execute("CREATE TABLE messages (" + ",".join(COLUMNS) + ")")
            question = dict.fromkeys(COLUMNS)
            question.update(message_key="question-key", user_scope="private-synthetic-user", message_kind="poll_question",
                created_at=since.timestamp()+1, generation_state="succeeded", callback_state="passive_reply_returned",
                callback_attempts=1, workflow_run_id="synthetic-run", insert_id=42, dify_http_status=200,
                reply_text="合成答复", poll_reply_text="生成中", send_state="not_started", send_attempts=0)
            lookup = dict.fromkeys(COLUMNS)
            lookup.update(message_key="lookup-key", user_scope=question["user_scope"], message_kind="lookup",
                created_at=since.timestamp()+2, generation_state="not_requested", callback_state="passive_reply_returned",
                callback_attempts=2, lookup_state="succeeded", lookup_target_key="question-key",
                poll_reply_text="合成答复", send_state="not_started", send_attempts=0)
            for row in (question, lookup):
                db.execute("INSERT INTO messages VALUES(" + ",".join("?" for _ in COLUMNS) + ")",
                           tuple(row[key] for key in COLUMNS))
        rows = [(42, "合成问题", "合成答复")]
        result = verify("合成问题", since, rows, state)
        assert result["status"] == "server_verified" and result["phone_receipt_confirmed"] is False
        assert not any(value in json.dumps(result, ensure_ascii=False)
                       for value in ("private-synthetic-user", "合成问题", "合成答复"))
        assert verify("合成问题", since, rows*2, state)["status"] == "ambiguous_mysql_rows"
        for sql, expected in [
            ("UPDATE messages SET user_scope='other' WHERE message_kind='lookup'", "lookup_pending"),
            ("UPDATE messages SET user_scope='private-synthetic-user',poll_reply_text='wrong' WHERE message_kind='lookup'", "lookup_mismatch"),
            ("UPDATE messages SET user_scope=NULL WHERE message_kind='poll_question'", "question_mismatch")]:
            with closing(sqlite3.connect(state)) as db, db:
                db.execute(sql)
            assert verify("合成问题", since, rows, state)["status"] == expected
        absent = Path(folder) / "missing.sqlite3"
        try:
            verify("合成问题", since, rows, absent)
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("Read-only verifier created a missing SQLite database")
        assert not absent.exists()
    print("Offline poll linkage, isolation, timezone and redaction checks passed; no private env, live DB or API accessed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--since", help="Explicit baseline, e.g. 2026-10-22T10:00:00+08:00")
    parser.add_argument("--question", help="Exact dedicated new question; never included in the report")
    args = parser.parse_args()
    if args.self_check:
        self_check()
    elif not args.since or not args.question:
        parser.error("Use --since with +08:00 and the exact --question, or --self-check")
    else:
        sys.exit(main(args))
