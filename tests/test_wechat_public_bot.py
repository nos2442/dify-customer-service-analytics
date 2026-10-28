"""Synthetic XML and mock Dify only: no .env, network, live app or MySQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from hashlib import sha1, sha256
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services import wechat_public_bot as bot

TOKEN = "synthetic-callback-token"
MESSAGE = {"ToUserName": "gh_synthetic", "FromUserName": "openid_synthetic",
           "CreateTime": "1792540000", "MsgType": "text", "Content": "测试热水器 <&> ]]>",
           "MsgId": "18446744073709551615"}


class WeChatLocalTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.state = Path(self.folder.name) / "messages.sqlite3"
        self.post = patch.object(bot.requests.Session, "post").start()
        self.addCleanup(patch.stopall)
        self.post.return_value = Mock(status_code=200)
        self.post.return_value.json.return_value = {
            "workflow_run_id": "synthetic-run-1",
            "data": {"id": "synthetic-run-1", "status": "succeeded", "elapsed_time": 0.02,
                     "outputs": {"text": "合成答案 <&> ]]>\n下一行",
                                 "db_write_result": [{"affected_rows": 1, "insert_id": 42}]}},
        }
        self.query = {"timestamp": "1792540000", "nonce": "nonce-for-test"}
        self.query["signature"] = sha1("".join(sorted(
            [TOKEN, self.query["timestamp"], self.query["nonce"]])).encode()).hexdigest()

    def app(self, mode="workflow", **kwargs):
        return bot.create_app(TOKEN, self.state, reply_mode=mode,
                              dify_base_url="http://dify.test", dify_api_key="synthetic-app-key",
                              **kwargs)

    def body(self, **values):
        root = ET.Element("xml")
        for name, value in (MESSAGE | values).items():
            ET.SubElement(root, name).text = value
        return ET.tostring(root, encoding="utf-8")

    def send(self, app, body=None, **query):
        with app.test_client() as client:
            return client.post("/wechat", query_string=self.query | query,
                               data=self.body() if body is None else body,
                               content_type="application/xml")

    def record(self):
        with sqlite3.connect(self.state) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM messages").fetchall()
        self.assertEqual(len(rows), 1)
        return dict(rows[0])

    def wait_generated(self):
        limit = time.monotonic() + 2
        while time.monotonic() < limit:
            row = self.record()
            if row["generation_state"] != "pending":
                return row
            time.sleep(0.01)
        self.fail("Synthetic worker did not complete")

    def row(self, msg_id):
        with sqlite3.connect(self.state) as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute("SELECT * FROM messages WHERE msg_id=?", (msg_id,)).fetchone())

    def wait_poll_question(self, msg_id=MESSAGE["MsgId"]):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            row = self.row(msg_id)
            if row["generation_state"] != "pending":
                return row
            time.sleep(0.01)
        self.fail("Synthetic poll generation did not finish")

    def async_app(self):
        return self.app("async", app_id="wx_synthetic", app_secret="synthetic-app-secret")

    def direct_events(self, question, text="合成答案 <&> ]]>\n下一行"):
        run_id = "synthetic-run-1"
        return [
            {"event": "workflow_started", "workflow_run_id": run_id, "data": {"id": run_id}},
            {"event": "node_finished", "workflow_run_id": run_id,
             "data": {"node_id": "llm", "node_type": "llm", "status": "succeeded",
                      "outputs": {"text": "UNTRUSTED RAW MODEL TEXT"}}},
            {"event": "node_finished", "workflow_run_id": run_id,
             "data": {"node_id": "sql", "node_type": "code", "status": "succeeded", "outputs": {
                 "sql_statement": bot.DIRECT_SQL,
                 "parameters_json": json.dumps([question, text]), "safe_text": text}}},
            {"event": "workflow_finished", "workflow_run_id": run_id,
             "data": {"id": run_id, "status": "succeeded", "elapsed_time": 0.02,
                      "outputs": {"text": text, "db_write_result": [{"affected_rows": 1, "insert_id": 42}]}}},
        ]

    def stream_lines(self, events):
        yield b": heartbeat"
        yield b"event: message"
        yield b""
        for event in events:
            yield b"data: " + json.dumps(event, ensure_ascii=False).encode("utf-8")
            yield b""

    def mock_direct_stream(self, question):
        response = self.post.return_value
        response.iter_lines.side_effect = lambda **kwargs: self.stream_lines(self.direct_events(question))
        return response

    def mock_async_apis(self, *, token_payload=None, send_payload=None, send_error=None, send_status=200):
        dify = self.post.return_value
        self.api_counts = {"dify": 0, "token": 0, "send": 0}

        def route(url, **kwargs):
            if url == "http://dify.test/v1/workflows/run":
                self.api_counts["dify"] += 1
                return dify
            if url == bot.STABLE_TOKEN_URL:
                self.api_counts["token"] += 1
                return Mock(status_code=200, json=Mock(return_value=token_payload if token_payload is not None else {
                    "access_token": "synthetic-access-token", "expires_in": 7200}))
            if url == bot.CUSTOM_SEND_URL:
                self.api_counts["send"] += 1
                self.assertEqual(kwargs["json"]["touser"], MESSAGE["FromUserName"])
                self.assertEqual(kwargs["json"]["msgtype"], "text")
                self.assertEqual(kwargs["params"], {"access_token": "synthetic-access-token"})
                self.assertFalse(kwargs["allow_redirects"])
                if send_error:
                    raise send_error
                return Mock(status_code=send_status, json=Mock(return_value=(
                    send_payload if send_payload is not None else {"errcode": 0, "errmsg": "ok"})))
            self.fail("Unexpected external API in the local mock")

        self.post.side_effect = route
        return route

    def wait_sent(self, msg_id=MESSAGE["MsgId"]):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with sqlite3.connect(self.state) as db:
                db.row_factory = sqlite3.Row
                row = db.execute("SELECT * FROM messages WHERE msg_id=?", (msg_id,)).fetchone()
            if row and row["send_finished_at"] is not None:
                return dict(row)
            time.sleep(0.01)
        self.fail("Synthetic customer-message attempt did not finish")

    def test_get_signature_and_echo(self):
        app = self.app("fixed")
        with app.test_client() as client:
            echo = "合成echo<&>"
            self.assertEqual(client.get("/wechat", query_string=self.query | {
                "echostr": echo}).get_data(as_text=True), echo)
            for signature in ("wrong", "中文", "0" * 40):
                self.assertEqual(client.get("/wechat", query_string=self.query | {
                    "signature": signature, "echostr": echo}).status_code, 403)
        self.post.assert_not_called()

    def test_missing_token_or_workflow_configuration_refuses_startup(self):
        for values in ({"token": ""}, {"token": TOKEN, "reply_mode": "workflow"},
                       {"token": TOKEN, "reply_mode": "poll"},
                       {"token": TOKEN, "reply_mode": "unexpected"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                bot.create_app(state_path=self.state, **values)
        self.post.assert_not_called()

    def test_post_signature_fixed_reply_and_duplicate_persistence(self):
        app = self.app("fixed")
        self.assertEqual(self.send(app, signature="0" * 40).status_code, 403)
        first = self.send(app)
        reply = ET.fromstring(first.data)
        self.assertEqual(reply.findtext("ToUserName"), MESSAGE["FromUserName"])
        self.assertEqual(reply.findtext("FromUserName"), MESSAGE["ToUserName"])
        self.assertEqual(reply.findtext("Content"), bot.FIXED_REPLY)
        second = self.send(self.app("fixed"))  # New application process state still reuses SQLite.
        self.assertEqual(first.data, second.data)
        self.assertEqual(self.record()["callback_attempts"], 2)
        self.assertEqual(self.record()["generation_state"], "fixed")
        self.post.assert_not_called()

    def test_workflow_contract_trace_and_duplicate_only_calls_once(self):
        app = self.app()
        first = self.send(app)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(ET.fromstring(first.data).findtext("Content"), "合成答案 <&> ]]>\n下一行")
        self.assertEqual(first.data, self.send(self.app()).data)
        self.post.assert_called_once()
        args, values = self.post.call_args
        self.assertEqual(args, ("http://dify.test/v1/workflows/run",))
        self.assertEqual(values["json"]["inputs"], {"input": MESSAGE["Content"]})
        self.assertEqual(values["json"]["response_mode"], "blocking")
        self.assertTrue(values["json"]["user"].startswith("wechat-"))
        self.assertNotIn(MESSAGE["FromUserName"], values["json"]["user"])
        self.assertFalse(values["allow_redirects"])
        row = self.record()
        self.assertEqual((row["generation_state"], row["workflow_run_id"], row["insert_id"]),
                         ("succeeded", "synthetic-run-1", 42))
        self.assertEqual(row["callback_state"], "passive_reply_returned")
        self.assertIsNone(row["llm_elapsed"])

    def test_slow_workflow_acknowledges_then_records_late_result_without_resend(self):
        entered, release = threading.Event(), threading.Event()
        response = self.post.return_value

        def slow(*args, **kwargs):
            entered.set()
            release.wait(2)
            return response

        self.post.side_effect = slow
        app = self.app()
        try:
            with patch.object(bot, "CALLBACK_WAIT_SECONDS", 0.02):
                first = self.send(app)
            self.assertTrue(entered.is_set())
            self.assertEqual(first.data, b"success")
            self.assertEqual(self.send(app).data, b"success")
            self.assertEqual(self.record()["generation_state"], "pending")
            self.assertLess(self.record()["callback_elapsed"], 0.5)
        finally:
            release.set()
        row = self.wait_generated()
        self.assertEqual(row["generation_state"], "succeeded")
        self.assertEqual(row["callback_state"], "ack_only")
        self.assertEqual(row["insert_id"], 42)
        self.post.assert_called_once()

    def test_concurrent_duplicate_does_not_invoke_again(self):
        app = self.app()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.send(app), range(4)))
        self.assertTrue(all(r.status_code == 200 for r in results))
        self.assertEqual(self.record()["callback_attempts"], 4)
        self.post.assert_called_once()

    def test_network_timeout_acknowledges_and_never_retries(self):
        self.post.side_effect = bot.requests.Timeout("synthetic timeout")
        app = self.app()
        self.assertEqual(self.send(app).data, b"success")
        self.assertEqual(self.send(self.app()).data, b"success")
        self.assertEqual(self.record()["generation_state"], "unknown")
        self.assertEqual(self.record()["error_type"], "Timeout")
        self.post.assert_called_once()

    def test_failed_http_or_workflow_never_returns_answer(self):
        self.post.return_value.status_code = 500
        app = self.app()
        self.assertEqual(self.send(app).data, b"success")
        self.assertEqual(self.record()["generation_state"], "unknown")
        self.post.return_value.status_code = 200
        self.post.return_value.json.return_value["data"]["status"] = "failed"
        self.assertEqual(self.send(app, self.body(MsgId="2")).data, b"success")
        with sqlite3.connect(self.state) as db:
            self.assertEqual(db.execute("SELECT generation_state FROM messages WHERE msg_id='2'")
                             .fetchone()[0], "failed")

    def test_invalid_contract_acknowledges_without_guessing_ids(self):
        self.post.return_value.json.return_value["data"]["outputs"]["db_write_result"] = []
        self.assertEqual(self.send(self.app()).data, b"success")
        self.assertEqual(self.record()["generation_state"], "unknown")
        self.assertIsNone(self.record()["insert_id"])
        for value in (None, [], "bad"):
            with self.subTest(value=value):
                self.post.return_value.json.return_value = {"data": value}
                self.assertEqual(self.send(self.app(), self.body(MsgId=str(10 + len(str(value))))).data,
                                 b"success")

    def test_aes_and_compatible_modes_rejected(self):
        for values in ({"message_mode": "safe"}, {"message_mode": "compatible"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.app(**values)
        # 微信新版控制台强制填写 EncodingAESKey，即使选择明文模式；
        # 明文下该值不参与加解密，但真实加密报文仍必须拒绝。
        keyed = self.app(encoding_aes_key="a" * 43)
        self.assertEqual(self.send(keyed, encrypt_type="aes").status_code, 400)
        self.assertEqual(self.send(keyed, msg_signature="synthetic").status_code, 400)
        app = self.app("fixed")
        self.assertEqual(self.send(app, encrypt_type="aes").status_code, 400)
        self.assertEqual(self.send(app, msg_signature="synthetic").status_code, 400)
        self.assertEqual(self.send(app, b"<xml><Encrypt>synthetic</Encrypt></xml>").status_code, 400)
        self.post.assert_not_called()

    def test_bad_xml_entities_missing_msg_id_and_oversized_body_rejected(self):
        app = self.app()
        for body in (b"not XML", b"<xml>", b"\xff", self.body(MsgId=""),
                     b'<!DOCTYPE xml [<!ENTITY x "value">]><xml>&x;</xml>'):
            with self.subTest(body=body):
                self.assertEqual(self.send(app, body).status_code, 400)
        self.assertEqual(self.send(app, b"x" * (64 * 1024 + 1)).status_code, 413)
        self.post.assert_not_called()

    def test_unsupported_events_acknowledged_without_workflow(self):
        self.assertEqual(self.send(self.app(), self.body(MsgType="event", MsgId="")).data, b"success")
        self.post.assert_not_called()

    def test_pending_from_prior_process_not_automatically_replayed(self):
        app = self.app("fixed")
        self.send(app)
        with sqlite3.connect(self.state) as db:
            db.execute("UPDATE messages SET generation_state='pending',reply_xml=NULL")
        self.assertEqual(self.send(self.app()).data, b"success")
        self.assertEqual(self.record()["generation_state"], "pending")
        self.post.assert_not_called()

    def test_plain_mode_accepts_unused_recorded_aes_key(self):
        app = self.app("fixed", encoding_aes_key="synthetic-recorded-key")
        reply = self.send(app)
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(ET.fromstring(reply.data).findtext("Content"), bot.FIXED_REPLY)
        self.post.assert_not_called()

    def test_async_requires_app_id_and_secret(self):
        for values in ({}, {"app_id": "wx_synthetic"}, {"app_secret": "synthetic-secret"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.app("async", **values)
        self.post.assert_not_called()

    def test_async_success_duplicate_and_restart_never_double_reply(self):
        self.mock_async_apis()
        app = self.async_app()
        self.assertEqual(self.send(app).data, b"success")
        row = self.wait_sent()
        self.assertEqual((row["generation_state"], row["send_state"], row["send_errcode"]),
                         ("succeeded", "accepted", 0))
        self.assertEqual(row["insert_id"], 42)
        self.assertEqual(row["callback_state"], "ack_only")
        self.assertEqual(row["send_attempts"], 1)
        self.assertEqual(self.send(app).data, b"success")
        self.assertEqual(self.send(self.async_app()).data, b"success")
        self.assertEqual(self.api_counts, {"dify": 1, "token": 1, "send": 1})
        token_call = next(call for call in self.post.call_args_list if call.args[0] == bot.STABLE_TOKEN_URL)
        self.assertFalse(token_call.kwargs["json"]["force_refresh"])
        send_call = next(call for call in self.post.call_args_list if call.args[0] == bot.CUSTOM_SEND_URL)
        self.assertEqual(send_call.kwargs["json"]["aimsgcontext"], {"is_ai_msg": 1})
        self.assertEqual(send_call.kwargs["json"]["text"]["content"], "合成答案 <&> ]]>\n下一行")
        with sqlite3.connect(self.state) as db:
            dumped = "\n".join(db.iterdump())
        self.assertNotIn("synthetic-access-token", dumped)
        self.assertNotIn("synthetic-app-secret", dumped)

    def test_async_quick_replies_are_immediate_idempotent_and_skip_all_apis(self):
        app = self.async_app()
        body = self.body(Content="你好，你是！？", MsgId="1")
        first = self.send(app, body)
        text = ET.fromstring(first.data).findtext("Content")
        self.assertIn("智能客服", text)
        self.assertIn("小沐", text)
        self.assertNotIn("结果", text)
        row = self.row("1")
        self.assertEqual((row["message_kind"], row["generation_state"]), ("quick", "quick"))
        self.assertEqual((row["send_state"], row["send_attempts"]), ("not_started", 0))
        self.assertIsNone(row["insert_id"])
        with patch.object(bot.time, "time", return_value=time.time() + 60):
            self.assertEqual(first.data, self.send(app, body).data)
            self.assertEqual(first.data, self.send(self.async_app(), body).data)
        thanks = self.send(app, self.body(Content="谢谢！", MsgId="2"))
        self.assertIn("不客气", ET.fromstring(thanks.data).findtext("Content"))
        self.assertEqual(self.row("2")["message_kind"], "quick")
        self.post.assert_not_called()

    def test_async_business_auto_sends_once_and_uses_scoped_poll_and_async_history(self):
        self.mock_async_apis()
        app = self.async_app()
        scope = "wechat-" + sha256((MESSAGE["ToUserName"] + "\0" + MESSAGE["FromUserName"]).encode()).hexdigest()
        other_account_scope = "wechat-" + sha256(("other-account\0" + MESSAGE["FromUserName"]).encode()).hexdigest()
        now = time.time()
        rows = [
            ("poll-prior", "10", now - 30, "succeeded", scope, "poll_question", "same-poll-question", "same-poll-answer"),
            ("async-prior", "11", now - 20, "succeeded", scope, "async_question", "same-async-question", "same-async-answer"),
            ("failed-prior", "12", now - 10, "failed", scope, "async_question", "failed-question", "failed-answer"),
            ("other-prior", "13", now - 5, "succeeded", "other-user-scope", "async_question", "other-user-question", "other-user-answer"),
            ("account-prior", "14", now - 4, "succeeded", other_account_scope, "poll_question", "other-account-question", "other-account-answer"),
        ]
        with sqlite3.connect(self.state) as db:
            db.executemany("INSERT INTO messages(message_key,msg_id,created_at,generation_state,user_scope,"
                           "message_kind,question_text,reply_text) VALUES(?,?,?,?,?,?,?,?)", rows)
            db.execute("UPDATE messages SET send_state='accepted' WHERE message_key='async-prior'")
        content = "那它的保修呢？"
        body = self.body(Content=content, MsgId="1")
        self.assertEqual(self.send(app, body).data, b"success")
        row = self.wait_sent("1")
        self.assertEqual((row["message_kind"], row["generation_state"], row["send_state"]),
                         ("async_question", "succeeded", "accepted"))
        self.assertEqual(row["question_text"], content)
        self.assertEqual(row["reply_text"], "合成答案 <&> ]]>\n下一行")
        self.assertEqual(ET.fromstring(row["reply_xml"]).findtext("Content"), row["reply_text"])
        dify_call = next(call for call in self.post.call_args_list if call.args[0] == "http://dify.test/v1/workflows/run")
        inputs = dify_call.kwargs["json"]["inputs"]
        self.assertEqual(inputs["input"], content)
        history = inputs.get("history", "")
        self.assertIsInstance(history, str)
        for expected in ("same-poll-question", "same-poll-answer", "same-async-question", "same-async-answer"):
            self.assertIn(expected, history)
        for excluded in ("failed-", "other-user-", "other-account-"):
            self.assertNotIn(excluded, history)
        self.assertEqual(row["history_text"], history)
        send_call = next(call for call in self.post.call_args_list if call.args[0] == bot.CUSTOM_SEND_URL)
        self.assertEqual(send_call.kwargs["json"]["text"]["content"], row["reply_text"])
        self.assertEqual(self.send(app, body).data, b"success")
        self.assertEqual(self.send(self.async_app(), body).data, b"success")
        self.assertEqual(self.api_counts, {"dify": 1, "token": 1, "send": 1})

    def test_async_slow_generation_immediately_acknowledges(self):
        route = self.mock_async_apis()
        entered, release = threading.Event(), threading.Event()

        def slow(url, **kwargs):
            if url == "http://dify.test/v1/workflows/run":
                entered.set()
                release.wait(2)
            return route(url, **kwargs)

        self.post.side_effect = slow
        app = self.async_app()
        try:
            started = time.monotonic()
            self.assertEqual(self.send(app).data, b"success")
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertTrue(entered.wait(0.5))
            self.assertEqual(self.send(app).data, b"success")
        finally:
            release.set()
        self.assertEqual(self.wait_sent()["send_state"], "accepted")
        self.assertEqual(self.api_counts, {"dify": 1, "token": 1, "send": 1})

    def test_async_concurrent_duplicate_only_generates_and_sends_once(self):
        self.mock_async_apis()
        app = self.async_app()
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: self.send(app), range(4)))
        self.assertTrue(all(reply.data == b"success" for reply in replies))
        self.assertEqual(self.wait_sent()["send_state"], "accepted")
        self.assertEqual(self.api_counts, {"dify": 1, "token": 1, "send": 1})

    def test_async_token_cache_reused_for_a_new_message(self):
        self.mock_async_apis()
        app = self.async_app()
        self.send(app)
        self.wait_sent()
        self.send(app, self.body(MsgId="2"))
        self.wait_sent("2")
        self.assertEqual(self.api_counts, {"dify": 2, "token": 1, "send": 2})

    def test_async_expiring_token_uses_normal_refresh(self):
        self.mock_async_apis(token_payload={"access_token": "synthetic-access-token", "expires_in": 1})
        app = self.async_app()
        self.send(app)
        self.wait_sent()
        self.send(app, self.body(MsgId="2"))
        self.wait_sent("2")
        self.assertEqual(self.api_counts, {"dify": 2, "token": 2, "send": 2})
        for call in self.post.call_args_list:
            if call.args[0] == bot.STABLE_TOKEN_URL:
                self.assertFalse(call.kwargs["json"]["force_refresh"])

    def test_async_dify_timeout_does_not_fetch_token_or_send(self):
        self.post.side_effect = bot.requests.Timeout("synthetic Dify timeout")
        app = self.async_app()
        self.assertEqual(self.send(app).data, b"success")
        row = self.wait_generated()
        self.assertEqual(row["generation_state"], "unknown")
        self.assertEqual(row["send_state"], "not_started")
        self.assertEqual(self.send(self.async_app()).data, b"success")
        self.post.assert_called_once()

    def test_async_invalid_write_result_never_sends(self):
        self.mock_async_apis()
        self.post.return_value.json.return_value["data"]["outputs"]["db_write_result"] = []
        self.send(self.async_app())
        self.assertEqual(self.wait_generated()["generation_state"], "unknown")
        self.assertEqual(self.api_counts, {"dify": 1, "token": 0, "send": 0})

    def test_async_token_rejection_keeps_generation_success_separate(self):
        self.mock_async_apis(token_payload={"errcode": 40164, "errmsg": "synthetic IP not allowed"})
        app = self.async_app()
        self.send(app)
        row = self.wait_sent()
        self.assertEqual((row["generation_state"], row["send_state"], row["send_errcode"]),
                         ("succeeded", "token_error", 40164))
        self.assertEqual(row["send_attempts"], 0)
        self.assertEqual(self.send(self.async_app()).data, b"success")
        self.assertEqual(self.api_counts, {"dify": 1, "token": 1, "send": 0})

    def test_async_permission_rejection_is_not_delivery_or_regeneration(self):
        self.mock_async_apis(send_payload={"errcode": 48001, "errmsg": "synthetic permission denied"})
        self.send(self.async_app())
        row = self.wait_sent()
        self.assertEqual((row["generation_state"], row["send_state"], row["send_errcode"]),
                         ("succeeded", "rejected", 48001))
        self.assertEqual(self.send(self.async_app()).data, b"success")
        self.assertEqual(self.api_counts, {"dify": 1, "token": 1, "send": 1})

    def test_async_invalid_token_is_not_resent_and_next_message_refreshes(self):
        payload = {"errcode": 40014, "errmsg": "synthetic invalid token"}
        self.mock_async_apis(send_payload=payload)
        app = self.async_app()
        self.send(app)
        self.assertEqual(self.wait_sent()["send_state"], "rejected")
        self.assertEqual(self.send(app).data, b"success")
        self.assertEqual(self.api_counts, {"dify": 1, "token": 1, "send": 1})
        payload["errcode"] = 0
        self.send(app, self.body(MsgId="2"))
        self.assertEqual(self.wait_sent("2")["send_state"], "accepted")
        self.assertEqual(self.api_counts, {"dify": 2, "token": 2, "send": 2})

    def test_async_token_timeout_does_not_attempt_customer_send(self):
        route = self.mock_async_apis()

        def token_timeout(url, **kwargs):
            if url == bot.STABLE_TOKEN_URL:
                self.api_counts["token"] += 1
                raise bot.requests.Timeout("synthetic token timeout")
            return route(url, **kwargs)

        self.post.side_effect = token_timeout
        self.send(self.async_app())
        row = self.wait_sent()
        self.assertEqual((row["generation_state"], row["send_state"], row["send_error_type"]),
                         ("succeeded", "token_error", "Timeout"))
        self.assertEqual(row["send_attempts"], 0)
        self.assertEqual(self.api_counts, {"dify": 1, "token": 1, "send": 0})

    def test_async_send_timeout_unknown_survives_restart_without_resend(self):
        self.mock_async_apis(send_error=bot.requests.Timeout("synthetic-access-token must not be persisted"))
        self.send(self.async_app())
        row = self.wait_sent()
        self.assertEqual((row["generation_state"], row["send_state"], row["send_error_type"]),
                         ("succeeded", "unknown", "Timeout"))
        self.assertEqual(row["send_attempts"], 1)
        self.assertEqual(self.send(self.async_app()).data, b"success")
        self.assertEqual(self.api_counts, {"dify": 1, "token": 1, "send": 1})
        self.assertNotIn("synthetic-access-token", str(row))

    def test_async_bad_send_http_or_json_remains_unknown(self):
        for index, (status, payload) in enumerate(((503, {"errcode": 0}), (200, {}), (200, []))):
            with self.subTest(status=status, payload=payload):
                self.state = Path(self.folder.name) / f"case-{index}.sqlite3"
                self.mock_async_apis(send_status=status, send_payload=payload)
                self.send(self.async_app())
                row = self.wait_sent()
                self.assertEqual(row["generation_state"], "succeeded")
                self.assertEqual(row["send_state"], "unknown")
                self.assertEqual(row["send_http_status"], status)
                self.assertEqual(self.api_counts, {"dify": 1, "token": 1, "send": 1})

    def test_existing_sqlite_schema_migration_preserves_prior_fixed_record(self):
        with sqlite3.connect(self.state) as db:
            db.execute("""CREATE TABLE messages (
                message_key TEXT PRIMARY KEY, msg_id TEXT NOT NULL, created_at REAL NOT NULL,
                generation_state TEXT NOT NULL, callback_state TEXT NOT NULL DEFAULT 'pending',
                callback_attempts INTEGER NOT NULL DEFAULT 0, callback_elapsed REAL,
                workflow_run_id TEXT, insert_id INTEGER, workflow_elapsed REAL,
                dify_http_elapsed REAL, dify_http_status INTEGER, llm_elapsed REAL,
                reply_xml TEXT, error_type TEXT)""")
            db.execute("INSERT INTO messages(message_key,msg_id,created_at,generation_state,callback_state) "
                       "VALUES('old-key','7',1,'fixed','passive_reply_returned')")
        self.async_app()
        row = self.record()
        self.assertEqual(row["generation_state"], "fixed")
        self.assertEqual(row["callback_state"], "passive_reply_returned")
        self.assertEqual(row["send_state"], "not_started")
        self.assertEqual(row["send_attempts"], 0)
        self.post.assert_not_called()

    def test_poll_immediate_hint_and_restart_lookup_without_customer_api(self):
        app = self.app("poll")  # AppID/AppSecret are deliberately absent.
        first = self.send(app)
        self.assertEqual(ET.fromstring(first.data).findtext("Content"), bot.POLL_WAIT_REPLY)
        question = self.wait_poll_question()
        answer = question["reply_text"]
        self.assertEqual(answer, "合成答案 <&> ]]>\n下一行")
        self.assertEqual(question["message_kind"], "poll_question")
        self.assertEqual(question["reply_text"], answer)
        self.assertIsNone(question["reply_xml"])
        self.assertEqual(question["send_attempts"], 0)
        self.assertEqual(question["user_scope"], "wechat-" + sha256(
            (MESSAGE["ToUserName"] + "\0" + MESSAGE["FromUserName"]).encode()).hexdigest())
        lookup = self.send(app, self.body(Content="结果", MsgId="2"))
        self.assertEqual(ET.fromstring(lookup.data).findtext("Content"), answer)
        row = self.row("2")
        self.assertEqual((row["generation_state"], row["lookup_state"]), ("not_requested", "succeeded"))
        self.assertEqual(row["lookup_target_key"], question["message_key"])
        self.assertIsNone(row["insert_id"])
        self.assertIsNone(row["workflow_run_id"])
        self.assertIsNone(row["reply_xml"])
        with patch.object(bot.time, "time", return_value=time.time() + 600):
            duplicate = self.send(self.app("poll"), self.body(Content="结果", MsgId="2"))
        self.assertEqual(lookup.data, duplicate.data)
        restarted = self.send(self.app("poll"), self.body(Content="结果", MsgId="3"))
        self.assertEqual(ET.fromstring(restarted.data).findtext("Content"), answer)
        self.post.assert_called_once()
        self.assertEqual(self.post.call_args.args[0], "http://dify.test/v1/workflows/run")
        with sqlite3.connect(self.state) as db:
            persisted = "\n".join(db.iterdump())
        self.assertNotIn(MESSAGE["FromUserName"], persisted)
        self.assertNotIn(MESSAGE["ToUserName"], persisted)

    def test_poll_scope_isolates_user_and_wechat_account(self):
        app = self.app("poll")
        self.send(app)
        self.wait_poll_question()
        for index, changed in enumerate(({"FromUserName": "other-user"}, {"ToUserName": "other-account"})):
            with self.subTest(changed=changed):
                reply = self.send(app, self.body(Content="结果", MsgId=str(index + 2), **changed))
                self.assertEqual(ET.fromstring(reply.data).findtext("Content"), bot.POLL_STATUS_TEXT["not_found"])
                self.assertIsNone(self.row(str(index + 2))["lookup_target_key"])
        self.post.assert_called_once()

    def test_poll_pending_query_snapshot_and_question_reply_are_frozen(self):
        entered, release = threading.Event(), threading.Event()
        response = self.post.return_value
        app = self.app("poll")
        self.send(app, self.body(MsgId="1"))  # A pending latest question must hide this older success.
        self.wait_poll_question("1")

        def slow(*args, **kwargs):
            entered.set()
            release.wait(2)
            return response

        self.post.side_effect = slow
        try:
            first = self.send(app)
            self.assertTrue(entered.wait(0.5))
            self.assertEqual(ET.fromstring(first.data).findtext("Content"), bot.POLL_WAIT_REPLY)
            self.assertLess(self.row(MESSAGE["MsgId"])["callback_elapsed"], 0.5)
            self.assertEqual(self.row(MESSAGE["MsgId"])["generation_state"], "pending")
            pending = self.send(app, self.body(Content="结果", MsgId="2"))
            self.assertEqual(ET.fromstring(pending.data).findtext("Content"), bot.POLL_STATUS_TEXT["pending"])
            self.assertEqual(self.row("2")["lookup_state"], "pending")
        finally:
            release.set()
        self.assertEqual(self.wait_poll_question()["insert_id"], 42)
        restarted = self.app("poll")
        self.assertEqual(first.data, self.send(restarted).data)
        self.assertEqual(pending.data, self.send(restarted, self.body(Content="结果", MsgId="2")).data)
        ready = self.send(restarted, self.body(Content="结果", MsgId="3"))
        self.assertEqual(ET.fromstring(ready.data).findtext("Content"), "合成答案 <&> ]]>\n下一行")
        self.assertEqual(self.post.call_count, 2)

    def test_poll_concurrent_question_duplicates_generate_once(self):
        release = threading.Event()
        response = self.post.return_value
        self.post.side_effect = lambda *args, **kwargs: (release.wait(2), response)[1]
        app = self.app("poll")
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                replies = list(pool.map(lambda _: self.send(app).data, range(4)))
            self.assertEqual(len(set(replies)), 1)
            self.assertEqual(ET.fromstring(replies[0]).findtext("Content"), bot.POLL_WAIT_REPLY)
        finally:
            release.set()
        self.assertEqual(self.wait_poll_question()["insert_id"], 42)
        self.post.assert_called_once()

    def test_poll_concurrent_lookup_claims_one_persistent_snapshot(self):
        app = self.app("poll")
        self.send(app)
        self.wait_poll_question()
        body = self.body(Content="结果", MsgId="2")
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: self.send(app, body).data, range(4)))
        self.assertEqual(len(set(replies)), 1)
        row = self.row("2")
        self.assertEqual(row["callback_attempts"], 4)
        self.assertEqual(row["lookup_target_key"], self.row(MESSAGE["MsgId"])["message_key"])
        self.assertEqual(row["lookup_state"], "succeeded")
        self.post.assert_called_once()

    def test_poll_latest_failed_or_unknown_never_returns_old_success(self):
        app = self.app("poll")
        self.send(app)
        self.wait_poll_question()
        self.post.return_value.json.return_value["data"]["status"] = "failed"
        failure = self.send(app, self.body(MsgId="2"))
        self.assertEqual(ET.fromstring(failure.data).findtext("Content"), bot.POLL_WAIT_REPLY)
        self.wait_poll_question("2")
        lookup = self.send(app, self.body(Content="结果", MsgId="3"))
        self.assertEqual(ET.fromstring(lookup.data).findtext("Content"), bot.POLL_STATUS_TEXT["failed"])
        self.assertEqual(self.row("3")["lookup_target_key"], self.row("2")["message_key"])
        self.post.side_effect = bot.requests.Timeout("synthetic uncertain write")
        unknown = self.send(app, self.body(MsgId="4"))
        self.assertEqual(ET.fromstring(unknown.data).findtext("Content"), bot.POLL_WAIT_REPLY)
        self.wait_poll_question("4")
        restarted = self.app("poll")
        lookup = self.send(restarted, self.body(Content="结果", MsgId="5"))
        self.assertEqual(ET.fromstring(lookup.data).findtext("Content"), bot.POLL_STATUS_TEXT["unknown"])
        self.assertEqual(self.row("5")["lookup_target_key"], self.row("4")["message_key"])
        self.assertEqual(unknown.data, self.send(restarted, self.body(MsgId="4")).data)
        self.assertEqual(self.post.call_count, 3)

    def test_poll_only_exact_result_command_skips_workflow(self):
        app = self.app("poll")
        for index, content in enumerate(("结果 ", "查询结果")):
            self.send(app, self.body(Content=content, MsgId=str(index + 2)))
            self.wait_poll_question(str(index + 2))
            self.assertEqual(self.row(str(index + 2))["message_kind"], "poll_question")
        self.send(app, self.body(Content="结果", MsgId="4"))
        self.assertEqual(self.row("4")["lookup_state"], "succeeded")
        self.assertEqual(self.post.call_count, 2)

    def test_poll_legacy_null_scope_and_restarted_pending_are_not_replayed(self):
        self.send(self.app("fixed"))
        with sqlite3.connect(self.state) as db:
            db.execute("UPDATE messages SET generation_state='succeeded',user_scope=NULL")
        app = self.app("poll")
        no_legacy = self.send(app, self.body(Content="结果", MsgId="2"))
        self.assertEqual(ET.fromstring(no_legacy.data).findtext("Content"), bot.POLL_STATUS_TEXT["not_found"])
        self.post.assert_not_called()
        self.send(app, self.body(MsgId="3"))
        self.wait_poll_question("3")
        with sqlite3.connect(self.state) as db:
            db.execute("UPDATE messages SET generation_state='pending',reply_text=NULL WHERE msg_id='3'")
        restarted = self.app("poll")
        pending = self.send(restarted, self.body(Content="结果", MsgId="4"))
        self.assertEqual(ET.fromstring(pending.data).findtext("Content"), bot.POLL_STATUS_TEXT["pending"])
        self.assertEqual(self.row("4")["lookup_target_key"], self.row("3")["message_key"])
        self.assertEqual(self.row("3")["generation_state"], "pending")
        self.post.assert_called_once()

    def test_poll_old_async_duplicate_only_acknowledges_without_adopting_record(self):
        self.send(self.app("fixed"))
        old_xml = bot.text_reply(MESSAGE, "旧模式合成答复")
        with sqlite3.connect(self.state) as db:
            db.execute("UPDATE messages SET generation_state='succeeded',user_scope=NULL,reply_xml=?,"
                       "send_state='rejected',send_errcode=48001,insert_id=88", (old_xml,))
        duplicate = self.send(self.app("poll"))
        self.assertEqual(duplicate.data, b"success")
        row = self.record()
        self.assertEqual(row["message_kind"], "question")
        self.assertIsNone(row["user_scope"])
        self.assertIsNone(row["poll_reply_text"])
        self.assertEqual(row["reply_xml"], old_xml)
        self.assertEqual((row["generation_state"], row["insert_id"], row["send_state"]),
                         ("succeeded", 88, "rejected"))
        self.assertEqual(row["callback_state"], "ack_only")
        self.post.assert_not_called()

    def test_poll_quick_identity_is_immediate_and_duplicate_xml_is_stable(self):
        app = self.app("poll")
        for index, content in enumerate(("你好，你是！？", "  你 是 ?  ", "您好！"), 1):
            with self.subTest(content=content):
                body = self.body(Content=content, MsgId=str(index))
                first = self.send(app, body)
                text = ET.fromstring(first.data).findtext("Content")
                self.assertIn("智能客服", text)
                self.assertIn("小沐", text)
                self.assertNotEqual(text, bot.POLL_WAIT_REPLY)
                row = self.row(str(index))
                self.assertEqual((row["message_kind"], row["generation_state"]), ("quick", "quick"))
                self.assertIsNone(row["workflow_run_id"])
                self.assertIsNone(row["insert_id"])
                with patch.object(bot.time, "time", return_value=time.time() + 60):
                    self.assertEqual(first.data, self.send(app, body).data)
        self.post.assert_not_called()

    def test_poll_quick_does_not_replace_lookup_and_mixed_business_is_not_quick(self):
        app = self.app("poll")
        self.send(app, self.body(Content="MX-60B容量是多少", MsgId="1"))
        original = self.wait_poll_question("1")
        self.send(app, self.body(Content="谢谢！", MsgId="2"))
        self.assertEqual(self.row("2")["message_kind"], "quick")
        lookup = self.send(app, self.body(Content="结果", MsgId="3"))
        self.assertEqual(self.row("3")["lookup_target_key"], original["message_key"])
        self.assertEqual(ET.fromstring(lookup.data).findtext("Content"), original["reply_text"])
        mixed = "你好，MX-60B保修多久"
        reply = self.send(app, self.body(Content=mixed, MsgId="4"))
        self.assertEqual(ET.fromstring(reply.data).findtext("Content"), bot.POLL_WAIT_REPLY)
        self.assertEqual(self.wait_poll_question("4")["message_kind"], "poll_question")
        self.assertEqual(self.post.call_args.kwargs["json"]["inputs"]["input"], mixed)
        self.assertEqual(self.post.call_count, 2)

    def test_poll_history_is_recent_successful_bounded_and_scoped(self):
        app = self.app("poll")
        self.send(app, self.body(Content="旧版无原文记录", MsgId="1"))
        original = self.wait_poll_question("1")
        now = time.time()
        rows = [("valid-" + str(i), str(10 + i), now - 200 + i, "succeeded",
                 original["user_scope"], "poll_question", "recent-question-" + str(i),
                 "recent-answer-" + str(i)) for i in range(1, 5)]
        rows += [
            ("expired", "20", now - 1801, "succeeded", original["user_scope"], "poll_question", "expired-question", "expired-answer"),
            ("pending", "21", now - 10, "pending", original["user_scope"], "poll_question", "pending-question", "pending-answer"),
            ("failed", "22", now - 9, "failed", original["user_scope"], "poll_question", "failed-question", "failed-answer"),
            ("unknown", "23", now - 8, "unknown", original["user_scope"], "poll_question", "unknown-question", "unknown-answer"),
            ("other", "24", now - 7, "succeeded", "other-user-scope", "poll_question", "other-question", "other-answer"),
        ]
        with sqlite3.connect(self.state) as db:
            db.execute("UPDATE messages SET question_text=NULL WHERE msg_id='1'")
            db.executemany("INSERT INTO messages(message_key,msg_id,created_at,generation_state,user_scope,"
                           "message_kind,question_text,reply_text) VALUES(?,?,?,?,?,?,?,?)", rows)
        followup = "那它的保修呢？"
        self.send(app, self.body(Content=followup, MsgId="30"))
        self.wait_poll_question("30")
        inputs = self.post.call_args.kwargs["json"]["inputs"]
        self.assertEqual(inputs["input"], followup)
        history = inputs.get("history", "")
        self.assertIsInstance(history, str)
        for i in (2, 3, 4):
            self.assertIn("recent-question-" + str(i), history)
            self.assertIn("recent-answer-" + str(i), history)
        for excluded in ("recent-question-1", "recent-answer-1", "expired-", "pending-",
                         "failed-", "unknown-", "other-", "旧版无原文记录"):
            self.assertNotIn(excluded, history)
        self.send(app, self.body(Content=followup, MsgId="31", FromUserName="second-synthetic-user"))
        self.wait_poll_question("31")
        other_history = self.post.call_args.kwargs["json"]["inputs"].get("history", "")
        self.assertEqual(other_history, "")

    def test_poll_history_snapshot_excludes_later_messages_before_worker_runs(self):
        queued = []
        with patch.object(bot, "ThreadPoolExecutor") as pool:
            pool.return_value.submit.side_effect = lambda fn, *args: queued.append((fn, args))
            app = self.app("poll")
        self.send(app, self.body(Content="当前追问", MsgId="1"))
        current = self.row("1")
        self.assertEqual(len(queued), 1)
        with sqlite3.connect(self.state) as db:
            db.execute("INSERT INTO messages(message_key,msg_id,created_at,generation_state,user_scope,"
                       "message_kind,question_text,reply_text) VALUES(?,?,?,?,?,?,?,?)",
                       ("later-synthetic-message", "2", time.time() + 1, "succeeded", current["user_scope"],
                        "poll_question", "future-question-must-not-leak", "future-answer-must-not-leak"))
        function, args = queued[0]
        function(*args)
        self.assertEqual(self.row("1")["generation_state"], "succeeded")
        inputs = self.post.call_args.kwargs["json"]["inputs"]
        self.assertEqual(inputs["input"], "当前追问")
        self.assertEqual(inputs.get("history", ""), current["history_text"] or "")
        self.assertNotIn("future-", inputs.get("history", ""))
        self.send(app, self.body(Content="当前追问", MsgId="1"))
        self.assertEqual(len(queued), 1)
        self.post.assert_called_once()

    def test_direct_business_returns_answer_and_freezes_duplicate_reply(self):
        app = self.app("direct")  # Personal-account passive replies need no AppID/Secret.
        content = "MX-60B的容量和额定功率是多少？"
        self.mock_direct_stream(content)
        body = self.body(Content=content, MsgId="1")
        with patch.object(bot, "DIRECT_WAIT_SECONDS", 0.5):
            first = self.send(app, body)
        text = ET.fromstring(first.data).findtext("Content")
        self.assertEqual(text, "合成答案 <&> ]]>\n下一行")
        self.assertNotIn("正在生成", text)
        self.assertNotIn("发送", text)
        row = self.wait_poll_question("1")
        self.assertEqual((row["message_kind"], row["generation_state"]), ("direct_question", "succeeded"))
        self.assertEqual(row["question_text"], content)
        self.assertEqual(row["reply_text"], text)
        self.assertEqual(row["poll_reply_text"], text)
        self.assertEqual(row["callback_state"], "passive_reply_returned")
        self.assertEqual(row["send_attempts"], 0)
        inputs = self.post.call_args.kwargs["json"]["inputs"]
        self.assertEqual(inputs["input"], content)
        self.assertEqual(inputs.get("history", ""), row["history_text"] or "")
        self.assertEqual(self.send(app, body).data, first.data)
        with patch.object(bot.time, "time", return_value=time.time() + 60):
            self.assertEqual(self.send(self.app("direct"), body).data, first.data)
        self.post.assert_called_once()
        self.assertEqual(self.post.call_args.args[0], "http://dify.test/v1/workflows/run")
        self.assertEqual(self.post.call_args.kwargs["json"]["response_mode"], "streaming")
        self.assertTrue(self.post.call_args.kwargs["stream"])

    def test_direct_timeout_is_bounded_and_late_answer_cannot_change_cached_reply(self):
        entered, release = threading.Event(), threading.Event()
        response = self.mock_direct_stream("MX-60B保修多久？")

        def slow(*args, **kwargs):
            entered.set()
            release.wait(2)
            return response

        self.post.side_effect = slow
        app = self.app("direct")
        body = self.body(Content="MX-60B保修多久？", MsgId="1")
        try:
            with patch.object(bot, "DIRECT_WAIT_SECONDS", 0.03):
                started = time.monotonic()
                first = self.send(app, body)
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertTrue(entered.wait(0.5))
            self.assertEqual(ET.fromstring(first.data).findtext("Content"), bot.DIRECT_TIMEOUT_REPLY)
            self.assertEqual(self.row("1")["poll_reply_text"], bot.DIRECT_TIMEOUT_REPLY)
            self.assertEqual(self.send(app, body).data, first.data)
        finally:
            release.set()
        late = self.wait_poll_question("1")
        self.assertEqual(late["generation_state"], "succeeded")
        self.assertNotEqual(late["reply_text"], bot.DIRECT_TIMEOUT_REPLY)
        self.assertEqual(late["poll_reply_text"], bot.DIRECT_TIMEOUT_REPLY)
        self.assertEqual(self.send(app, body).data, first.data)
        self.assertEqual(self.send(self.app("direct"), body).data, first.data)
        self.post.assert_called_once()

    def test_direct_quick_skips_model_and_history_only_uses_eligible_deliveries(self):
        app = self.app("direct")
        quick = self.send(app, self.body(Content="你好，你是！？", MsgId="1"))
        self.assertIn("小沐", ET.fromstring(quick.data).findtext("Content"))
        self.assertEqual(self.row("1")["message_kind"], "quick")
        self.post.assert_not_called()
        scope = self.row("1")["user_scope"]
        now = time.time()
        rows = [
            ("old-direct", "10", now - 100, "succeeded", scope, "direct_question", "too-old-for-three-question", "too-old-for-three-answer", "too-old-for-three-answer", "passive_reply_returned", "not_started"),
            ("poll-good", "11", now - 90, "succeeded", scope, "poll_question", "eligible-poll-question", "eligible-poll-answer", bot.POLL_WAIT_REPLY, "passive_reply_returned", "not_started"),
            ("async-good", "12", now - 80, "succeeded", scope, "async_question", "eligible-async-question", "eligible-async-answer", None, "ack_only", "accepted"),
            ("direct-good", "13", now - 70, "succeeded", scope, "direct_question", "eligible-direct-question", "eligible-direct-answer", "eligible-direct-answer", "passive_reply_returned", "not_started"),
            ("late-direct", "14", now - 20, "succeeded", scope, "direct_question", "late-question", "late-answer", bot.DIRECT_TIMEOUT_REPLY, "passive_reply_returned", "not_started"),
            ("undelivered", "15", now - 15, "succeeded", scope, "direct_question", "undelivered-question", "undelivered-answer", "undelivered-answer", "ack_only", "not_started"),
            ("rejected", "16", now - 10, "succeeded", scope, "async_question", "rejected-question", "rejected-answer", None, "ack_only", "rejected"),
            ("other-user", "17", now - 5, "succeeded", "other-user-scope", "direct_question", "other-user-question", "other-user-answer", "other-user-answer", "passive_reply_returned", "not_started"),
            ("other-account", "18", now - 4, "succeeded", "other-account-scope", "async_question", "other-account-question", "other-account-answer", None, "ack_only", "accepted"),
            ("expired", "19", now - 1801, "succeeded", scope, "direct_question", "expired-question", "expired-answer", "expired-answer", "passive_reply_returned", "not_started"),
        ]
        with sqlite3.connect(self.state) as db:
            db.executemany("INSERT INTO messages(message_key,msg_id,created_at,generation_state,user_scope,"
                           "message_kind,question_text,reply_text,poll_reply_text,callback_state,send_state) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.mock_direct_stream("那保修呢？")
        with patch.object(bot, "DIRECT_WAIT_SECONDS", 0.5):
            reply = self.send(app, self.body(Content="那保修呢？", MsgId="2"))
        self.assertEqual(ET.fromstring(reply.data).findtext("Content"), "合成答案 <&> ]]>\n下一行")
        history = self.post.call_args.kwargs["json"]["inputs"].get("history", "")
        self.assertIsInstance(history, str)
        for expected in ("eligible-poll-question", "eligible-poll-answer", "eligible-async-question",
                         "eligible-async-answer", "eligible-direct-question", "eligible-direct-answer"):
            self.assertIn(expected, history)
        for excluded in ("too-old-for-three-", "late-", "undelivered-", "rejected-", "other-user-", "other-account-", "expired-"):
            self.assertNotIn(excluded, history)
        self.post.assert_called_once()

    def test_direct_history_caps_preserve_recent_model_and_exclude_unseen_answers(self):
        app = self.app("direct")
        self.send(app, self.body(Content="你好", MsgId="1"))
        scope, now = self.row("1")["user_scope"], time.time()
        history = [{"user": model + "型号问题" + "问" * 400,
                    "assistant": model + "参数答案" + "答" * 700}
                   for model in ("MX-80C", "MX-60B", "MX-40A")]
        rows = [("long-" + str(i), str(i + 10), now - 90 + i, "succeeded", scope,
                 "direct_question", turn["user"], turn["assistant"], turn["assistant"],
                 "passive_reply_returned") for i, turn in enumerate(history)]
        rows += [
            ("older", "20", now - 100, "succeeded", scope, "direct_question",
             "older-question", "older-answer", "older-answer", "passive_reply_returned"),
            ("late", "21", now - 2, "succeeded", scope, "direct_question",
             "late-question", "late-answer", bot.DIRECT_TIMEOUT_REPLY, "passive_reply_returned"),
            ("unseen", "22", now - 1, "succeeded", scope, "direct_question",
             "unseen-question", "unseen-answer", "unseen-answer", "ack_only"),
        ]
        with sqlite3.connect(self.state) as db:
            db.executemany("INSERT INTO messages(message_key,msg_id,created_at,generation_state,user_scope,"
                           "message_kind,question_text,reply_text,poll_reply_text,callback_state) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
        db.close()
        self.mock_direct_stream("那额定功率呢？")
        with patch.object(bot, "DIRECT_WAIT_SECONDS", 0.5):
            self.send(app, self.body(Content="那额定功率呢？", MsgId="30"))
        self.wait_poll_question("30")
        outbound = self.post.call_args.kwargs["json"]["inputs"]["history"]
        expected = [{"user": turn["user"][:150], "assistant": turn["assistant"][:160]}
                    for turn in history]
        self.assertEqual(json.loads(outbound), expected)
        self.assertTrue(expected[-1]["user"].startswith("MX-40A"))
        self.assertEqual(self.row("30")["history_text"], outbound)
        self.post.assert_called_once()

    def test_direct_returns_validated_sql_text_before_logging_finishes(self):
        question, text = "MX-40A多大容量？", "MX-40A容量是40L。"
        events = self.direct_events(question, text)
        waiting, release = threading.Event(), threading.Event()

        def delayed_lines(**kwargs):
            self.assertEqual(kwargs, {"chunk_size": 128})
            yield from self.stream_lines(events[:3])
            waiting.set()
            release.wait(2)
            yield from self.stream_lines(events[3:])

        self.post.return_value.iter_lines.side_effect = delayed_lines
        app, body = self.app("direct"), self.body(Content=question, MsgId="1")
        try:
            with patch.object(bot, "DIRECT_WAIT_SECONDS", 0.5):
                first = self.send(app, body)
            self.assertTrue(waiting.wait(0.5))
            self.assertEqual(ET.fromstring(first.data).findtext("Content"), text)
            row = self.row("1")
            self.assertEqual(row["generation_state"], "pending")
            self.assertEqual(row["ready_text"], text)
            self.assertIsNone(row["insert_id"])
            self.assertLess(row["reply_ready_elapsed"], 0.5)
            self.assertEqual(self.send(self.app("direct"), body).data, first.data)
        finally:
            release.set()
        row = self.wait_poll_question("1")
        self.assertEqual((row["generation_state"], row["reply_text"], row["insert_id"]), ("succeeded", text, 42))
        self.assertEqual(row["send_attempts"], 0)
        self.post.assert_called_once()

    def test_direct_failed_or_ambiguous_logging_preserves_returned_text_without_retry(self):
        question, text = "MX-40A容量？", "40L"
        for index, failure in enumerate(("failed", "mismatched_text", "missing_finish", "malformed")):
            with self.subTest(failure=failure):
                self.state = Path(self.folder.name) / f"late-{index}.sqlite3"
                self.post.reset_mock()
                events = self.direct_events(question, text)
                if failure == "failed":
                    events[-1]["data"]["status"] = "failed"
                elif failure == "mismatched_text":
                    events[-1]["data"]["outputs"]["text"] = "different terminal answer"
                waiting, release = threading.Event(), threading.Event()

                def delayed_lines(**kwargs):
                    yield from self.stream_lines(events[:3])
                    waiting.set()
                    release.wait(2)
                    if failure == "malformed":
                        yield b"data: {broken"
                    elif failure != "missing_finish":
                        yield from self.stream_lines(events[3:])

                self.post.return_value.iter_lines.side_effect = delayed_lines
                app, body = self.app("direct"), self.body(Content=question, MsgId="1")
                try:
                    with patch.object(bot, "DIRECT_WAIT_SECONDS", 0.5):
                        first = self.send(app, body)
                    self.assertTrue(waiting.wait(0.5))
                    self.assertEqual(ET.fromstring(first.data).findtext("Content"), text)
                finally:
                    release.set()
                row = self.wait_poll_question("1")
                self.assertEqual(row["generation_state"], "failed" if failure == "failed" else "unknown")
                self.assertEqual((row["ready_text"], row["poll_reply_text"]), (text, text))
                self.assertIsNone(row["reply_text"])
                self.assertEqual(row["send_attempts"], 0)
                self.assertEqual(self.send(self.app("direct"), body).data, first.data)
                self.post.assert_called_once()

    def test_direct_immediate_followup_keeps_returned_answer_before_logging_finishes(self):
        question, answer = 'MX-40A容量是多少？', 'MX-40A的容量是40L。'
        first_events = self.direct_events(question, answer)
        waiting, release = threading.Event(), threading.Event()
        first_response = Mock(status_code=200)
        second_response = Mock(status_code=200)

        def first_lines(**kwargs):
            yield from self.stream_lines(first_events[:3])
            waiting.set()
            release.wait(2)
            yield from self.stream_lines(first_events[3:])

        first_response.iter_lines.side_effect = first_lines
        second_response.iter_lines.side_effect = lambda **kwargs: self.stream_lines(
            self.direct_events('功率呢', 'MX-40A的额定功率是2000W。'))
        self.post.side_effect = lambda *args, **kwargs: (
            first_response if kwargs['json']['inputs']['input'] == question else second_response)
        app = self.app('direct')
        try:
            with patch.object(bot, 'DIRECT_WAIT_SECONDS', 0.5):
                first = self.send(app, self.body(Content=question, MsgId='1'))
                self.assertTrue(waiting.wait(0.5))
                self.assertEqual(ET.fromstring(first.data).findtext('Content'), answer)
                self.assertEqual(self.row('1')['generation_state'], 'pending')
                second = self.send(app, self.body(Content='功率呢', MsgId='2'))
            self.assertIn('2000W', ET.fromstring(second.data).findtext('Content'))
            history = json.loads(self.post.call_args_list[1].kwargs['json']['inputs']['history'])
            self.assertEqual(history, [{'user': question, 'assistant': answer}])
        finally:
            release.set()
        self.assertEqual(self.wait_poll_question('1')['generation_state'], 'succeeded')
        self.wait_poll_question('2')
        self.assertEqual(self.post.call_count, 2)

    def test_direct_fragmented_utf8_sse_and_heartbeats_use_only_safe_sql_text(self):
        question = "中文型号容量？"
        raw = b"\r\n".join(self.stream_lines(self.direct_events(question))) + b"\r\n"
        response = bot.requests.Response()
        response.status_code = 200
        # Exercise requests.iter_lines with UTF-8 characters and SSE fields split across HTTP chunks.
        response.iter_content = Mock(side_effect=lambda **kwargs: (raw[i:i + 2] for i in range(0, len(raw), 2)))
        response.close = Mock()
        self.post.return_value = response
        with patch.object(bot, "DIRECT_WAIT_SECONDS", 0.5):
            reply = self.send(self.app("direct"), self.body(Content=question, MsgId="1"))
        self.assertEqual(ET.fromstring(reply.data).findtext("Content"), "合成答案 <&> ]]>\n下一行")
        self.assertNotIn("UNTRUSTED", reply.data.decode())
        self.assertEqual(self.wait_poll_question("1")["generation_state"], "succeeded")
        response.close.assert_called_once()

    def test_direct_rejects_untrusted_or_malformed_sql_stream_before_answer(self):
        question = "MX-60B容量？"
        for index, mutation in enumerate(("identity", "node_type", "status", "statement", "parameters", "empty", "missing_sql", "bad_json", "bad_data")):
            with self.subTest(mutation=mutation):
                self.state = Path(self.folder.name) / f"invalid-{index}.sqlite3"
                events = self.direct_events(question)
                sql = events[2]["data"]
                if mutation == "identity":
                    events[2]["workflow_run_id"] = "unrelated-run"
                elif mutation in ("node_type", "status"):
                    sql[mutation] = "llm" if mutation == "node_type" else "failed"
                elif mutation == "statement":
                    sql["outputs"]["sql_statement"] += "; DELETE FROM wechat.chathistory"
                elif mutation == "parameters":
                    sql["outputs"]["parameters_json"] = json.dumps(["different question", sql["outputs"]["safe_text"]])
                elif mutation == "empty":
                    sql["outputs"]["safe_text"] = " "
                elif mutation == "missing_sql":
                    events.pop(2)
                elif mutation == "bad_data":
                    events[2]["data"] = []
                if mutation == "bad_json":
                    self.post.return_value.iter_lines.side_effect = lambda **kwargs: iter([b"data: {broken"])
                else:
                    self.post.return_value.iter_lines.side_effect = lambda **kwargs: self.stream_lines(events)
                with patch.object(bot, "DIRECT_WAIT_SECONDS", 0.5):
                    reply = self.send(self.app("direct"), self.body(Content=question, MsgId="1"))
                self.assertEqual(ET.fromstring(reply.data).findtext("Content"), bot.DIRECT_FAILED_REPLY)
                row = self.wait_poll_question("1")
                self.assertEqual(row["generation_state"], "unknown")
                self.assertIsNone(row["ready_text"])
                self.assertIsNone(row["reply_text"])

    def test_direct_hazard_reply_is_immediate_deterministic_and_skips_ai_and_logging(self):
        app = self.app("direct")
        for index, hazard in enumerate(("麻手", "冒烟", "焦糊", "烧焦", "漏电了", "一直跳闸", "反复跳闸")):
            body = self.body(Content="MX-60B" + hazard + "，帮我创建工单", MsgId=str(index + 1))
            first = self.send(app, body)
            self.assertEqual(ET.fromstring(first.data).findtext("Content"), bot.SAFETY_REPLY)
            row = self.row(str(index + 1))
            self.assertEqual((row["message_kind"], row["generation_state"]), ("quick", "quick"))
            self.assertIsNone(row["workflow_run_id"])
            self.assertIsNone(row["insert_id"])
            self.assertEqual(row["send_attempts"], 0)
            self.assertEqual(self.send(app, body).data, first.data)
        self.post.assert_not_called()


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(WeChatLocalTests)
    test_names = [test.id().rsplit(".", 1)[-1] for test in suite]
    with patch("socket.socket.connect", side_effect=AssertionError("Network is forbidden in local tests")), \
            patch.object(bot, "load_dotenv", side_effect=AssertionError("Private .env must not be read")):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    folder = ROOT / "evidence" / datetime.now().astimezone().strftime("%Y%m%d")
    folder.mkdir(parents=True, exist_ok=True)
    evidence = folder / "p05_wechat_direct_local_tests.json"
    evidence.write_text(json.dumps({
        "updated_at": datetime.now().astimezone().isoformat(),
        "status": "passed" if result.wasSuccessful() else "failed",
        "scope": "Flask test_client; synthetic XML; mock Dify/WeChat; temporary SQLite",
        "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "checks": test_names,
        "real_wechat_called": False, "real_dify_called": False, "private_env_read": False,
        "public_listener_started": False, "mobile_acceptance_passed": False,
        "customer_message_api_implemented": True, "async_mode": "async", "aes_implemented": False,
        "poll_mode": "poll", "poll_uses_customer_api": False,
        "poll_lookup_calls_dify": False, "poll_scope_contains_plain_openid": False,
        "stable_token_cache": "memory only; normal force_refresh=false",
        "source_sha256": sha256((ROOT / "services/wechat_public_bot.py").read_bytes()).hexdigest(),
        "test_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Evidence: " + str(evidence))
    sys.exit(0 if result.wasSuccessful() else 1)
