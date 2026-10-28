"""Signed synthetic GET/XML POST only; never submit WeChat account settings."""
import argparse
import datetime
from hashlib import sha1
import json
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from dotenv import get_key
import requests

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('base_url')
parser.add_argument('--scope', choices=['localhost', 'public'], required=True)
args = parser.parse_args()
url = urlsplit(args.base_url)
assert (url.scheme == 'http' and url.netloc == '127.0.0.1:8080') or (
    url.scheme == 'https' and url.hostname.endswith('.trycloudflare.com') and not url.username)
assert url.path in ['', '/'] and not url.query and not url.fragment
endpoint = args.base_url.rstrip('/') + '/wechat'
token = get_key(root / '.env', 'WECHAT_TOKEN')
assert token and len(token) == 32
timestamp, nonce = str(int(time.time())), secrets.token_hex(12)
signature = sha1(''.join(sorted([token, timestamp, nonce])).encode()).hexdigest()
params = {'timestamp': timestamp, 'nonce': nonce, 'signature': signature}
report = {'checked_at': datetime.datetime.now().astimezone().isoformat(), 'scope': args.scope,
          'endpoint': endpoint, 'test_data': 'synthetic only; no WeChat server or phone involved',
          'reply_mode': 'fixed', 'real_wechat_validation': False, 'secrets_logged': False,
          'workflow_mode_used': False, 'tls_verification_enabled': True}
try:
    session = requests.Session()
    session.trust_env = False
    echo = 'synthetic-echo-' + secrets.token_hex(8)
    started = time.monotonic()
    response = session.get(endpoint, params={**params, 'echostr': echo}, timeout=(10, 20))
    report['signed_get'] = {'http_status': response.status_code,
                            'elapsed_seconds': round(time.monotonic() - started, 3),
                            'echo_matches': response.text == echo}
    assert response.status_code == 200 and response.text == echo
    msg_id = str(time.time_ns())
    message = ET.Element('xml')
    for key, value in {'ToUserName': 'synthetic-public-account', 'FromUserName': 'synthetic-test-user',
                       'CreateTime': timestamp, 'MsgType': 'text',
                       'Content': '合成测试数据：fixed回调核验，中文与"引号"。', 'MsgId': msg_id}.items():
        ET.SubElement(message, key).text = value
    xml = ET.tostring(message, encoding='utf-8')
    attempts = []
    first_reply = None
    for _ in range(2):
        started = time.monotonic()
        response = session.post(endpoint, params=params, data=xml,
                                headers={'Content-Type': 'application/xml; charset=utf-8'}, timeout=(10, 20))
        reply = ET.fromstring(response.content) if response.status_code == 200 else None
        correct = reply is not None and reply.findtext('Content') == '已收到您的消息，当前正在验证公众号回调。'
        reversed_names = reply is not None and reply.findtext('ToUserName') == 'synthetic-test-user' and reply.findtext('FromUserName') == 'synthetic-public-account'
        attempts.append({'http_status': response.status_code,
                         'elapsed_seconds': round(time.monotonic() - started, 3),
                         'fixed_reply_matches': correct, 'names_reversed': reversed_names})
        assert response.status_code == 200 and correct and reversed_names
        if first_reply is None:
            first_reply = response.content
        else:
            assert response.content == first_reply, 'Repeated MsgId did not reuse the cached XML'
    with sqlite3.connect(root / 'data/private/wechat_messages.sqlite3') as db:
        rows = db.execute('SELECT generation_state,callback_attempts,workflow_run_id,insert_id FROM messages WHERE msg_id=?', (msg_id,)).fetchall()
    assert rows == [('fixed', 2, None, None)]
    report.update(status='passed', synthetic_msg_id=msg_id, xml_post_attempts=attempts,
                  deduplication={'matching_cache_rows': len(rows), 'callback_attempts': 2,
                                 'same_cached_xml': True, 'generation_state': 'fixed'},
                  no_workflow_or_mysql_insert=True, synthetic_cache_row_retained=True)
except Exception as error:
    report.update(status='failed', error_type=type(error).__name__)
folder = root / 'evidence' / datetime.datetime.now().astimezone().strftime('%Y%m%d')
folder.mkdir(parents=True, exist_ok=True)
target = folder / ('p05_wechat_' + args.scope + '_fixed.json')
target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
if report['status'] != 'passed':
    raise SystemExit(1)
