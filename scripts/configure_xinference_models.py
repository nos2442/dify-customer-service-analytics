"""Register one READY local LLM using Dify 1.8.0's model credential API.

Reviewed sources: Dify 1.8.0 api/controllers/console/workspace/models.py;
Xinference plugin 0.0.5 provider/xinference.yaml and models/llm/llm.py;
Xinference v1.17.1 RESTfulClient.get_instance_info/describe_model.
This script never calls Dify's default-model API (even GET can set a default).
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

from dotenv import dotenv_values
import requests

from configure_dify import make_api

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "http://192.168.18.176:9997"
PROVIDER = "langgenius/xinference/xinference"
MODELS = ("qwen2.5-instruct", "deepseek-r1-distill-qwen")
MODEL_PATH = "/workspaces/current/model-providers/" + PROVIDER + "/models"


def configure(uid, xinference, api, report):
    report["stage"] = "xinference_ready"
    query = urlencode({"model_name": uid, "model_uid": uid})
    instances = xinference("GET", "/v1/models/instances?" + query)
    statuses = [instance.get("status") for instance in instances]
    report["instance_statuses"] = statuses
    if not instances or any(status != "READY" for status in statuses):
        raise RuntimeError("Selected Xinference model is not READY; no Dify model configuration was written")
    model = xinference("GET", "/v1/models/" + uid)
    if model.get("model_type") != "LLM" or "chat" not in model.get("model_ability", []):
        raise RuntimeError("Selected Xinference UID is not a chat LLM; registration refused")
    report["model_description"] = {key: model[key] for key in
                                   ("model_name", "model_type", "model_format", "model_size_in_billions",
                                    "quantization", "context_length", "model_ability") if key in model}
    report["stage"] = "xinference_generation"
    # Synthetic health probe only; no project documents or chat records are read.
    generated = xinference("POST", "/v1/chat/completions",
                           {"model": uid, "messages": [{"role": "user", "content": "Reply with OK."}],
                            "stream": False, "max_tokens": 8, "temperature": 0})
    choices = generated.get("choices", [])
    message = choices[0].get("message", {}) if choices else {}
    if not any(isinstance(message.get(key), str) and message[key].strip()
               for key in ("content", "reasoning_content")):
        raise RuntimeError("Local chat completion returned no text; registration refused")
    report.update(local_generation_verified=True, generation_text_logged=False,
                  completion_tokens=generated.get("usage", {}).get("completion_tokens"))

    report["stage"] = "read_existing_credentials"
    credential_query = urlencode({"model": uid, "model_type": "llm", "config_from": "custom-model"})
    existing = api("GET", MODEL_PATH + "/credentials?" + credential_query)
    credentials = {"server_url": ENDPOINT, "model_uid": uid, "api_key": "",
                   "invoke_timeout": "60", "max_retries": "3"}
    current_id = existing.get("current_credential_id")
    if current_id:
        stored = existing.get("credentials") or {}
        if (str(stored.get("server_url", "")).rstrip("/") != ENDPOINT or
                stored.get("model_uid") != uid or stored.get("api_key")):
            raise RuntimeError("Existing model credentials differ from this local endpoint/UID; nothing was overwritten")
        credentials = stored
        report.update(action="reused", credential_id=current_id)
    elif existing.get("available_credentials"):
        raise RuntimeError("Saved model credentials exist without an active selection; refusing to create a duplicate")

    report["stage"] = "dify_validate"
    payload = {"model": uid, "model_type": "llm", "credentials": credentials}
    validation = api("POST", MODEL_PATH + "/credentials/validate", payload)
    if validation.get("result") != "success":
        # Error text may include credentials; preserve only a safe failure stage.
        raise RuntimeError("Dify rejected the selected Xinference model credentials; no credentials were saved")
    report["dify_validation_passed"] = True
    if not current_id:
        report["stage"] = "dify_save"
        if api("POST", MODEL_PATH + "/credentials", {**payload, "name": uid}).get("result") != "success":
            raise RuntimeError("Dify did not confirm the model credential save")
        report["action"] = "created"
    report["stage"] = "verify_dify_model"
    models = api("GET", MODEL_PATH)["data"]
    selected = [model for model in models if model["model"] == uid and model["model_type"] == "llm"]
    if len(selected) != 1 or selected[0].get("status") != "active":
        raise RuntimeError("Expected exactly one active model entry after registration")
    report.update(status="passed", stage="complete", dify_model_status=selected[0]["status"],
                  default_model_api_called=False, other_providers_modified=False, secrets_logged=False)


def make_xinference(session):
    def request(method, path, payload=None):
        try:
            response = session.request(method, ENDPOINT + path, json=payload, timeout=(10, 180),
                                       allow_redirects=False)
        except requests.RequestException as error:
            raise RuntimeError(f"Local Xinference is unavailable ({type(error).__name__}); registration refused") from None
        if response.status_code != 200:
            raise RuntimeError(f"Xinference {method} {path.split('?')[0]}: HTTP {response.status_code}; registration refused")
        try:
            return response.json()
        except ValueError:
            raise RuntimeError("Xinference returned non-JSON data; registration refused") from None
    return request


def main(uid):
    env = dotenv_values(ROOT / ".env", interpolate=False)
    report = {"status": "failed", "stage": "login", "model_uid": uid, "provider": PROVIDER,
              "endpoint": ENDPOINT, "checked_at": datetime.now().astimezone().isoformat()}
    evidence = ROOT / "evidence/20261020" / f"p03_dify_model_{uid}.json"
    try:
        if env.get("DIFY_BASE_URL") != "http://192.168.18.176":
            raise RuntimeError("DIFY_BASE_URL is not the reviewed local Dify VM")
        with requests.Session() as session:
            session.trust_env = False
            api = make_api(env["DIFY_BASE_URL"], session)
            login = api("POST", "/login", {"email": env["DIFY_ADMIN_EMAIL"],
                                          "password": env["DIFY_ADMIN_PASSWORD"]})
            if login.get("result") != "success" or not login.get("data", {}).get("access_token"):
                raise RuntimeError("Existing local administrator login failed")
            api.token = login["data"]["access_token"]
            configure(uid, make_xinference(session), api, report)
    except Exception as error:
        report["error"] = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        raise RuntimeError(report["error"]) from None
    finally:
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def self_check():
    state = {"ready": True, "saved": False, "saves": 0, "api_calls": 0, "validation": True}
    uid = MODELS[0]

    def xinference(method, path, payload=None):
        if path.startswith("/v1/models/instances?"):
            return [{"status": "READY" if state["ready"] else "LOADING"}]
        if path == "/v1/models/" + uid:
            return {"model_type": "LLM", "model_ability": ["chat"]}
        assert method == "POST" and path == "/v1/chat/completions"
        assert payload["model"] == uid and payload["messages"][0]["content"] == "Reply with OK."
        return {"choices": [{"message": {"content": "OK"}}], "usage": {"completion_tokens": 1}}

    def api(method, path, payload=None):
        state["api_calls"] += 1
        assert "default-model" not in path and PROVIDER in path
        if method == "GET" and "/credentials?" in path:
            return {"current_credential_id": "offline-test" if state["saved"] else None,
                    "credentials": {"server_url": ENDPOINT, "model_uid": uid, "api_key": ""}}
        if method == "POST" and path.endswith("/validate"):
            return {"result": "success" if state["validation"] else "error"}
        if method == "POST" and path.endswith("/credentials"):
            state.update(saved=True, saves=state["saves"] + 1)
            assert payload["name"] == uid and payload["model_type"] == "llm"
            return {"result": "success"}
        assert method == "GET" and path == MODEL_PATH
        return {"data": [{"model": uid, "model_type": "llm", "status": "active"}]}

    first, second = {}, {}
    configure(uid, xinference, api, first)
    configure(uid, xinference, api, second)
    assert first["action"] == "created" and second["action"] == "reused" and state["saves"] == 1
    calls = state["api_calls"]
    state["ready"] = False
    try:
        configure(uid, xinference, api, {})
    except RuntimeError as error:
        assert "not READY" in str(error) and state["api_calls"] == calls
    else:
        raise AssertionError("A model without READY status reached Dify configuration")
    state.update(ready=True, saved=False, validation=False)
    try:
        configure(uid, xinference, api, {})
    except RuntimeError as error:
        assert "Dify rejected" in str(error) and state["saves"] == 1
    else:
        raise AssertionError("Rejected credentials were saved")
    print("Offline checks passed: create/reuse, READY gate, validation gate, no default-model calls. No real HTTP or .env changes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_uid", nargs="?", choices=MODELS)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check and args.model_uid:
        parser.error("--self-check does not register a model")
    if not args.self_check and not args.model_uid:
        parser.error("Choose exactly one model_uid, or use --self-check")
    try:
        self_check() if args.self_check else main(args.model_uid)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
