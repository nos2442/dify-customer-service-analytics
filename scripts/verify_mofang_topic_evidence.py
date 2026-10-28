"""Check saved topic-grouping evidence against its CSV; no network or secrets."""
import csv
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
report = json.loads((ROOT / "evidence/20261023/mofang/p07_topic_grouping.json").read_text(encoding="utf-8"))
source = ROOT / report["csv"]["path"]
with source.open(encoding="utf-8-sig", newline="") as stream:
    expected = [[row["month"], int(row["topic"]), int(row["count"])] for row in csv.DictReader(stream)]
actual = report["after"]["month_topic_count_rows"]
assert len(actual) == 54 and sorted(actual) == sorted(expected)
assert len({(row[0], row[1]) for row in actual}) == 54
assert sha256(source.read_bytes()).hexdigest() == report["csv"]["sha256"]
months = sorted({row[0] for row in expected})
assert len(months) == 18 and report["after"]["months"] == months
assert report["after"]["topic_group_type"] == "d"
assert report["after"]["topic_numeric_type"] == "LONG"
assert report["after"]["topic_sort"] == report["after"]["month_sort"] == "asc"
assert report["after"]["count_aggregation"] == "sum"
assert report["after"]["chart_type"] == "line-stack" and report["after"]["line_area"] is True
series = report["after"]["series"]
assert [item["name"] for item in series] == ["0", "1", "2"]
for item, total in zip(series, [18274, 35426, 27936]):
    expected_values = [next(row[2] for row in expected if row[:2] == [month, int(item["name"])]) for month in months]
    assert item["values"] == expected_values and item["points"] == 18
    assert sum(item["values"]) == item["total"] == total
assert sum(row[2] for row in actual) == report["checks"]["total"] == 81636
for key in ("topic_field_save_success", "topic_field_role_readback", "view_save_success",
            "uncached_saved_chart_readback_matches", "panel_layout_unchanged"):
    assert report["checks"][key] is True
assert report["panel_data_sha256"]["before"] == report["panel_data_sha256"]["after"]
assert len(report["other_views"]) == 4
assert all(view["before_config_sha256"] == view["after_config_sha256"] for view in report["other_views"])
print("Saved topic evidence verified: 18 months, 3 series, 54 exact counts; no live page check.")
