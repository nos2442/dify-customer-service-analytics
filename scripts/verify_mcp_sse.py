"""Temporary loopback SSE smoke check. Never connects to MySQL."""
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from fastmcp import Client

ROOT = Path(__file__).resolve().parents[1]


async def main():
    # Refuse to reuse or terminate an existing service.
    import socket
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 8095)) == 0:
            raise RuntimeError("Port 8095 is occupied; existing service left unchanged.")
    report = {"checked_at": datetime.now().astimezone().isoformat(),
              "scope": "loopback SSE registration and invalid-SQL rejection, no database access"}
    output = ROOT / "evidence" / "20261019"
    child_env = os.environ.copy()
    child_env.update(PYTHONUTF8="1", MCP_HOST="127.0.0.1", MCP_PORT="8095")
    for key in ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD"):
        child_env.pop(key, None)
    with (output / "mcp_sse_server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, str(ROOT / "services" / "mysql_server.py")],
                                   cwd=ROOT, env=child_env, stdout=log, stderr=log)
        try:
            for _ in range(40):
                if process.poll() is not None:
                    raise RuntimeError("MCP service exited; inspect mcp_sse_server.log")
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.1", 8095)) == 0:
                        break
                await asyncio.sleep(0.5)
            else:
                raise TimeoutError("MCP loopback server did not become ready")
            async with Client("http://127.0.0.1:8095/sse") as client:
                tools = await client.list_tools()
                report["tool_names"] = [tool.name for tool in tools]
                assert "execute_sqlstatement" in report["tool_names"]
                report["schema"] = next(tool.inputSchema for tool in tools
                                        if tool.name == "execute_sqlstatement")
                try:
                    await client.call_tool("execute_sqlstatement", {
                        "sql_statement": "DROP TABLE wechat.chathistory",
                        "parameters_json": '["probe", "probe"]'})
                except Exception as exc:
                    assert "fixed" in str(exc).lower(), str(exc)
                    report["invalid_sql_rejected"] = True
                else:
                    raise AssertionError("Invalid SQL was accepted")
            report["status"] = "passed"
        except Exception as exc:
            report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            (output / "p04_mcp_sse_smoke.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
