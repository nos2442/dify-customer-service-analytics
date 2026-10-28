"""Validate an existing preset analysis batch and export only its five aggregates."""
import argparse
import calendar
import csv
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BATCH = ROOT / 'analysis/output/preset_refresh_20261024_jan_jul'
DEFAULT_OUTPUT = ROOT / 'dashboard/public/data/dashboard.json'
FILES = {
    'monthly': ('聊天密度月分布.csv', ['month', 'count']),
    'hourly': ('聊天密度小时分布.csv', ['hour', 'count']),
    'words': ('聊天词语频次数据.csv', ['word', 'count']),
    'topics': ('月度主题趋势数据.csv', ['month', 'topic', 'count']),
    'sentiment': ('情感分布.csv', ['sentiment', 'count']),
}
LABELS = ['负面', '中性', '正面']
TOPIC_LABELS = ['质量与收货异常倾向', '订单处理与规格咨询倾向', '寄回运费与优惠咨询倾向']


def check(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def integer(value, field):
    check(isinstance(value, str) and re.fullmatch(r'0|[1-9][0-9]*', value) is not None,
          f'{field}: expected a non-negative integer')
    return int(value)


def month_key(value):
    check(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}', value) is not None,
          'Invalid YYYY-MM month')
    datetime.strptime(value, '%Y-%m')
    return value


def build_payload(batch_dir):
    batch_dir = Path(batch_dir).resolve()
    manifest_path = batch_dir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    check(manifest['source_mode'] == 'preset', 'Only preset batches are supported by this dashboard')
    cleaning = manifest['reports']['cleaning']
    raw, excluded, retained = (cleaning[key] for key in ('raw_rows', 'excluded_rows', 'retained_rows'))
    check(all(type(value) is int and value >= 0 for value in (raw, excluded, retained))
          and retained > 0 and raw == excluded + retained, 'Invalid raw/excluded/retained totals')
    period_start = datetime.fromisoformat(cleaning['time_min']).date()
    period_end = datetime.fromisoformat(cleaning['time_max']).date()
    check(period_start <= period_end, 'Analysis period is reversed')
    tables, provenance = {}, []
    for key, (filename, columns) in FILES.items():
        path = batch_dir / filename
        meta = manifest['artifacts'][filename]
        file_hash = digest(path)
        check(file_hash == meta['sha256'], f'{filename}: SHA256 mismatch')
        with path.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream)
            check(reader.fieldnames == columns, f'{filename}: unexpected column headers')
            rows = list(reader)
        check(type(meta['rows']) is int and len(rows) == meta['rows'], f'{filename}: row count mismatch')
        for row in rows:
            check(set(row) == set(columns) and all(value is not None for value in row.values()),
                  f'{filename}: malformed CSV row')
            row['count'] = integer(row['count'], filename + '.count')
            for field in ('hour', 'topic'):
                if field in row:
                    row[field] = integer(row[field], filename + '.' + field)
            if 'month' in row:
                month_key(row['month'])
        tables[key] = rows
        provenance.append({'name': filename, 'sha256': file_hash, 'rows': len(rows)})

    monthly, hourly, words, topics, sentiment = (tables[key] for key in FILES)
    months = [row['month'] for row in monthly]
    check(len(set(months)) == len(months), 'Duplicate monthly key')
    expected_months = []
    year, month = period_start.year, period_start.month
    while (year, month) <= (period_end.year, period_end.month):
        expected_months.append(f'{year:04d}-{month:02d}')
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    check(sorted(months) == expected_months, 'Missing or out-of-period months')
    check(len(hourly) == 24 and {row['hour'] for row in hourly} == set(range(24)),
          'Hours must contain each integer from 0 through 23 once')
    check(len(words) == 100 and len({row['word'] for row in words}) == 100
          and all(row['word'].strip() and row['count'] > 0 for row in words),
          'Word cloud requires 100 unique non-empty words with positive counts')
    expected_topics = {(month, topic) for month in expected_months for topic in range(3)}
    check(len(topics) == len(expected_topics)
          and {(row['month'], row['topic']) for row in topics} == expected_topics,
          'Topics must contain each monthly topic 0, 1 and 2 once')
    check(len(sentiment) == 3 and {row['sentiment'] for row in sentiment} == set(LABELS),
          'Sentiment must contain the three model labels once')
    for key in ('monthly', 'hourly', 'topics', 'sentiment'):
        check(sum(row['count'] for row in tables[key]) == retained, f'{key}: count total mismatch')
    for row in monthly:
        check(sum(item['count'] for item in topics if item['month'] == row['month']) == row['count'],
              f"{row['month']}: topic subtotal differs from monthly count")
    if 'sentiment_counts' in manifest:
        check({row['sentiment']: row['count'] for row in sentiment} == manifest['sentiment_counts'],
              'Sentiment counts differ from manifest')
    if 'topic_counts' in manifest:
        check({str(topic): sum(row['count'] for row in topics if row['topic'] == topic)
               for topic in range(3)} == manifest['topic_counts'], 'Topic counts differ from manifest')

    monthly.sort(key=lambda row: row['month'])
    hourly.sort(key=lambda row: row['hour'])
    words.sort(key=lambda row: (-row['count'], row['word']))
    topics.sort(key=lambda row: (row['month'], row['topic']))
    sentiment.sort(key=lambda row: LABELS.index(row['sentiment']))
    batch_id = manifest['batch_id']
    if not batch_id.startswith('preset_'):
        batch_id = 'preset_' + batch_id
    partial = period_end.day != calendar.monthrange(period_end.year, period_end.month)[1]
    notes = ['课程预置聊天记录的离线批次快照，不包含微信公众号新增消息。',
             '小时分布为整个批次累计，按0至23时排列。',
             '词频为分词出现次数，不等于聊天记录条数。',
             '主题为LDA混合分组，非互斥业务分类。',
             '情感为SnowNLP模型标签，不直接解释为投诉率或满意度。']
    if partial:
        notes.append(f'末月实际截至{period_end.isoformat()}，未满自然月。')
    source_dir = str(batch_dir.relative_to(ROOT)).replace('\\', '/') if batch_dir.is_relative_to(ROOT) else batch_dir.name
    return {
        'schemaVersion': 1,
        'batch': {'id': batch_id, 'sourceMode': 'preset',
                  'generatedAt': datetime.now(timezone(timedelta(hours=8))).isoformat(),
                  'periodStart': period_start.isoformat(), 'periodEnd': period_end.isoformat(),
                  'rawRows': raw, 'excludedRows': excluded, 'retainedRows': retained,
                  'lastMonthPartial': partial, 'sourceLabel': '课程预置聊天记录'},
        **tables,
        'topicLabels': [{'id': index, 'label': label} for index, label in enumerate(TOPIC_LABELS)],
        'provenance': {'sourceDir': source_dir, 'manifestSha256': digest(manifest_path), 'files': provenance},
        'notes': notes,
    }


def export(batch_dir=DEFAULT_BATCH, output=DEFAULT_OUTPUT):
    payload = build_payload(batch_dir)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent,
                                         prefix=output.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return payload


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-dir', type=Path, default=DEFAULT_BATCH)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = export(args.batch_dir, args.output)
    print(json.dumps({'batch': result['batch']['id'], 'records': result['batch']['retainedRows'],
                      'output': str(args.output)}, ensure_ascii=True))
