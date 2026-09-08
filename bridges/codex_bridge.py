"""Codex Responses gateway with bounded same-model account failover."""
import http.server
import json
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"cli"))
from config_env import load
load()
import socketserver
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pool_runtime import AccountPool, PoolError, record, retry_seconds

HOST = os.environ.get('CODEX_BRIDGE_HOST','127.0.0.1')
PORT = int(os.environ.get('CODEX_BRIDGE_PORT','8124'))
UPSTREAM_URL = os.environ.get('CODEX_UPSTREAM_URL','https://chatgpt.com/backend-api/codex/responses')
POOL = AccountPool('codex')
MODELS = [
    'gpt-6-astra',
    'gpt-5.6-luna',
    'gpt-5.6-sol',
    'gpt-5.6-terra',
    'gpt-5.5',
    'gpt-5.4-mini',
]


def response_request(payload, chat=False):
    model = payload.get('model')
    if not isinstance(model,str) or not model or model != model.strip():
        raise PoolError('An exact model ID is required',400)
    out = dict(payload)
    if chat:
        out.pop('messages',None)
        messages=[]
        instructions=[]
        for msg in payload.get('messages',[]):
            role=msg.get('role')
            content=msg.get('content')
            if role in ('system','developer'):
                if not isinstance(content,str):
                    raise PoolError('System messages must be text',400)
                instructions.append(content)
            elif role=='tool':
                messages.append({'type':'function_call_output','call_id':msg['tool_call_id'],'output':content or ''})
            else:
                if content:
                    if isinstance(content,list):
                        parts=[]
                        for part in content:
                            if part.get('type')=='text':
                                parts.append({'type':'output_text' if role=='assistant' else 'input_text','text':part['text']})
                            elif part.get('type')=='image_url':
                                parts.append({'type':'input_image','image_url':part['image_url']['url']})
                            else:
                                raise PoolError('Unsupported message content type',400)
                        content=parts
                    messages.append({'role':role,'content':content})
                for tool in msg.get('tool_calls') or []:
                    f=tool['function']
                    messages.append({'type':'function_call','call_id':tool['id'],'name':f['name'],'arguments':f['arguments']})
        out['instructions']='\n\n'.join(instructions)
        out['input']=messages
        if payload.get('tools'):
            out['tools']=[{'type':'function',**t['function']} if t.get('type')=='function' else t for t in payload['tools']]
        if isinstance(payload.get('tool_choice'),dict) and payload['tool_choice'].get('type')=='function':
            out['tool_choice']={'type':'function','name':payload['tool_choice']['function']['name']}
        for key in ('max_tokens','max_completion_tokens','stream_options','temperature','top_p','frequency_penalty','presence_penalty','n'):
            out.pop(key,None)
    out.setdefault('instructions','')
    out.update(store=False,stream=True)
    return out


def events(response):
    buffer=[]
    while True:
        line=response.readline()
        if not line:
            if buffer:
                yield b''.join(buffer)
            return
        buffer.append(line)
        if line in (b'\n',b'\r\n'):
            yield b''.join(buffer)
            buffer=[]


def event_data(raw):
    data=b'\n'.join(line[5:].strip() for line in raw.splitlines() if line.startswith(b'data:'))
    if not data or data==b'[DONE]':
        return {}
    try:
        value=json.loads(data)
        return value if isinstance(value,dict) else {}
    except ValueError:
        return {}


def failed(event):
    return event.get('type') in ('error','response.failed','response.incomplete')


def rotate_event(event):
    text=json.dumps(event).lower()
    return failed(event) and any(x in text for x in ('usage_limit','rate_limit','quota','exhausted','invalid_api_key','unauthorized'))


def chat_result(response,model):
    text=[]
    tools=[]
    for item in response.get('output',[]):
        if item.get('type')=='message':
            text.extend(p.get('text','') for p in item.get('content',[]) if p.get('type')=='output_text')
        elif item.get('type')=='function_call':
            tools.append({'id':item.get('call_id',item.get('id')),'type':'function','function':{'name':item.get('name'),'arguments':item.get('arguments','')}})
    msg={'role':'assistant','content':''.join(text) or None}
    if tools:
        msg['tool_calls']=tools
    out={'id':response.get('id','chatcmpl-'+uuid.uuid4().hex),'object':'chat.completion','created':int(time.time()),'model':model,'choices':[{'index':0,'message':msg,'finish_reason':'tool_calls' if tools else 'stop'}]}
    usage=response.get('usage')
    if usage:
        out['usage']={'prompt_tokens':usage.get('input_tokens'),'completion_tokens':usage.get('output_tokens'),'total_tokens':usage.get('total_tokens')}
    return out


class CodexHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def send_json(self,data,status=200):
        body=json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path=urllib.parse.urlsplit(self.path).path
        if path in ('/v1/models','/models'):
            self.send_json({'object':'list','data':[{'id':m,'object':'model','owned_by':'codex'} for m in MODELS]})
        elif path in ('/','/health','/healthz'):
            self.send_json({'status':'ok'})
        else:
            self.send_json({'error':'Not found'},404)

    def do_POST(self):
        model=None
        number=None
        usage=None
        started=False
        outcome='FAILED'
        try:
            path=urllib.parse.urlsplit(self.path).path
            if path not in ('/v1/responses','/responses','/v1/chat/completions','/chat/completions'):
                raise PoolError('Unsupported endpoint',404)
            length=int(self.headers.get('Content-Length','0'))
            if length<=0 or length>32*1024*1024:
                raise PoolError('Invalid request size',400)
            payload=json.loads(self.rfile.read(length))
            chat=path.endswith('/chat/completions')
            body=response_request(payload,chat)
            model=body['model']
            wants_stream=bool(payload.get('stream'))
            attempts=POOL.candidates(model)
            last_status=429 if not attempts else 503
            for number in attempts:
                try:
                    credentials=POOL.credentials(number)['tokens']
                    headers={'Authorization':'Bearer '+credentials['access_token'],'ChatGPT-Account-Id':credentials.get('account_id',''),'Content-Type':'application/json','Accept':'text/event-stream','User-Agent':'codex_cli_rs/0.1.0'}
                    req=urllib.request.Request(UPSTREAM_URL,data=json.dumps(body).encode(),headers=headers)
                    print(f"[codex-bridge] attempt account={number} model={model}", flush=True)
                    upstream=urllib.request.urlopen(req,timeout=120)
                except urllib.error.HTTPError as exc:
                    # Authentication/quota errors may belong to one account; invalid payloads do not.
                    last_status=exc.code
                    if exc.code in (401,403,429):
                        print(f"[codex-bridge] account {number} exhausted: HTTP {exc.code}", flush=True)
                        POOL.exhausted(number,model,retry_seconds(exc.headers))
                        exc.close()
                        continue
                    exc.close()
                    raise PoolError(f'Codex upstream HTTP {last_status}',last_status) from None
                except (PoolError, ValueError, OSError) as exc:
                    last_status=getattr(exc,'status',503)
                    POOL.exhausted(number,model,30)
                    continue
                with upstream:
                    iterator=events(upstream)
                    # Lifecycle events are not generated content. Buffer them until a
                    # real output event so quota errors following `created` can rotate.
                    prelude=[]
                    first=b''
                    for raw in iterator:
                        first=raw
                        first_event=event_data(raw)
                        if first_event.get('type') not in (None,'response.created','response.in_progress','ping'):
                            break
                        prelude.append(raw)
                        if len(prelude)>64 or sum(map(len,prelude))>262144:
                            raise PoolError('Codex sent excessive lifecycle events without output',502)
                    else:
                        first=b''
                    first_event=event_data(first)
                    if rotate_event(first_event):
                        print(f"[codex-bridge] account {number} exhausted: stream {first_event.get('type')}", flush=True)
                        POOL.exhausted(number,model)
                        last_status=429
                        continue
                    if not first or failed(first_event):
                        raise PoolError('Codex upstream rejected the request',502)
                    if wants_stream:
                        self._stream_started = True
                        self.send_response(200)
                        self.send_header('Content-Type','text/event-stream')
                        self.send_header('Cache-Control','no-cache')
                        self.send_header('Connection','close')
                        self.end_headers()
                        started=True
                    import itertools
                    completed=None
                    observed_items={}
                    tool_indexes={}
                    for raw in itertools.chain(prelude,[first],iterator):
                        event=event_data(raw)
                        if failed(event):
                            raise PoolError('Codex stream failed after it started; request was not replayed',502)
                        if event.get('type')=='response.output_item.done' and isinstance(event.get('item'),dict):
                            observed_items[event.get('output_index',len(observed_items))]=event['item']
                        if event.get('type')=='response.completed':
                            completed=event.get('response') or {}
                            if not completed.get('output') and observed_items:
                                completed=dict(completed,output=[observed_items[k] for k in sorted(observed_items)])
                            usage=completed.get('usage')
                        if wants_stream:
                            if not chat:
                                self.wfile.write(raw)
                            else:
                                delta=None
                                typ=event.get('type')
                                if typ=='response.output_text.delta':
                                    delta={'content':event.get('delta','')}
                                elif typ=='response.output_item.added' and event.get('item',{}).get('type')=='function_call':
                                    item=event['item']; index=len(tool_indexes); tool_indexes[event.get('output_index')]=index
                                    delta={'tool_calls':[{'index':index,'id':item.get('call_id',item.get('id')),'type':'function','function':{'name':item.get('name'),'arguments':''}}]}
                                elif typ=='response.function_call_arguments.delta':
                                    delta={'tool_calls':[{'index':tool_indexes.get(event.get('output_index'),0),'function':{'arguments':event.get('delta','')}}]}
                                if delta is not None:
                                    chunk={'id':'chatcmpl-stream','object':'chat.completion.chunk','created':int(time.time()),'model':model,'choices':[{'index':0,'delta':delta,'finish_reason':None}]}
                                    self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode())
                            self.wfile.flush()
                    if completed is None:
                        raise PoolError('Codex stream ended without response.completed',502)
                    outcome='OK'
                    try:
                        POOL.promote(number)
                    except Exception:
                        # Delivered response remains successful; surface pointer-sync failure separately.
                        print('[codex-bridge] Response delivered but active-account synchronization failed',file=sys.stderr)
                        outcome='OK_ACTIVE_SYNC_FAILED'
                    if wants_stream and chat:
                        finish=chat_result(completed,model)
                        chunk={'id':finish['id'],'object':'chat.completion.chunk','model':model,'choices':[{'index':0,'delta':{},'finish_reason':finish['choices'][0]['finish_reason']}], 'usage':finish.get('usage')}
                        self.wfile.write(('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode())
                    elif not wants_stream:
                        self.send_json(chat_result(completed,model) if chat else completed)
                    return
            raise PoolError('No usable Codex account for this model; account quota/authentication checks failed',last_status)
        except (PoolError,ValueError,KeyError,OSError) as exc:
            if started:
                try:
                    self.wfile.write(('event: error\ndata: '+json.dumps({'error':{'message':str(exc)}})+'\n\n').encode())
                except OSError:
                    pass
            else:
                self.send_json({'error':{'message':str(exc),'type':'codex_pool'}},getattr(exc,'status',400 if isinstance(exc,(ValueError,KeyError)) else 502))
        finally:
            if model:
                record('Codex',model,usage,number,outcome)


class ThreadingHTTPServer(socketserver.ThreadingMixIn,http.server.HTTPServer):
    daemon_threads=True
    allow_reuse_address=True


def main():
    server=ThreadingHTTPServer((HOST,PORT),CodexHandler)
    print(f'[codex-bridge] Listening on http://{HOST}:{PORT}',flush=True)
    server.serve_forever()


if __name__=='__main__':
    main()
