"""Private bearer credential registry with loopback trust.

Local (loopback) clients are trusted by project policy: Hermes, OpenClaw, and
local tooling connect without credentials. Non-loopback peers must present a
valid registered credential; no caller-supplied identity is trusted remotely.
"""
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

def _default_registry() -> Path:
    runtime_path = Path("/var/lib/aipool/runtime/client-identities.json")
    if runtime_path.is_file() or runtime_path.parent.is_dir():
        return runtime_path
    return Path(__file__).resolve().parents[1] / 'dashboard/client-identities.json'

REGISTRY = _default_registry()
LOOPBACK = ('127.0.0.1', '::1', '::ffff:127.0.0.1')


def _resolved_legacy_client_id(config, label):
    """Resolve a label only when it identifies exactly one configured key."""
    value = str(label or '').strip().lower()
    if not value:
        return None
    candidates = {
        str(client.get('id') or '').strip().lower()
        for client in config.get('clients', [])
        if str(client.get('id') or '').strip().lower() == value
        or str(client.get('name') or '').strip().lower() == value
    }
    candidates.discard('')
    return next(iter(candidates)) if len(candidates) == 1 else None


def _runtime_quota(value):
    if value is None or value == 0:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError('Invalid stored quota')
    return value


def is_loopback(peer_ip):
    return str(peer_ip or '') in LOOPBACK


def identify(headers, path=None, peer_ip=None):
    path = Path(path or os.environ.get('AIPOOL_CLIENT_REGISTRY', REGISTRY))
    # Missing/invalid configuration fails closed for remote peers.
    config = json.loads(path.read_text())
    if not isinstance(config, dict):
        raise ValueError('Invalid client registry')
    mode = config.get('mode')
    clients = config.get('clients')
    if mode not in ('observe', 'enforce') or not isinstance(clients, list):
        raise ValueError('Invalid client authentication mode')
    if any(not isinstance(client, dict) for client in clients):
        raise ValueError('Invalid client registry entry')
    value = headers.get('Authorization', '')
    token = value[7:] if value.startswith('Bearer ') else ''
    digest = hashlib.sha256(token.encode()).hexdigest()
    verified = False
    client_name = None
    credential_id = None
    client_obj = None
    for client in clients:
        if token and hmac.compare_digest(digest, client['sha256']):
            client_name = client.get('name')
            credential_id = client.get('id')
            client_obj = client
            # Check expiration
            expires_at = client.get('expires_at')
            if expires_at and time.time() > float(expires_at):
                verified = False
                client['expired'] = True
                break
            if client.get('enabled', False):
                verified = True
            break
    # Policy: loopback peers are trusted local clients (Hermes/OpenClaw/local).
    local = is_loopback(peer_ip)
    allowed = (mode == 'observe') or local or verified
    return {'authenticated_client': client_name, 'credential_id': credential_id,
            'identity_verified': verified, 'auth_mode': mode, 'allowed': allowed,
            'client_obj': client_obj, 'registry_config': config}


def authorize(handler, provider):
    from request_log import record
    from request_audit import capture
    peer = ''
    try:
        # Check X-Real-IP or X-Forwarded-For if proxied by reverse proxy (nginx)
        forwarded = handler.headers.get('X-Real-IP') or handler.headers.get('X-Forwarded-For', '').split(',')[0].strip()
        peer = forwarded if forwarded else handler.client_address[0]
    except Exception:
        peer = ''
    try:
        identity = identify(handler.headers, peer_ip=peer)
    except (OSError, ValueError, KeyError, TypeError):
        # Registry/configuration error: local traffic stays up; remote fails closed.
        identity = {'authenticated_client': None, 'credential_id': None,
                    'identity_verified': False, 'auth_mode': 'configuration_error',
                    'allowed': is_loopback(peer), 'registry_config': {}}
    handler._client_identity = identity
    if identity['allowed']:
        # Enforce provider and quota permissions if configured on the client
        client_obj = identity.get('client_obj')
        if client_obj and not is_loopback(peer):
            norm_prov = 'codex' if provider.lower() in ('codex', 'openai') else 'gemini'
            allowed_provs = client_obj.get('allowed_providers')
            if allowed_provs is not None and norm_prov not in [p.lower() for p in allowed_provs]:
                body = f'{{"error":{{"message":"Provider {provider} is not permitted for this API Key","type":"permission_denied"}}}}'.encode()
                handler.send_response(403)
                handler.send_header('Content-Type', 'application/json')
                handler.send_header('Content-Length', str(len(body)))
                handler.send_header('Connection', 'close')
                handler.end_headers()
                handler.wfile.write(body)
                handler.close_connection = True
                return False

            # Check model permission if model is known
            requested_model = getattr(handler, '_requested_model', None)
            allowed_models = client_obj.get('allowed_models')
            if requested_model and allowed_models:
                # If non-empty list of allowed models is specified, restrict to those
                clean_req = str(requested_model).lower()
                clean_allowed = [str(m).lower() for m in allowed_models]
                if clean_req not in clean_allowed:
                    body = f'{{"error":{{"message":"Model {requested_model} is not permitted for this API Key","type":"permission_denied"}}}}'.encode()
                    handler.send_response(403)
                    handler.send_header('Content-Type', 'application/json')
                    handler.send_header('Content-Length', str(len(body)))
                    handler.send_header('Connection', 'close')
                    handler.end_headers()
                    handler.wfile.write(body)
                    handler.close_connection = True
                    return False

            # Check provider token quota. Invalid stored policy fails closed.
            token_limits = client_obj.get('token_limits', {})
            try:
                if not isinstance(token_limits, dict):
                    raise ValueError('Invalid stored quota')
                prov_limit = _runtime_quota(token_limits.get(norm_prov))
                overall_limit = _runtime_quota(client_obj.get('max_tokens'))
            except ValueError:
                body = b'{"error":{"message":"API key quota configuration is invalid","type":"configuration_error"}}'
                handler.send_response(500)
                handler.send_header('Content-Type', 'application/json')
                handler.send_header('Content-Length', str(len(body)))
                handler.send_header('Connection', 'close')
                handler.end_headers()
                handler.wfile.write(body)
                handler.close_connection = True
                return False
            if prov_limit is not None or overall_limit is not None:
                # Query used tokens from request_events DB
                db_path = os.environ.get('AUTH_DB_PATH') or '/var/lib/aipool/runtime/auth.db'
                if os.path.isfile(db_path):
                    try:
                        import sqlite3
                        with sqlite3.connect(db_path, timeout=3) as conn:
                            conn.row_factory = sqlite3.Row
                            cur = conn.execute("SELECT audit_json, total_tokens, pool, timestamp FROM request_events WHERE audit_json IS NOT NULL AND audit_json != ''")
                            c_name = (client_obj.get('name') or '').lower()
                            c_id = (client_obj.get('id') or '').lower()
                            registry_config = identity.get('registry_config') or {}
                            total_used = 0
                            prov_used = 0
                            counter_row = conn.execute(
                                "SELECT total_tokens, codex_tokens, gemini_tokens, created_at "
                                "FROM client_usage_counters WHERE client_id=?",
                                (c_id,),
                            ).fetchone()
                            if not counter_row and _resolved_legacy_client_id(registry_config, c_name) == c_id:
                                counter_row = conn.execute(
                                    "SELECT total_tokens, codex_tokens, gemini_tokens, created_at "
                                    "FROM client_usage_counters WHERE client_id=?",
                                    (c_name,),
                                ).fetchone()

                            # Retained events provide current refill-window usage.
                            raw_events = []
                            first_used_ts = None
                            for r in cur.fetchall():
                                try:
                                    aud = json.loads(r['audit_json'])
                                    cl = (aud.get('client_label') or '').lower()
                                    cid = (aud.get('client_id') or '').lower()
                                    is_current_identity = bool(c_id and cid == c_id)
                                    is_legacy_identity = bool(
                                        not cid and cl
                                        and _resolved_legacy_client_id(registry_config, cl) == c_id
                                    )
                                    if is_current_identity or is_legacy_identity:
                                        row_time = r['timestamp']
                                        row_ts = 0.0
                                        if row_time:
                                            import datetime
                                            if isinstance(row_time, str):
                                                row_dt = datetime.datetime.fromisoformat(row_time.replace('Z', '+00:00'))
                                                row_ts = float(row_dt.timestamp())
                                            else:
                                                row_ts = float(row_time)
                                        if row_ts > 0:
                                            if first_used_ts is None or row_ts < first_used_ts:
                                                first_used_ts = row_ts
                                        raw_events.append({
                                            'ts': row_ts,
                                            'tokens': int(r['total_tokens'] or 0),
                                            'pool': str(r['pool']).lower()
                                        })
                                except Exception:
                                    pass

                            refill_period_s = client_obj.get('refill_period_seconds')
                            window_start_ts = None
                            if refill_period_s and float(refill_period_s) > 0:
                                counter_created_at = float(counter_row['created_at']) if counter_row else None
                                base_ts = counter_created_at or first_used_ts or float(client_obj.get('created_at_ts') or time.time())
                                now = time.time()
                                elapsed = max(0, now - base_ts)
                                cycle_num = int(elapsed // float(refill_period_s))
                                window_start_ts = base_ts + (cycle_num * float(refill_period_s))
                            elif not refill_period_s and counter_row:
                                total_used = int(counter_row['total_tokens'] or 0)
                                if norm_prov == 'codex':
                                    prov_used = int(counter_row['codex_tokens'] or 0)
                                else:
                                    prov_used = int(counter_row['gemini_tokens'] or 0)

                            if window_start_ts is not None or not counter_row:
                                for ev in raw_events:
                                    if window_start_ts is not None and ev['ts'] < window_start_ts:
                                        continue
                                    toks = ev['tokens']
                                    total_used += toks
                                    row_prov = 'codex' if ev['pool'] in ('codex', 'openai') else 'gemini'
                                    if row_prov == norm_prov:
                                        prov_used += toks
                            if prov_limit is not None and prov_used >= prov_limit:
                                body = f'{{"error":{{"message":"Token quota for provider {provider} exceeded ({prov_used:,}/{prov_limit:,})","type":"quota_exceeded"}}}}'.encode()
                                handler.send_response(429)
                                handler.send_header('Content-Type', 'application/json')
                                handler.send_header('Content-Length', str(len(body)))
                                handler.send_header('Connection', 'close')
                                handler.end_headers()
                                handler.wfile.write(body)
                                handler.close_connection = True
                                return False
                            if overall_limit is not None and total_used >= overall_limit:
                                body = f'{{"error":{{"message":"Total token quota exceeded ({total_used:,}/{overall_limit:,})","type":"quota_exceeded"}}}}'.encode()
                                handler.send_response(429)
                                handler.send_header('Content-Type', 'application/json')
                                handler.send_header('Content-Length', str(len(body)))
                                handler.send_header('Connection', 'close')
                                handler.end_headers()
                                handler.wfile.write(body)
                                handler.close_connection = True
                                return False
                    except Exception:
                        pass
        return True
    try:
        record(provider, '', status='UNAUTHORIZED', audit=capture(handler, {}))
    except Exception:
        # Never expose credentials or permit traffic because logging failed.
        import logging
        logging.error('Unable to record unauthorized client attempt')
    body = b'{"error":{"message":"A valid client credential is required","type":"client_authentication"}}'
    handler.send_response(401)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Connection', 'close')
    handler.end_headers()
    handler.wfile.write(body)
    handler.close_connection = True
    return False
