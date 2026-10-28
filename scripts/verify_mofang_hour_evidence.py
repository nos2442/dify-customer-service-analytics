"""Check saved API evidence against the original CSV; no credentials or network."""
import csv
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
report = json.loads((ROOT / "evidence/20261023/mofang/p07_hour_repair.json").read_text(encoding="utf-8"))
source = ROOT / report["csv"]["path"]
with source.open(encoding="utf-8-sig", newline="") as stream:
    expected = [[int(row["hour"]), int(row["count"])] for row in csv.DictReader(stream)]
actual = report["after"]["hour_count_pairs"]
assert actual == expected and [row[0] for row in actual] == list(range(24))
assert sha256(source.read_bytes()).hexdigest() == report["csv"]["sha256"]
assert sum(row[1] for row in actual) == 81636
assert max(actual, key=lambda row: row[1]) == [10, 5943]
assert min(actual, key=lambda row: row[1]) == [1, 401]
assert report["after"]["hour_group_type"] == "d"
assert report["after"]["hour_numeric_type"] == "LONG"
assert report["after"]["hour_sort"] == "asc"
assert report["after"]["count_group_type"] == "q"
assert report["after"]["count_aggregation"] == "sum"
for key in ("field_role_save_success", "view_save_success", "panel_save_success",
            "panel_reload_matches", "uncached_chart_reload_matches"):
    assert report["checks"][key] is True
assert len(report["other_views"]) == 4
assert all(view["before_config_sha256"] == view["after_config_sha256"] for view in report["other_views"])
boxes = report["layout"]["rectangles"]
width, height = report["layout"]["canvas"]
assert len(boxes) == 5
for index, box in enumerate(boxes):
    assert 0 <= box["left"] and box["left"] + box["width"] <= width
    assert 0 <= box["top"] and box["top"] + box["height"] <= height
    for other in boxes[index + 1:]:
        assert (box["left"] + box["width"] <= other["left"]
                or other["left"] + other["width"] <= box["left"]
                or box["top"] + box["height"] <= other["top"]
                or other["top"] + other["height"] <= box["top"])
print("Saved API evidence verified: 24 hourly counts, numeric order, SUM and layout; no live page check.")
