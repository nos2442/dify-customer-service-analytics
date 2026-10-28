"""Initialize Dify 1.8.0 and register its native MySQL MCP provider.

Source: api/controllers/console/{setup.py,init_validate.py,auth/login.py,
workspace/tool_providers.py}, services/tools/{mcp_tools_manage_service.py,
tools_transform_service.py} in the project's official dify-1.8.0.zip.
Run with the project Python; --self-check performs only an offline check.
"""
from datetime import datetime
import json
from pathlib import Path
import re
import secrets
import sys
from tempfile import TemporaryDirectory
from uuid import UUID

from dotenv import dotenv_values
import requests

ROOT = Path(__file__).resolve().parents[1]
MCP_NAME = "wechat-mysql"
MCP_URL = "http://192.168.18.231:8095/sse"
MCP_PATH = "/workspaces/current/tool-provider/mcp"


def save_missing_credentials(path, values):
    """Write only absent/blank credential keys, preserving this file's ACL."""
    text = path.read_text(encoding="utf-8")
    for key, value in values.items():
        pattern = rf"(?m)^[ \t]*(?:export[ \t]+)?{key}[ \t]*=.*$"
        line = f"{key}={value}"
        if re.search(pattern, text):
            text = re.sub(pattern, line, text)
        else:
            text = text.rstrip("\r\n") + "\n" + line + "\n"
    path.write_text(text, encoding="utf-8")


def configure(env_path, api, report):
    env = dotenv_values(env_path, interpolate=False)
    report["stage"] = "setup_status"
    step = api("GET", "/setup").get("step")
    if step not in {"not_started", "finished"}:
        raise RuntimeError("Dify returned an unknown setup status")
    report.update(setup_before=step, setup_created=False)
    email, password = env.get("DIFY_ADMIN_EMAIL"), env.get("DIFY_ADMIN_PASSWORD")
    if step == "not_started":
        report["stage"] = "init_validation"
        init_status = api("GET", "/init").get("status")
        if init_status == "not_started":
            init_password = env.get("DIFY_INIT_PASSWORD")
            if not init_password:
                raise RuntimeError("Dify requires DIFY_INIT_PASSWORD in the project .env")
            if api("POST", "/init", {"password": init_password}).get("result") != "success":
                raise RuntimeError("Dify initialization password validation failed")
        elif init_status != "finished":
            raise RuntimeError("Dify returned an unknown initialization status")
        missing = {}
        if not email:
            email = missing["DIFY_ADMIN_EMAIL"] = "admin@example.com"
        if not password:
            password = missing["DIFY_ADMIN_PASSWORD"] = "Dify-" + secrets.token_urlsafe(32) + "9"
        if missing:
            # Persist before POST: an interrupted response must not lose the password.
            save_missing_credentials(env_path, missing)
        report["stage"] = "setup_create"
        result = api("POST", "/setup", {"email": email, "name": "Project Admin", "password": password})
        if result.get("result") != "success" or api("GET", "/setup").get("step") != "finished":
            raise RuntimeError("Dify setup did not finish")
        report["setup_created"] = True
    elif not email or not password:
        raise RuntimeError("Dify is already initialized; existing DIFY_ADMIN_EMAIL and DIFY_ADMIN_PASSWORD are required")

    report["stage"] = "login"
    login = api("POST", "/login", {"email": email, "password": password, "remember_me": False,
                                    "language": "zh-Hans"})
    if login.get("result") != "success" or not login.get("data", {}).get("access_token"):
        raise RuntimeError("Dify login failed; existing credentials were not changed")
    api.token = login["data"]["access_token"]
    report["login_verified"] = True

    report["stage"] = "mcp_provider"
    # /tools/mcp uses server_identifier as id. This management list returns UUIDs.
    providers = api("GET", "/workspaces/current/tool-providers?type=mcp")
    matches = [p for p in providers if p["name"] == MCP_NAME or p.get("server_identifier") == MCP_NAME]
    if len(matches) > 1:
        raise RuntimeError("Multiple MCP providers match wechat-mysql; no provider was changed")
    payload = {"name": MCP_NAME, "server_identifier": MCP_NAME, "server_url": MCP_URL,
               "icon": "🗄️", "icon_type": "emoji", "icon_background": "#E0F2FE",
               "timeout": 30, "sse_read_timeout": 300}
    if matches:
        provider = matches[0]
        if provider["name"] != MCP_NAME or provider.get("server_identifier") != MCP_NAME:
            raise RuntimeError("Existing MCP provider name/identifier conflicts; no provider was changed")
        provider_id = str(UUID(provider["id"]))
        # The API masks URL paths. Reapply our endpoint to guarantee /sse.
        if api("PUT", MCP_PATH, {**payload, "provider_id": provider_id}).get("result") != "success":
            raise RuntimeError("MCP endpoint reconciliation failed")
        report["provider_action"] = "reused"
    else:
        provider = api("POST", MCP_PATH, payload)
        provider_id = str(UUID(provider["id"]))
        report["provider_action"] = "created"
    report.update(provider_id=provider_id, provider_name=MCP_NAME, server_url=MCP_URL)
    report["stage"] = "mcp_auth"
    if api("POST", MCP_PATH + "/auth", {"provider_id": provider_id}).get("result") != "success":
        raise RuntimeError("MCP authentication/connection did not succeed")
    report["stage"] = "mcp_list_tools"
    remote = api("GET", MCP_PATH + "/update/" + provider_id)
    detail = api("GET", MCP_PATH + "/tools/" + provider_id)
    cached = api("GET", "/workspaces/current/tools/mcp")
    cached_matches = [p for p in cached if p["id"] == MCP_NAME]
    if len(cached_matches) != 1 or not detail.get("is_team_authorization"):
        raise RuntimeError("MCP provider authorization/cache verification failed")
    for provider in (remote, detail, cached_matches[0]):
        tools = provider.get("tools", [])
        if [t["name"] for t in tools] != ["execute_sqlstatement"]:
            raise RuntimeError("MCP must expose exactly execute_sqlstatement")
        parameters = tools[0].get("parameters") or []
        if {p["name"] for p in parameters} != {"sql_statement", "parameters_json"}:
            raise RuntimeError("MCP tool parameters differ from the project's fixed SQL tool")
    report.update(status="passed", stage="complete", tool_names=["execute_sqlstatement"],
                  tool_parameters=["sql_statement", "parameters_json"], auth_verified=True,
                  remote_and_cached_tools_verified=True, secrets_logged=False)


def make_api(base_url, session):
    def api(method, path, payload=None):
        headers = {"Authorization": "Bearer " + api.token} if api.token else {}
        try:
            response = session.request(method, base_url + "/console/api" + path,
                                       json=payload, headers=headers, timeout=(10, 120), allow_redirects=False)
        except requests.RequestException as error:
            raise RuntimeError(f"Dify {method} {path}: network failure ({type(error).__name__})") from None
        if not 200 <= response.status_code < 300:
            # Response bodies can contain credentials; never print or persist them.
            raise RuntimeError(f"Dify {method} {path}: HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError:
            raise RuntimeError(f"Dify {method} {path}: expected JSON response") from None
    api.token = ""
    return api


def self_check():
    """Exercise fresh setup, rerun and missing-credential protection without HTTP."""
    state = {"setup": "not_started", "provider": None, "setup_posts": 0, "provider_posts": 0}
    tool = {"name": "execute_sqlstatement", "parameters": [{"name": "sql_statement"}, {"name": "parameters_json"}]}

    def api(method, path, payload=None):
        if path == "/setup":
            if method == "POST":
                state["setup_posts"] += 1
                state["setup"] = "finished"
                return {"result": "success"}
            return {"step": state["setup"]}
        if path == "/init":
            return {"status": "finished"}
        if path == "/login":
            assert len(payload["password"]) >= 40
            return {"result": "success", "data": {"access_token": "offline-test-only"}}
        if path == "/workspaces/current/tool-providers?type=mcp":
            return [state["provider"]] if state["provider"] else []
        if path == MCP_PATH and method == "POST":
            state["provider_posts"] += 1
            state["provider"] = {"id": "00000000-0000-4000-8000-000000000001", "name": MCP_NAME,
                                 "server_identifier": MCP_NAME, "tools": [tool], "is_team_authorization": True}
            return state["provider"]
        if path == MCP_PATH and method == "PUT" or path == MCP_PATH + "/auth":
            return {"result": "success"}
        if path.startswith(MCP_PATH + "/"):
            return state["provider"]
        if path == "/workspaces/current/tools/mcp":
            return [{**state["provider"], "id": MCP_NAME}]
        raise AssertionError((method, path))

    with TemporaryDirectory() as folder:
        env_path = Path(folder) / ".env"
        env_path.write_text("UNCHANGED=value\nDIFY_ADMIN_PASSWORD=\n", encoding="utf-8")
        first = {}
        configure(env_path, api, first)
        credentials = env_path.read_bytes()
        second = {}
        configure(env_path, api, second)
        assert first["setup_created"] and second["provider_action"] == "reused"
        assert state["setup_posts"] == state["provider_posts"] == 1
        assert env_path.read_bytes() == credentials and "UNCHANGED=value" in credentials.decode()
        env_path.write_text("UNCHANGED=value\n", encoding="utf-8")
        try:
            configure(env_path, api, {})
        except RuntimeError as error:
            assert "already initialized" in str(error)
        else:
            raise AssertionError("Initialized installation accepted missing credentials")
        assert env_path.read_text(encoding="utf-8") == "UNCHANGED=value\n"
    print("Offline self-check passed; no real service or project .env was changed.")


def main():
    env_path = ROOT / ".env"
    env = dotenv_values(env_path, interpolate=False)
    base_url = (env.get("DIFY_BASE_URL") or "").rstrip("/")
    if not re.fullmatch(r"http://192\.168\.18\.176(?::\d+)?", base_url):
        raise RuntimeError("DIFY_BASE_URL must target the project's local Dify VM")
    report = {"checked_at": datetime.now().astimezone().isoformat(), "status": "failed", "base_url": base_url}
    evidence = ROOT / "evidence" / datetime.now().strftime("%Y%m%d") / "p03_dify_setup_mcp.json"
    try:
        with requests.Session() as session:
            session.trust_env = False
            configure(env_path, make_api(base_url, session), report)
    except Exception as error:
        report["error"] = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        raise RuntimeError(report["error"]) from None
    finally:
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--self-check"]:
            self_check()
        elif sys.argv[1:]:
            raise RuntimeError("Usage: configure_dify.py [--self-check]")
        else:
            main()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
