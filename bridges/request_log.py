"""SQLite request history and backward-compatible schema migration."""
import os, sqlite3, time, uuid, json
from pathlib import Path

_POOL_ALIASES = {
    'codex': 'Codex',
    'gemini': 'Gemini',
    'antigravity': 'Gemini',
    'mixture': 'Mixture',
}


def canonical_pool_name(value):
    """Return the public pool name without treating unknown pools as totals."""
    if not isinstance(value, str):
        return None
    return _POOL_ALIASES.get(value.strip().lower())


def db_path():
    return Path(os.environ.get('AUTH_DB_PATH','/var/lib/aipool/runtime/auth.db'))


def initialize(path=None):
    path = Path(path or db_path())
    if path.is_symlink():
        raise ValueError('Request database must not be a symlink')
    ancestor = path.parent
    while ancestor != ancestor.parent:
        if ancestor.is_symlink():
            raise ValueError('Request database parent must not be a symlink')
        ancestor = ancestor.parent
    path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path,timeout=10) as conn:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        conn.execute('''CREATE TABLE IF NOT EXISTS request_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT, pool TEXT,
            prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER,
            timestamp REAL, time_formatted TEXT)''')
        columns = {r[1] for r in conn.execute('PRAGMA table_info(request_events)')}
        for name,kind in [('request_id','TEXT'),('account','TEXT'),('status','TEXT'),('usage_known','INTEGER'),('audit_json','TEXT'),('wire_input_bytes','INTEGER'),('wire_provider_bytes','INTEGER'),('latency_ms','REAL'),('error_code','TEXT')]:
            if name not in columns:
                try:
                    conn.execute('ALTER TABLE request_events ADD COLUMN '+name+' '+kind)
                except sqlite3.OperationalError as exc:
                    if 'duplicate column' not in str(exc):
                        raise
        conn.execute('''CREATE TABLE IF NOT EXISTS account_usage_counters (
            pool TEXT NOT NULL,
            account TEXT NOT NULL,
            total_tokens INTEGER DEFAULT 0,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (pool, account)
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS usage_rollups (
            day TEXT NOT NULL,
            pool TEXT NOT NULL,
            model TEXT NOT NULL,
            requests INTEGER NOT NULL DEFAULT 0,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            wire_input_bytes INTEGER NOT NULL DEFAULT 0,
            wire_provider_bytes INTEGER NOT NULL DEFAULT 0,
            latency_ms_sum REAL NOT NULL DEFAULT 0,
            latency_count INTEGER NOT NULL DEFAULT 0,
            classified_errors INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(day, pool, model)
        )''')
        rollup_columns = {r[1] for r in conn.execute('PRAGMA table_info(usage_rollups)')}
        for name, kind in [('latency_ms_sum', 'REAL NOT NULL DEFAULT 0'), ('latency_count', 'INTEGER NOT NULL DEFAULT 0'), ('classified_errors', 'INTEGER NOT NULL DEFAULT 0')]:
            if name not in rollup_columns:
                conn.execute('ALTER TABLE usage_rollups ADD COLUMN ' + name + ' ' + kind)
        # the oldest copy before enforcing the idempotency invariant.
        conn.execute("""DELETE FROM request_events
                        WHERE request_id IS NOT NULL
                          AND id NOT IN (SELECT MIN(id) FROM request_events
                                         WHERE request_id IS NOT NULL GROUP BY request_id)""")
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


def record(provider, model, usage=None, account=None, status='OK', request_id=None, audit=None, error_code=None):
    """Persist request history when available; logging must never break a response."""
    try:
        return _record(provider, model, usage, account, status, request_id, audit, error_code)
    except (OSError, OverflowError, sqlite3.Error, TypeError, ValueError) as exc:
        import logging
        logging.getLogger(__name__).error('Request history write failed: %s', type(exc).__name__)
        return None


def _usage_int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _record(provider, model, usage=None, account=None, status='OK', request_id=None, audit=None, error_code=None):
    path = initialize()
    model = clean_model_name(model)
    provider = canonical_pool_name(provider) or provider
    usage = usage or {}
    prompt = _usage_int(usage.get('input_tokens', usage.get('prompt_tokens')))
    completion = _usage_int(usage.get('output_tokens', usage.get('completion_tokens')))
    known = prompt is not None and completion is not None
    # Provider usage is authoritative; never infer billing from request size.
    total = _usage_int(usage.get('total_tokens'))
    if prompt is not None and completion is not None:
        if total is None:
            total = prompt + completion
    now = time.time()
    with sqlite3.connect(path, timeout=10) as conn:
     conn.execute('''INSERT OR IGNORE INTO request_events
      (model,pool,prompt_tokens,completion_tokens,total_tokens,timestamp,time_formatted,request_id,account,status,usage_known,audit_json,wire_input_bytes,wire_provider_bytes,latency_ms,error_code)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
      (model, provider, prompt, completion, total, now,
       time.strftime('%m/%d %H:%M', time.localtime(now)),
       request_id or (audit or {}).get('request_id') or str(uuid.uuid4()),
       account, status, int(known),
       json.dumps(audit or {}, ensure_ascii=False),
       int((audit or {}).get('wire_input_bytes') or 0),
       int((audit or {}).get('wire_provider_bytes') or 0),
       float((audit or {}).get('latency_ms') or 0), error_code or (audit or {}).get('error_code')))
     if account is not None and str(account).strip() and total:
         try:
             conn.execute('''INSERT INTO account_usage_counters(pool, account, total_tokens, prompt_tokens, completion_tokens, created_at, updated_at)
                 VALUES(?,?,?,?,?,?,?)
                 ON CONFLICT(pool, account) DO UPDATE SET
                 total_tokens = total_tokens + excluded.total_tokens,
                 prompt_tokens = prompt_tokens + excluded.prompt_tokens,
                 completion_tokens = completion_tokens + excluded.completion_tokens,
                 updated_at = excluded.updated_at''',
                 (provider, str(account).strip(), total or 0, prompt or 0, completion or 0, now, now))
         except Exception:
             pass
     # Roll old detailed rows into compact daily aggregates; never discard usage.
     try:
         retention = max(0, int(os.environ.get('AIPOOL_REQUEST_RETENTION', '300') or 300))
     except (TypeError, ValueError):
         retention = 300
     if retention > 0:
         old_rows = conn.execute("SELECT id, model, pool, prompt_tokens, completion_tokens, total_tokens, timestamp, wire_input_bytes, wire_provider_bytes, latency_ms, error_code, status FROM request_events WHERE id NOT IN (SELECT id FROM request_events ORDER BY id DESC LIMIT ?)", (retention,)).fetchall()
         if old_rows:
             for row in old_rows:
                 day = time.strftime('%Y-%m-%d', time.localtime(row[6] or time.time()))
                 conn.execute("""INSERT INTO usage_rollups(day,pool,model,requests,prompt_tokens,completion_tokens,total_tokens,wire_input_bytes,wire_provider_bytes,latency_ms_sum,latency_count,classified_errors)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(day,pool,model) DO UPDATE SET
                     requests=requests+excluded.requests,prompt_tokens=prompt_tokens+excluded.prompt_tokens,
                     completion_tokens=completion_tokens+excluded.completion_tokens,total_tokens=total_tokens+excluded.total_tokens,
                     wire_input_bytes=wire_input_bytes+excluded.wire_input_bytes,
                     wire_provider_bytes=wire_provider_bytes+excluded.wire_provider_bytes,
                     latency_ms_sum=latency_ms_sum+excluded.latency_ms_sum,
                     latency_count=latency_count+excluded.latency_count,
                     classified_errors=classified_errors+excluded.classified_errors""",
                     (day, row[2] or '', clean_model_name(row[1]), 1, row[3] or 0, row[4] or 0, row[5] or 0, row[7] or 0, row[8] or 0, float(row[9] or 0), 1 if row[9] is not None else 0, 1 if (row[10] or (row[11] not in (None, '', 'OK'))) else 0))
             ids = [(row[0],) for row in old_rows]
             conn.executemany("DELETE FROM request_events WHERE id=?", ids)
     conn.commit()


def report(path):
    initialize(path)
    providers = {name:{'total_tokens':0,'requests':0,'models':{}} for name in ('Gemini','Codex','Mixture')}
    recent = []
    with sqlite3.connect(path,timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        for r in conn.execute("SELECT pool,model,count(*) calls,sum(prompt_tokens) p,sum(completion_tokens) c,sum(total_tokens) t,max(timestamp) ts FROM request_events WHERE lower(pool) IN ('codex','gemini','antigravity','mixture') GROUP BY pool,model"):
            pool_name = canonical_pool_name(r['pool'])
            if pool_name is None:
                continue
            p = providers[pool_name]
            p['total_tokens'] += r['t'] or 0
            p['requests'] += r['calls']
            m_name = clean_model_name(r['model'])
            if m_name in p['models']:
                entry = p['models'][m_name]
                entry['requests_count'] += r['calls']
                entry['prompt_tokens'] += (r['p'] or 0)
                entry['completion_tokens'] += (r['c'] or 0)
                entry['total_tokens'] += (r['t'] or 0)
            else:
                p['models'][m_name] = {'model':m_name,'provider':pool_name,'requests_count':r['calls'],'prompt_tokens':r['p'] or 0,'completion_tokens':r['c'] or 0,'total_tokens':r['t'] or 0,'last_used':time.strftime('%m/%d %H:%M',time.localtime(r['ts']))}
        for r in conn.execute("SELECT pool,model,SUM(requests) calls,SUM(prompt_tokens) p,SUM(completion_tokens) c,SUM(total_tokens) t,MAX(day) day FROM usage_rollups WHERE lower(pool) IN ('codex','gemini','antigravity','mixture') GROUP BY pool,model"):
            pool_name = canonical_pool_name(r['pool'])
            if pool_name is None:
                continue
            p = providers[pool_name]
            p['total_tokens'] += r['t'] or 0
            p['requests'] += r['calls'] or 0
            m_name = clean_model_name(r['model'])
            entry = p['models'].get(m_name)
            if entry:
                entry['requests_count'] += r['calls'] or 0
                entry['prompt_tokens'] += r['p'] or 0
                entry['completion_tokens'] += r['c'] or 0
                entry['total_tokens'] += r['t'] or 0
            else:
                p['models'][m_name] = {'model':m_name,'provider':pool_name,'requests_count':r['calls'] or 0,'prompt_tokens':r['p'] or 0,'completion_tokens':r['c'] or 0,'total_tokens':r['t'] or 0,'last_used':r['day']}
        for r in conn.execute(
                "SELECT id, model, pool, prompt_tokens, completion_tokens, total_tokens, "
                "time_formatted, status, account, usage_known, request_id, "
                "(audit_json IS NOT NULL AND audit_json != '') AS audit_available "
                "FROM request_events WHERE lower(pool) IN ('codex','gemini','antigravity','mixture') ORDER BY id DESC LIMIT 500"):
            pool_name = canonical_pool_name(r['pool'])
            if pool_name is None:
                continue
            recent.append({
                'id': r['id'],
                'call_num': r['id'],
                'model': clean_model_name(r['model']),
                'provider': pool_name,
                'pool': pool_name,
                'prompt': r['prompt_tokens'],
                'completion': r['completion_tokens'],
                'tokens': r['total_tokens'],
                'time_formatted': r['time_formatted'],
                'status': r['status'] or 'LEGACY',
                'account': r['account'],
                'usage_known': r['usage_known'],
                'request_id': r['request_id'],
                # Full prompt fields are intentionally fetched by get_request_prompt().
                'audit': {'prompt_capture_status': 'on_demand' if r['audit_available'] else 'not_available'},
            })
    models = [v for p in providers.values() for v in p['models'].values()]
    with sqlite3.connect(path, timeout=10) as conn:
        detailed = conn.execute("SELECT COALESCE(SUM(wire_input_bytes),0), COALESCE(SUM(wire_provider_bytes),0), COALESCE(SUM(latency_ms),0), COUNT(latency_ms), COUNT(CASE WHEN error_code IS NOT NULL OR status NOT IN ('OK','') THEN 1 END) FROM request_events").fetchone()
        rolled = conn.execute("SELECT COALESCE(SUM(wire_input_bytes),0), COALESCE(SUM(wire_provider_bytes),0), COALESCE(SUM(latency_ms_sum),0), COALESCE(SUM(latency_count),0), COALESCE(SUM(classified_errors),0) FROM usage_rollups").fetchone()
    latency_count = (detailed[3] or 0) + (rolled[3] or 0)
    avg_latency = ((detailed[2] or 0) + (rolled[2] or 0)) / latency_count if latency_count else 0
    return {'providers':providers,'total_pool_tokens':sum(p['total_tokens'] for p in providers.values()),'top_model':max(models,key=lambda x:x['total_tokens'])['model'] if models else 'None','recent_requests':recent,'metrics':{'wire_input_bytes':detailed[0] + rolled[0],'wire_provider_bytes':detailed[1] + rolled[1],'avg_latency_ms':round(avg_latency,2),'classified_errors':detailed[4] + rolled[4]}}


def get_request_prompt(path, request_id):
    initialize(path)
    with sqlite3.connect(path, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT audit_json FROM request_events WHERE request_id = ? OR id = ? LIMIT 1", (str(request_id), str(request_id)))
        row = cur.fetchone()
        if not row or not row['audit_json']:
            return {
                'prompt_capture_status': 'not_available',
                'prompt_text': '',
                'provider_prompt_text': '',
            }
        try:
            audit = json.loads(row['audit_json'])
            if not isinstance(audit, dict):
                raise ValueError('audit record must be an object')
            original = audit.get('prompt_text', '')
            response = audit.get('response_text', '')
            provider_prompt = audit.get('provider_prompt_text', '')
            original = original if isinstance(original, str) else str(original or '')
            response = response if isinstance(response, str) else str(response or '')
            provider_prompt = provider_prompt if isinstance(provider_prompt, str) else str(provider_prompt or '')
            truncated = bool(audit.get('prompt_capture_truncated') or audit.get('response_capture_truncated'))
            status = audit.get('prompt_capture_status') or ('recorded' if original or response else 'not_available')
            return {
                'prompt_capture_status': status,
                'prompt_text': original,
                'response_text': response,
                'provider_prompt_text': provider_prompt,
                'prompt_capture_truncated': truncated,
            }
        except Exception:
            return {
                'prompt_capture_status': 'not_available',
                'prompt_text': '',
                'provider_prompt_text': '',
            }
