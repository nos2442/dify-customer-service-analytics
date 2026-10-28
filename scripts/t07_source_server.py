"""Serve only the explicitly public T07 acceptance page on loopback."""
import argparse
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path


def main(state_file, port):
    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/robots.txt':
                body = b'User-agent: *\nAllow: /source\n'
            elif self.path == '/source':
                state = json.loads(state_file.read_text(encoding='utf-8'))
                body = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                        '<title>T07网页更新验收专用页</title><main><h1>T07网页更新验收专用页</h1>'
                        '<p>本页仅为项目更新验收测试，不是商品政策或真实客服承诺。</p>'
                        '<h2>当前版本</h2><p>' + escape(state['version']) + '</p>'
                        '<h2>验收核对码</h2><p>' + escape(state['code']) + '</p>'
                        '<h2>演练说明</h2><p>' + escape(state['message']) + '</p>'
                        '</main></html>').encode('utf-8')
            else:
                self.send_error(404); return
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8' if self.path == '/robots.txt' else 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    ThreadingHTTPServer(('127.0.0.1', port), Page).serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-file', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8099)
    args = parser.parse_args()
    main(args.state_file, args.port)
