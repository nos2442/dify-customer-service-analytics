"""Fixed chat-history INSERT tool; bind a LAN IP only with network restrictions."""

import json
import os
from pathlib import Path

from fastmcp import FastMCP
import pymysql
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


INSERT_SQL = (
    "INSERT INTO wechat.chathistory (user_input, response, create_time) "
    "VALUES (%s, %s, NOW())"
)
mcp_server = FastMCP("wechat-mysql")


def execute_sqlstatement(sql_statement: str, parameters_json: str) -> dict[str, int]:
    """Insert one chat record using the fixed SQL template and two JSON strings."""
    if sql_statement != INSERT_SQL:
        raise ValueError("Only the fixed wechat.chathistory INSERT template is allowed.")
    parameters = json.loads(parameters_json)
    if (
        not isinstance(parameters, list)
        or len(parameters) != 2
        or any(not isinstance(value, str) for value in parameters)
    ):
        raise ValueError("parameters_json must be an array of exactly two strings.")

    host = os.environ.get("MYSQL_HOST")
    user = os.environ.get("MYSQL_USER")
    password = os.environ.get("MYSQL_PASSWORD")
    if not host or not user or password is None:
        raise ValueError("Configure MYSQL_HOST, MYSQL_USER and MYSQL_PASSWORD first.")
    connection = pymysql.connect(
        host=host,
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=user,
        password=password,
        charset="utf8mb4",
        autocommit=False,
    )
    try:
        with connection.cursor() as cursor:
            affected_rows = cursor.execute(sql_statement, parameters)
            insert_id = cursor.lastrowid
        connection.commit()
        return {"affected_rows": affected_rows, "insert_id": insert_id}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


mcp_server.tool(execute_sqlstatement)

if __name__ == "__main__":
    mcp_server.run(
        transport="sse",
        host=os.environ.get("MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCP_PORT", "8095")),
    )
