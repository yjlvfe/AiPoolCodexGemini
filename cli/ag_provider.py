"""Built-in Google OAuth, validation and Code Assist onboarding. No Hermes/agy dependency."""
import ast
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import select
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

TOKEN_URL = 'https://oauth2.googleapis.com/token'
USERINFO_URL = 'https://www.googleapis.com/oauth2/v1/userinfo?alt=json'
BASE = 'https://daily-cloudcode-pa.googleapis.com/v1internal'
SCOPES = ['https://www.googleapis.com/auth/cloud-platform', 'https://www.googleapis.com/auth/userinfo.email', 'https://www.googleapis.com/auth/userinfo.profile']


def oauth_client():
    client = os.environ.get('AG_OAUTH_CLIENT_ID')
    secret = os.environ.get('AG_OAUTH_CLIENT_SECRET')
    if client and secret:
        return client, secret
    # Read the suite's existing installed-application client without executing the bridge.
    path = Path(__file__).resolve().parents[1] / 'bridges/gemini_bridge.py'
    tree = ast.parse(path.read_text())
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ('CLIENT_ID', 'CLIENT_SECRET'):
            constants[node.targets[0].id] = ast.literal_eval(node.value)
    return constants['CLIENT_ID'], constants['CLIENT_SECRET']


def request(url, payload=None, access=None, form=False):
    headers = {'User-Agent': 'antigravity/hub/2.1.4 linux/amd64'}
    if access:
        headers['Authorization'] = 'Bearer ' + access
    data = None
    if payload is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
        data = urllib.parse.urlencode(payload).encode() if form else json.dumps(payload).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=30) as response:
            result = json.load(response)
        if not isinstance(result, dict):
            raise ValueError('Provider returned a non-object response')
        return result
    except urllib.error.HTTPError as exc:
        # Never include OAuth codes, refresh tokens, or response bodies in errors.
        label = {400:'OAuth rejected; sign in again', 401:'Login expired or revoked', 403:'Account is not eligible or lacks permission', 429:'Provider quota/rate limit reached'}.get(exc.code, 'Provider request failed')
        raise ValueError(f'{label} (HTTP {exc.code})') from None
    except urllib.error.URLError:
        raise ValueError('Cannot reach Google; check network, DNS and proxy settings') from None


def project_id(access):
    metadata = {'ideType':'ANTIGRAVITY', 'platform':'PLATFORM_UNSPECIFIED', 'pluginType':'GEMINI'}
    loaded = request(BASE + ':loadCodeAssist', {'metadata':metadata}, access)
    value = loaded.get('cloudaicompanionProject')
    if isinstance(value, dict):
        value = value.get('id')
    if value:
        return str(value)
    tiers = loaded.get('allowedTiers') or []
    tier = next((t.get('id') for t in tiers if isinstance(t, dict) and t.get('isDefault')), None)
    if not tier:
        raise ValueError('Google returned no eligible Code Assist tier; open Antigravity once to review account eligibility')
    payload = {'tierId':tier, 'metadata':metadata}
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        result = request(BASE + ':onboardUser', payload, access)
        if result.get('done'):
            project = result.get('response', {}).get('cloudaicompanionProject')
            if isinstance(project, dict):
                project = project.get('id')
            if project:
                return str(project)
            raise ValueError('Onboarding completed without a project ID')
        time.sleep(2)
    raise ValueError('Google onboarding timed out; no account was saved. Try again later')


def validate(data):
    token = data.get('token', data)
    if not isinstance(token, dict) or not isinstance(token.get('refresh_token'), str) or not token['refresh_token']:
        raise ValueError('Credential does not contain a Google refresh token')
    client, secret = oauth_client()
    fresh = request(TOKEN_URL, {'client_id':client, 'client_secret':secret, 'grant_type':'refresh_token', 'refresh_token':token['refresh_token']}, form=True)
    access = fresh.get('access_token')
    if not access:
        raise ValueError('Google returned no access token')
    info = request(USERINFO_URL, access=access)
    email = info.get('email')
    if not isinstance(email, str) or not email or info.get('verified_email') is False:
        raise ValueError('Google returned no verified account identity')
    project = project_id(access)
    expiry = time.time() + int(fresh.get('expires_in', 3599))
    canonical = {'token':{**token, 'access_token':access, 'refresh_token':fresh.get('refresh_token') or token['refresh_token'], 'token_type':'Bearer', 'expiry':int(expiry * 1000)}, 'email':email, 'project_id':project}
    derived = {**canonical['token'], 'expires_at':expiry, 'email':email, 'project_id':project}
    return canonical, derived, {'identity':email.casefold(), 'email':email, 'project_id':project, 'status':'OK'}


def authorization(redirect):
    client, _ = oauth_client()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    params = {'client_id':client, 'redirect_uri':redirect, 'response_type':'code', 'scope':' '.join(SCOPES), 'access_type':'offline', 'prompt':'consent select_account', 'state':state, 'code_challenge':challenge, 'code_challenge_method':'S256'}
    return 'https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode(params), state, verifier


def callback_code(url, state, redirect):
    parsed, expected = urllib.parse.urlparse(url), urllib.parse.urlparse(redirect)
    if (parsed.scheme, parsed.netloc, parsed.path) != (expected.scheme, expected.netloc, expected.path):
        raise ValueError('Callback URL does not match this login session')
    query = urllib.parse.parse_qs(parsed.query)
    if not secrets.compare_digest(query.get('state', [''])[0], state):
        raise ValueError('OAuth state mismatch; use the link from this login session')
    if query.get('error'):
        raise ValueError('Google authorization was denied or cancelled')
    code = query.get('code', [''])[0]
    if not code:
        raise ValueError('Callback URL has no authorization code')
    return code


def login():
    port = int(os.environ.get('AG_OAUTH_PORT', '51121'))
    redirect = f'http://localhost:{port}/oauth-callback'
    url, state, verifier = authorization(redirect)
    received = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                received['code'] = callback_code('http://localhost:' + str(port) + self.path, state, redirect)
                body, status = b'Authorization received. Return to your terminal for validation.', 200
            except ValueError as exc:
                body, status = str(exc).encode(), 400
                # A mismatched request must not terminate the legitimate session.
            self.send_response(status)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass

    try:
        server = HTTPServer(('127.0.0.1', port), Callback)
    except OSError:
        raise ValueError(f'OAuth callback port {port} is in use. Close the other login or set AG_OAUTH_PORT') from None
    server.timeout = 0.25
    print('Open this URL and choose a DIFFERENT Google account:\n' + url, flush=True)
    print('On SSH: if localhost cannot connect in your browser, copy the FULL resulting callback URL and paste it here. Do not share it.\nWaiting for callback or pasted URL (5 minutes; Ctrl+C cancels)...', flush=True)
    if not os.environ.get('SSH_CONNECTION') and os.environ.get('AIPOOL_NO_BROWSER') != '1' and (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        webbrowser.open(url)
    deadline = time.monotonic() + 300
    stdin_open = True
    try:
        while time.monotonic() < deadline and not received.get('code'):
            server.handle_request()
            if stdin_open and select.select([sys.stdin], [], [], 0)[0]:
                line = sys.stdin.readline()
                if not line:
                    stdin_open = False
                elif line.strip():
                    received['code'] = callback_code(line.strip(), state, redirect)
    finally:
        server.server_close()
    if not received.get('code'):
        raise ValueError('OAuth login timed out; no account was saved')
    client, secret = oauth_client()
    token = request(TOKEN_URL, {'client_id':client, 'client_secret':secret, 'grant_type':'authorization_code', 'code':received['code'], 'redirect_uri':redirect, 'code_verifier':verifier}, form=True)
    if not token.get('refresh_token'):
        raise ValueError('Google returned no refresh token; approve offline consent and retry')
    return {'token':token}


def quota(data):
    access = data['token']['access_token']
    return request(BASE + ':retrieveUserQuotaSummary', {'project':data['project_id']}, access)
