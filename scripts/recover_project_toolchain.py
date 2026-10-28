"""Recover existing endpoints on either reviewed LAN and probe the synthetic app.

Reads private credentials without logging them. Never creates resources, writes
.env, publishes a workflow, changes defaults, or touches the callback service.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from urllib.parse import urlencode
from uuid import uuid4

from dotenv import dotenv_values
import pymysql
import requests

from configure_dify import make_api, MCP_PATH
from configure_xinference_models import MODEL_PATH
from configure_test_rag import verify_case, KNOWN_QUESTION, APP_NAME
from verify_dify_workflow import safe_error, stop_and_confirm

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'evidence' / datetime.now().astimezone().strftime('%Y%m%d') / 'network_recovery'
APP_ID = '3d446716-7f6b-4af1-83e0-005b122cdd11'
DATASET_ID = '5576ed9e-42d7-4f43-9677-5e91628fbecb'
PROVIDER_ID = '2f1399ab-64c7-4658-ada7-065030f88b83'
PROFILES = {
    ('http://192.168.18.176', '192.168.18.176', '192.168.18.231'): 'original_ethernet',
    ('http://192.168.110.108', '192.168.110.108', '192.168.110.123'): 'temporary_wifi',
    ('http://192.168.110.124', '192.168.110.124', '192.168.110.141'): 'temporary_wifi_20261023',
}


def profile(env):
    endpoints = tuple(env.get(key) for key in ('DIFY_BASE_URL', 'MYSQL_HOST', 'MCP_HOST'))
    if endpoints not in PROFILES:
        raise RuntimeError('Only reviewed VM/Windows profiles identified by VMware Tools are allowed')
    return PROFILES[endpoints]


def configure(api, session, env, report):
    target = f"http://{env['MCP_HOST']}:8095/sse"
    providers = api('GET', '/workspaces/current/tool-providers?type=mcp')
    matches = [p for p in providers if p.get('server_identifier') == 'wechat-mysql']
    if len(matches) != 1 or matches[0]['id'] != PROVIDER_ID:
        raise RuntimeError('Existing MCP provider identity differs; no duplicate will be created')
    provider = matches[0]
    icon = provider['icon']
    if not isinstance(icon, dict) or icon.get('content') != '🗄️':
        raise RuntimeError('Existing MCP icon differs; update stopped')
    payload = {key: provider[key] for key in ('name', 'server_identifier')}
    payload.update(provider_id=PROVIDER_ID, server_url=target, icon=icon['content'],
                   icon_type='emoji', icon_background=icon['background'])
    if api('PUT', MCP_PATH, payload).get('result') != 'success':
        raise RuntimeError('MCP URL update failed')
    if api('POST', MCP_PATH + '/auth', {'provider_id': PROVIDER_ID}).get('result') != 'success':
        raise RuntimeError('Existing MCP provider auth failed')
    remote = api('GET', MCP_PATH + '/update/' + PROVIDER_ID)
    detail = api('GET', MCP_PATH + '/tools/' + PROVIDER_ID)
    if not detail.get('is_team_authorization'):
        raise RuntimeError('MCP provider is not team authorized')
    tools = remote.get('tools', [])
    if [t['name'] for t in tools] != ['execute_sqlstatement'] or {
            p['name'] for p in tools[0]['parameters']} != {'sql_statement', 'parameters_json'}:
        raise RuntimeError('Fixed SQL MCP schema differs')
    report['mcp'] = {'provider_id': PROVIDER_ID, 'server_url': target,
                     'provider_reused': True, 'auth_verified': True, 'remote_schema_verified': True}
    endpoint = f"http://{env['MYSQL_HOST']}:9997"
    response = session.get(endpoint + '/v1/models', timeout=(5, 30))
    response.raise_for_status()
    uids = [m['id'] for m in response.json()['data']]
    report['xinference_model_uids'] = uids
    uid = 'qwen2.5-instruct'
    if uids != [uid]:
        raise RuntimeError('Expected the existing single READY Qwen model; no model was loaded or unloaded')
    query = urlencode({'model': uid, 'model_type': 'llm', 'config_from': 'custom-model'})
    existing = api('GET', MODEL_PATH + '/credentials?' + query)
    credential_id = existing.get('current_credential_id')
    stored = existing.get('credentials') or {}
    old_url = str(stored.get('server_url', '')).rstrip('/')
    if (not credential_id or old_url not in {base + ':9997' for base, _, _ in PROFILES}
            or stored.get('model_uid') != uid or stored.get('api_key')):
        raise RuntimeError('Existing Qwen credential differs from the reviewed local configuration')
    payload = {'model': uid, 'model_type': 'llm', 'credentials': {**stored, 'server_url': endpoint}}
    if api('POST', MODEL_PATH + '/credentials/validate', payload).get('result') != 'success':
        raise RuntimeError('Qwen credential validation failed')
    if old_url != endpoint:
        updated = api('PUT', MODEL_PATH + '/credentials', {**payload, 'credential_id': credential_id,
                       'name': existing['current_credential_name']})
        if updated.get('result') != 'success':
            raise RuntimeError('Qwen credential update failed')
    after = api('GET', MODEL_PATH + '/credentials?' + query)
    if (after['current_credential_id'] != credential_id
            or after['credentials']['server_url'].rstrip('/') != endpoint):
        raise RuntimeError('Qwen credential persistence mismatch')
    report['qwen'] = {'credential_id': credential_id, 'before_server_url': old_url,
                      'server_url': endpoint, 'credential_reused': True,
                      'validated': True, 'persisted': True, 'api_key_empty': True}


def probe(api, session, env, report):
    if env.get('DIFY_TEST_WORKFLOW_APP_ID') != APP_ID or env.get('DIFY_TEST_DATASET_ID') != DATASET_ID:
        raise RuntimeError('Private test resource IDs differ from the reviewed synthetic resources')
    app = api('GET', '/apps/' + APP_ID)
    if app.get('name') != APP_NAME or not app.get('enable_api'):
        raise RuntimeError('Existing synthetic app identity/API state differs')
    case = {'app_id': APP_ID, 'kind': 'published', 'input':
            '[合成换网恢复测试 ' + uuid4().hex + '] ' + KNOWN_QUESTION}
    report['case'] = case
    connection = pymysql.connect(host=env['MYSQL_HOST'], port=3306, user='root',
        password=env['MYSQL_ROOT_PASSWORD'], database='wechat', charset='utf8mb4',
        autocommit=True, connect_timeout=10, read_timeout=20)
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM chathistory WHERE user_input=%s', (case['input'],))
            if cursor.fetchone()[0] != 0:
                raise RuntimeError('Unique synthetic input already exists')
        case['marker_verified_absent'] = True
        case['run_requested'] = True
        (FOLDER / 'p04_published-probe.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        start = datetime.now().astimezone()
        response = session.post(env['DIFY_BASE_URL'] + '/v1/workflows/run',
            headers={'Authorization': 'Bearer ' + env['DIFY_TEST_WORKFLOW_API_KEY']},
            json={'inputs': {'input': case['input']}, 'response_mode': 'blocking',
                  'user': 'synthetic-network-recovery-' + uuid4().hex}, timeout=(10, 180))
        case.update(http_status=response.status_code,
                    request_elapsed_seconds=(datetime.now().astimezone()-start).total_seconds())
        if response.status_code != 200:
            raise RuntimeError('Published synthetic workflow HTTP ' + str(response.status_code))
        result = response.json()
        case.update(workflow_run_id=result['workflow_run_id'],
                    workflow_terminal=result['data']['status'] in {'succeeded', 'failed', 'stopped', 'partial-succeeded'},
                    workflow_status=result['data']['status'])
        verify_case(api, connection, APP_ID, DATASET_ID, case)
    finally:
        if not case.get('workflow_terminal') and not case.get('workflow_run_id'):
            case['cleanup_deferred'] = 'Own run identity unavailable; no shared-app run was stopped or deleted'
            connection.close()
            raise RuntimeError(case['cleanup_deferred'])
        stop_and_confirm(api, case)
        if case.get('marker_verified_absent'):
            with connection.cursor() as cursor:
                cursor.execute('DELETE FROM chathistory WHERE user_input=%s', (case['input'],))
                removed = cursor.rowcount
                cursor.execute('SELECT COUNT(*) FROM chathistory WHERE user_input=%s', (case['input'],))
                case['cleanup'] = {'rows_removed': removed, 'rows_remaining': cursor.fetchone()[0]}
        connection.close()
    if case.get('cleanup') != {'rows_removed': 1, 'rows_remaining': 0}:
        raise RuntimeError('Expected precise cleanup of one synthetic row')


def main(mode):
    env = dotenv_values(ROOT / '.env', interpolate=False)
    report = {'checked_at': datetime.now().astimezone().isoformat(), 'mode': mode, 'status': 'failed',
              'scope': 'existing synthetic project resources on a reviewed LAN profile',
              'secrets_logged': False, 'env_modified': False, 'cherry_modified': False}
    FOLDER.mkdir(parents=True, exist_ok=True)
    output = FOLDER / ('p04_' + mode + '.json')
    with requests.Session() as session:
        session.trust_env = False
        api = make_api(env['DIFY_BASE_URL'], session)
        try:
            report['network_profile'] = profile(env)
            api.token = api('POST', '/login', {'email': env['DIFY_ADMIN_EMAIL'],
                       'password': env['DIFY_ADMIN_PASSWORD']})['data']['access_token']
            configure(api, session, env, report) if mode == 'configure' else probe(api, session, env, report)
            report['status'] = 'passed'
        except Exception as error:
            report['error'] = safe_error(error, env, api.token)
        finally:
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['configure', 'published-probe', 'self-check'])
    mode = parser.parse_args().mode
    if mode == 'self-check':
        for endpoints, name in PROFILES.items():
            assert profile(dict(zip(('DIFY_BASE_URL', 'MYSQL_HOST', 'MCP_HOST'), endpoints))) == name
        for endpoints in [('http://192.168.18.176', '192.168.18.176', '192.168.110.123'),
                          ('http://127.0.0.1', '127.0.0.1', '127.0.0.1')]:
            try:
                profile(dict(zip(('DIFY_BASE_URL', 'MYSQL_HOST', 'MCP_HOST'), endpoints)))
            except RuntimeError:
                pass
            else:
                raise AssertionError('Unreviewed or mixed profile accepted')
        print('Offline LAN profile checks passed; no credential or service accessed.')
    else:
        sys.exit(main(mode))
