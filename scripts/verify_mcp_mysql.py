"""Verify the live MCP INSERT, read it back, then remove only that probe row."""
import asyncio
from datetime import datetime
import json
from pathlib import Path
import uuid

from dotenv import dotenv_values
from fastmcp import Client
import pymysql

ROOT = Path(__file__).resolve().parents[1]
SQL = "INSERT INTO wechat.chathistory (user_input, response, create_time) VALUES (%s, %s, NOW())"


async def main():
    config = dotenv_values(ROOT / ".env")
    marker = "deployment-probe-" + uuid.uuid4().hex
    probe_response = "中文单引号'与双引号\"回读\n第二行参数化验证"
    report = {"checked_at": datetime.now().astimezone().isoformat(), "scope": "live Windows MCP to openEuler MySQL"}
    insert_id = None
    connection = pymysql.connect(host=config["MYSQL_HOST"], port=3306, user="root",
                                 password=config["MYSQL_ROOT_PASSWORD"], database="wechat",
                                 charset="utf8mb4", autocommit=True, connect_timeout=10)
    try:
        async with Client(f"http://{config['MCP_HOST']}:{config['MCP_PORT']}/sse") as client:
            report["tools"] = [tool.name for tool in await client.list_tools()]
            result = await client.call_tool("execute_sqlstatement", {
                "sql_statement": SQL, "parameters_json": json.dumps([marker, probe_response]),
            })
            data = result.data
            if not isinstance(data, dict):
                data = json.loads(result.content[0].text)
            insert_id = int(data["insert_id"])
            assert data["affected_rows"] == 1
        with connection.cursor() as cursor:
            cursor.execute("SELECT user_input,response FROM chathistory WHERE chathistoryid=%s", (insert_id,))
            assert cursor.fetchone() == (marker, probe_response)
        report.update(status="passed", inserted_rows=1, utf8_readback=True,
                      single_quote_readback=True, double_quote_readback=True, newline_readback=True)
    finally:
        with connection.cursor() as cursor:
            if insert_id is not None:
                cursor.execute("DELETE FROM chathistory WHERE chathistoryid=%s AND user_input=%s", (insert_id, marker))
                report["probe_rows_removed"] = cursor.rowcount
        connection.close()
    assert report["probe_rows_removed"] == 1
    output = ROOT / "evidence" / datetime.now().strftime("%Y%m%d") / "p04_mcp_mysql_quoted_live.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
