"""Unit tests with a mocked database; these do not establish P04 acceptance."""

import json
import os
import unittest
from unittest.mock import patch

from services import mysql_server


class MySQLToolTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(
            os.environ,
            {"MYSQL_HOST": "db.test", "MYSQL_PORT": "3306",
             "MYSQL_USER": "chat_test", "MYSQL_PASSWORD": "unit-test-only"},
        )
        environment.start()
        self.addCleanup(environment.stop)
        connector = patch.object(mysql_server.pymysql, "connect")
        self.connect = connector.start()
        self.addCleanup(connector.stop)
        self.connection = self.connect.return_value
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.execute.return_value = 1
        self.cursor.lastrowid = 42
        self.parameters = ["漏水怎么办？'; DROP TABLE wechat.chathistory; --", "请联系售后。"]

    def invoke(self):
        return mysql_server.execute_sqlstatement(
            mysql_server.INSERT_SQL, json.dumps(self.parameters, ensure_ascii=False)
        )

    def test_parameterized_insert_commits_and_returns_real_result(self):
        result = self.invoke()
        self.cursor.execute.assert_called_once_with(mysql_server.INSERT_SQL, self.parameters)
        self.connect.assert_called_once_with(
            host="db.test", port=3306, user="chat_test", password="unit-test-only",
            charset="utf8mb4", autocommit=False,
        )
        self.assertEqual(result, {"affected_rows": 1, "insert_id": 42})
        self.connection.commit.assert_called_once_with()
        self.connection.rollback.assert_not_called()
        self.connection.close.assert_called_once_with()

    def test_other_sql_is_rejected_before_connecting(self):
        for sql in ("SELECT 1", mysql_server.INSERT_SQL + "; DROP TABLE wechat.chathistory"):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                mysql_server.execute_sqlstatement(sql, '["question", "answer"]')
        self.connect.assert_not_called()

    def test_invalid_json_or_parameters_are_rejected_before_connecting(self):
        for payload in ('not json', '{}', 'null', '"text"', '[]', '["one"]',
                        '["one", "two", "three"]', '["one", 1]', '[true, "two"]'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                mysql_server.execute_sqlstatement(mysql_server.INSERT_SQL, payload)
        self.connect.assert_not_called()

    def test_missing_or_empty_host_does_not_default_to_localhost(self):
        for host in (None, ""):
            with self.subTest(host=host), patch.dict(os.environ):
                if host is None:
                    os.environ.pop("MYSQL_HOST", None)
                else:
                    os.environ["MYSQL_HOST"] = host
                with self.assertRaisesRegex(ValueError, "MYSQL_HOST"):
                    self.invoke()
        self.connect.assert_not_called()

    def test_execute_failure_rolls_back_and_propagates(self):
        self.cursor.execute.side_effect = RuntimeError("insert failed")
        with self.assertRaisesRegex(RuntimeError, "insert failed"):
            self.invoke()
        self.connection.rollback.assert_called_once_with()
        self.connection.commit.assert_not_called()
        self.connection.close.assert_called_once_with()

    def test_commit_failure_rolls_back_and_does_not_return_success(self):
        self.connection.commit.side_effect = RuntimeError("commit failed")
        with self.assertRaisesRegex(RuntimeError, "commit failed"):
            self.invoke()
        self.connection.rollback.assert_called_once_with()
        self.connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
