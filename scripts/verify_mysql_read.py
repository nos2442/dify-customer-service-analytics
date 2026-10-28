"""Check the real course data from Windows using the analysis read-only account."""
from datetime import datetime
import json
import os
from pathlib import Path

from dotenv import load_dotenv
import pymysql

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
with pymysql.connect(host=os.environ['MYSQL_HOST'], port=3306,
                     user=os.environ['MYSQL_READ_USER'], password=os.environ['MYSQL_READ_PASSWORD'],
                     database='chat', charset='utf8mb4', connect_timeout=10, read_timeout=30) as connection:
    with connection.cursor() as cursor:
        cursor.execute('SELECT COUNT(*), COUNT(records), SUM(records IS NULL), COUNT(DISTINCT id) FROM records')
        counts = cursor.fetchone()
        assert counts == (84252, 81636, 2616, 84252), counts
        cursor.execute('SHOW COLUMNS FROM records')
        columns = [row[0] for row in cursor.fetchall()]
        assert columns == ['id', 'time', 'records'], columns
        cursor.execute('SELECT records FROM records WHERE records IS NOT NULL LIMIT 3')
        samples = cursor.fetchall()
        assert len(samples) == 3 and all(any('\u4e00' <= char <= '\u9fff' for char in row[0]) for row in samples)
report = {'checked_at': datetime.now().astimezone().isoformat(), 'status': 'passed',
          'host': os.environ['MYSQL_HOST'], 'total_rows': counts[0], 'non_null_rows': counts[1],
          'null_rows': int(counts[2]), 'distinct_ids': counts[3], 'columns': columns,
          'chinese_decoding_samples_passed': 3, 'sample_text_logged': False}
folder = ROOT / 'evidence' / datetime.now().strftime('%Y%m%d')
folder.mkdir(exist_ok=True)
(folder / 'p02_windows_mysql_read.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
