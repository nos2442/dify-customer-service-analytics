"""Prepare blind local samples; import only sentiment labels supplied by the user."""
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
from pathlib import Path
import random
import re
import shutil
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
BATCH = 'preset_refresh_20261024_jan_jul'
PRIVATE = ROOT / 'data/private/analysis/sentiment_review_20261024'
SAMPLES = PRIVATE / 'samples.json'
REPORT = ROOT / 'evidence/20261024/sentiment_manual_review/progress.json'
LABELS = ('负面', '中性', '正面')
RULES = ('判断句子明确表达的情绪：明确不满、焦虑或失望为负面；客观询问/描述且无明确评价为中性；'
         '明确好评、满意或赞许为正面。谢谢/麻烦/请问等礼貌用语不单独决定标签。'
         '无法判断或正负情绪难以取舍时标待定，不强行归为中性。')


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def redact(text):
    text = re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[链接或邮箱]', text)
    return re.sub(r'\s+', ' ', re.sub(r'[A-Za-z0-9_-]{6,}|\d{5,}', '[编号]', text)).strip()


def read_workbook(path, samples):
    """Read this review template without changing the user's workbook."""
    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with ZipFile(path) as archive:
        sheets = ET.fromstring(archive.read('xl/workbook.xml')).findall('s:sheets/s:sheet', ns)
        assert len(sheets) == 1 and sheets[0].get('name') == '情感人工复核', 'Unexpected workbook template'
        shared = ([''.join(item.itertext()) for item in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
                  if 'xl/sharedStrings.xml' in archive.namelist() else [])
        sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        cells = {cell.get('r'): cell for cell in sheet.findall('s:sheetData/s:row/s:c', ns)}

        def value(address):
            cell = cells.get(address)
            if cell is None:
                return ''
            assert cell.find('s:f', ns) is None, 'Review fields must contain literal values'
            if cell.get('t') == 'inlineStr':
                return ''.join(cell.find('s:is', ns).itertext())
            text = cell.findtext('s:v', default='', namespaces=ns)
            return shared[int(text)] if cell.get('t') == 's' else text

        assert [value(f'{col}8') for col in 'ABCD'] == ['编号', '脱敏问题', '人工标签', '判断理由／备注（选填）']
        expected = {row['sample_id']: row for row in samples}
        supplied = {}
        for index in range(9, 9 + len(samples)):
            sample_id = value(f'A{index}')
            assert sample_id in expected and sample_id not in supplied, 'Missing, duplicate or unknown sample ID'
            assert value(f'B{index}') == expected[sample_id]['question'], 'Review question changed'
            label = value(f'C{index}').strip()
            assert label in (*LABELS, '待定'), 'Missing or invalid human label'
            supplied[sample_id] = {'label': label, 'note': value(f'D{index}').strip()}
        assert set(supplied) == set(expected)
        return supplied


def save_progress(state):
    rows = state['samples']
    valid = [r for r in rows if r['human_label'] in LABELS]
    labelled = [r for r in rows if r['human_label']]
    pending = [r for r in rows if not r['human_label']]
    confusion = {model: {human: 0 for human in LABELS} for model in LABELS}
    for row in valid:
        confusion[row['model_label']][row['human_label']] += 1
    agreement = sum(r['model_label'] == r['human_label'] for r in valid)
    complete = not pending
    status = ('completed_pilot_with_undetermined' if any(r['human_label'] == '待定' for r in rows) else 'completed_pilot_review') if complete else 'awaiting_user_labels'
    report = {
        'checked_at': now(), 'status': status, 'batch': BATCH, 'rules': RULES,
        'source_sha256': state['source_sha256'], 'sample_selection': state['selection'],
        'total_samples': len(rows), 'human_labels_recorded': len(labelled),
        'unlabelled': len(pending), 'undetermined': sum(r['human_label'] == '待定' for r in rows),
        'valid_three_class_comparisons': len(valid), 'agreement_count': agreement,
        'agreement_sample_ids': [r['sample_id'] for r in valid if r['model_label'] == r['human_label']],
        'disagreement_sample_ids': [r['sample_id'] for r in valid if r['model_label'] != r['human_label']],
        'sample_agreement_rate': agreement / len(valid) if valid else None,
        'agreement_is_provisional': not complete,
        'confusion_rows_model_columns_human': confusion,
        'human_label_distribution': dict(Counter(r['human_label'] for r in labelled)),
        'per_model_stratum': {label: {'sampled': sum(r['model_label'] == label for r in rows),
                                    'human_labelled': sum(r['model_label'] == label and bool(r['human_label']) for r in rows)} for label in LABELS},
        'next_sample_ids': [r['sample_id'] for r in pending[:6]],
        'private_samples': str(SAMPLES.relative_to(ROOT)), 'private_samples_sha256': digest(SAMPLES),
        'no_verbatim_questions_in_public_evidence': True,
        'whole_population_accuracy_claimed': False, 'threshold_changed': False,
        'dashboard_changed': False, 'model_or_external_api_called': False,
        'caution': '24 samples balanced by MODEL label; sample agreement is not whole-batch accuracy. Undetermined items are excluded from the three-class denominator and counted separately. User labels are one reviewer, not independently adjudicated truth.'}
    if 'workbook_import' in state:
        report['workbook_import'] = state['workbook_import']
    if 'rubric_confirmation' in state:
        report['rubric_confirmation'] = state['rubric_confirmation']
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    blind = ['# 情感标签人工复核（先隐藏模型结果）', '', RULES, '',
             '共24条，用户通过Excel一次填写。标签仅来自实际用户输入；模型标签和分数保留在私有JSON。', '']
    for index in range(0, len(rows), 6):
        blind += [f'## 第{index // 6 + 1}轮', '', '| 编号 | 问题（脱敏） | 用户标签 |', '| --- | --- | --- |']
        for row in rows[index:index+6]:
            blind.append('| ' + row['sample_id'] + ' | ' + row['question'].replace('|', '\\|') + ' | ' + (row['human_label'] or '待填写') + ' |')
        blind.append('')
    (PRIVATE / '盲审样本.md').write_text('\n'.join(blind) + '\n', encoding='utf-8')
    if labelled:
        comparison = ['# 情感复核逐条对照（私有）', '',
                      '人工列仅来自用户填写；是否一致不表示模型或人工已经过多人裁决。', '',
                      '| 编号 | 脱敏问题 | 模型分数 | 模型标签 | 用户标签 | 是否一致 | 用户备注 |',
                      '| --- | --- | --- | --- | --- | --- | --- |']
        for row in rows:
            match = ('是' if row['model_label'] == row['human_label'] else '否') if row['human_label'] in LABELS else '不比较'
            values = [row['sample_id'], row['question'], str(row.get('model_score', '')),
                      row['model_label'], row['human_label'], match, row.get('human_note', '')]
            comparison.append('| ' + ' | '.join(v.replace('|', '\\|').replace('\n', ' ') for v in values) + ' |')
        (PRIVATE / '人工与模型逐条对照.md').write_text('\n'.join(comparison) + '\n', encoding='utf-8')
    return report


def main(stage, answers=None, workbook=None):
    source = ROOT / 'data/private/analysis' / BATCH / 'record_results.jsonl'
    if stage == 'prepare':
        assert not SAMPLES.exists(), 'Existing manual labels must not be overwritten'
        rows = [json.loads(line) for line in source.open(encoding='utf-8')]
        assert len(rows) == 42860
        rng = random.Random(20261024)
        chosen = {label: rng.sample(sorted([r for r in rows if r['sentiment'] == label], key=lambda r: r['id']), 8) for label in LABELS}
        samples = []
        for i in range(8):
            order = list(LABELS)
            rng.shuffle(order)
            for label in order:
                row = chosen[label][i]
                score = float(row['sentiment_score'])
                assert label == ('负面' if score < .4 else '正面' if score > .6 else '中性')
                samples.append({'sample_id': f'S{len(samples)+1:02d}', 'source_id': row['id'],
                                'time': row['time'], 'question': redact(row['records']),
                                'model_label': label, 'model_score': score,
                                'human_label': '', 'label_source': '', 'history': []})
        assert len({r['source_id'] for r in samples}) == 24
        assert Counter(r['model_label'] for r in samples) == {label: 8 for label in LABELS}
        state = {'batch': BATCH, 'created_at': now(), 'source_sha256': digest(source),
                 'selection': {'method': 'simple random sample within each model-label stratum; eight per model label; shuffled display',
                               'seed': 20261024, 'model_strata_population': dict(Counter(r['sentiment'] for r in rows)),
                               'months_in_sample': sorted({r['time'][:7] for r in samples})},
                 'samples': samples}
        PRIVATE.mkdir(parents=True, exist_ok=True)
        SAMPLES.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    else:
        state = json.loads(SAMPLES.read_text(encoding='utf-8'))
        assert state['source_sha256'] == digest(source), 'Source changed; do not mix review batches'
        if stage == 'record':
            assert answers, 'Provide an answers JSON file containing labels explicitly sent by the user'
            supplied = json.loads(answers.read_text(encoding='utf-8'))
            mapping = {row['sample_id']: row for row in state['samples']}
            assert supplied and all(k in mapping and v in (*LABELS, '待定') for k, v in supplied.items())
            for sample_id, label in supplied.items():
                row = mapping[sample_id]
                row['history'].append({'at': now(), 'old_label': row['human_label'], 'new_label': label, 'source': 'explicit_user_chat_labels'})
                row.update(human_label=label, label_source='explicit_user_chat_labels')
            SAMPLES.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        elif stage == 'record-workbook':
            assert workbook, 'Provide the user-completed workbook'
            supplied = read_workbook(workbook, state['samples'])
            workbook_hash = digest(workbook)
            snapshot = PRIVATE / f'user_completed_{workbook_hash[:12]}.xlsx'
            if not snapshot.exists():
                shutil.copy2(workbook, snapshot)
            backup = PRIVATE / f'before_workbook_{digest(SAMPLES)[:12]}.json'
            if not backup.exists():
                shutil.copy2(SAMPLES, backup)
            for row in state['samples']:
                item = supplied[row['sample_id']]
                if (row['human_label'] == item['label'] and row.get('human_note', '') == item['note']
                        and row.get('label_workbook_sha256') == workbook_hash):
                    continue
                row['history'].append({'at': now(), 'old_label': row['human_label'],
                                       'old_note': row.get('human_note', ''),
                                       'new_label': item['label'], 'new_note': item['note'],
                                       'source': 'user_completed_workbook', 'workbook_sha256': workbook_hash})
                row.update(human_label=item['label'], human_note=item['note'],
                           label_source='user_completed_workbook', label_workbook_sha256=workbook_hash)
            state['workbook_import'] = {'at': now(), 'workbook_sha256': workbook_hash,
                                       'source': 'user_completed_workbook', 'rows_validated': len(supplied),
                                       'private_snapshot': str(snapshot.relative_to(ROOT)),
                                       'input_workbook_unchanged': digest(workbook) == workbook_hash}
            SAMPLES.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    report = save_progress(state)
    print(json.dumps({k: report[k] for k in ['status', 'total_samples', 'human_labels_recorded', 'unlabelled', 'next_sample_ids']}, ensure_ascii=False))
    for row in [r for r in state['samples'] if not r['human_label']][:6]:
        print(row['sample_id'] + '：' + row['question'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['prepare', 'record', 'record-workbook', 'summary'])
    parser.add_argument('--answers', type=Path)
    parser.add_argument('--workbook', type=Path)
    args = parser.parse_args()
    main(args.stage, args.answers, args.workbook)
