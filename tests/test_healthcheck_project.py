"""Offline checks: bypass proxy settings and reject an unrelated 404 callback."""
import contextlib
import io
import runpy
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/healthcheck_project.py"


class HealthcheckTests(unittest.TestCase):
    def run_probe(self, callback_status, optional_configured=True, mysql_error=None):
        session = MagicMock(trust_env=True)

        def get(url, **kwargs):
            self.assertFalse(session.trust_env, "Health probes must bypass system proxies")
            if url.endswith("/v1/models") and not optional_configured:
                raise requests.ConnectTimeout("test-only model timeout")
            response = MagicMock(status_code=200)
            response.json.return_value = {"data": [{"id": "qwen"}]}
            if url == "http://127.0.0.1:8080/wechat":
                response.status_code = callback_status
            return response

        session.get.side_effect = get
        mysql = MagicMock()
        mysql.cursor.return_value.fetchone.side_effect = [(5,), (84252,)]
        sqlite = MagicMock()
        sqlite.execute.return_value.fetchone.return_value = (18,)
        env = {"DIFY_BASE_URL": "http://192.0.2.1", "DIFY_COURSE_WORKFLOW_API_KEY": "test-only",
               "MYSQL_HOST": "192.0.2.1", "MYSQL_ROOT_PASSWORD": "test-only",
               "MCP_HOST": "192.0.2.2"}
        if not optional_configured:
            env.pop("DIFY_COURSE_WORKFLOW_API_KEY")
        output = io.StringIO()
        with patch.object(sys, "argv", [str(SCRIPT), "--tunnel", "https://callback.invalid"]), \
                patch("dotenv.dotenv_values", return_value=env), \
                patch("requests.Session", return_value=session), \
                patch("pymysql.connect", return_value=mysql, side_effect=mysql_error), \
                patch("sqlite3.connect", return_value=sqlite) as sqlite_connect, \
                contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            runpy.run_path(str(SCRIPT), run_name="__main__")
        self.assertIn("mode=ro", sqlite_connect.call_args.args[0])
        self.assertTrue(sqlite_connect.call_args.kwargs["uri"])
        sqlite.execute.assert_called_once_with("SELECT COUNT(*) FROM messages")
        self.assertNotIn("test-only", output.getvalue())
        return stopped.exception.code, output.getvalue()

    def test_direct_probes_accept_signature_rejection_without_guessing_phone_counts(self):
        code, output = self.run_probe(403)
        self.assertEqual(code, 0)
        self.assertIn("8/8", output)
        self.assertIn("共 18 条；真实手机收件以单独验收证据为准", output)
        self.assertNotIn("其中真实消息", output)

    def test_unrelated_service_with_404_is_not_a_working_wechat_callback(self):
        code, output = self.run_probe(404)
        self.assertEqual(code, 1)
        self.assertIn("7/8", output)
        self.assertIn("[失败] 回调服务（本机）", output)

    def test_optional_unavailable_components_do_not_become_failures(self):
        code, output = self.run_probe(403, optional_configured=False)
        self.assertEqual(code, 0)
        self.assertEqual(output.count("[未配]"), 2)
        self.assertIn("失败 0 项", output)

    def test_database_error_does_not_log_its_secret_message(self):
        code, output = self.run_probe(403, mysql_error=RuntimeError("test-only database secret"))
        self.assertEqual(code, 1)
        self.assertIn("RuntimeError", output)


if __name__ == "__main__":
    unittest.main()
