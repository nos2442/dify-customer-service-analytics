"""Backfill the three T07 documents from captured Firecrawl Markdown, preserving IDs."""
import argparse
from datetime import datetime
from hashlib import sha256
import json
import sys
import time

from dotenv import dotenv_values
import requests

from configure_dify import make_api
from verify_dify_workflow import safe_error
from verify_web_crawler_workflow import ROOT, FOLDER, STATE, NAME, SINGLE, MULTI, FULL_TEXT_CODE, check_fulltext_readback

REPORT = FOLDER / "fulltext_repair_report.json"


def main(stage):
    env = dotenv_values(ROOT / ".env", interpolate=False)
    state = json.loads(STATE.read_text(encoding="utf-8"))
    report = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {
        "status": "prepared", "repair_kind": "Pipeline loss repair from captured source, not a remote webpage change",
        "documents": {}, "new_documents_created": 0, "firecrawl_requests_made": 0,
        "automatic_idempotency_verified": False, "remote_source_change_verified": False,
        "new_fulltext_draft_run_verified": False, "secrets_logged": False}
    def save():
        report["updated_at"] = datetime.now().astimezone().isoformat()
        text = json.dumps(report, ensure_ascii=False, indent=2)
        for key, value in env.items():
            if value and any(word in key for word in ("KEY", "TOKEN", "PASSWORD", "SECRET")):
                text = text.replace(value, "[REDACTED]")
        REPORT.write_text(text, encoding="utf-8")
    try:
        with requests.Session() as session:
            session.trust_env = False
            if env.get("DIFY_BASE_URL") not in {"http://192.168.18.176", "http://192.168.110.108"}:
                raise RuntimeError("Dify address differs from the reviewed VM")
            # One bounded connectivity attempt; never loop while the user changes networks.
            response = session.get(env["DIFY_BASE_URL"] + "/console/api/setup", timeout=(3, 5))
            if response.status_code != 200:
                raise RuntimeError("Dify readiness probe is not HTTP200")
            console = make_api(env["DIFY_BASE_URL"], session)
            console.token = console("POST", "/login", {"email": env["DIFY_ADMIN_EMAIL"], "password": env["DIFY_ADMIN_PASSWORD"]})["data"]["access_token"]
            def dataset(method, path, payload=None):
                result = session.request(method, env["DIFY_BASE_URL"] + "/v1" + path,
                    headers={"Authorization": "Bearer " + env["DIFY_DATASET_API_KEY"]}, json=payload,
                    timeout=(5, 90), allow_redirects=False)
                if result.status_code != 200:
                    raise RuntimeError(f"Dataset {method} {path}: HTTP {result.status_code}")
                return result.json()
            if dataset("GET", "/datasets/" + state["dataset_id"])["name"] != NAME:
                raise RuntimeError("Dedicated test dataset identity differs")
            if stage == "cache":
                cache = {}
                for run in state["runs"]:
                    if not run.get("workflow_run_id"):
                        continue
                    nodes = sorted(console("GET", f"/apps/{state['app_id']}/workflow-runs/{run['workflow_run_id']}/node-executions")["data"], key=lambda node: node["index"])
                    scrapes = [node for node in nodes if node["node_id"] == "firecrawl" and node.get("status") == "succeeded"]
                    serialized = [node for node in nodes if node["node_id"] == "serialize" and node.get("status") == "succeeded"]
                    if len(scrapes) != len(serialized):
                        raise RuntimeError("Captured scrape-to-source mapping is incomplete")
                    for scraped, encoded in zip(scrapes, serialized):
                        old_payload = json.loads(encoded["outputs"]["request_body"])
                        source = old_payload["text"].split("\n", 1)[0].removeprefix("来源URL：")
                        outputs = scraped["outputs"]
                        body = json.loads(outputs["body"])
                        markdown = body.get("data", {}).get("markdown")
                        if source not in SINGLE + MULTI or outputs.get("status_code") != 200 or body.get("success") is not True or not markdown:
                            raise RuntimeError("Cached public scrape lacks approved source/success/Markdown")
                        cache[source] = {"scrape_status": 200, "scrape_body": outputs["body"], "workflow_run_id": run["workflow_run_id"]}
                        report.setdefault("captured_markdown", {})[source] = {"chars": len(markdown), "sha256": sha256(markdown.encode()).hexdigest()}
                if set(cache) != set(SINGLE + MULTI):
                    raise RuntimeError("Not all three full scrape bodies are available")
                text = json.dumps(cache, ensure_ascii=False, indent=2)
                if any(value in text for key, value in env.items() if value and any(word in key for word in ("KEY", "TOKEN", "PASSWORD", "SECRET"))):
                    raise RuntimeError("Secret detected in captured response; cache was not saved")
                (FOLDER / "captured_public_scrapes.json").write_text(text, encoding="utf-8")
                report["status"] = "cached_full_markdown_pending_updates"
            elif stage in {"first", "remaining"}:
                cache = json.loads((FOLDER / "captured_public_scrapes.json").read_text(encoding="utf-8"))
                if stage == "remaining" and not report["documents"].get(SINGLE[0], {}).get("fulltext_verified"):
                    raise RuntimeError("Verify the first full document before updating the other two")
                scope = {}
                exec(compile(FULL_TEXT_CODE, "reviewed_fulltext_serializer", "exec"), scope)
                for source in SINGLE if stage == "first" else MULTI:
                    old = state["sources"][source]
                    capture = cache[source]
                    payload = json.loads(scope["main"](old["title"], source, capture["scrape_status"], capture["scrape_body"])["request_body"])
                    markdown = json.loads(capture["scrape_body"])["data"]["markdown"]
                    if payload["text"] != "来源URL：" + source + "\n\n" + markdown:
                        raise RuntimeError("Submitted text differs from the full cached Markdown")
                    item = report["documents"].get(source)
                    if item is None:
                        tick = time.perf_counter()
                        result = dataset("POST", f"/datasets/{state['dataset_id']}/documents/{old['document_id']}/update-by-text", payload)
                        if result.get("document", {}).get("id") != old["document_id"] or not result.get("batch"):
                            raise RuntimeError("Update did not preserve document ID or return batch")
                        item = {"document_id": old["document_id"], "source_url": source, "batch": result["batch"],
                                "update_http_status": 200, "update_elapsed_seconds": round(time.perf_counter() - tick, 3),
                                "old_text_chars": old["text_chars"], "full_text_chars": len(payload["text"]),
                                "full_text_sha256": sha256(payload["text"].encode()).hexdigest(), "fulltext_verified": False}
                        report["documents"][source] = item
                        save()
                    item.update(cached_markdown_chars=len(markdown), transmitted_markdown_chars=len(markdown),
                                transmitted_markdown_sha256=sha256(markdown.encode()).hexdigest(),
                                markdown_matches_cached_response_exactly=True)
                    deadline = time.monotonic() + 180
                    while True:
                        result = dataset("GET", f"/datasets/{state['dataset_id']}/documents/{item['batch']}/indexing-status")
                        item["indexing_statuses"] = [doc.get("indexing_status") for doc in result["data"]]
                        if item["indexing_statuses"] and all(status == "completed" for status in item["indexing_statuses"]): break
                        save()
                        if any(status in {"error", "paused"} for status in item["indexing_statuses"]) or time.monotonic() >= deadline:
                            raise RuntimeError("Existing document indexing failed/pending; do not create or update it again")
                        time.sleep(3)
                    segments, page = [], 1
                    while True:
                        result = dataset("GET", f"/datasets/{state['dataset_id']}/documents/{item['document_id']}/segments?limit=100&page={page}")
                        segments.extend(result["data"])
                        if not result.get("has_more"): break
                        page += 1
                    item.update(check_fulltext_readback(payload["text"], segments))
                    item.update(segment_count=len(segments), source_url_present=any(source in segment["content"] for segment in segments))
                    if not item["readback_body_matches"] or not item["source_url_present"]:
                        save()
                        raise RuntimeError("Full text readback coverage/source is not 100%; preserve existing document for diagnosis")
                    item["fulltext_verified"] = True
                    print(f"Full Markdown verified: {source} ({item['full_text_chars']} chars, {len(segments)} chunks)", flush=True)
                    save()
                report["status"] = "first_fulltext_verified" if stage == "first" else "all_fulltext_verified"
            elif stage == "final":
                if not all(report["documents"].get(source, {}).get("fulltext_verified") for source in SINGLE + MULTI):
                    raise RuntimeError("All full-text backfills must pass before final checks")
                docs = dataset("GET", f"/datasets/{state['dataset_id']}/documents?limit=100")["data"]
                expected_ids = {entry["document_id"] for entry in state["sources"].values()}
                if {doc["id"] for doc in docs} != expected_ids or len(docs) != 3:
                    raise RuntimeError("Dedicated library document IDs/count changed")
                retrieval = dataset("POST", f"/datasets/{state['dataset_id']}/retrieve", {"query": "remaining credits authenticated team",
                    "retrieval_model": {"search_method": "semantic_search", "reranking_enable": False, "top_k": 5, "score_threshold_enabled": False}})
                records = retrieval.get("records", [])
                report["retrieval"] = [{"document_id": record["segment"]["document_id"], "score": record.get("score"),
                                         "content_chars": len(record["segment"].get("content", ""))} for record in records]
                if not any(record["document_id"] == state["sources"][SINGLE[0]]["document_id"] for record in report["retrieval"]):
                    raise RuntimeError("Retrieval did not return the expected credit-usage source")
                report.update(status="passed_manual_fulltext_backfill", document_count=3, document_ids_preserved=True,
                              retrieval_expected_source_found=True, workflow_tasks_terminal=all(run.get("workflow_terminal", True) for run in state["runs"]))
            report.pop("safe_error", None)
    except Exception as error:
        report.update(status="incomplete", safe_error=safe_error(error, env), interrupted_stage=stage)
        save()
        raise RuntimeError(report["safe_error"]) from None
    save()
    print(json.dumps({"status": report["status"], "fulltext_documents_verified": sum(item.get("fulltext_verified", False) for item in report["documents"].values()),
                     "new_documents_created": 0, "firecrawl_requests_made": 0}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("cache", "first", "remaining", "final"))
    args = parser.parse_args()
    try:
        main(args.stage)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
