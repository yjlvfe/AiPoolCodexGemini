import sys,unittest,json,tempfile,threading,os,hashlib,urllib.request,io
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'bridges'))
from client_identity import authorize
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_POST(self):
  if not authorize(self,'Codex'):return
  data=json.dumps(self._client_identity).encode();self.send_response(200);self.end_headers();self.wfile.write(data)
class HTTPTests(unittest.TestCase):
 def make_registry(self,d):
  p=Path(d)/'registry.json';p.write_text(json.dumps({'mode':'enforce','clients':[{'name':'Synthetic Hermes','id':'fixture','enabled':True,'sha256':hashlib.sha256(b'test-only-secret').hexdigest()}]}));return p
 def test_loopback_trust_and_verified(self):
  with tempfile.TemporaryDirectory() as d:
   with patch.dict(os.environ,{'AIPOOL_CLIENT_REGISTRY':str(self.make_registry(d)),'AUTH_DB_PATH':d+'/audit.db'}):
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
     url='http://127.0.0.1:'+str(server.server_port)+'/v1/responses'
     # Loopback clients are trusted even without credentials.
     for headers in ({},{'X-AI-Client':'Hermes'},{'Authorization':'Bearer incorrect'}):
      with urllib.request.urlopen(urllib.request.Request(url,data=b'{}',headers=headers)) as resp:
       body=json.load(resp);self.assertTrue(body['allowed']);self.assertFalse(body['identity_verified'])
     with urllib.request.urlopen(urllib.request.Request(url,data=b'{}',headers={'Authorization':'Bearer test-only-secret'})) as resp:
      body=json.load(resp);self.assertTrue(body['identity_verified']);self.assertTrue(body['allowed'])
    finally:server.shutdown();server.server_close();thread.join()
 def test_remote_denied(self):
  class Remote:
   client_address=('203.0.113.9',443);headers={};_client_identity=None;path='/v1/responses'
   def __init__(self):self.wfile=io.BytesIO();self.code=None
   def send_response(self,c):self.code=c
   def send_header(self,*a,**k):pass
   def end_headers(self):pass
  with tempfile.TemporaryDirectory() as d:
   with patch.dict(os.environ,{'AIPOOL_CLIENT_REGISTRY':str(self.make_registry(d)),'AUTH_DB_PATH':d+'/audit.db'}):
    h=Remote();self.assertFalse(authorize(h,'Codex'));self.assertEqual(h.code,401)
    import sqlite3
    with sqlite3.connect(d+'/audit.db') as c:self.assertEqual(c.execute("select count(*) from request_events where status='UNAUTHORIZED'").fetchone()[0],1)
if __name__=='__main__':unittest.main()
