"""Run and remove a synthetic five-node Dify draft workflow and its SQL row.

Scope: Start -> local LLM -> fixed parameterized SQL code -> MCP -> End.
No knowledge base is used and no app/workflow is published. API and node fields
are taken from the official Dify 1.8.0 source in this project's release ZIP.
"""
import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlencode
from uuid import uuid4

from dotenv import dotenv_values
import pymysql
import requests

from configure_dify import make_api
from configure_xinference_models import ENDPOINT, MODELS, MODEL_PATH, PROVIDER, make_xinference

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "evidence" / datetime.now().astimezone().strftime("%Y%m%d")
SQL = "INSERT INTO wechat.chathistory (user_input, response, create_time) VALUES (%s, %s, NOW())"
CODE = (
    "import json\n\n"
    "def main(user_input: str, response: str) -> dict:\n"
    "    return {\"sql_statement\": " + repr(SQL) + ",\n"
    "            \"parameters_json\": json.dumps([user_input, response], ensure_ascii=False)}\n"
)
NODE_IDS = ["start", "llm", "sql", "mcp", "end"]


def build_graph(uid):
    data = [
        {"type": "start", "title": "Synthetic input", "variables": [
            {"variable": "probe", "label": "Synthetic deployment probe", "type": "text-input",
             "required": True, "max_length": 128}]},
        {"type": "llm", "title": "Local model", "model": {"provider": PROVIDER, "name": uid,
             "mode": "chat", "completion_params": {"temperature": 0.01, "max_tokens": 32}},
         "prompt_template": [
             {"role": "system", "text": "This is a synthetic deployment check. Reply with ACK only."},
             {"role": "user", "text": "{{#start.probe#}}"}], "context": {"enabled": False},
         "vision": {"enabled": False}},
        {"type": "code", "title": "Fixed SQL parameters", "code_language": "python3", "code": CODE,
         "variables": [{"variable": "user_input", "value_selector": ["start", "probe"]},
                       {"variable": "response", "value_selector": ["llm", "text"]}],
         "outputs": {"sql_statement": {"type": "string"}, "parameters_json": {"type": "string"}}},
        {"type": "tool", "title": "MySQL MCP", "provider_type": "mcp", "provider_id": "wechat-mysql",
         "provider_name": "wechat-mysql", "tool_name": "execute_sqlstatement", "tool_label": "execute_sqlstatement",
         "version": "2", "tool_configurations": {}, "tool_node_version": "2", "tool_parameters": {
             name: {"type": "variable", "value": ["sql", name]} for name in ("sql_statement", "parameters_json")}},
        {"type": "end", "title": "Synthetic result", "outputs": [
            {"variable": "reply", "value_selector": ["llm", "text"]},
            {"variable": "sql_result", "value_selector": ["mcp", "json"]}]},
    ]
    return {"nodes": [{"id": node_id, "type": "custom", "position": {"x": index * 300, "y": 0},
                       "data": {"version": "1", **node_data}}
                      for index, (node_id, node_data) in enumerate(zip(NODE_IDS, data))],
            "edges": [{"id": source + "-" + target, "source": source, "target": target,
                       "sourceHandle": "source", "targetHandle": "target", "type": "custom"}
                      for source, target in zip(NODE_IDS, NODE_IDS[1:])],
            "viewport": {"x": 0, "y": 0, "zoom": 0.7}}


def safe_error(error, env, token=""):
    text = str(error)
    for value in [token, *[value for key, value in env.items() if value and
                           any(word in key for word in ("PASSWORD", "TOKEN", "SECRET", "KEY"))]]:
        if value:
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"eyJ[A-Za-z0-9_.-]+", "[REDACTED_TOKEN]", text)
    text = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [REDACTED]", text)
    return text[:2000]


def save(report, evidence):
    FOLDER.mkdir(parents=True, exist_ok=True)
    report["updated_at"] = datetime.now().astimezone().isoformat()
    evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def run_stream(session, base, api, report, marker, evidence, env):
    report["run_requested"] = True
    save(report, evidence)
    with session.post(base + f"/apps/{report['app_id']}/workflows/draft/run",
                      headers={"Authorization": "Bearer " + api.token}, json={"inputs": {"probe": marker}, "files": []},
                      stream=True, timeout=(10, 180), allow_redirects=False) as response:
        if response.status_code != 200:
            raise RuntimeError(f"Draft workflow run returned HTTP {response.status_code}")
        response.encoding = "utf-8"
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            event = json.loads(line[5:].strip())
            if event.get("task_id"):
                report["task_id"] = event["task_id"]
            if event.get("workflow_run_id"):
                report["workflow_run_id"] = event["workflow_run_id"]
            data = event.get("data") or {}
            if event.get("event") == "node_finished":
                node = {key: data.get(key) for key in ("node_id", "node_type", "status", "elapsed_time", "error")}
                node["error"] = safe_error(node["error"], env, api.token) if node.get("error") else None
                report.setdefault("stream_nodes", []).append(node)
                print(f"Node {data.get('node_id')}: {data.get('status')}", flush=True)
            if event.get("event") == "workflow_finished":
                report.update(workflow_status=data["status"], workflow_terminal=True)
                save(report, evidence)
                return
            if event.get("event") == "error":
                raise RuntimeError("Draft workflow emitted an error event: " + str(event.get("message", "")))
            save(report, evidence)
    raise RuntimeError("Draft workflow event stream ended without a terminal event")


def stop_and_confirm(api, report):
    """Wait for our run to stop before removing its row, avoiding a late INSERT."""
    if not report.get("run_requested") or report.get("workflow_terminal"):
        return
    if report.get("task_id"):
        api("POST", f"/apps/{report['app_id']}/workflow-runs/tasks/{report['task_id']}/stop")
        report["stop_requested"] = True
    for _ in range(40):
        run_id = report.get("workflow_run_id")
        if not run_id:
            runs = api("GET", f"/apps/{report['app_id']}/workflow-runs?limit=10")["data"]
            if runs:
                if len(runs) != 1:
                    raise RuntimeError("Unexpected multiple runs in this temporary application")
                run_id = report["workflow_run_id"] = runs[0]["id"]
        if run_id:
            detail = api("GET", f"/apps/{report['app_id']}/workflow-runs/{run_id}")
            if detail.get("status") in {"succeeded", "failed", "stopped", "partial-succeeded"}:
                report.update(workflow_terminal=True, workflow_status=detail["status"])
                return
        elif not report.get("task_id"):
            # A rejected request creates no run. Confirm absence on two polls.
            if _ >= 1:
                report["no_run_created"] = True
                return
        time.sleep(3)
    raise RuntimeError("Temporary workflow has not stopped; automatic cleanup was deferred to prevent a late INSERT")


def main(uid):
    env = dotenv_values(ROOT / ".env", interpolate=False)
    marker = "dify-workflow-probe-" + uuid4().hex
    app_name = "TEMP " + marker
    evidence = FOLDER / ("p04_" + marker + ".json")
    report = {"status": "failed", "stage": "preflight", "scope": "synthetic five-node draft workflow",
              "model_uid": uid, "marker": marker, "published": False, "knowledge_base_used": False,
              "six_node_rag_verified": False, "response_text_logged": False, "secrets_logged": False}
    connection = None
    with requests.Session() as session:
        session.trust_env = False
        api = None
        try:
            if env.get("DIFY_BASE_URL") != "http://192.168.18.176" or env.get("MYSQL_HOST") != "192.168.18.176":
                raise RuntimeError("Only the reviewed local Dify/MySQL VM is allowed")
            states = make_xinference(session)("GET", "/v1/models/instances?" + urlencode({"model_name": uid, "model_uid": uid}))
            if not states or any(state.get("status") != "READY" for state in states):
                raise RuntimeError("Requested local model is not READY; no temporary app was created")
            api = make_api(env["DIFY_BASE_URL"], session)
            base = env["DIFY_BASE_URL"] + "/console/api"
            api.token = api("POST", "/login", {"email": env["DIFY_ADMIN_EMAIL"],
                                               "password": env["DIFY_ADMIN_PASSWORD"]})["data"]["access_token"]
            configured = api("GET", MODEL_PATH)["data"]
            if not any(model["model"] == uid and model["status"] == "active" for model in configured):
                raise RuntimeError("Selected model is not registered and active in Dify")
            tools = api("GET", "/workspaces/current/tools/mcp")
            provider = [provider for provider in tools if provider["id"] == "wechat-mysql"]
            if len(provider) != 1 or not provider[0].get("is_team_authorization"):
                raise RuntimeError("Existing MySQL MCP provider is not authorized")
            connection = pymysql.connect(host=env["MYSQL_HOST"], port=3306, user="root",
                                         password=env["MYSQL_ROOT_PASSWORD"], database="wechat", charset="utf8mb4",
                                         autocommit=True, connect_timeout=10, read_timeout=20)
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM chathistory WHERE user_input=%s", (marker,))
                if cursor.fetchone()[0] != 0:
                    raise RuntimeError("Fresh probe marker unexpectedly exists")
            report["marker_verified_absent"] = True
            report["stage"] = "create_temporary_app"
            created = api("POST", "/apps", {"name": app_name, "mode": "workflow", "icon_type": "emoji", "icon": "🧪",
                                           "icon_background": "#E0F2FE", "description": "Temporary synthetic deployment probe; delete after verification."})
            report["app_id"] = created["id"]
            save(report, evidence)
            graph = build_graph(uid)
            (FOLDER / ("p04_" + marker + ".graph.json")).write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
            report["stage"] = "sync_draft"
            result = api("POST", f"/apps/{report['app_id']}/workflows/draft",
                         {"graph": graph, "features": {}, "environment_variables": [], "conversation_variables": []})
            if result.get("result") != "success":
                raise RuntimeError("Dify did not confirm the draft save")
            report["stage"] = "run_five_node_draft"
            run_stream(session, base, api, report, marker, evidence, env)
            report["stage"] = "verify_persisted_run"
            run_path = f"/apps/{report['app_id']}/workflow-runs/{report['workflow_run_id']}"
            detail = api("GET", run_path)
            nodes = api("GET", run_path + "/node-executions")["data"]
            report["persisted_nodes"] = [{key: node.get(key) for key in ("node_id", "node_type", "status", "error", "elapsed_time")}
                                         for node in nodes]
            if detail.get("status") != "succeeded" or {node["node_id"] for node in nodes} != set(NODE_IDS):
                raise RuntimeError("Expected successful execution of all five nodes")
            if any(node["status"] != "succeeded" for node in nodes):
                raise RuntimeError("At least one workflow node did not succeed")
            llm = next(node for node in nodes if node["node_id"] == "llm")
            tool = next(node for node in nodes if node["node_id"] == "mcp")
            reply = (llm.get("outputs") or {}).get("text")
            if not isinstance(reply, str) or not reply.strip():
                raise RuntimeError("LLM node returned no reply")
            sql_result = (tool.get("outputs") or {}).get("json") or []
            if len(sql_result) != 1 or sql_result[0].get("affected_rows") != 1:
                raise RuntimeError("MCP output did not confirm exactly one inserted row")
            report["stage"] = "mysql_readback"
            with connection.cursor() as cursor:
                cursor.execute("SELECT chathistoryid,user_input,response FROM chathistory WHERE user_input=%s", (marker,))
                rows = cursor.fetchall()
            if len(rows) != 1 or rows[0] != (int(sql_result[0]["insert_id"]), marker, reply):
                raise RuntimeError("MySQL readback differs from the workflow LLM reply and MCP insert_id")
            report.update(status="passed", stage="verified", mysql_readback=True, inserted_rows=1,
                          insert_id=int(rows[0][0]), response_sha256=sha256(reply.encode()).hexdigest(),
                          response_characters=len(reply), total_tokens=detail.get("total_tokens"),
                          total_steps=detail.get("total_steps"), local_llm_to_mcp_to_mysql_verified=True)
        except Exception as error:
            report["error"] = safe_error(error, env, api.token if api else "")
        finally:
            cleanup = report["cleanup"] = {}
            terminal_safe = True
            if report.get("app_id") and api:
                try:
                    stop_and_confirm(api, report)
                except Exception as error:
                    terminal_safe = False
                    cleanup["stop_error"] = safe_error(error, env, api.token)
            if connection:
                if terminal_safe and report.get("marker_verified_absent"):
                    try:
                        with connection.cursor() as cursor:
                            cursor.execute("DELETE FROM chathistory WHERE user_input=%s", (marker,))
                            cleanup["synthetic_rows_removed"] = cursor.rowcount
                            cursor.execute("SELECT COUNT(*) FROM chathistory WHERE user_input=%s", (marker,))
                            cleanup["synthetic_rows_remaining"] = cursor.fetchone()[0]
                    except Exception as error:
                        cleanup["mysql_error"] = safe_error(error, env, api.token if api else "")
                connection.close()
            if report.get("app_id") and api and terminal_safe:
                try:
                    app = api("GET", "/apps/" + report["app_id"])
                    if app.get("name") != app_name:
                        raise RuntimeError("Temporary application identity changed; refusing deletion")
                    response = session.delete(base + "/apps/" + report["app_id"],
                                              headers={"Authorization": "Bearer " + api.token}, timeout=30)
                    cleanup["app_delete_http_status"] = response.status_code
                    response = session.get(base + "/apps/" + report["app_id"],
                                           headers={"Authorization": "Bearer " + api.token}, timeout=30)
                    cleanup["temporary_app_removed"] = response.status_code == 404
                except Exception as error:
                    cleanup["app_error"] = safe_error(error, env, api.token)
            if report.get("app_id") and (cleanup.get("app_delete_http_status") not in {200, 204} or
                                         not cleanup.get("temporary_app_removed") or cleanup.get("synthetic_rows_remaining") != 0):
                report["status"] = "failed"
                report["cleanup_incomplete"] = True
            report["stream_nodes"] = [{**node, "error": safe_error(node["error"], env, api.token if api else "") if node.get("error") else None}
                                       for node in report.get("stream_nodes", [])]
            report["persisted_nodes"] = [{**node, "error": safe_error(node["error"], env, api.token if api else "") if node.get("error") else None}
                                          for node in report.get("persisted_nodes", [])]
            save(report, evidence)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("Evidence: " + str(evidence))
    return 0 if report["status"] == "passed" else 1


def self_check():
    for uid in MODELS:
        graph = build_graph(uid)
        assert [node["data"]["type"] for node in graph["nodes"]] == ["start", "llm", "code", "tool", "end"]
        assert [(edge["source"], edge["target"]) for edge in graph["edges"]] == list(zip(NODE_IDS, NODE_IDS[1:]))
        assert graph["nodes"][3]["data"]["provider_id"] == "wechat-mysql"
        assert graph["nodes"][3]["data"]["version"] == graph["nodes"][3]["data"]["tool_node_version"] == "2"
        namespace = {}
        exec(compile(CODE, "synthetic_sql_node", "exec"), namespace)
        value = namespace["main"]("synthetic 'quoted' input", "合成回复")
        assert value["sql_statement"] == SQL
        assert json.loads(value["parameters_json"]) == ["synthetic 'quoted' input", "合成回复"]
    assert FOLDER.name == datetime.now().astimezone().strftime("%Y%m%d")
    print("Offline graph and fixed parameterized SQL checks passed; no service or database was accessed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_uid", nargs="?", choices=MODELS)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
    elif args.model_uid:
        sys.exit(main(args.model_uid))
    else:
        parser.error("Choose one READY model_uid or use --self-check")
