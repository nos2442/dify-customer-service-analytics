"""Temporary fixed/plain callback; read only this project's WECHAT_TOKEN."""
import os
from pathlib import Path
import runpy
import sys

from dotenv import get_key

root = Path(__file__).resolve().parents[1]
token = get_key(root / '.env', 'WECHAT_TOKEN')
if not token or len(token) != 32:
    raise SystemExit('The private WECHAT_TOKEN is missing or has an unexpected length')
os.environ['WECHAT_TOKEN'] = token
os.environ['WECHAT_REPLY_MODE'] = 'fixed'
os.environ['WECHAT_MESSAGE_MODE'] = 'plain'
os.environ.pop('WECHAT_ENCODING_AES_KEY', None)
os.environ.pop('DIFY_WORKFLOW_API_KEY', None)
service = root / 'services/wechat_public_bot.py'
sys.argv = [str(service), '--host', '127.0.0.1', '--port', '8080']
runpy.run_path(str(service), run_name='__main__')
