"""Check the five saved chart datasets, static area geometry and visible layout."""
import csv
from hashlib import sha256
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
report = json.loads((ROOT / "evidence/20261023/mofang/p07_five_chart_acceptance.json").read_text(encoding="utf-8"))
for kind, expected_size in {"month": 18, "hour": 24, "word": 100, "topic": 54, "sentiment": 3}.items():
    chart = report["charts"][kind]
    source = ROOT / chart["csv"]
    with source.open(encoding="utf-8-sig", newline="") as stream:
        expected = list(csv.DictReader(stream))
    for row in expected:
        for key in ("hour", "topic", "count"):
            if key in row:
                row[key] = int(row[key])
    keys = list(expected[0])
    assert chart["rows"] == expected_size
    assert sorted(tuple(row[k] for k in keys) for row in chart["actual_rows"]) == sorted(
        tuple(row[k] for k in keys) for row in expected)
    assert sha256(source.read_bytes()).hexdigest() == chart["csv_sha256"]
    assert chart["sum_count"] == (615128 if kind == "word" else 81636)
    assert chart["bindings_unchanged"] and chart["remark_show"] and chart["remark"]
assert report["panel_save_success"] and report["panel_reload_matches"]
assert report["original_native_view_count"] == report["visible_tile_count"] == 5
assert report["original_native_view_bindings_retained"]
geometry = json.loads((ROOT / report["sentiment_display"]["geometry_file"]).read_text(encoding="utf-8"))
svg = ROOT / report["sentiment_display"]["source_svg"]
assert sha256(svg.read_bytes()).hexdigest() == geometry["svg_sha256"] == report["sentiment_display"]["svg_sha256"]
assert geometry["counts"] == [74264, 2837, 4535] and geometry["total"] == 81636
assert 0 < geometry["equal_angle_radians"] < 2 * math.pi / 3
assert all(math.isclose(radius ** 2 / count, 1 / 74264)
           for radius, count in zip(geometry["radii"], geometry["counts"]))
assert report["sentiment_display"]["native_sentiment_view_preserved_hidden"]
assert report["sentiment_display"]["interactive"] is False
chart_tiles = report["visible_layout"]["tiles"]
footer_tiles = report["visible_layout"]["footer_tiles"]
assert len(chart_tiles) == 5 and len(footer_tiles) == 2
assert report["permanent_footer"]["save_success"] and report["permanent_footer"]["reload_matches"]
assert "preset_deploy_20261020" in footer_tiles[0]["text"]
assert "8月未满自然月" in footer_tiles[1]["text"]
tiles = chart_tiles + footer_tiles
for index, tile in enumerate(tiles):
    box = tile["style"]
    assert 0 <= box["left"] and box["left"] + box["width"] <= 1600
    assert 0 <= box["top"] and box["top"] + box["height"] <= 900
    for other_tile in tiles[index + 1:]:
        other = other_tile["style"]
        assert (box["left"] + box["width"] <= other["left"]
                or other["left"] + other["width"] <= box["left"]
                or box["top"] + box["height"] <= other["top"]
                or other["top"] + other["height"] <= box["top"])
print("Five saved datasets, notes, equal-angle area geometry and visible layout verified; no live UI check.")
