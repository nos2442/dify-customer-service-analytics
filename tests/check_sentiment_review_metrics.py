"""Check pending/undetermined labels never count as successful classifications."""
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'analysis'))
import review_sentiment_manual as review


with tempfile.TemporaryDirectory(prefix='sentiment-metric-check-', dir=ROOT / 'data/private') as tmp:
    folder = Path(tmp).resolve()
    assert folder.is_relative_to((ROOT / 'data/private').resolve())
    review.PRIVATE = folder
    review.SAMPLES = folder / 'samples.json'
    review.REPORT = folder / 'report.json'
    state = {'source_sha256': 'synthetic', 'selection': 'synthetic check only', 'samples': [
        {'sample_id': 'S01', 'question': '查询', 'model_label': '负面', 'human_label': '中性'},
        {'sample_id': 'S02', 'question': '好评', 'model_label': '正面', 'human_label': '正面'},
        {'sample_id': 'S03', 'question': '语境缺失', 'model_label': '中性', 'human_label': '待定'},
        {'sample_id': 'S04', 'question': '未审核', 'model_label': '负面', 'human_label': ''},
    ]}
    review.SAMPLES.write_text(json.dumps(state), encoding='utf-8')
    report = review.save_progress(state)
    assert report['human_labels_recorded'] == 3 and report['unlabelled'] == 1
    assert report['undetermined'] == 1 and report['valid_three_class_comparisons'] == 2
    assert report['agreement_count'] == 1 and report['sample_agreement_rate'] == .5
    assert report['status'] == 'awaiting_user_labels' and report['agreement_is_provisional']
    for row in state['samples']:
        row['human_label'] = ''
    report = review.save_progress(state)
    assert report['sample_agreement_rate'] is None and report['agreement_count'] == 0
    assert report['human_labels_recorded'] == 0 and report['unlabelled'] == 4
print('passed: pending and undetermined exclusions; no fabricated agreement before labels')
