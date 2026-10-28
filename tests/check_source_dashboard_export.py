"""Check real-batch reconciliation and reject damaged aggregates before overwriting output."""
import csv
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import export_source_dashboard as dashboard

current = dashboard.build_payload(dashboard.DEFAULT_BATCH)
assert current['batch']['retainedRows'] == 42860
assert current['batch']['id'] == 'preset_refresh_20261024_jan_jul'
assert current['batch']['periodStart'] == '2024-01-01' and current['batch']['periodEnd'] == '2024-07-28'
assert current['batch']['lastMonthPartial']
assert [len(current[key]) for key in dashboard.FILES] == [7, 24, 100, 21, 3]
assert current['sentiment'] == [{'sentiment': '负面', 'count': 39046},
                                {'sentiment': '中性', 'count': 1466},
                                {'sentiment': '正面', 'count': 2348}]
assert [r['hour'] for r in current['hourly']] == list(range(24))
original = dashboard.build_payload(ROOT / 'analysis/output/preset_deploy_20261020')
assert original['batch']['id'] == 'preset_deploy_20261020'
assert original['batch']['retainedRows'] == 81636

with tempfile.TemporaryDirectory(prefix='source-dashboard-check-') as temporary:
    folder = Path(temporary)
    output = folder / 'dashboard.json'
    valid = dashboard.export(dashboard.DEFAULT_BATCH, output)
    before = output.read_bytes()
    assert json.loads(before)['monthly'] == valid['monthly']
    for case in ('sha', 'negative_count', 'duplicate_hour', 'subtotal', 'word_duplicate'):
        source = folder / case
        source.mkdir()
        for filename in ('manifest.json', *(file[0] for file in dashboard.FILES.values())):
            shutil.copy2(dashboard.DEFAULT_BATCH / filename, source / filename)
        filename = '聊天词语频次数据.csv' if case == 'word_duplicate' else '聊天密度小时分布.csv'
        if case == 'subtotal':
            filename = '月度主题趋势数据.csv'
        path = source / filename
        with path.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream)
            fields, rows = reader.fieldnames, list(reader)
        if case in ('sha', 'negative_count'):
            rows[0]['count'] = '-1'
        elif case == 'duplicate_hour':
            rows[1]['hour'] = rows[0]['hour']
        elif case == 'subtotal':
            rows[0]['count'] = str(int(rows[0]['count']) + 1)
            rows[3]['count'] = str(int(rows[3]['count']) - 1)
        else:
            rows[1]['word'] = rows[0]['word']
        with path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        if case != 'sha':
            manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
            manifest['artifacts'][filename]['sha256'] = sha256(path.read_bytes()).hexdigest()
            (source / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        try:
            dashboard.export(source, output)
        except ValueError:
            pass
        else:
            raise AssertionError(f'Accepted damaged batch: {case}')
        assert output.read_bytes() == before, f'Existing output overwritten on failure: {case}'
print('passed: current 42860 and historical 81636 batches; SHA, count, hour, monthly topic and word checks; failed export preserves output')
