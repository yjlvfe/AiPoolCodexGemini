"""Real local HTTP servers, synthetic credentials. Not live provider/OAuth evidence."""
import base64
import importlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'bridges'),str(ROOT/'cli')]


def jwt(data):
    return 'test.'+base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip('=')+'.test'


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='aipool-bridge-',dir=ROOT/'tests')
        self.home=Path(self.temp.name)
        self.env=patch.dict(os.environ,{'AIPOOL_CONFIG_ENV':os.devnull,'HOME':str(self.home),'CODEX_ACCOUNT_STORE':str(self.home/'codex'),'CODEX_HOME':str(self.home/'live-codex'),'AG_ACCOUNT_STORE':str(self.home/'ag'),'AG_SESSION_DIR':str(self.home/'live-ag'),'AUTH_DB_PATH':str(self.home/'logs.db')})
        self.env.start()
        self.calls=[]
        self.mode='rotate'
        self.servers=[]
        owner=self
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                token=self.headers.get('Authorization')
                owner.calls.append((self.path,token,body))
                code=404 if self.path=='/missing' else 429 if owner.mode=='all' or owner.mode=='rotate' and token=='Bearer account-one' else 400 if owner.mode=='bad' else 200
                self.send_response(code)
                self.send_header('Retry-After','60')
                self.end_headers()
                if code!=200:
                    self.wfile.write(b'{"error":{"message":"fixture quota"}}');return
                if self.path=='/ag':
                    out={'response':{'candidates':[{'content':{'parts':[{'text':'local fixture response'}]},'finishReason':'STOP'}]}}
                    if owner.mode!='unknown': out['response']['usageMetadata']={'promptTokenCount':7,'candidatesTokenCount':3,'totalTokenCount':10}
                    if owner.mode=='thoughts': out['response']['usageMetadata'].update(thoughtsTokenCount=5,totalTokenCount=15)
                    self.wfile.write(json.dumps(out).encode())
                else:
                    if owner.mode in ('ssequota','preludequota') and token=='Bearer account-one':
                        if owner.mode=='preludequota': self.wfile.write(b'data: {"type":"response.created"}\n\ndata: {"type":"response.in_progress"}\n\n')
                        self.wfile.write(b'data: {"type":"response.failed","response":{"error":{"code":"usage_limit_reached"}}}\n\n');return
                    out={'id':'resp_fixture','object':'response','model':body['model'],'output':[{'type':'message','content':[{'type':'output_text','text':'local fixture response'}]}]}
                    if owner.mode!='unknown': out['usage']={'input_tokens':7,'output_tokens':3,'total_tokens':10}
                    events=[{'type':'response.created','response':{'id':'resp_fixture'}},{'type':'response.output_text.delta','delta':'local fixture response'},{'type':'response.completed','response':out}]
                    if owner.mode=='sparse_completed':
                        events.insert(-1,{'type':'response.output_item.done','output_index':0,'item':out['output'][0]})
                        events[-1]={'type':'response.completed','response':dict(out,output=[])}
                    if owner.mode=='truncated': events=events[:-1]
                    for event in events:self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode())
        upstream=self.start(Upstream)
        self.urls=patch.dict(os.environ,{'CODEX_UPSTREAM_URL':upstream+'/codex','AG_UPSTREAM_URL':upstream+'/ag'})
        self.urls.start()
        for provider in ('codex','ag'):
            store=self.home/provider
            store.mkdir()
            (store/'active').write_text('1\n')
            for n,token in [('1','account-one'),('2','account-two')]:
                d=store/n;d.mkdir()
                if provider=='codex':
                    data={'tokens':{'access_token':token,'refresh_token':'fixture-refresh-'+n,'account_id':'fixture-'+n,'id_token':jwt({'email':n+'@example.test','https://api.openai.com/auth':{'chatgpt_account_id':'fixture-'+n}})}}
                    filename='auth.json'
                else:
                    data={'email':n+'@example.test','project_id':'project-'+n,'token':{'access_token':token,'refresh_token':'fixture-refresh-'+n,'expiry_date':int((time.time()+3600)*1000)}}
                    filename='antigravity-oauth-token'
                (d/filename).write_text(json.dumps(data))
        self.codex=importlib.reload(importlib.import_module('codex_bridge'))
        self.ag=importlib.reload(importlib.import_module('gemini_bridge'))
        self.codex_url=self.start(self.codex.CodexHandler)
        self.ag_url=self.start(self.ag.Handler)

    def start(self,handler):
        s=ThreadingHTTPServer(('127.0.0.1',0),handler)
        threading.Thread(target=s.serve_forever,daemon=True).start()
        self.servers.append(s)
        return 'http://127.0.0.1:'+str(s.server_port)

    def tearDown(self):
        for s in self.servers:s.shutdown();s.server_close()
        self.urls.stop();self.env.stop();self.temp.cleanup()

    def request(self,base,model,stream=False,path='/v1/chat/completions'):
        payload={'model':model,'messages':[{'role':'user','content':'fixture'}],'stream':stream}
        if path.endswith('responses'):payload={'model':model,'input':'fixture','stream':stream}
        req=urllib.request.Request(base+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=20) as r:return r.status,r.read().decode()
        except urllib.error.HTTPError as e:return e.code,e.read().decode()

    def test_reasoning_tokens_are_included_in_observed_output(self):
        self.mode='thoughts'
        code,body=self.request(self.ag_url,'gemini-3.8-flash')
        self.assertEqual(code,200,body)
        usage=json.loads(body)['usage']
        self.assertEqual(usage['completion_tokens'],8)
        import pool_runtime
        row=pool_runtime.report(self.home/'logs.db')['recent_requests'][0]
        self.assertEqual((row['prompt'],row['completion'],row['tokens']),(7,8,15))

    def test_both_pools_rotate_same_model_and_share_logs(self):
        for base,model,provider in [(self.codex_url,'gpt-6-astra','codex'),(self.ag_url,'gemini-3.8-flash','ag')]:
            code,body=self.request(base,model)
            self.assertEqual(code,200,body)
            self.assertIn('local fixture response',body)
            self.assertEqual((self.home/provider/'active').read_text().strip(),'2')
            self.assertEqual([c[2]['model'] for c in self.calls[-2:]],[model,model])
            self.assertEqual([c[1] for c in self.calls[-2:]],['Bearer account-one','Bearer account-two'])
        import pool_runtime
        result=pool_runtime.report(self.home/'logs.db')
        self.assertEqual(sum(x['prompt_tokens'] for p in result['providers'].values() for x in p['models'].values()),14)
        self.assertEqual(sum(x['completion_tokens'] for p in result['providers'].values() for x in p['models'].values()),6)
        self.assertEqual({r['pool'] for r in result['recent_requests']},{'Codex','Antigravity'})
        self.assertEqual(self.calls[-1][2]['project'],'project-2')

    def test_all_exhausted_is_bounded_error_even_with_stream(self):
        self.mode='all'
        for base,model,store in [(self.codex_url,'gpt-6-astra','codex'),(self.ag_url,'gemini-3.8-flash','ag')]:
            before=len(self.calls)
            code,body=self.request(base,model,stream=True)
            self.assertEqual(code,429,body)
            self.assertEqual(len(self.calls)-before,2)
            self.assertEqual((self.home/store/'active').read_text().strip(),'1')
            code,body=self.request(base,model)
            self.assertEqual(code,429,body)
            self.assertEqual(len(self.calls)-before,2)

    def test_invalid_request_does_not_rotate(self):
        self.mode='bad'
        for base,model in [(self.codex_url,'gpt-6-astra'),(self.ag_url,'gemini-3.8-flash')]:
            before=len(self.calls)
            code,body=self.request(base,model)
            self.assertEqual(code,400,body)
            self.assertEqual(len(self.calls)-before,1)

    def test_responses_stream_preserves_events_and_sse_quota_rotates(self):
        self.mode='ssequota'
        code,body=self.request(self.codex_url,'gpt-6-astra',True,'/v1/responses')
        self.assertEqual(code,200,body)
        self.assertIn('response.completed',body)
        self.assertNotIn('response.failed',body)
        self.assertEqual(len(self.calls),2)
        self.assertTrue(all(c[2]['store'] is False and c[2]['stream'] is True for c in self.calls))

    def test_quota_after_lifecycle_events_still_rotates(self):
        self.mode='preludequota'
        code,body=self.request(self.codex_url,'gpt-6-astra',True,'/v1/responses')
        self.assertEqual(code,200,body)
        self.assertIn('response.completed',body)
        self.assertNotIn('stream failed',body)
        self.assertEqual(len(self.calls),2)

    def test_ag_missing_endpoint_falls_through_before_rotating(self):
        url=os.environ.pop('AG_UPSTREAM_URL')
        try:
            base=url.rsplit('/',1)[0]
            with patch.object(self.ag,'ANTIGRAVITY_ENDPOINTS',[base+'/missing',base+'/ag']), patch.object(self.ag,'ANTIGRAVITY_PATH',''):
                code,body=self.request(self.ag_url,'gemini-3.8-flash')
            self.assertEqual(code,200,body)
            self.assertEqual((self.home/'ag/active').read_text().strip(),'2')
        finally:
            os.environ['AG_UPSTREAM_URL']=url

    def test_completed_empty_output_reconstructs_observed_items(self):
        self.mode='sparse_completed'
        for path in ('/v1/responses','/v1/chat/completions'):
            code,body=self.request(self.codex_url,'gpt-6-astra',False,path)
            self.assertEqual(code,200,body)
            self.assertIn('local fixture response',body)

    def test_missing_usage_is_unknown_not_zero(self):
        self.mode='unknown'
        for base,model in [(self.codex_url,'gpt-6-astra'),(self.ag_url,'gemini-3.8-flash')]:
            self.assertEqual(self.request(base,model)[0],200)
        import pool_runtime
        rows=pool_runtime.report(self.home/'logs.db')['recent_requests']
        self.assertEqual(len(rows),2)
        self.assertTrue(all(r['prompt'] is None and r['completion'] is None for r in rows),rows)

    def test_truncated_codex_response_is_not_success(self):
        self.mode='truncated'
        code,body=self.request(self.codex_url,'gpt-6-astra',False,'/v1/responses')
        self.assertEqual(code,502,body)
        self.assertIn('without response.completed',body)
        self.assertEqual(len(self.calls),1)


if __name__=='__main__':unittest.main()
