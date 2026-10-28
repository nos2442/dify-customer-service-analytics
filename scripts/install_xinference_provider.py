"""Install the signed official Xinference 0.0.5 provider on Dify 1.8.0.

Uses the 1.8.0 workspace/plugin.py upload/pkg, install/pkg, tasks and list APIs.
No model credentials, server endpoints, user data or existing .env are changed.
"""
from datetime import datetime
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import sys
import time
from zipfile import ZipFile

from dotenv import dotenv_values
import requests
import yaml

from configure_dify import make_api

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "evidence/20261020"
REPORT = FOLDER / "p03_xinference_provider.json"
PLUGIN_ID = "langgenius/xinference"
VERSION = "0.0.5"
IDENTIFIER = PLUGIN_ID + ":" + VERSION + "@f24040a5f10ca2d2724b1b345faf7a2a178a56ef9a7ad3faf9833bc1321f499b"
PACKAGE_SHA = "e09a2809e78d54ba52782242bf8fa291bc6446fc5a4b05021df70f1c256af4a1"
MARKETPLACE = "https://marketplace.dify.ai/api/v1/plugins"
PACKAGE = ROOT / f"deploy/installers/xinference-{VERSION}.difypkg"
PLUGIN_PATH = "/workspaces/current/plugin"
XINFERENCE_CONFIG = {
    "plugin_id": PLUGIN_ID, "version": VERSION, "identifier": IDENTIFIER,
    "sha256": PACKAGE_SHA, "package": PACKAGE, "report": REPORT,
    "provider": "langgenius/xinference/xinference",
}


def validate_package(content, config=None):
    config = XINFERENCE_CONFIG if config is None else config
    if sha256(content).hexdigest() != config["sha256"]:
        raise RuntimeError("Official package archive SHA256 differs from the reviewed download")
    with ZipFile(BytesIO(content)) as archive:
        manifest = yaml.safe_load(archive.read("manifest.yaml"))
        requirements = archive.read("requirements.txt").decode()
        verification = json.loads(archive.read(".verification.dify.json"))
    if (manifest["author"], manifest["name"], str(manifest["version"])) != (
            "langgenius", config["plugin_id"].split("/")[1], config["version"]):
        raise RuntimeError("Official package identity differs from the reviewed provider")
    sdk_requirement = config.get("sdk_requirement", "dify_plugin>=0.3.0,<0.5.0")
    if manifest["meta"]["version"] != "0.0.1" or sdk_requirement not in requirements:
        raise RuntimeError("Package manifest/SDK constraints differ from the reviewed compatible release")
    if verification.get("authorized_category") != "langgenius":
        raise RuntimeError("Package is missing official Langgenius verification metadata")
    return manifest, requirements


def check_installed(installed, config):
    if len(installed) > 1:
        raise RuntimeError("Multiple matching plugin installations found; no installation was changed")
    if installed and (installed[0]["plugin_unique_identifier"] != config["identifier"]
                      or installed[0]["version"] != config["version"]):
        raise RuntimeError("A different plugin version/identifier is installed; no installation was changed")


def safe_message(message, env):
    message = str(message)
    for key, value in env.items():
        if value and any(word in key for word in ("PASSWORD", "TOKEN", "SECRET", "KEY")):
            message = message.replace(value, "[REDACTED]")
    message = re.sub(r"eyJ[A-Za-z0-9_.-]+", "[REDACTED_TOKEN]", message)
    message = re.sub(r"https?://[^/\s]+:[^/@\s]+@", "https://[REDACTED]@", message)
    return re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[REDACTED]", message)[:4000]


def save(report, path=REPORT):
    path.parent.mkdir(parents=True, exist_ok=True)
    report["checked_at"] = datetime.now().astimezone().isoformat()
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main(config=None):
    config = XINFERENCE_CONFIG if config is None else config
    plugin_id, version, identifier = config["plugin_id"], config["version"], config["identifier"]
    package, report_path = config["package"], config["report"]
    if config.get("marketplace_versions"):
        validate_package(package.read_bytes(), config)
    env = dotenv_values(ROOT / ".env", interpolate=False)
    report = {"status": "in_progress", "stage": "login", "plugin_id": plugin_id,
              "selected_version": version, "endpoint_configured": False,
              "signature_settings_changed": False, "secrets_logged": False,
              "model_calls_made": False, "cloud_credentials_used": False,
              "default_models_changed": False}
    try:
        with requests.Session() as session:
            session.trust_env = False
            base_url = env["DIFY_BASE_URL"].rstrip("/")
            if base_url not in ("http://192.168.18.176", "http://192.168.110.124"):
                raise RuntimeError("DIFY_BASE_URL is not the reviewed local VM")
            api = make_api(base_url, session)
            login = api("POST", "/login", {"email": env["DIFY_ADMIN_EMAIL"],
                                          "password": env["DIFY_ADMIN_PASSWORD"]})
            if login.get("result") != "success":
                raise RuntimeError("Existing Dify administrator login failed")
            api.token = login["data"]["access_token"]
            report["stage"] = "check_existing"
            plugins = api("GET", PLUGIN_PATH + "/list?page=1&page_size=256")
            if plugins["total"] > 256:
                raise RuntimeError("Plugin list exceeds the supported single page; no installation was changed")
            installed = [p for p in plugins["plugins"] if p["plugin_id"] == plugin_id]
            check_installed(installed, config)
            if installed:
                report["action"] = "reused"
                report["daemon_verification_this_run"] = False
                if report_path.exists():
                    previous = json.loads(report_path.read_text(encoding="utf-8"))
                    if previous.get("plugin_unique_identifier") == identifier:
                        for key in ("source_url", "marketplace_versions_url", "plugin_unique_identifier",
                                    "package_sha256", "package_bytes", "published_at", "minimum_dify_version",
                                    "manifest_schema", "requirements", "compatibility_reason", "daemon_verification"):
                            if key in previous:
                                report[key] = previous[key]
            else:
                report["stage"] = "verify_official_package"
                if config.get("marketplace_versions"):
                    versions = json.loads(config["marketplace_versions"].read_text(encoding="utf-8"))
                    report["marketplace_metadata_source"] = "previously_reviewed_local_copy"
                else:
                    with requests.Session() as download:
                        download.trust_env = False
                        download.proxies = {"https": "http://127.0.0.1:7890"}
                        response = download.get(MARKETPLACE + "/" + plugin_id + "/versions", timeout=30)
                        response.raise_for_status()
                        versions = response.json()
                item = next(v for v in versions["data"]["versions"] if v["version"] == version)
                if item["unique_identifier"] != identifier or item["status"] != "active":
                    raise RuntimeError("Reviewed version is not an active official Marketplace release")
                minimum = tuple(item[f"minimum_dify_version_{part}"] for part in ("major", "minor", "patch"))
                if minimum > (1, 8, 0):
                    raise RuntimeError("Marketplace minimum Dify version exceeds 1.8.0")
                if not config.get("marketplace_versions"):
                    report_path.parent.mkdir(parents=True, exist_ok=True)
                    (report_path.parent / "p03_xinference_marketplace_versions.json").write_text(
                        json.dumps(versions, ensure_ascii=False, indent=2), encoding="utf-8")
                if not package.exists():
                    with requests.Session() as download:
                        download.trust_env = False
                        download.proxies = {"https": "http://127.0.0.1:7890"}
                        response = download.get(MARKETPLACE + "/download", params={"unique_identifier": identifier}, timeout=60)
                        response.raise_for_status()
                        validate_package(response.content, config)
                        package.parent.mkdir(parents=True, exist_ok=True)
                        package.write_bytes(response.content)
                manifest, requirements = validate_package(package.read_bytes(), config)
                report.update(source_url=MARKETPLACE + "/download?unique_identifier=" + identifier,
                              marketplace_versions_url=MARKETPLACE + "/" + plugin_id + "/versions",
                              plugin_unique_identifier=identifier, package_sha256=config["sha256"],
                              package_bytes=package.stat().st_size, published_at=item["created_at"],
                              minimum_dify_version=".".join(map(str, minimum)),
                              manifest_schema=manifest["meta"]["version"], requirements=requirements.splitlines(),
                              compatibility_reason="Official signed release with manifest schema 0.0.1; reviewed SDK "
                              + config.get("sdk_requirement", "dify_plugin>=0.3.0,<0.5.0")
                              + "; Marketplace minimum Dify " + ".".join(map(str, minimum)))
                report["stage"] = "upload_signed_package"
                save(report, report_path)
                with package.open("rb") as package_file:
                    response = session.post(base_url + "/console/api" + PLUGIN_PATH + "/upload/pkg",
                                            headers={"Authorization": "Bearer " + api.token},
                                            files={"pkg": (package.name, package_file, "application/octet-stream")}, timeout=120,
                                            allow_redirects=False)
                if response.status_code != 200:
                    raise RuntimeError(f"Official package upload HTTP {response.status_code}; signature settings were unchanged")
                uploaded = response.json()
                if uploaded.get("unique_identifier") != identifier or uploaded.get("verification", {}).get("authorized_category") != "langgenius":
                    raise RuntimeError("Dify daemon did not verify the expected official Langgenius package")
                report["daemon_verification"] = uploaded["verification"]
                report["daemon_verification_this_run"] = True
                report["stage"] = "install"
                result = api("POST", PLUGIN_PATH + "/install/pkg", {"plugin_unique_identifiers": [identifier]})
                report.update(action="installed", task_id=result["task_id"])
                save(report, report_path)
                deadline = time.monotonic() + 600
                previous = None
                while not result["all_installed"]:
                    task = api("GET", PLUGIN_PATH + "/tasks/" + result["task_id"])["task"]
                    report["task_status"] = task["status"]
                    if task["status"] != previous:
                        print(plugin_id + " plugin installation: " + task["status"], flush=True)
                        previous = task["status"]
                    save(report, report_path)
                    if task["status"] == "success":
                        break
                    if task["status"] == "failed":
                        report["task_messages"] = [safe_message(p["message"], env) for p in task["plugins"]]
                        raise RuntimeError("Official plugin installation failed; see sanitized task_messages")
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Installation is still pending after 600 seconds; task was preserved")
                    time.sleep(5)
            report["stage"] = "verify_provider"
            plugins = api("GET", PLUGIN_PATH + "/list?page=1&page_size=256")
            if plugins["total"] > 256:
                raise RuntimeError("Plugin list exceeds the supported single page")
            installed = [p for p in plugins["plugins"] if p["plugin_id"] == plugin_id]
            check_installed(installed, config)
            if len(installed) != 1:
                raise RuntimeError("Expected exactly one installed reviewed plugin")
            report["plugin_unique_identifier"] = installed[0]["plugin_unique_identifier"]
            report["installed_version"] = installed[0]["version"]
            report["installation_id"] = installed[0]["installation_id"]
            providers = api("GET", "/workspaces/current/model-providers")["data"]
            provider = [p for p in providers if p["provider"] == config["provider"]]
            if len(provider) != 1:
                raise RuntimeError("Reviewed model provider did not appear after installation")
            if not set(config.get("required_model_types", ())) <= set(provider[0]["supported_model_types"]):
                raise RuntimeError("Provider does not expose all reviewed model types")
            report.update(status="passed", stage="complete", provider=provider[0]["provider"],
                          supported_model_types=provider[0]["supported_model_types"])
    except Exception as error:
        report.update(status="failed", error=safe_message(str(error), env) if isinstance(error, RuntimeError) else type(error).__name__)
        raise RuntimeError(report["error"]) from None
    finally:
        save(report, report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--self-check"]:
            manifest, requirements = validate_package(PACKAGE.read_bytes())
            assert manifest["plugins"]["models"] == ["provider/xinference.yaml"]
            try:
                validate_package(PACKAGE.read_bytes() + b"tampered")
            except RuntimeError:
                pass
            else:
                raise AssertionError("Modified package passed integrity verification")
            print("Reviewed package and tamper-rejection self-check passed; no network or installation performed.")
        elif sys.argv[1:]:
            raise RuntimeError("Usage: install_xinference_provider.py [--self-check]")
        else:
            main()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
