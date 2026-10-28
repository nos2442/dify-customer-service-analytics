"""P06/P07 local analysis. No cloud model, arbitrary SQL, or mixed sources."""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
from importlib.metadata import version
import json
import logging
import math
from pathlib import Path
import platform
import re

from dotenv import dotenv_values
from gensim.corpora import Dictionary
from gensim.models import LdaModel
import jieba
import pandas as pd
import pymysql
from snownlp import SnowNLP

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = ["id", "time", "records"]
SOURCES = {
    "preset": ("chat", "records", "SELECT id, time, records FROM chat.records ORDER BY id"),
    "self_collected": ("wechat", "new_chathistory", "SELECT id, time, records FROM wechat.new_chathistory ORDER BY id"),
}
CSV_CONTRACT = {
    "聊天密度小时分布.csv": ["hour", "count"],
    "聊天密度月分布.csv": ["month", "count"],
    "聊天词语频次数据.csv": ["word", "count"],
    "月度主题趋势数据.csv": ["month", "topic", "count"],
    "情感分布.csv": ["sentiment", "count"],
}
LDA_PARAMETERS = {"num_topics": 3, "random_state": 42, "passes": 10,
                  "iterations": 100, "update_every": 0, "eval_every": None}
SENTIMENT_LABELS = ["负面", "中性", "正面"]
NEGATIONS = {"不", "没", "无", "未", "非", "别", "不是", "没有", "不能", "无法", "不会", "不要", "不太"}
jieba.setLogLevel(logging.WARNING)


def now():
    return datetime.now(timezone(timedelta(hours=8)))


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_definition(source_mode):
    if source_mode not in SOURCES:
        raise ValueError("Choose preset or self_collected; sources cannot be combined.")
    return SOURCES[source_mode]


def read_source(source_mode="preset"):
    database, _, query = source_definition(source_mode)
    config = dotenv_values(ROOT / ".env", interpolate=False)
    required = ["MYSQL_HOST", "MYSQL_READ_USER", "MYSQL_READ_PASSWORD"]
    if any(not config.get(key) for key in required):
        raise ValueError("Configure MYSQL_HOST, MYSQL_READ_USER and MYSQL_READ_PASSWORD in root .env; writer credentials are not reused.")
    port = int(config.get("MYSQL_PORT") or "3306")
    if not 1 <= port <= 65535:
        raise ValueError("MYSQL_PORT must be between 1 and 65535.")
    connection = pymysql.connect(
        host=config["MYSQL_HOST"], port=port, user=config["MYSQL_READ_USER"],
        password=config["MYSQL_READ_PASSWORD"], database=database,
        charset="utf8mb4", autocommit=True, cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10, read_timeout=120,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
        return pd.DataFrame.from_records(rows, columns=COLUMNS)
    finally:
        connection.close()


def clean_records(raw):
    if not set(COLUMNS).issubset(raw.columns):
        raise ValueError("The source must provide id, time and records.")
    frame = raw[COLUMNS].copy().reset_index(drop=True)
    frame["source_row"] = frame.index + 1
    report = {"raw_rows": len(frame)}
    mask = frame["records"].isna()
    report["excluded_null_text"] = int(mask.sum())
    frame = frame.loc[~mask].copy()
    frame["records"] = frame["records"].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    mask = frame["records"].eq("")
    report["excluded_blank_text"] = int(mask.sum())
    frame = frame.loc[~mask].copy()
    parsed_time = pd.to_datetime(frame["time"], errors="coerce", format="mixed")
    report["excluded_invalid_time"] = int(parsed_time.isna().sum())
    frame["time"] = parsed_time
    frame = frame.loc[parsed_time.notna()].copy().reset_index(drop=True)
    report.update(
        retained_rows=len(frame),
        duplicate_text_rows_kept=int(frame["records"].duplicated().sum()),
        duplicate_id_rows_kept=int(frame["id"].duplicated().sum()),
        missing_id_rows_kept=int(frame["id"].isna().sum()),
        time_min=frame["time"].min().isoformat() if len(frame) else None,
        time_max=frame["time"].max().isoformat() if len(frame) else None,
    )
    report["excluded_rows"] = sum(report[key] for key in ["excluded_null_text", "excluded_blank_text", "excluded_invalid_time"])
    assert report["raw_rows"] == report["retained_rows"] + report["excluded_rows"]
    return frame, report


def load_stopwords(path=None):
    path = Path(path or ROOT / "analysis" / "stopwords.txt")
    words = {line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()
             if line.strip() and not line.lstrip().startswith("#")}
    if words & NEGATIONS:
        raise ValueError("Stopwords must preserve negations.")
    return words


def tokenize_records(frame, stopwords=None):
    stopwords = load_stopwords() if stopwords is None else set(stopwords)
    if stopwords & NEGATIONS:
        raise ValueError("Stopwords must preserve negations.")
    result = frame.copy()
    result["tokens"] = result["records"].map(
        lambda text: [word for token in jieba.cut(text, HMM=False)
                      if (word := token.strip().lower()) not in stopwords
                      and any(character.isalpha() for character in word)]
    )
    result["processed_text"] = result["tokens"].map(" ".join)
    return result, {"rows": len(result), "empty_token_rows": int(result["tokens"].map(len).eq(0).sum()),
                    "token_count": int(result["tokens"].map(len).sum())}


def fit_topics(frame):
    result = frame.copy()
    dictionary = Dictionary(result["tokens"])
    corpus = [dictionary.doc2bow(tokens) for tokens in result["tokens"]]
    usable = [document for document in corpus if document]
    if not usable:
        raise ValueError("No usable LDA tokens; no topic model was trained.")
    model = LdaModel(corpus=usable, id2word=dictionary, **LDA_PARAMETERS)
    result["topic"] = pd.array([
        max(model.get_document_topics(document, minimum_probability=0), key=lambda item: (item[1], -item[0]))[0]
        if document else pd.NA for document in corpus
    ], dtype="Int64")
    result["topic_status"] = result["topic"].notna().map({True: "assigned", False: "empty_tokens"})
    report = {"assigned_rows": int(result["topic"].notna().sum()), "unassigned_rows": int(result["topic"].isna().sum()),
              "dictionary_size": len(dictionary), "parameters": dict(LDA_PARAMETERS),
              "keywords": {str(topic): [word for word, _ in model.show_topic(topic, topn=10)] for topic in range(3)}}
    return result, model, dictionary, report


def classify_sentiment(score):
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError("SnowNLP score must be finite and between 0 and 1.")
    return "负面" if score < 0.4 else "正面" if score > 0.6 else "中性"


def score_sentiments(frame):
    result = frame.copy()
    scores, labels, failures = [], [], Counter()
    for text in result["records"]:
        try:
            score = float(SnowNLP(text).sentiments)
            label = classify_sentiment(score)
        except Exception as error:
            score, label = float("nan"), pd.NA
            failures[type(error).__name__] += 1
        scores.append(score)
        labels.append(label)
    result["sentiment_score"] = scores
    result["sentiment"] = labels
    result["sentiment_status"] = result["sentiment"].notna().map({True: "scored", False: "failed"})
    return result, {"scored_rows": int(result["sentiment"].notna().sum()),
                    "failed_rows": int(result["sentiment"].isna().sum()), "failure_types": dict(failures),
                    "negative_below": 0.4, "positive_above": 0.6,
                    "threshold_origin": "Project provisional choice, not supplied by course; 0.4 and 0.6 are neutral."}


def build_tables(frame):
    if frame.empty:
        raise ValueError("No cleaned records; do not publish empty analysis as complete.")
    months = pd.period_range(frame["time"].min(), frame["time"].max(), freq="M").astype(str)
    month_key = frame["time"].dt.strftime("%Y-%m")
    hours = frame.groupby(frame["time"].dt.hour).size().reindex(range(24), fill_value=0)
    monthly = frame.groupby(month_key).size().reindex(months, fill_value=0)
    frequencies = Counter(word for tokens in frame["tokens"] for word in tokens)
    words = sorted(frequencies.items(), key=lambda item: (-item[1], item[0]))[:100]
    index = pd.MultiIndex.from_product([months, range(3)], names=["month", "topic"])
    topics = frame.groupby([month_key.rename("month"), frame["topic"]]).size().reindex(index, fill_value=0)
    sentiments = frame["sentiment"].value_counts().reindex(SENTIMENT_LABELS, fill_value=0)
    tables = {
        "聊天密度小时分布.csv": hours.rename_axis("hour").reset_index(name="count"),
        "聊天密度月分布.csv": monthly.rename_axis("month").reset_index(name="count"),
        "聊天词语频次数据.csv": pd.DataFrame(words, columns=["word", "count"]),
        "月度主题趋势数据.csv": topics.reset_index(name="count"),
        "情感分布.csv": sentiments.rename_axis("sentiment").reset_index(name="count"),
    }
    assert int(hours.sum()) == int(monthly.sum()) == len(frame)
    assert int(topics.sum()) == int(frame["topic"].notna().sum())
    assert int(sentiments.sum()) == int(frame["sentiment"].notna().sum())
    for name, table in tables.items():
        assert list(table.columns) == CSV_CONTRACT[name]
    return tables


def create_batch(raw, source_mode, batch_id=None):
    source_definition(source_mode)
    batch_id = batch_id or now().strftime("%Y%m%dT%H%M%S%f")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", batch_id):
        raise ValueError("batch_id must use 1-64 ASCII letters, digits, underscore or hyphen.")
    name = f"{source_mode}_{batch_id}"
    output_dir = ROOT / "analysis" / "output" / name
    private_dir = ROOT / "data" / "private" / "analysis" / name
    if output_dir.exists() or private_dir.exists():
        raise FileExistsError("Batch already exists; choose a new batch_id.")
    output_dir.mkdir(parents=True)
    private_dir.mkdir(parents=True)
    snapshot = private_dir / "source_snapshot.jsonl"
    raw[COLUMNS].to_json(snapshot, orient="records", lines=True, force_ascii=False, date_format="iso")
    return {"source_mode": source_mode, "batch_id": batch_id, "started_at": now().isoformat(),
            "output_dir": output_dir, "private_dir": private_dir,
            "snapshot": {"path": str(snapshot.relative_to(ROOT)), "sha256": sha256(snapshot), "rows": len(raw)}}


def export_csvs(tables, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for name, table in tables.items():
        if list(table.columns) != CSV_CONTRACT[name]:
            raise ValueError(f"CSV contract mismatch: {name}")
        path = output_dir / name
        table.to_csv(path, index=False, encoding="utf-8-sig", lineterminator="\n")
        artifacts[name] = {"rows": len(table), "columns": list(table.columns), "sha256": sha256(path)}
    return artifacts


def finish_batch(batch, analysed, model, dictionary, reports):
    if any(batch["output_dir"].iterdir()):
        raise FileExistsError("Batch output already contains files; create a new batch before exporting again.")
    tables = build_tables(analysed)
    artifacts = export_csvs(tables, batch["output_dir"])
    models = batch["output_dir"] / "models"
    models.mkdir()
    model.save(str(models / "lda.model"))
    dictionary.save(str(models / "dictionary.gensim"))
    analysed.to_json(batch["private_dir"] / "record_results.jsonl", orient="records", lines=True,
                     force_ascii=False, date_format="iso")
    database, table, _ = source_definition(batch["source_mode"])
    manifest = {
        "state": "generated_with_scoring_failures_pending_review" if reports["sentiment"]["failed_rows"] else "generated_pending_manual_review",
        "source_mode": batch["source_mode"], "batch_id": batch["batch_id"],
        "started_at": batch["started_at"], "finished_at": now().isoformat(),
        "source": {"database": database, "table": table, "columns": COLUMNS, "query_type": "single fixed SELECT snapshot"},
        "snapshot": batch["snapshot"], "reports": reports,
        "preprocessing": {"rule_version": "1", "jieba_HMM": False,
                          "token_policy": "lowercase; remove stopwords and tokens without alphabetic characters; preserve negation; do not delete duplicate rows",
                          "stopwords_sha256": sha256(ROOT / "analysis" / "stopwords.txt")},
        "csv_contract": {"origin": "Project provisional supplement; course headers and dashboard mapping not yet verified",
                         "encoding": "utf-8-sig", "files": CSV_CONTRACT},
        "artifacts": artifacts,
        "model_artifacts": {path.name: sha256(path) for path in models.iterdir() if path.is_file()},
        "implementation_sha256": sha256(__file__),
        "versions": {"python": platform.python_version(), **{package: version(package) for package in
                    ["numpy", "pandas", "scipy", "gensim", "jieba", "snownlp", "PyMySQL", "python-dotenv"]}},
        "pending_acceptance": ["T11 model-assisted rule review", "topic naming and K review", "sentiment manual review",
                               "course CSV field mapping", "actual dashboard import and CP5 verification"],
    }
    (batch["output_dir"] / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return tables, manifest


def run_analysis(source_mode="preset", batch_id=None):
    raw = read_source(source_mode)
    batch = create_batch(raw, source_mode, batch_id)
    reports = {}
    try:
        cleaned, reports["cleaning"] = clean_records(raw)
        tokenized, reports["tokens"] = tokenize_records(cleaned)
        topics, model, dictionary, reports["topics"] = fit_topics(tokenized)
        analysed, reports["sentiment"] = score_sentiments(topics)
        _, manifest = finish_batch(batch, analysed, model, dictionary, reports)
        return batch, manifest
    except Exception as error:
        failure = {"state": "failed", "source_mode": source_mode, "batch_id": batch["batch_id"],
                   "error_type": type(error).__name__, "reports": reports, "snapshot": batch["snapshot"]}
        (batch["output_dir"] / "manifest.json").write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local course analysis; root .env must contain read-only MySQL credentials.")
    parser.add_argument("--source-mode", choices=list(SOURCES), default="preset")
    parser.add_argument("--batch-id")
    arguments = parser.parse_args()
    batch, manifest = run_analysis(arguments.source_mode, arguments.batch_id)
    print(json.dumps({"state": manifest["state"], "source_mode": batch["source_mode"], "batch_id": batch["batch_id"],
                      "retained_rows": manifest["reports"]["cleaning"]["retained_rows"],
                      "output_directory": str(batch["output_dir"])}, ensure_ascii=False))
