"""Check/download one reviewed official SiliconFlow package; never contact Dify or read keys."""
import argparse
from datetime import datetime
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "evidence/20261021/siliconflow_provider"
VERSION = "0.0.23"
PLUGIN_ID = "langgenius/siliconflow"
IDENTIFIER = PLUGIN_ID + ":" + VERSION + "@da2cc8bd076ff081b0c4f69e6d484e3e1e79bd3ad1d937a9c00d9ffdbc8dd14f"
PACKAGE_SHA = "c6e6e5c199d716ca37f0e372d8a3459e57eb66dfd302590d7641ad9307d547f9"
PACKAGE = FOLDER / f"siliconflow-{VERSION}.difypkg"
MARKETPLACE = "https://marketplace.dify.ai/api/v1/plugins"
GITHUB_COMMIT = "0ba92d0de0769b552972c2a6f905a1ce847b99f0"
MODEL_FILES = {
    "llm": ("models/llm/deepseek-v3.yaml", "deepseek-ai/DeepSeek-V3"),
    "text-embedding": ("models/text_embedding/bce-embedding-base-v1.yaml", "netease-youdao/bce-embedding-base_v1"),
    "rerank": ("models/rerank/bce-reranker-base_v1.yaml", "netease-youdao/bce-reranker-base_v1"),
}


def validate_package(content):
    if sha256(content).hexdigest() != PACKAGE_SHA:
        raise RuntimeError("Archive SHA256 differs from the reviewed official download")
    with ZipFile(BytesIO(content)) as archive:
        manifest = yaml.safe_load(archive.read("manifest.yaml"))
        provider = yaml.safe_load(archive.read("provider/siliconflow.yaml"))
        requirements = archive.read("requirements.txt").decode()
        verification = json.loads(archive.read(".verification.dify.json"))
        models = {kind: yaml.safe_load(archive.read(file)) for kind, (file, _) in MODEL_FILES.items()}
    if (manifest["author"], manifest["name"], str(manifest["version"])) != ("langgenius", "siliconflow", VERSION):
        raise RuntimeError("Package identity does not match the reviewed release")
    if manifest["meta"]["version"] != "0.0.1" or "dify_plugin>=0.3.0,<0.5.0" not in requirements:
        raise RuntimeError("Manifest/SDK differs from the reviewed Dify 1.8 candidate")
    if verification.get("authorized_category") != "langgenius":
        raise RuntimeError("Official verification metadata is missing")
    if provider["provider"] != "siliconflow" or not set(MODEL_FILES) <= set(provider["supported_model_types"]):
        raise RuntimeError("Provider does not cover all three required model types")
    for kind, (_, expected_id) in MODEL_FILES.items():
        if models[kind]["model"] != expected_id or models[kind]["model_type"] != kind:
            raise RuntimeError("A course model declaration differs from the reviewed package")
    return manifest, provider, requirements, models


def download():
    # Follow the existing Xinference installer public-download route; change no proxy settings.
    with requests.Session() as session:
        session.trust_env = False
        session.proxies = {"https": "http://127.0.0.1:7890"}
        response = session.get(MARKETPLACE + "/langgenius/siliconflow/versions", timeout=30)
        response.raise_for_status()
        versions = response.json()
        item = next(v for v in versions["data"]["versions"] if v["version"] == VERSION)
        minimum = tuple(item[f"minimum_dify_version_{part}"] for part in ("major", "minor", "patch"))
        if item["unique_identifier"] != IDENTIFIER or item["status"] != "active" or minimum > (1, 8, 0):
            raise RuntimeError("The reviewed release is no longer an active Dify 1.8 candidate")
        FOLDER.mkdir(parents=True, exist_ok=True)
        (FOLDER / "marketplace_versions.json").write_text(json.dumps(versions, ensure_ascii=False, indent=2), encoding="utf-8")
        if not PACKAGE.exists():
            response = session.get(MARKETPLACE + "/download", params={"unique_identifier": IDENTIFIER}, timeout=60)
            response.raise_for_status()
            validate_package(response.content)
            PACKAGE.write_bytes(response.content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--download", action="store_true", help="Fetch only public Marketplace metadata/package")
    group.add_argument("--self-check", action="store_true", help="Offline identity/model checks and tamper rejection")
    args = parser.parse_args()
    if args.download:
        download()
    manifest, provider, requirements, models = validate_package(PACKAGE.read_bytes())
    if args.self_check:
        try:
            validate_package(PACKAGE.read_bytes() + b"tampered")
        except RuntimeError:
            pass
        else:
            raise AssertionError("Tampered archive passed the checksum gate")
    versions = json.loads((FOLDER / "marketplace_versions.json").read_text(encoding="utf-8"))
    item = next(v for v in versions["data"]["versions"] if v["version"] == VERSION)
    report = {
        "status": "prepared_not_installed", "checked_at": datetime.now().astimezone().isoformat(),
        "provider": "langgenius/siliconflow/siliconflow", "version": VERSION,
        "plugin_unique_identifier": IDENTIFIER, "published_at": item["created_at"],
        "package_sha256": PACKAGE_SHA, "package_bytes": PACKAGE.stat().st_size,
        "manifest_schema": manifest["meta"]["version"], "requirements": requirements.splitlines(),
        "marketplace_minimum_dify": ".".join(str(item[f"minimum_dify_version_{part}"]) for part in ("major", "minor", "patch")),
        "source_commit": GITHUB_COMMIT, "source_comparison": "github_source_comparison.json",
        "supported_model_types": provider["supported_model_types"],
        "target_model_ids": {kind: model["model"] for kind, model in models.items()},
        "provider_credential_fields": provider["provider_credential_schema"]["credential_form_schemas"],
        "model_credential_fields": provider["model_credential_schema"]["credential_form_schemas"],
        "package_verification_metadata": "authorized_category=langgenius; daemon signature verification still pending",
        "runtime_compatibility": "candidate only; installation and all three real calls remain unverified",
        "keys_read": False, "dify_contacted": False, "paid_api_called": False,
        "default_models_changed": False, "network_settings_changed": False,
        "self_check_tamper_rejection": args.self_check,
    }
    FOLDER.mkdir(parents=True, exist_ok=True)
    (FOLDER / "package_review.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
