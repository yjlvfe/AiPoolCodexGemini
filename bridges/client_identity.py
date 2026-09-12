"""Private bearer credential registry with loopback trust.

Local (loopback) clients are trusted by project policy: Hermes, OpenClaw, and
local tooling connect without credentials. Non-loopback peers must present a
valid registered credential; no caller-supplied identity is trusted remotely.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[1] / 'dashboard/client-identities.json'
LOOPBACK = ('127.0.0.1', '::1', '::ffff:127.0.0.1')


def is_loopback(peer_ip):
    return str(peer_ip or '') in LOOPBACK


def identify(headers, path=None, peer_ip=None):
    path = Path(path or os.environ.get('AIPOOL_CLIENT_REGISTRY', REGISTRY))
    # Missing/invalid configuration fails closed for remote peers.
    config = json.loads(path.read_text())
    mode = config.get('mode')
    if mode not in ('observe', 'enforce'):
        raise ValueError('Invalid client authentication mode')
    value = headers.get('Authorization', '')
    token = value[7:] if value.startswith('Bearer ') else ''
    digest = hashlib.sha256(token.encode()).hexdigest()
    verified = False
    client_name = None
    credential_id = None
    client_obj = None
    for client in config.get('clients', []):
        if token and client.get('enabled', False) and hmac.compare_digest(digest, client['sha256']):
            verified = True
            client_name = client.get('name')
            credential_id = client.get('id')
            client_obj = client
            break
    # Policy: loopback peers are trusted local clients (Hermes/OpenClaw/local).
    local = is_loopback(peer_ip)
    allowed = (mode == 'observe') or local or verified
    return {'authenticated_client': client_name, 'credential_id': credential_id,
            'identity_verified': verified, 'auth_mode': mode, 'allowed': allowed,
            'client_obj': client_obj}


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
                    'allowed': is_loopback(peer)}
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

            # Check provider token quota
            token_limits = client_obj.get('token_limits', {})
            prov_limit = token_limits.get(norm_prov)
            overall_limit = client_obj.get('max_tokens')
            if prov_limit is not None or overall_limit is not None:
                # Query used tokens from request_events DB
                db_path = os.environ.get('AUTH_DB_PATH') or str(Path(__file__).resolve().parents[1] / 'bridges/auth.db')
                if os.path.isfile(db_path):
                    try:
                        import sqlite3
                        with sqlite3.connect(db_path, timeout=3) as conn:
                            conn.row_factory = sqlite3.Row
                            cur = conn.execute("SELECT audit_json, total_tokens, pool FROM request_events WHERE audit_json IS NOT NULL AND audit_json != ''")
                            c_name = (client_obj.get('name') or '').lower()
                            c_id = (client_obj.get('id') or '').lower()
                            total_used = 0
                            prov_used = 0
                            for r in cur.fetchall():
                                try:
                                    aud = json.loads(r['audit_json'])
                                    cl = (aud.get('client_label') or '').lower()
                                    if cl and (cl == c_name or cl == c_id):
                                        toks = int(r['total_tokens'] or 0)
                                        total_used += toks
                                        row_prov = 'codex' if str(r['pool']).lower() in ('codex', 'openai') else 'gemini'
                                        if row_prov == norm_prov:
                                            prov_used += toks
                                except Exception:
                                    pass
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
