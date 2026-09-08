"""Explicit live smoke test: small inference, no account/model setting changes."""
import argparse
import json
import sqlite3
import urllib.request
import urllib.error
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser()
p.add_argument('provider',choices=['codex','antigravity'])
p.add_argument('model')
a=p.parse_args()
port=8124 if a.provider=='codex' else 8123
pool='Codex' if a.provider=='codex' else 'Antigravity'
path='/v1/responses' if a.provider=='codex' else '/v1/chat/completions'
with sqlite3.connect('file:'+str(ROOT/'dashboard/auth.db')+'?mode=ro',uri=True) as c:
    before=c.execute('SELECT coalesce(max(id),0) FROM request_events').fetchone()[0]
payload={'model':a.model,'stream':False}
if a.provider=='codex':payload.update(input=[{'role':'user','content':'Reply with exactly POOL_OK.'}],instructions='',store=False)
else:payload.update(messages=[{'role':'user','content':'Reply with exactly POOL_OK.'}],max_tokens=32)
request=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
try:
    with urllib.request.urlopen(request,timeout=150) as r:
        response=json.load(r);code=r.status
except urllib.error.HTTPError as e:
    response=json.loads(e.read());code=e.code
print('HTTP',code)
if code!=200:print(json.dumps(response));raise SystemExit(1)
if a.provider=='codex':
    text=''.join(p.get('text','') for x in response.get('output',[]) for p in x.get('content',[]))
else:text=response['choices'][0]['message'].get('content','')
print('Response:',text)
print('Usage:',json.dumps(response.get('usage')))
assert text.strip()=='POOL_OK', 'Upstream response differs from probe acceptance'
# The event can commit a few milliseconds after the response. Read boundedly.
import time
rows=[]
for _ in range(30):
    with sqlite3.connect('file:'+str(ROOT/'dashboard/auth.db')+'?mode=ro',uri=True) as c:
        rows=c.execute('SELECT id,model,pool,account,status,prompt_tokens,completion_tokens FROM request_events WHERE id>? AND pool=? AND model=? ORDER BY id DESC',(before,pool,a.model)).fetchall()
    if rows:break
    time.sleep(.1)
assert rows,'Live request did not enter the shared database'
print('Verified real request event:',json.dumps(rows[0]))
assert rows[0][4]=='OK',rows[0]
