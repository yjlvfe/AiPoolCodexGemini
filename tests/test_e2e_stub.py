import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))
from error_taxonomy import ErrorCode, classify


class Stub(BaseHTTPRequestHandler):
    mode = 'ok'
    def log_message(self, *args):
        pass
    def do_POST(self):
        if self.mode == '429':
            self.send_response(429); self.end_headers(); return
        if self.mode == 'drop':
            self.connection.close(); return
        body = json.dumps({'ok': True}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)


class E2EProviderStubTests(unittest.TestCase):
    def test_ok_and_faults(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Stub)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = f'http://127.0.0.1:{server.server_port}/provider'
        try:
            with urlopen(Request(url, data=b'{}', method='POST'), timeout=2) as response:
                self.assertEqual(response.status, 200)
            Stub.mode = '429'
            with self.assertRaises(Exception) as ctx:
                urlopen(Request(url, data=b'{}', method='POST'), timeout=2)
            self.assertEqual(classify(status=getattr(ctx.exception, 'code', 0)).code, ErrorCode.ACCOUNT_QUOTA_EXHAUSTED)
            Stub.mode = 'drop'
            with self.assertRaises(Exception):
                urlopen(Request(url, data=b'{}', method='POST'), timeout=2)
        finally:
            Stub.mode = 'ok'; server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
