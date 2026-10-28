"""Synthetic small-sample tests only: no real database or external model calls."""

from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from analysis import run_analysis as analysis


def sample_frame():
    # Constructed test sentences, not course chat records or operational results.
    return pd.DataFrame([
        [1, "2026-01-01 08:00:00", " 热水器  漏水，不能正常使用。 "],
        [2, "2026-01-02 09:00:00", None],
        [3, "2026-01-03 10:00:00", "  \t\n "],
        [4, "invalid-date", "水温忽冷忽热。"],
        [5, "2026-03-01 08:00:00", "安装师傅服务很好，谢谢。"],
        [6, "2026-03-02 15:00:00", "安装师傅服务很好，谢谢。"],
        [7, "2026-03-03 16:00:00", "保修期内如何申请维修？"],
        [8, "2026-03-04 18:00:00", "!!! 123"],
    ], columns=analysis.COLUMNS)


class AnalysisTests(unittest.TestCase):
    def test_cleaning_counts_and_duplicates_are_preserved(self):
        cleaned, report = analysis.clean_records(sample_frame())
        self.assertEqual(report["raw_rows"], 8)
        self.assertEqual([report[key] for key in ["excluded_null_text", "excluded_blank_text", "excluded_invalid_time"]], [1, 1, 1])
        self.assertEqual(report["retained_rows"], 5)
        self.assertEqual(report["duplicate_text_rows_kept"], 1)
        self.assertEqual(cleaned["source_row"].tolist(), [1, 5, 6, 7, 8])
        self.assertEqual(cleaned.iloc[0]["records"], "热水器 漏水，不能正常使用。")
        empty, empty_report = analysis.clean_records(sample_frame().iloc[0:0])
        self.assertTrue(empty.empty)
        self.assertEqual(empty_report["retained_rows"], 0)

    def test_sentiment_boundaries_and_invalid_scores(self):
        expected = [(0, "负面"), (0.3999, "负面"), (0.4, "中性"), (0.5, "中性"), (0.6, "中性"), (0.6001, "正面"), (1, "正面")]
        for score, label in expected:
            self.assertEqual(analysis.classify_sentiment(score), label)
        for score in [float("nan"), float("inf"), -0.01, 1.01]:
            with self.assertRaises(ValueError):
                analysis.classify_sentiment(score)

    def test_fixed_read_sources_and_separate_credentials(self):
        fake = {"MYSQL_HOST": "synthetic.invalid", "MYSQL_PORT": "3306",
                "MYSQL_READ_USER": "synthetic_reader", "MYSQL_READ_PASSWORD": "test-only",
                "MYSQL_USER": "must-not-use-writer", "MYSQL_PASSWORD": "must-not-use-writer"}
        for mode in ["preset", "self_collected"]:
            with patch.object(analysis, "dotenv_values", return_value=fake), patch.object(analysis.pymysql, "connect") as connect:
                cursor = connect.return_value.cursor.return_value.__enter__.return_value
                cursor.fetchall.return_value = []
                frame = analysis.read_source(mode)
                cursor.execute.assert_called_once_with(analysis.SOURCES[mode][2])
                self.assertEqual(connect.call_args.kwargs["user"], "synthetic_reader")
                self.assertEqual(connect.call_args.kwargs["database"], analysis.SOURCES[mode][0])
                self.assertEqual(list(frame.columns), analysis.COLUMNS)
                connect.return_value.close.assert_called_once()
        with patch.object(analysis.pymysql, "connect") as connect:
            with self.assertRaises(ValueError):
                analysis.read_source("preset; DROP TABLE records")
            with patch.object(analysis, "dotenv_values", return_value={"MYSQL_USER": "writer"}), self.assertRaises(ValueError):
                analysis.read_source("preset")
            connect.assert_not_called()

    def test_negations_and_no_usable_corpus(self):
        cleaned, _ = analysis.clean_records(sample_frame())
        with self.assertRaises(ValueError):
            analysis.tokenize_records(cleaned, {"不"})
        tokenized, _ = analysis.tokenize_records(cleaned)
        self.assertIn("不能", tokenized.iloc[0]["tokens"])
        self.assertEqual(tokenized.iloc[-1]["tokens"], [])
        with self.assertRaises(ValueError):
            analysis.fit_topics(tokenized.iloc[-1:].copy())

    def test_gensim_snownlp_and_five_csv_contracts(self):
        cleaned, _ = analysis.clean_records(sample_frame())
        tokenized, _ = analysis.tokenize_records(cleaned)
        topics, model, dictionary, topic_report = analysis.fit_topics(tokenized)
        self.assertEqual(model.num_topics, 3)
        self.assertTrue(type(model).__module__.startswith("gensim."))
        self.assertGreater(len(dictionary), 0)
        self.assertEqual(topic_report["assigned_rows"], 4)
        self.assertTrue(pd.isna(topics.iloc[-1]["topic"]))
        analysed, sentiment_report = analysis.score_sentiments(topics)
        self.assertEqual(sentiment_report["scored_rows"] + sentiment_report["failed_rows"], 5)
        self.assertEqual(sentiment_report["failed_rows"], 0)
        self.assertTrue(analysed["sentiment_score"].between(0, 1).all())
        tables = analysis.build_tables(analysed)
        self.assertEqual(tables["聊天密度月分布.csv"]["count"].tolist(), [1, 0, 4])
        self.assertEqual(int(tables["月度主题趋势数据.csv"]["count"].sum()), 4)
        self.assertEqual(int(tables["情感分布.csv"]["count"].sum()), 5)
        self.assertEqual(tables["聊天密度小时分布.csv"]["hour"].tolist(), list(range(24)))
        with tempfile.TemporaryDirectory(prefix="dify_synthetic_analysis_") as folder:
            artifacts = analysis.export_csvs(tables, folder)
            self.assertEqual(set(artifacts), set(analysis.CSV_CONTRACT))
            for name, headers in analysis.CSV_CONTRACT.items():
                path = Path(folder) / name
                self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
                loaded = pd.read_csv(path, encoding="utf-8-sig")
                self.assertEqual(list(loaded.columns), headers)
                self.assertEqual(len(loaded), len(tables[name]))
            model.save(str(Path(folder) / "synthetic_lda.model"))
            reloaded = analysis.LdaModel.load(str(Path(folder) / "synthetic_lda.model"))
            self.assertEqual(reloaded.num_topics, 3)

    def test_sentiment_failure_is_not_forced_to_neutral(self):
        cleaned, _ = analysis.clean_records(sample_frame().iloc[:1])
        with patch.object(analysis, "SnowNLP", side_effect=RuntimeError("synthetic failure")):
            result, report = analysis.score_sentiments(cleaned)
        self.assertEqual(report["failed_rows"], 1)
        self.assertEqual(report["scored_rows"], 0)
        self.assertTrue(pd.isna(result.iloc[0]["sentiment"]))
        self.assertEqual(result.iloc[0]["sentiment_status"], "failed")

    def test_synthetic_batch_manifest_and_private_snapshot(self):
        stopwords = (analysis.ROOT / "analysis" / "stopwords.txt").read_bytes()
        with tempfile.TemporaryDirectory(prefix="dify_synthetic_batch_") as folder:
            root = Path(folder)
            (root / "analysis").mkdir()
            (root / "analysis" / "stopwords.txt").write_bytes(stopwords)
            with patch.object(analysis, "ROOT", root), patch.object(analysis, "read_source", return_value=sample_frame()):
                batch, manifest = analysis.run_analysis("preset", "synthetic_test_only")
                self.assertEqual(manifest["state"], "generated_pending_manual_review")
                self.assertEqual(manifest["source"]["table"], "records")
                self.assertEqual(manifest["snapshot"]["rows"], 8)
                self.assertEqual(manifest["reports"]["cleaning"]["retained_rows"], 5)
                self.assertIn("gensim", manifest["versions"])
                self.assertTrue((batch["private_dir"] / "source_snapshot.jsonl").exists())
                self.assertTrue((batch["private_dir"] / "record_results.jsonl").exists())
                text = (batch["output_dir"] / "manifest.json").read_text(encoding="utf-8")
                self.assertNotIn("保修期内如何申请维修", text)
                self.assertEqual(json.loads(text)["batch_id"], "synthetic_test_only")
                with self.assertRaises(FileExistsError):
                    analysis.finish_batch(batch, None, None, None, {})
                self.assertEqual((batch["output_dir"] / "manifest.json").read_text(encoding="utf-8"), text)
                with self.assertRaises(FileExistsError):
                    analysis.create_batch(sample_frame(), "preset", "synthetic_test_only")
                with self.assertRaises(ValueError):
                    analysis.create_batch(sample_frame(), "preset", "../invalid")


if __name__ == "__main__":
    unittest.main()
