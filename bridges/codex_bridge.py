"""Codex Responses gateway with bounded same-model account failover."""
import http.server
import json
import os
import socket
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"cli"))
from config_env import load
load()
import socketserver
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pool_runtime import AccountPool, PoolError, record, retry_seconds, hard_account_failure
from account_manager import Manager
from retry_policy import provider_attempts, retry_delay, transient_event, transient_exception, transient_status
from model_catalog import CatalogError, DynamicCatalog, catalog_cache_dir, discover_codex_models

HOST = os.environ.get('CODEX_BRIDGE_HOST','127.0.0.1')
PORT = int(os.environ.get('CODEX_BRIDGE_PORT','8124'))
UPSTREAM_URL = os.environ.get('CODEX_UPSTREAM_URL','https://chatgpt.com/backend-api/codex/responses')
REQUEST_BODY_TIMEOUT = 30.0
DEFAULT_REQUEST_TOTAL_DEADLINE = 180.0


def request_total_deadline():
    try:
        value = float(os.environ.get('AIPOOL_REQUEST_TOTAL_DEADLINE', DEFAULT_REQUEST_TOTAL_DEADLINE))
    except (TypeError, ValueError):
        value = DEFAULT_REQUEST_TOTAL_DEADLINE
    return max(0.1, value)


POOL = AccountPool('codex')
def _fetch_codex_catalog():
    """Read the official Codex client caches for every configured account."""
    return discover_codex_models()


_codex_catalog = DynamicCatalog(
    'codex',
    _fetch_codex_catalog,
    catalog_cache_dir() / 'codex.json',
)


# Model IDs are caller-owned.  Keep this name as a compatibility surface,
# but do not translate one requested model into another model implicitly.
CODEX_MODEL_ALIASES = {}


def list_available_models(force_refresh=True):
    models = _codex_catalog.get(force=force_refresh)
    # Order models newest to oldest
    ORDER_PREFERENCE = [
        "gpt-6-astra",
        "gpt-5.6-terra",
        "gpt-5.6-sol",
        "gpt-5.6-luna",
        "gpt-5.5",
    ]
    sorted_models = []
    for pref in ORDER_PREFERENCE:
        if pref in models and pref not in sorted_models:
            sorted_models.append(pref)
    for m in models:
        if m not in sorted_models:
            sorted_models.append(m)
    return [
        {'id': model, 'object': 'model', 'owned_by': 'codex'}
        for model in sorted_models
    ]


def response_request(payload, chat=False):
    from canonical_request import CanonicalRequest
    try:
        payload = CanonicalRequest.from_payload(payload).to_payload()
    except ValueError as exc:
        raise PoolError(str(exc), 400) from None
    model = payload.get('model')
    if not isinstance(model,str) or not model or model != model.strip():
        raise PoolError('An exact model ID is required',400)
    # Automatically map common model names / aliases (e.g. gpt-5, gpt-4o) to valid upstream Codex models
    model = CODEX_MODEL_ALIASES.get(model, model)
    out = dict(payload)
    out['model'] = model
    if isinstance(out.get('instructions'), list):
        out['instructions'] = '\n\n'.join(str(item) for item in out['instructions'])
    # Codex supports the full native range low..ultra.
    # Only aliases below the provider floor are raised to low.
    effort = str(payload.get('reasoning_effort') or '').lower()
    if effort in ('none', 'minimal'):
        effort = 'low'
    if effort in ('low', 'medium', 'high', 'xhigh', 'max', 'ultra'):
        out['reasoning'] = {'effort': effort}
    out.pop('reasoning_effort', None)
    if chat:
        out.pop('messages',None)
        messages=[]
        instructions=[out['instructions']] if out.get('instructions') is not None else []
        for msg in payload.get('messages',[]):
            role=msg.get('role')
            content=msg.get('content')
            if role in ('system','developer'):
                if not isinstance(content,str):
                    raise PoolError('System messages must be text',400)
                instructions.append(content)
            elif role=='tool':
                call_id=msg.get('tool_call_id')
                if not isinstance(call_id,str) or not call_id:
                    raise PoolError('Tool messages require tool_call_id',400)
                messages.append({'type':'function_call_output','call_id':call_id,'output':content if isinstance(content,str) else json.dumps(content or '',ensure_ascii=False)})
            else:
                if content is not None:
                    if isinstance(content,list):
                        parts=[]
                        for part in content:
                            if not isinstance(part,dict):
                                raise PoolError('Message content parts must be objects',400)
                            if part.get('type')=='text':
                                if not isinstance(part.get('text'),str):
                                    raise PoolError('Text content parts require text',400)
                                parts.append({'type':'output_text' if role=='assistant' else 'input_text','text':part['text']})
                            elif part.get('type')=='image_url':
                                image=part.get('image_url')
                                if not isinstance(image,dict) or not isinstance(image.get('url'),str) or not image['url']:
                                    raise PoolError('Image content parts require image_url.url',400)
                                parts.append({'type':'input_image','image_url':image['url']})
                            else:
                                raise PoolError('Unsupported message content type',400)
                        content=parts
                    messages.append({'role':role,'content':content})
                for tool in msg.get('tool_calls') or []:
                    if not isinstance(tool,dict):
                        raise PoolError('Each tool_call must be an object',400)
                    f=tool.get('function') or {}
                    if not tool.get('id') or not isinstance(f.get('name'),str) or not f.get('name'):
                        raise PoolError('Tool calls require id and function.name',400)
                    args=f.get('arguments')
                    if isinstance(args,(dict,list)):
                        args=json.dumps(args,ensure_ascii=False)
                    messages.append({'type':'function_call','call_id':tool['id'],'name':f['name'],'arguments':args if isinstance(args,str) else ''})
        out['instructions']='\n\n'.join(instructions)
        out['input']=messages
        if payload.get('tools'):
            for tool in payload['tools']:
                if not isinstance(tool, dict) or (tool.get('type') == 'function' and (
                    not isinstance(tool.get('function'), dict)
                    or not isinstance(tool['function'].get('name'), str)
                    or not tool['function']['name']
                )):
                    raise PoolError('Function tools require function.name', 400)
            out['tools']=[{'type':'function',**t['function']} if t.get('type')=='function' else t for t in payload['tools']]
        if isinstance(payload.get('tool_choice'),dict) and payload['tool_choice'].get('type')=='function':
            choice_function = payload['tool_choice'].get('function')
            if not isinstance(choice_function, dict) or not isinstance(choice_function.get('name'), str) or not choice_function['name']:
                raise PoolError('Function tool_choice requires function.name', 400)
            out['tool_choice']={'type':'function','name':choice_function['name']}
        for key in ('max_tokens','max_completion_tokens','stream_options','temperature','top_p','frequency_penalty','presence_penalty','n'):
            out.pop(key,None)
    else:
        # CanonicalRequest supplies an empty messages list for native
        # Responses payloads that use `input`; the upstream schema rejects the
        # foreign field even when it is empty.
        out.pop('messages', None)
    out.setdefault('instructions','')
    out.update(store=False,stream=True)
    return out


CODEX_CHUNK_IDLE_TIMEOUT = float(os.environ.get('AIPOOL_CODEX_CHUNK_IDLE_TIMEOUT', '30'))


def events(response, idle_timeout=CODEX_CHUNK_IDLE_TIMEOUT):
    """Yield SSE frames while enforcing a deadline between upstream chunks."""
    buffer=[]
    sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
    previous_timeout = None
    if sock is not None:
        try:
            previous_timeout = sock.gettimeout()
            sock.settimeout(idle_timeout)
        except (AttributeError, OSError):
            sock = None
    try:
        while True:
            try:
                line=response.readline()
            except (socket.timeout, TimeoutError) as exc:
                raise TimeoutError(f'Codex upstream idle for {idle_timeout:g}s') from exc
            if not line:
                if buffer:
                    yield b''.join(buffer)
                return
            buffer.append(line)
            if line in (b'\n',b'\r\n'):
                yield b''.join(buffer)
                buffer=[]
    finally:
        if sock is not None:
            try:
                sock.settimeout(previous_timeout)
            except OSError:
                pass


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
    if not failed(event):
        return False
    status = event.get('status') or event.get('status_code') or 429
    return hard_account_failure(status, json.dumps(event))


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
        try:
            self.send_response(status)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
            print('[codex-bridge] client disconnected before/during response', file=sys.stderr)

    def do_GET(self):
        from client_identity import authorize, is_loopback
        forwarded = self.headers.get('X-Real-IP') or self.headers.get('X-Forwarded-For', '').split(',')[0].strip()
        peer = forwarded if forwarded else self.client_address[0]
        if not is_loopback(peer) and not authorize(self, 'Codex'):
            return
        path=urllib.parse.urlsplit(self.path).path
        if path in ('/v1/models','/models'):
            self.send_json({'object':'list','data':list_available_models(force_refresh=True)})
        elif path in ('/','/health','/healthz'):
            self.send_json({'status':'ok'})
        else:
            self.send_json({'error':'Not found'},404)

    def do_POST(self):
        from client_identity import authorize
        if not authorize(self, 'Codex'):
            return
        from request_audit import attach_provider_payload, attach_response, capture
        audit = None
        model=None
        number=None
        usage=None
        started=False
        outcome='FAILED'
        from stream_state import StreamLifecycle
        lifecycle = StreamLifecycle()
        request_started_at = time.perf_counter()
        deadline = request_started_at + request_total_deadline()
        completed = None
        request_id = str(uuid.uuid4())
        attempted_accounts = []
        try:
            request_started_at = time.perf_counter()
            path=urllib.parse.urlsplit(self.path).path
            if path not in ('/v1/responses','/responses','/v1/chat/completions','/chat/completions'):
                raise PoolError('Unsupported endpoint',404)
            length=int(self.headers.get('Content-Length','0'))
            if length<=0 or length>32*1024*1024:
                raise PoolError('Invalid request size',400)
            try:
                self.connection.settimeout(REQUEST_BODY_TIMEOUT)
                raw_body = self.rfile.read(length)
            except (socket.timeout, TimeoutError):
                self.close_connection = True
                raise PoolError('Request body read timed out', 408) from None
            finally:
                try:
                    self.connection.settimeout(None)
                except OSError:
                    pass
            raw_payload=json.loads(raw_body)
            if not isinstance(raw_payload, dict):
                raise PoolError('Request body must be a JSON object', 400)
            payload = raw_payload
            audit=capture(self,raw_payload)
            audit['wire_input_bytes'] = length
            chat=path.endswith('/chat/completions')
            body=response_request(payload,chat)
            # The adapter may add/reshape fields; record the exact provider body,
            # not merely the client protocol representation.
            attach_provider_payload(audit, body)
            audit['wire_provider_bytes'] = len(json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
            audit['wire_tokens_estimate'] = max(1, (audit['wire_provider_bytes'] + 3) // 4)
            audit['latency_ms'] = round((time.perf_counter() - request_started_at) * 1000, 2)
            model=body['model']
            self._requested_model = model

            # Validate client model permissions
            client_identity = getattr(self, '_client_identity', {})
            client_obj = client_identity.get('client_obj')
            if client_obj and client_obj.get('allowed_models'):
                clean_req = str(model).lower()
                clean_allowed = [str(m).lower() for m in client_obj['allowed_models']]
                if clean_req not in clean_allowed:
                    raise PoolError(f"Model {model} is not permitted for this API Key", 403)
            wants_stream=bool(payload.get('stream'))
            attempts=POOL.candidates(model, include_cooldown=False) or POOL.candidates(model, include_cooldown=True)
            last_status=429 if not attempts else 503
            quota_exhausted=set()
            next_index=0
            try:
                active_at_start = Manager('codex').active()
            except (ValueError, OSError):
                active_at_start = ''
            rotated_after_hard_failure = False
            total_attempts=provider_attempts(os.environ.get('AIPOOL_CODEX_MAX_ATTEMPTS'))
            attempted_accounts = []

            for candidate in list(attempts):
                if candidate in attempted_accounts:
                    continue
                if time.perf_counter() >= deadline:
                    raise PoolError('Request deadline exceeded', 504)

                number = candidate
                attempted_accounts.append(number)
                try:
                    credentials=POOL.credentials(number)['tokens']
                    headers={'Authorization':'Bearer '+credentials['access_token'],'ChatGPT-Account-Id':credentials.get('account_id',''),'Content-Type':'application/json','Accept':'text/event-stream','User-Agent':'codex_cli_rs/0.1.0'}
                    req=urllib.request.Request(UPSTREAM_URL,data=json.dumps(body).encode(),headers=headers)
                    print(f"[codex-bridge] attempt account={number} model={model}", flush=True)
                    upstream=urllib.request.urlopen(req,timeout=max(0.1, min(120, deadline - time.perf_counter())))
                except urllib.error.HTTPError as exc:
                    last_status=exc.code
                    raw_err = exc.read().decode(errors='replace') if hasattr(exc, 'read') else ''
                    retry_sec = retry_seconds(exc.headers)
                    resets_at = None
                    resets_in_seconds = None
                    try:
                        err_json = json.loads(raw_err)
                        if isinstance(err_json, dict):
                            err_details = err_json.get('error', err_json) if isinstance(err_json.get('error'), dict) else err_json
                            resets_at = err_details.get('resets_at') or err_details.get('reset_at')
                            resets_in_seconds = err_details.get('resets_in_seconds') or err_details.get('retry_after')
                    except Exception:
                        pass
                    if resets_in_seconds is not None:
                        try:
                            retry_sec = float(resets_in_seconds)
                        except (TypeError, ValueError):
                            pass
                    exc.close()
                    # Failover on HTTP 401, 403, or 429 (rate limit OR quota)
                    if exc.code in (401, 403, 429):
                        reason = 'quota_exhausted' if exc.code == 429 else 'invalid_credentials'
                        print(f"[codex-bridge] failover account {number} exhausted: HTTP {exc.code} (resets_at={resets_at}, seconds={retry_sec})", flush=True)
                        POOL.exhausted(number, model, seconds=retry_sec, resets_at=resets_at, reason=reason)
                        quota_exhausted.add(number)
                        rotated_after_hard_failure = True
                        continue
                    # Transient error (5xx) -> immediate return
                    err_obj = PoolError(f'Codex upstream HTTP {last_status}', last_status)
                    err_obj.retry_after = retry_sec
                    raise err_obj from None
                except (PoolError, ValueError, OSError, TypeError, RuntimeError) as exc:
                    # Network / Timeout -> immediate return
                    raise exc
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
                        if len(prelude) > 64 or sum(map(len, prelude)) > 262144:
                            last_status = 502
                            prelude = []
                            first = b''
                            break
                    else:
                        first = b''
                    first_event = event_data(first)
                    if rotate_event(first_event):
                        print(f"[codex-bridge] account {number} exhausted: stream {first_event.get('type')}", flush=True)
                        POOL.exhausted(number, model, reason='quota_exhausted')
                        quota_exhausted.add(number)
                        rotated_after_hard_failure = True
                        last_status = 429
                        continue
                    if not first or failed(first_event):
                        raise PoolError('Codex upstream rejected the request', 502)
                    if wants_stream:
                        lifecycle.begin()
                        self._stream_started = True
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/event-stream')
                        self.send_header('Cache-Control', 'no-cache')
                        self.send_header('Connection', 'close')
                        self.send_header('X-Request-ID', request_id)
                        self.end_headers()
                        started = True
                    import itertools
                    completed = None
                    observed_items = {}
                    tool_indexes = {}
                    for raw in itertools.chain(prelude, [first], iterator):
                        event = event_data(raw)
                        if wants_stream and lifecycle.state.value == 'started':
                            lifecycle.observe()
                        if failed(event):
                            raise PoolError('Codex stream failed after it started; request was not replayed', 502)
                        if event.get('type') == 'response.output_item.done' and isinstance(event.get('item'), dict):
                            observed_items[event.get('output_index', len(observed_items))] = event['item']
                        if event.get('type') == 'response.completed':
                            completed = event.get('response') or {}
                            if not completed.get('output') and observed_items:
                                completed = dict(completed, output=[observed_items[k] for k in sorted(observed_items)])
                            usage = completed.get('usage')
                        if wants_stream:
                            if not chat:
                                self.wfile.write(raw)
                            else:
                                delta = None
                                typ = event.get('type')
                                if typ == 'response.output_text.delta':
                                    delta = {'content': event.get('delta', '')}
                                elif typ == 'response.output_item.added' and event.get('item', {}).get('type') == 'function_call':
                                    item = event['item']; index = len(tool_indexes); tool_indexes[event.get('output_index')] = index
                                    delta = {'tool_calls': [{'index': index, 'id': item.get('call_id', item.get('id')), 'type': 'function', 'function': {'name': item.get('name'), 'arguments': ''}}]}
                                elif typ == 'response.function_call_arguments.delta':
                                    delta = {'tool_calls': [{'index': tool_indexes.get(event.get('output_index'), 0), 'function': {'arguments': event.get('delta', '')}}]}
                                if delta is not None:
                                    chunk = {'id': 'chatcmpl-stream', 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': model, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]}
                                    self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                            self.wfile.flush()
                    if completed is None:
                        if wants_stream and lifecycle.state.value == 'started':
                            lifecycle.interrupt()
                        raise PoolError('Codex stream ended without response.completed', 502)
                    if wants_stream:
                        lifecycle.complete()
                    outcome='OK'
                    POOL.clear_cooldown(number, model)
                    if rotated_after_hard_failure or (number != active_at_start and active_at_start not in attempts):
                        try:
                            POOL.promote(number, expected_current=active_at_start)
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
        except (PoolError,ValueError,KeyError,OSError,TypeError,RuntimeError) as exc:
            if started:
                if lifecycle.state.value == 'started':
                    lifecycle.interrupt()
                try:
                    msg = str(exc) if isinstance(exc, PoolError) else str(exc)
                    from stream_state import terminal_error_event, terminal_event
                    request_id = (audit or {}).get('request_id') or str(uuid.uuid4())
                    self.wfile.write(terminal_error_event(msg, request_id))
                    self.wfile.write(terminal_event())
                    self.wfile.flush()
                except OSError:
                    pass
                self.close_connection = True
            else:
                msg = str(exc) if isinstance(exc, PoolError) else 'Codex upstream request failed'
                from error_taxonomy import classify, public_error
                classified = classify(exc, status=getattr(exc, 'status', None), stream_started=False)
                if audit is not None:
                    audit['error_code'] = classified.code.value
                self.send_json({'error':public_error(classified)},getattr(exc,'status',400 if isinstance(exc,(ValueError,KeyError)) else 502))
        finally:
            print(f"REQUEST_PROVIDER_CALLS request_id={request_id} accounts={attempted_accounts} total_calls={len(attempted_accounts)}", flush=True)
            if audit is not None:
                # OUT means the assistant response returned to the client.
                # Keep the transformed provider request in provider_prompt_text
                # for diagnostics, but expose the actual model output as OUT.
                if completed is not None:
                    from request_audit import attach_response
                    response_payload = chat_result(completed, model) if chat else completed
                    attach_response(audit, response_payload)
                audit['latency_ms'] = round((time.perf_counter() - request_started_at) * 1000, 2)
            if model:
                record('Codex',model,usage,number,outcome,audit=audit)


class ThreadingHTTPServer(socketserver.ThreadingMixIn,http.server.HTTPServer):
    daemon_threads=True
    allow_reuse_address=True


def main():
    server=ThreadingHTTPServer((HOST,PORT),CodexHandler)
    print(f'[codex-bridge] Listening on http://{HOST}:{PORT}',flush=True)
    server.serve_forever()


if __name__=='__main__':
    main()
