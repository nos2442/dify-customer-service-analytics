"""Render the initial equal-angle, count-proportional-area chart for a 500x440 tile."""
import argparse
import csv
from hashlib import sha256
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--batch-dir', type=Path, default=ROOT / 'analysis/output/preset_deploy_20261020')
parser.add_argument('--output-dir', type=Path, default=ROOT / 'artifacts/mofang')
parser.add_argument('--preview', type=Path, default=ROOT / 'evidence/20261023/mofang/sentiment_area_preview.png')
args = parser.parse_args()
args.batch_dir = args.batch_dir.resolve()
args.output_dir = args.output_dir.resolve()
args.preview = args.preview.resolve()
source = args.batch_dir / '情感分布.csv'
manifest = json.loads((args.batch_dir / 'manifest.json').read_text(encoding='utf-8'))
with source.open(encoding="utf-8-sig", newline="") as stream:
    counts = {row["sentiment"]: int(row["count"]) for row in csv.DictReader(stream)}
assert set(counts) == {'负面', '中性', '正面'} and all(v >= 0 for v in counts.values()) and sum(counts.values()) > 0
labels = ["负面", "中性", "正面"]
colors = ["#c6757e", "#99aab4", "#52a8a5"]
total = sum(counts.values())
maximum = max(counts.values())
radii = [math.sqrt(counts[label] / maximum) for label in labels]
width = 2 * math.pi / 3 * 0.94
assert all(math.isclose(radius ** 2 / counts[label], 1 / maximum)
           for label, radius in zip(labels, radii))
plt.rcParams.update({"font.family": "Microsoft YaHei", "svg.fonttype": "none"})
figure = plt.figure(figsize=(5, 4.4), facecolor="white")
figure.text(0.04, 0.945, "情感标签分布（SnowNLP）", fontsize=12, weight="bold", color="#193b4d")
figure.text(0.04, 0.895, "等角扇区；面积对应条数，半径为条数的平方根", fontsize=7.5, color="#65747d")
axes = figure.add_axes([0.04, 0.25, 0.51, 0.57], projection="polar")
axes.set_theta_zero_location("N")
axes.set_theta_direction(-1)
axes.bar([0, 2 * math.pi / 3, 4 * math.pi / 3], radii,
         width=width, color=colors, edgecolor="white", linewidth=1)
axes.set_ylim(0, 1.05)
axes.set_thetagrids([0, 120, 240], labels, fontsize=8)
ticks = [10000, 30000, 50000, 70000] if maximum == 74264 else [10000 * n for n in range(1, int(maximum / 10000) + 1)]
axes.set_yticks([math.sqrt(value / maximum) for value in ticks])
axes.set_yticklabels([f"{value // 10000}万" for value in ticks], fontsize=6, color="#71818b")
axes.set_rlabel_position(52)
axes.grid(color="#dce2e6", linewidth=0.6, alpha=0.65)
axes.spines["polar"].set_color("#e6ebee")
figure.text(0.63, 0.77, f"共 {total:,} 条", fontsize=10, color="#193b4d")
for index, (label, color) in enumerate(zip(labels, colors)):
    vertical = 0.66 - 0.14 * index
    figure.text(0.63, vertical, label, fontsize=9, color=color, weight="bold")
    figure.text(0.63, vertical - 0.05,
                f"{counts[label]:,} 条  {counts[label] / total:.2%}", fontsize=8.5, color=color)
figure.text(0.04, 0.185, "模型标签未经人工校准，不等同于投诉率或满意度。", fontsize=7.5, color="#a6575d")
period = manifest['reports']['cleaning']
figure.text(0.04, 0.13, f"数据期间：{period['time_min'][:10]} 至 {period['time_max'][:10]}", fontsize=7, color="#65747d")
figure.text(0.04, 0.085, '课程预置批次 ' + args.batch_dir.name, fontsize=6.5, color="#65747d")
figure.text(0.04, 0.04, "初版静态视图；比例与计数均取自固定批次。", fontsize=7, color="#65747d")
folder = args.output_dir
folder.mkdir(parents=True, exist_ok=True)
svg = folder / "情感玫瑰图_初版面积.svg"
figure.savefig(svg, format="svg", facecolor="white")
preview = args.preview
preview.parent.mkdir(parents=True, exist_ok=True)
figure.savefig(preview, dpi=180, facecolor="white")
plt.close(figure)
geometry = {"source_csv": str(source.relative_to(ROOT)),
            "source_sha256": sha256(source.read_bytes()).hexdigest(),
            "svg_sha256": sha256(svg.read_bytes()).hexdigest(), "labels": labels,
            "counts": [counts[label] for label in labels], "radii": radii,
            "equal_angle_radians": width, "area_proportional_to_count": True,
            "interactive": False, "total": total}
(folder / "情感玫瑰图_初版面积.geometry.json").write_text(
    json.dumps(geometry, ensure_ascii=False, indent=2), encoding="utf-8")
print(str(svg))
print(str(preview))
