"""Explain the saved three-topic model using local evidence; never rename or relabel it."""
from collections import Counter
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import csv
import json
from pathlib import Path
import random
import re

from gensim.models import LdaModel

ROOT = Path(__file__).resolve().parents[1]
BATCH = 'preset_refresh_20261024_jan_jul'
OUT = ROOT / 'evidence/20261024/topic_business'
PRIVATE = ROOT / 'data/private/analysis/topic_business_20261024'
CUES = {
    '物流与收货': ['物流', '快递', '发货', '发出', '配送', '送货', '到货', '签收', '收货', '取件码', '没收到', '包裹'],
    '退换与退款': ['退款', '退货', '换货', '退换', '退回', '退掉', '退一下', '更换', '退了', '寄回', '能退', '想退', '想换', '换成', '退吗'],
    '商品规格与选购': ['尺码', '尺寸', '材质', '颜色', '规格', '适合', '型号', '容量', '功率', '库存', '推荐', '标准码', '运动码', '偏小', '偏大', '一码', '码数', '上架', '现货'],
    '价格与优惠': ['优惠', '凑单', '折扣', '满减', '价格', '便宜', '批发价', '活动', '价差', '定金', '尾款', '会员价', '普通价', '涨', '降价', '保价', '打折'],
    '订单与账户': ['订单', '下单', '取消', '账号', '账户', '手机号', '验证码', '登录', '登陆', '登不', '发票', '重开'],
    '支付与账务': ['到账', '退钱', '付款', '支付', '金额', '扣款', '运费', '快递费', '银行卡', '花呗', '实付'],
    '质量与售后': ['维修', '保修', '故障', '坏了', '不能用', '安装', '售后', '质量', '破损', '漏水', '开胶', '螺丝', '掉色', '色差', '划痕', '松了'],
    '时效或沟通表达': ['客服', '在线', '回复', '尽快', '赶紧', '帮帮忙', '麻烦', '多久', '什么时候', '几天', '着急'],
}


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def hits(text):
    return [name for name, words in CUES.items() if any(w in text for w in words)]


def redact(text):
    text = re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[链接或邮箱]', text)
    text = re.sub(r'[A-Za-z0-9_-]{6,}|\d{5,}', '[编号]', text)
    return re.sub(r'\s+', ' ', text).strip()


def main():
    # One check of the privacy/counting boundary: multi-cue hits are not exclusive labels.
    assert '物流与收货' in hits('快递未收到，要求退款') and '退换与退款' in hits('快递未收到，要求退款')
    assert '13800138000' not in redact('手机号13800138000 https://example.com/order')
    folder = ROOT / 'analysis/output' / BATCH
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    parent = ROOT / 'analysis/output' / manifest['parent_batch']
    for name, expected in manifest['model_artifacts'].items():
        assert digest(parent / 'models' / name) == expected
    source = ROOT / 'data/private/analysis' / BATCH / 'record_results.jsonl'
    before = digest(source)
    rows = [json.loads(line) for line in source.open(encoding='utf-8')]
    assert len(rows) == manifest['reports']['cleaning']['retained_rows'] == 42860
    counts = Counter(int(r['topic']) for r in rows)
    with (folder / '月度主题趋势数据.csv').open(encoding='utf-8-sig', newline='') as f:
        csv_counts = Counter()
        for row in csv.DictReader(f):
            csv_counts[int(row['topic'])] += int(row['count'])
    assert counts == csv_counts and set(counts) == {0, 1, 2}
    model = LdaModel.load(str(parent / 'models/lda.model'))
    assert model.num_topics == 3
    # Interpretation filter only: do not change the saved tokens, assignments or trained model.
    stopwords = {l.split('#', 1)[0].strip() for l in (ROOT / 'analysis/stopwords_v2.txt').read_text(encoding='utf-8').splitlines()}
    groups = {t: [r for r in rows if r['topic'] == t] for t in range(3)}
    df = {t: Counter(w for r in group for w in set(r['tokens']) if w not in stopwords) for t, group in groups.items()}
    global_df = sum(df.values(), Counter())
    cue_counts = {t: Counter(c for r in group for c in hits(r['records'])) for t, group in groups.items()}
    global_cues = sum(cue_counts.values(), Counter())
    profiles, selected = {}, []
    for t, group in groups.items():
        n = len(group)
        distinctive = sorted(((word, amount, amount / n / (global_df[word] / len(rows)))
                              for word, amount in df[t].items() if amount >= max(30, n * .005)),
                             key=lambda v: (-v[2], -v[1], v[0]))[:20]
        profile = {
            'count': n, 'share': n / len(rows), 'unique_texts': len({r['records'] for r in group}),
            'model_top_terms': [{'word': word, 'weight': float(weight)} for word, weight in model.show_topic(t, topn=20)],
            'interpretation_top_document_terms': df[t].most_common(20),
            'distinctive_terms': [{'word': w, 'documents': a, 'share': a / n, 'lift_vs_all_records': lift} for w, a, lift in distinctive],
            'cue_profiles': {name: {'documents': cue_counts[t][name], 'share': cue_counts[t][name] / n,
                                    'global_share': global_cues[name] / len(rows),
                                    'lift_vs_all_records': cue_counts[t][name] / n / (global_cues[name] / len(rows)) if global_cues[name] else None}
                             for name in CUES},
            'multiple_business_cues': sum(len([x for x in hits(r['records']) if x != '时效或沟通表达']) >= 2 for r in group),
            'no_business_cue_matched': sum(not [x for x in hits(r['records']) if x != '时效或沟通表达'] for r in group),
        }
        picked, used = [], set()
        def choose(candidates, reason):
            for r in candidates:
                if r['records'] not in used:
                    picked.append((r, reason)); used.add(r['records']); return
        rng = random.Random(20261024 + t)
        for month in sorted({r['time'][:7] for r in group}):
            candidates = [r for r in group if r['time'].startswith(month)]
            rng.shuffle(candidates); choose(candidates, '按月随机：' + month)
        frequency = Counter(r['records'] for r in group)
        for text, amount in frequency.most_common(2):
            choose([r for r in group if r['records'] == text], '高频表达，出现' + str(amount) + '次')
        covered_words = set()
        distinctive_picks = 0
        for word, _, _ in distinctive:
            if word in covered_words or distinctive_picks >= 3:
                continue
            previous = len(picked)
            choose(sorted((r for r in group if word in r['tokens']), key=lambda r: (len(r['records']), r['id'])), '区别词线索：' + word)
            if len(picked) > previous:
                covered_words.update(picked[-1][0]['tokens'])
                distinctive_picks += 1
        rest = list(group); rng.shuffle(rest)
        while len(picked) < 12:
            choose(rest, '补充分层样本')
        for i, (r, reason) in enumerate(picked[:12], 1):
            selected.append({'sample_id': f'T{t}-{i:02d}', 'topic': t, 'source_id': r['id'], 'time': r['time'],
                             'selection_reason': reason, 'redacted_question': redact(r['records']),
                             'lexical_cues_only': hits(r['records']), 'human_business_label': '',
                             'human_agrees_with_topic_explanation': '', 'human_notes': ''})
        profiles[str(t)] = profile
    OUT.mkdir(parents=True, exist_ok=True); PRIVATE.mkdir(parents=True, exist_ok=True)
    sample_file = PRIVATE / 'review_samples.json'
    # Never overwrite answers entered by the user during a later rerun.
    assert not sample_file.exists(), 'Review package exists; preserve existing human annotations'
    sample_file.write_text(json.dumps(selected, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# 当前三主题人工复核样本', '', '共36条：每主题12条，覆盖7个月，并含高频和区别词样本。它是目的性复核样本，不是准确率随机验证集。',
             '“词项线索”只是规则命中，允许多项，不是人工标签。请填写业务类别、是否支持主题解释及备注；未填写前不记为人工验收。', '']
    for t in range(3):
        lines += [f'## 主题{t}', '', '| 样本ID／源记录 | 时间 | 抽样方式 | 脱敏问题 | 词项线索 | 人工类别 | 支持解释？ |', '| --- | --- | --- | --- | --- | --- | --- |']
        for row in selected:
            if row['topic'] == t:
                lines.append('| ' + ' | '.join([row['sample_id'] + '／' + str(row['source_id']), row['time'][:10], row['selection_reason'], row['redacted_question'].replace('|', '\\|'), '、'.join(row['lexical_cues_only']) or '无', '待填写', '待填写']) + ' |')
        lines.append('')
    (PRIVATE / '人工复核样本.md').write_text('\n'.join(lines), encoding='utf-8')
    report = {'checked_at': datetime.now(timezone(timedelta(hours=8))).isoformat(), 'status': 'evidence_prepared_human_confirmation_pending',
              'batch': BATCH, 'parent_model_batch': manifest['parent_batch'], 'rows': len(rows), 'num_topics': 3,
              'source_sha256': before, 'model_sha256': digest(parent / 'models/lda.model'), 'topic_counts_match_current_csv': True,
              'profiles': profiles, 'lexical_cue_definitions': CUES,
              'cue_policy': 'Literal substring presence per record; overlapping cues are allowed and do not define true intent or accuracy. Separate politeness/timing expressions from business clues.',
              'interpretation_filter_only': True, 'model_retrained': False, 'assignments_changed': False,
              'dashboard_changed': False, 'external_api_called': False,
              'private_review_samples': str(sample_file.relative_to(ROOT)), 'private_sample_sha256': digest(sample_file),
              'samples': 36, 'human_annotations_completed': False,
              'old_k_comparison_caution': 'The 20261022 K=2..7 metrics all use v2 preprocessing; its K=3 score is not a measured score of the original v1 model. K=7 is only the maximum within that tested range.'}
    assert digest(source) == before
    assert sum(p['count'] for p in profiles.values()) == len(rows)
    (OUT / 'evidence.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for t, profile in profiles.items():
        print('TOPIC', t, profile['count'], f"{profile['share']:.2%}")
        print('distinctive', [(x['word'], x['documents'], round(x['lift_vs_all_records'], 2)) for x in profile['distinctive_terms'][:10]])
        print('business_cues', {k: round(v['share'] * 100, 2) for k, v in profile['cue_profiles'].items()})
    print('private_samples_prepared', len(selected))


if __name__ == '__main__':
    main()
