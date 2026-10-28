"""Prepare SiliconFlow 0.0.23 offline; only --install contacts the local Dify VM."""
import argparse
from datetime import datetime
import json
import sys

from install_xinference_provider import check_installed, main as install_provider
from install_xinference_provider import validate_package as validate_install_package
from prepare_siliconflow_provider import (
    FOLDER, IDENTIFIER, PACKAGE, PACKAGE_SHA, PLUGIN_ID, VERSION, validate_package,
)

CONFIG = {
    "plugin_id": PLUGIN_ID, "version": VERSION, "identifier": IDENTIFIER,
    "sha256": PACKAGE_SHA, "package": PACKAGE,
    "report": FOLDER / "p03_siliconflow_provider_installation.json",
    "provider": "langgenius/siliconflow/siliconflow",
    "marketplace_versions": FOLDER / "marketplace_versions.json",
    "required_model_types": ("llm", "text-embedding", "rerank"),
}


def preflight(self_check=False):
    content = PACKAGE.read_bytes()
    manifest, provider, _, _ = validate_package(content)
    validate_install_package(content, CONFIG)
    versions = json.loads(CONFIG["marketplace_versions"].read_text(encoding="utf-8"))
    item = next(v for v in versions["data"]["versions"] if v["version"] == VERSION)
    if item["unique_identifier"] != IDENTIFIER or item["status"] != "active":
        raise RuntimeError("Cached official release identity/status differs from the reviewed package")
    minimum = tuple(item[f"minimum_dify_version_{part}"] for part in ("major", "minor", "patch"))
    if minimum > (1, 8, 0):
        raise RuntimeError("Reviewed package requires a newer Dify version")
    if self_check:
        assert manifest["plugins"]["models"] == ["provider/siliconflow.yaml"]
        for validator in (validate_package, lambda data: validate_install_package(data, CONFIG)):
            try:
                validator(content + b"tampered")
            except RuntimeError:
                pass
            else:
                raise AssertionError("Tampered archive passed integrity verification")
        matching = {"plugin_unique_identifier": IDENTIFIER, "version": VERSION}
        check_installed([matching], CONFIG)
        for installed in ([{**matching, "version": "0.0.63"}],
                          [{**matching, "plugin_unique_identifier": "different"}],
                          [matching, matching]):
            try:
                check_installed(installed, CONFIG)
            except RuntimeError:
                pass
            else:
                raise AssertionError("Conflicting installation passed the no-overwrite guard")
    return provider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--self-check", action="store_true", help="Offline package/tamper/conflict checks")
    group.add_argument("--install", action="store_true", help="Explicitly install the fixed signed package on local Dify")
    args = parser.parse_args()
    provider = preflight(args.self_check)
    report = {
        "status": "offline_preflight_passed" if args.install else "prepared_not_installed",
        "checked_at": datetime.now().astimezone().isoformat(),
        "plugin_unique_identifier": IDENTIFIER, "package_sha256": PACKAGE_SHA,
        "package_bytes": PACKAGE.stat().st_size, "supported_model_types": provider["supported_model_types"],
        "self_check_passed": args.self_check, "remote_install_requested": args.install,
        "preflight_reads_keys": False, "preflight_contacts_dify": False,
        "download_performed": False, "model_or_credential_calls": False,
        "default_model_calls": False, "signature_settings_changed": False,
        "daemon_signature_verification": "pending until explicit --install upload; local metadata is not signature verification",
        "installation_report": str(CONFIG["report"].relative_to(FOLDER)),
    }
    (FOLDER / "installation_entry_review.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.install:
        install_provider(CONFIG)
    else:
        print("Offline preparation complete. Remote installation requires explicit --install.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print(str(error) if isinstance(error, RuntimeError) else type(error).__name__, file=sys.stderr)
        sys.exit(1)
