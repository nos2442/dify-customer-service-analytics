"""Offline T07 checks. Never load keys, create Dify apps, or send requests."""
import json
from pathlib import Path

import yaml


def check():
    root = Path(__file__).resolve().parents[1]
    doc = yaml.safe_load((root / 'services/web_crawler_workflow.yml').read_text(encoding='utf-8'))
    graph = doc['workflow']['graph']
    nodes = {n['id']: n for n in graph['nodes']}
    assert doc['version'] == '0.4.0' and len(nodes) == 9 and len(graph['edges']) == 7
    assert len([n for n in nodes.values() if n['data']['type'] == 'iteration-start']) == 1
    inner = {'iteration_start', 'firecrawl', 'extract_page', 'serialize', 'write_knowledge'}
    assert {n['id'] for n in nodes.values() if n.get('parentId') == 'each_url'} == inner
    assert nodes['each_url']['data']['start_node_id'] == 'iteration_start'
    assert nodes['each_url']['data']['is_parallel'] is False
    assert all(e['source'] in nodes and e['target'] in nodes for e in graph['edges'])
    assert all('firecrawl:' not in d['value']['plugin_unique_identifier'] for d in doc['dependencies'])
    env = {v['name']: v for v in doc['workflow']['environment_variables']}
    assert all(env[k]['value'] == '' and env[k]['value_type'] == 'secret'
               for k in ['firecrawl_api_key', 'dataset_api_key'])
    assert env['dataset_id']['value'] == ''
    assert nodes['firecrawl']['data']['url'] == 'https://api.firecrawl.dev/v2/scrape'
    assert nodes['write_knowledge']['data']['body']['type'] == 'raw-text'
    for node in ['firecrawl', 'write_knowledge']:
        assert not nodes[node]['data']['retry_config']['retry_enabled']
    namespace = {}
    exec(compile(nodes['serialize']['data']['code'], '<T07 code node>', 'exec'), namespace)
    main = namespace['main']
    samples = ['中文正文', '他说“保留原文”，并写下 "quoted"。\n第二行\n路径C:\\tmp\\资料',
               "单引号'、Tab\t、回车\r\n、反斜杠\\及表情🙂"]
    for content in samples:
        response = json.dumps({'success': True, 'data': {'markdown': content}}, ensure_ascii=False)
        result = main('测试标题 "quoted"', content, 'https://example.com/test', 200, response)
        body = json.loads(result['request_body'])
        assert body['text'] == '来源URL：https://example.com/test\n\n' + content
        assert body['name'] == '测试标题 "quoted"'
        assert body['process_rule'] == {'mode': 'automatic'}
    rejected = 0
    for status, response, content in [(402, {'success': False}, 'x'),
                                     (200, {'success': False}, 'x'),
                                     (200, {'success': True, 'data': {'markdown': ''}}, 'x'),
                                     (200, {'success': True, 'data': {'markdown': '原文'}}, '改写')]:
        try:
            main('测试', content, 'https://example.com/test', status, json.dumps(response))
        except ValueError:
            rejected += 1
        else:
            raise AssertionError('Unsuccessful scrape or rewritten content was accepted')
    return {'status': 'passed', 'logical_steps': 8, 'actual_nodes': 9, 'edges': 7,
            'roundtrip_samples': len(samples), 'rejected_failure_cases': rejected,
            'network_requests': 0, 'keys_loaded': False, 'dify_apps_created': 0,
            'runtime_business_validation': 'not_performed'}


if __name__ == '__main__':
    print(json.dumps(check(), ensure_ascii=False, indent=2))
