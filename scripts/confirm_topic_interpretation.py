"""Apply the user's accepted tendency descriptions without changing topic data."""
from copy import deepcopy
import csv
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path

import requests

from refresh_mofang_batch import ROOT, BASE, PANEL, CHARTS, canonical, decoded


def main():
    now = datetime.now(timezone(timedelta(hours=8)))
    view_id = CHARTS['topic'][2]
    token = json.loads((ROOT / 'data/private/mofang-auth.json').read_text(encoding='utf-8'))['token']
    session = requests.Session()
    session.trust_env = False
    session.headers.update({'Authorization': token, 'IdToken': token})
    def api(method, path, payload=None):
        response = session.request(method, BASE + path, json=payload, timeout=(10, 35))
        value = response.json()
        if not value.get('success'):
            raise RuntimeError(path + ': ' + str(value.get('message')))
        return value['data']
    panel = api('GET', 'panel/group/findOne/' + PANEL)
    view = api('POST', 'chart/view/get/' + view_id + '/' + PANEL, {})
    before_data = api('POST', 'chart/view/getData/' + view_id + '/' + PANEL, {})['data']
    with (ROOT / 'analysis/output/preset_refresh_20261024_jan_jul/月度主题趋势数据.csv').open(encoding='utf-8-sig', newline='') as stream:
        expected = list(csv.DictReader(stream))
    assert len(expected) == 21 and sum(int(r['count']) for r in expected) == 42860
    assert canonical(decoded(before_data['tableRow'], before_data['fields'])) == canonical(expected)
    backup = ROOT / 'data/private' / ('topic-confirmation-before-' + now.strftime('%Y%m%d-%H%M%S') + '.json')
    assert not backup.exists()
    backup.write_text(json.dumps({'panel': panel, 'view': view}, ensure_ascii=False), encoding='utf-8')
    original_view = deepcopy(view)
    title = '月度主题趋势（混合分组）'
    explanation = '0：质量与收货异常倾向；1：订单处理与规格咨询倾向；2：寄回运费与优惠咨询倾向。均为混合模型组，非准确业务分类；计数不等于各类业务发生次数。'
    style = json.loads(view['customStyle'])
    style['text']['remark'] = ('批次：preset_refresh_20261024_jan_jul；' + explanation)
    view.update(name=title, title=title, customStyle=json.dumps(style, ensure_ascii=False, separators=(',', ':')))
    view.pop('data', None)
    view.pop('sql', None)
    api('POST', 'chart/view/save/' + PANEL, view)
    tiles = json.loads(panel['panelData'])
    original_tiles = deepcopy(tiles)
    footer_id = 'b00a904e-ad2c-4451-820a-278a65100f1d'
    footer = next(t for t in tiles if t['id'] == footer_id)
    footer_text = '主题解释（混合）：0质量与收货异常倾向；1订单处理与规格咨询倾向；2寄回运费与优惠咨询倾向。末月未满；小时累计；词频非聊天数；情感未校准，非投诉率/满意度。'
    footer['propValue'] = footer_text
    # Conservative text-width bound: each character consumes no more than one full font em.
    assert len(footer_text) * footer['style']['fontSize'] < footer['style']['width']
    payload = {k: panel[k] for k in ['id', 'name', 'pid', 'nodeType', 'panelStyle', 'mobileLayout', 'panelType']}
    payload.update(panelData=json.dumps(tiles, ensure_ascii=False, separators=(',', ':')), optType='update')
    api('POST', 'panel/group/update', payload)
    saved_panel = api('GET', 'panel/group/findOne/' + PANEL)
    saved_view = api('POST', 'chart/view/get/' + view_id + '/' + PANEL, {})
    saved_data = api('POST', 'chart/view/getData/' + view_id + '/' + PANEL, {})['data']
    assert saved_view['name'] == title and json.loads(saved_view['customStyle'])['text']['remark'].endswith(explanation)
    assert canonical(decoded(saved_data['tableRow'], saved_data['fields'])) == canonical(expected)
    for field in ['tableId', 'type', 'xaxis', 'yaxis', 'extStack']:
        assert saved_view[field] == original_view[field], 'Topic binding changed: ' + field
    saved_tiles = json.loads(saved_panel['panelData'])
    assert saved_tiles == tiles
    for before, after in zip(original_tiles, saved_tiles):
        if before['id'] != footer_id:
            assert before == after
        else:
            before['propValue'] = footer_text
            assert before == after
    out = ROOT / 'evidence/20261024/topic_business/platform_confirmation.json'
    report = {'checked_at': now.isoformat(), 'status': 'saved_reopened_data_unchanged',
              'user_decision': '保留三组，确认倾向解释', 'interpretation_accepted_by_user': True,
              'title': title, 'chart_remark': json.loads(saved_view['customStyle'])['text']['remark'],
              'visible_footer': footer_text, 'footer_width_bound_passed': True,
              'rows': 21, 'sum_count': 42860, 'all_rows_match_csv': True,
              'model_retrained': False, 'topic_bindings_unchanged': True,
              'other_tiles_and_layout_unchanged': True, 'numeric_legend_ids_preserved': True,
              'human_sample_labels_completed': False, 'classification_accuracy_claimed': False,
              'private_backup': str(backup.relative_to(ROOT)), 'secrets_logged': False}
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'rows': 21, 'sum_count': 42860, 'footer_width_bound_passed': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
