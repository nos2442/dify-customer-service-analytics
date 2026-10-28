"""Create the minimal T05 chatbot with an explicitly selected model and one synthetic probe."""
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
import time

from dotenv import dotenv_values
import requests
import yaml

from configure_dify import make_api
from verify_dify_workflow import safe_error

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "evidence/20261021/t05_kuakua"
REPORT = FOLDER / "verification.json"
NAME = "夸夸机器人"
PROVIDER = "langgenius/siliconflow/siliconflow"
MODEL = "deepseek-ai/DeepSeek-V3"
PROMPT = "你是夸夸机器人。根据用户描述，用两句简短中文真诚夸奖并鼓励。只依据用户说的内容，不杜撰成就或身份，不作夸大保证。回复不超过80字。"
QUERY = "我今天坚持把计划里的学习任务做完了。"
CONFIG = {"model": {"provider": PROVIDER, "name": MODEL, "mode": "chat",
                    "completion_params": {"temperature": 0.5, "max_tokens": 128, "stop": []}},
          "prompt_type": "simple", "pre_prompt": PROMPT, "user_input_form": [],
          "opening_statement": "告诉我你今天完成的一件小事，我来夸夸你。",
          "agent_mode": {"enabled": False, "tools": []},
          "dataset_configs": {"retrieval_model": "single", "datasets": {"strategy": "router", "datasets": []}}}


def main():
    env = dotenv_values(ROOT / ".env", interpolate=False)
    report = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {
        "status": "preparing", "name": NAME, "mode": "chat", "provider": PROVIDER, "model": MODEL,
        "prompt_origin": "Self-authored validation prompt; not original course text",
        "course_requirement_evidence": ["docs/参考资料/项目二需求与架构总览.html:1164,1304,1419,1821",
                                        "项目实施框架与任务检查清单.md:100"],
        "default_model_api_called": False, "api_key_created": False, "env_written": False,
        "other_apps_modified": False, "synthetic_query_only": True, "created_app_count": 0}
    def save():
        report["checked_at"] = datetime.now().astimezone().isoformat()
        text = json.dumps(report, ensure_ascii=False, indent=2)
        for key, value in env.items():
            if value and any(word in key for word in ("KEY", "PASSWORD", "TOKEN", "SECRET")):
                text = text.replace(value, "[REDACTED]")
        REPORT.write_text(text, encoding="utf-8")
    try:
        if env.get("DIFY_BASE_URL") not in {"http://192.168.18.176", "http://192.168.110.108"}:
            raise RuntimeError("Only the reviewed local VM is permitted")
        with requests.Session() as session:
            session.trust_env = False
            api = make_api(env["DIFY_BASE_URL"], session)
            api.token = api("POST", "/login", {"email": env["DIFY_ADMIN_EMAIL"], "password": env["DIFY_ADMIN_PASSWORD"]})["data"]["access_token"]
            apps = api("GET", "/apps?page=1&limit=100")
            if apps.get("has_more"):
                raise RuntimeError("Application listing is incomplete; no app created")
            matches = [app for app in apps["data"] if "夸夸" in app["name"]]
            if matches and (len(matches) != 1 or matches[0]["id"] != report.get("app_id")):
                raise RuntimeError("An existing unregistered praise chatbot exists; no duplicate created")
            models = api("GET", "/workspaces/current/model-providers/" + PROVIDER + "/models")["data"]
            if not any(model["model"] == MODEL and model["model_type"] == "llm" and model["status"] == "active" for model in models):
                raise RuntimeError("The explicitly selected cloud model is not active")
            if not report.get("app_id"):
                doc = {"version": "0.4.0", "kind": "app", "app": {"name": NAME, "mode": "chat",
                    "description": "T05普通聊天验证；提示词为项目自拟，非课件原文。", "icon_type": "emoji", "icon": "👏", "icon_background": "#E0F2FE"},
                    "dependencies": [{"current_identifier": None, "type": "package", "value": {"plugin_unique_identifier":
                    "langgenius/siliconflow:0.0.23@da2cc8bd076ff081b0c4f69e6d484e3e1e79bd3ad1d937a9c00d9ffdbc8dd14f"}}], "model_config": CONFIG}
                created = api("POST", "/apps/imports", {"mode": "yaml-content", "yaml_content": yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)})
                if created.get("status") != "completed" or not created.get("app_id"):
                    raise RuntimeError("Ordinary chatbot DSL import did not complete")
                report.update(app_id=created["app_id"], created_app_count=1)
                save()
                if api("POST", f"/apps/{report['app_id']}/model-config", CONFIG).get("result") != "success":
                    raise RuntimeError("Chatbot model configuration save failed")
            app = api("GET", "/apps/" + report["app_id"])
            config = app["model_config"]
            if app["mode"] != "chat" or app["name"] != NAME or config["model"]["provider"] != PROVIDER or config["model"]["name"] != MODEL or config["pre_prompt"] != PROMPT:
                raise RuntimeError("Saved chatbot identity/model/prompt differs; nothing overwritten")
            report["saved_config_readback_passed"] = True
            if report.get("status") != "passed":
                tick = time.perf_counter()
                result = api("POST", f"/apps/{report['app_id']}/chat-messages", {"inputs": {}, "query": QUERY, "files": [],
                    "model_config": config, "response_mode": "blocking"})
                answer = result.get("answer", "")
                if not isinstance(answer, str) or not answer.strip() or not re.search(r"[\u4e00-\u9fff]", answer):
                    raise RuntimeError("Chatbot returned no Chinese answer")
                report.update(query=QUERY, answer=answer, answer_characters=len(answer), answer_sha256=sha256(answer.encode()).hexdigest(),
                    elapsed_seconds=round(time.perf_counter() - tick, 3), message_id=result.get("message_id") or result.get("id"),
                    conversation_id=result.get("conversation_id"), usage=result.get("metadata", {}).get("usage"), status="passed")
            exported = api("GET", f"/apps/{report['app_id']}/export?include_secret=false")["data"]
            if any(value in exported for key, value in env.items() if value and any(word in key for word in ("KEY", "TOKEN", "PASSWORD", "SECRET"))):
                raise RuntimeError("Secret detected in export; local DSL not written")
            (FOLDER / "kuakua_redacted.yml").write_text("# 普通chatbot；自拟验证提示词，无API密钥。\n" + exported, encoding="utf-8")
            report.update(export_contains_credentials=False, background_generation_pending=False)
            report.pop("safe_error", None)
    except Exception as error:
        report.update(status="failed", safe_error=safe_error(error, env))
        save()
        raise RuntimeError(report["safe_error"]) from None
    save()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
