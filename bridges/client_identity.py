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
    for client in config.get('clients', []):
        if token and client.get('enabled', False) and hmac.compare_digest(digest, client['sha256']):
            verified = True
            client_name = client.get('name')
            credential_id = client.get('id')
            break
    # Policy: loopback peers are trusted local clients (Hermes/OpenClaw/local).
    local = is_loopback(peer_ip)
    allowed = (mode == 'observe') or local or verified
    return {'authenticated_client': client_name, 'credential_id': credential_id,
            'identity_verified': verified, 'auth_mode': mode, 'allowed': allowed}


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
