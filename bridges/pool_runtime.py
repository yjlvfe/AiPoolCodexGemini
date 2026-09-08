"""Shared account rotation and observed request logging; never changes model IDs."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from account_manager import Manager, read_json, atomic_bytes, encoded, decode_claims, ag_module


class PoolError(RuntimeError):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


class AccountPool:
    def __init__(self, provider):
        self.provider = provider
        self._lock = threading.RLock()
        self.cooldowns = {}

    def candidates(self, model):
        manager = Manager(self.provider)
        active = manager.active()
        numbers = manager.ids()
        if active in numbers:
            index = numbers.index(active)
            numbers = numbers[index:] + numbers[:index]
        seen = set()
        result = []
        for number in numbers:
            try:
                data = read_json(manager.credential(number))
                identity = manager.identity(data)
                key = identity.get('identity') or identity.get('refresh')
                if not key or key in seen:
                    continue
                seen.add(key)
                with self._lock:
                    if self.cooldowns.get((number,model),0) > time.time():
                        continue
                result.append(number)
            except (OSError,ValueError):
                continue
        return result

    def exhausted(self, number, model, seconds=60):
        with self._lock:
            self.cooldowns[number,model] = time.time()+min(max(seconds,1),3600)

    def credentials(self, number):
        with self._lock:
            return self._credentials(number)

    def _credentials(self, number):
        manager = Manager(self.provider)
        with manager.locked():
            path = manager.credential(number)
            data = read_json(path)
            original = path.read_bytes()
            if manager.active() == number and manager.live.is_file():
                live = read_json(manager.live)
                if manager.identity(live).get('identity') == manager.identity(data).get('identity'):
                    data = live
        if manager.ag:
            inner = data.get('token',data)
            expiry = inner.get('expires_at') or (inner.get('expiry') or inner.get('expiry_date') or data.get('expiry_date',0))/1000
            if not inner.get('access_token') or float(expiry or 0) < time.time()+60 or not data.get('project_id'):
                fresh,_,_ = ag_module().validate(data)
            else:
                fresh = data
        else:
            fresh = data
            expiry = decode_claims(data['tokens']['access_token']).get('exp')
            if isinstance(expiry,(int,float)) and expiry < time.time()+60:
                from usage_format import inspect
                info = inspect(manager,number)
                if info.get('status')!='OK':
                    raise PoolError('Account refresh failed',401)
                fresh = read_json(path)
        if fresh != data:
            with manager.locked():
                if path.read_bytes() == original:
                    atomic_bytes(path,encoded(fresh))
                    if manager.active()==number:
                        atomic_bytes(manager.live,encoded(fresh))
        return fresh

    def promote(self, number):
        manager = Manager(self.provider)
        # An upstream success already verified this credential. Do not run OAuth again.
        if manager.active() == number:
            return
        manager.add_or_switch(number, server_verified=True)


def retry_seconds(headers):
    try:
        return int(headers.get('Retry-After','60'))
    except (TypeError,ValueError):
        return 60


def db_path():
    return Path(os.environ.get('AUTH_DB_PATH',ROOT/'dashboard/auth.db'))


def initialize(path=None):
    path = Path(path or db_path())
    path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path,timeout=10) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS request_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT, pool TEXT,
            prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER,
            timestamp REAL, time_formatted TEXT)''')
        columns = {r[1] for r in conn.execute('PRAGMA table_info(request_events)')}
        for name,kind in [('request_id','TEXT'),('account','TEXT'),('status','TEXT'),('usage_known','INTEGER')]:
            if name not in columns:
                try:
                    conn.execute('ALTER TABLE request_events ADD COLUMN '+name+' '+kind)
                except sqlite3.OperationalError as exc:
                    if 'duplicate column' not in str(exc):
                        raise
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS aipool_request_id ON request_events(request_id)')
        # Automatically clean any legacy model names with internal suffixes
        conn.execute("UPDATE request_events SET model = 'gemini-3.8-flash' WHERE model = 'gemini-3.8-flash-tiered'")
        conn.execute("UPDATE request_events SET model = 'gemini-3.7-flash' WHERE model = 'gemini-3.7-flash-tiered'")
        conn.execute("UPDATE request_events SET model = 'gemini-3.6-flash' WHERE model = 'gemini-3.6-flash-tiered'")
        conn.execute("UPDATE request_events SET model = 'gemini-3.1-pro' WHERE model = 'gemini-3.1-pro-low'")
    return path


def clean_model_name(model):
    if not model or not isinstance(model, str):
        return model or ''
    # Strip internal routing suffixes like -tiered, -high, -medium, -low if applicable
    m = model.strip()
    if m.endswith('-tiered'):
        m = m[:-7]
    return m


def record(provider, model, usage=None, account=None, status='OK', request_id=None):
    path = initialize()
    model = clean_model_name(model)
    usage = usage or {}
    prompt = usage.get('input_tokens',usage.get('prompt_tokens'))
    completion = usage.get('output_tokens',usage.get('completion_tokens'))
    known = isinstance(prompt,int) and isinstance(completion,int)
    total = usage.get('total_tokens',prompt+completion if known else None)
    now = time.time()
    with sqlite3.connect(path,timeout=10) as conn:
        conn.execute('''INSERT OR IGNORE INTO request_events
            (model,pool,prompt_tokens,completion_tokens,total_tokens,timestamp,time_formatted,request_id,account,status,usage_known)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',(model,provider,prompt,completion,total,now,time.strftime('%m/%d %H:%M',time.localtime(now)),request_id or str(uuid.uuid4()),account,status,int(known)))


def report(path):
    initialize(path)
    providers = {name:{'total_tokens':0,'requests':0,'models':{}} for name in ('Antigravity','Codex')}
    recent = []
    with sqlite3.connect(path,timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        for r in conn.execute("SELECT pool,model,count(*) calls,sum(prompt_tokens) p,sum(completion_tokens) c,sum(total_tokens) t,max(timestamp) ts FROM request_events WHERE pool IN ('Codex','Antigravity') GROUP BY pool,model"):
            m_name = clean_model_name(r['model'])
            p = providers[r['pool']]
            p['total_tokens'] += r['t'] or 0
            p['requests'] += r['calls']
            if m_name in p['models']:
                entry = p['models'][m_name]
                entry['requests_count'] += r['calls']
                entry['prompt_tokens'] += (r['p'] or 0)
                entry['completion_tokens'] += (r['c'] or 0)
                entry['total_tokens'] += (r['t'] or 0)
            else:
                p['models'][m_name] = {'model':m_name,'provider':r['pool'],'requests_count':r['calls'],'prompt_tokens':r['p'] or 0,'completion_tokens':r['c'] or 0,'total_tokens':r['t'] or 0,'last_used':time.strftime('%m/%d %H:%M',time.localtime(r['ts']))}
        for r in conn.execute("SELECT * FROM request_events WHERE pool IN ('Codex','Antigravity') ORDER BY id DESC LIMIT 100"):
            recent.append({'model':clean_model_name(r['model']),'provider':r['pool'],'pool':r['pool'],'prompt':r['prompt_tokens'],'completion':r['completion_tokens'],'tokens':r['total_tokens'],'time_formatted':r['time_formatted'],'status':r['status'] or 'LEGACY','account':r['account'],'usage_known':r['usage_known']})
    models = [v for p in providers.values() for v in p['models'].values()]
    return {'providers':providers,'total_pool_tokens':sum(p['total_tokens'] for p in providers.values()),'top_model':max(models,key=lambda x:x['total_tokens'])['model'] if models else 'None','recent_requests':recent}
