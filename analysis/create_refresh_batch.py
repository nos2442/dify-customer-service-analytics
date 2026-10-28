"""Reaggregate a dated subset of an immutable analysed batch, without retraining."""
import argparse
from collections import Counter
import csv
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def main(parent, batch, start, end):
    assert Path(batch).name == batch and batch.startswith('preset_')
    assert start < end
    original = ROOT / 'analysis/output' / parent
    manifest = json.loads((original / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['source_mode'] == 'preset'
    snapshot = ROOT / manifest['snapshot']['path']
    assert digest(snapshot) == manifest['snapshot']['sha256']
    source = snapshot.parent / 'record_results.jsonl'
    raw = [json.loads(line) for line in snapshot.open(encoding='utf-8')]
    selected_raw = [r for r in raw if start <= r['time'][:10] < end]
    raw_ids = {r['id'] for r in selected_raw}
    rows = [json.loads(line) for line in source.open(encoding='utf-8')]
    selected = [r for r in rows if start <= r['time'][:10] < end]
    assert selected and all(r['id'] in raw_ids for r in selected)
    assert len({r['id'] for r in selected}) == len(selected)
    out = ROOT / 'analysis/output' / batch
    private = ROOT / 'data/private/analysis' / batch
    assert not out.exists() and not private.exists(), 'Choose a new batch ID'
    months = sorted({r['time'][:7] for r in selected})
    hourly = Counter(int(r['time'][11:13]) for r in selected)
    monthly = Counter(r['time'][:7] for r in selected)
    words = Counter(w for r in selected for w in r['tokens'])
    topics = Counter((r['time'][:7], int(r['topic'])) for r in selected)
    sentiment = Counter(r['sentiment'] for r in selected)
    assert set(sentiment) <= {'负面', '中性', '正面'}
    assert all(r['topic_status'] == 'assigned' and r['sentiment_status'] == 'scored' for r in selected)
    assert sum(hourly.values()) == sum(monthly.values()) == sum(topics.values()) == sum(sentiment.values()) == len(selected)
    tables = {
        '聊天密度小时分布.csv': (['hour', 'count'], [(h, hourly[h]) for h in range(24)]),
        '聊天密度月分布.csv': (['month', 'count'], [(m, monthly[m]) for m in months]),
        '聊天词语频次数据.csv': (['word', 'count'], sorted(words.items(), key=lambda v: (-v[1], v[0]))[:100]),
        '月度主题趋势数据.csv': (['month', 'topic', 'count'], [(m, t, topics[m, t]) for m in months for t in range(3)]),
        '情感分布.csv': (['sentiment', 'count'], [(label, sentiment[label]) for label in ['负面', '中性', '正面']]),
    }
    out.mkdir(); private.mkdir()
    for name, data in [('source_snapshot.jsonl', selected_raw), ('record_results.jsonl', selected)]:
        (private / name).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in data), encoding='utf-8')
    artifacts = {}
    for name, (headers, values) in tables.items():
        p = out / name
        with p.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.writer(handle, lineterminator='\n'); writer.writerow(headers); writer.writerows(values)
        with p.open(encoding='utf-8-sig', newline='') as handle:
            actual = list(csv.reader(handle))
        assert actual == [headers] + [[str(v) for v in row] for row in values]
        assert digest(p) != manifest['artifacts'][name]['sha256'], 'Refresh should visibly change this table'
        artifacts[name] = {'rows': len(values), 'columns': headers, 'sha256': digest(p)}
    report = {
        'state': 'generated_pending_platform_refresh_and_manual_review', 'source_mode': 'preset', 'batch_id': batch,
        'created_at': datetime.now().astimezone().isoformat(), 'parent_batch': parent,
        'source': manifest['source'], 'selection': {'start_inclusive': start, 'end_exclusive': end},
        'snapshot': {'path': str((private / 'source_snapshot.jsonl').relative_to(ROOT)), 'sha256': digest(private / 'source_snapshot.jsonl'), 'rows': len(selected_raw)},
        'parent_snapshot_sha256': digest(snapshot), 'parent_results_sha256': digest(source),
        'model_policy': 'Reuse unchanged per-record tokens, topic IDs and SnowNLP scores from parent; no retraining or new cloud inference; recompute all five aggregates for selected period',
        'model_artifacts': manifest['model_artifacts'], 'reports': {'cleaning': {'raw_rows': len(selected_raw), 'retained_rows': len(selected), 'excluded_rows': len(selected_raw)-len(selected), 'time_min': min(r['time'] for r in selected), 'time_max': max(r['time'] for r in selected)}},
        'artifacts': artifacts, 'sentiment_counts': dict(sentiment), 'topic_counts': {str(t): sum(v for (m, k), v in topics.items() if k == t) for t in range(3)},
        'manual_topic_and_sentiment_review_completed': False, 'online_wechat_or_synthetic_rows_included': False,
        'implementation_sha256': digest(Path(__file__)),
    }
    (out / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'batch': batch, 'raw_rows': len(selected_raw), 'retained_rows': len(selected), 'months': months, 'sentiment': dict(sentiment)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', default='preset_deploy_20261020')
    parser.add_argument('--batch', required=True)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    args = parser.parse_args()
    main(args.parent, args.batch, args.start, args.end)
