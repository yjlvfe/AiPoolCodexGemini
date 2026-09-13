"""Built-in Google OAuth, validation and Code Assist onboarding. No Hermes/agy dependency."""
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
import io
import tempfile

TOKEN_URL = 'https://oauth2.googleapis.com/token'
USERINFO_URL = 'https://www.googleapis.com/oauth2/v1/userinfo?alt=json'
BASE = 'https://daily-cloudcode-pa.googleapis.com/v1internal'
SCOPES = ['https://www.googleapis.com/auth/cloud-platform', 'https://www.googleapis.com/auth/userinfo.email', 'https://www.googleapis.com/auth/userinfo.profile']


def oauth_client():
    # Resolve from environment first, then the private credentials file; never
    # parse constants out of source files (secrets must not live in code).
    import importlib.util
    loader_path = Path(__file__).resolve().parents[1] / 'bridges/oauth_credentials.py'
    spec = importlib.util.spec_from_file_location('oauth_credentials', loader_path)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load oauth_credentials loader at ' + str(loader_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.oauth_credentials()


class ValidationRequiredError(ValueError):
    def __init__(self, message, validation_url=None):
        super().__init__(message)
        self.validation_url = validation_url


def request(url, payload=None, access=None, form=False):
    headers = {'User-Agent':'antigravity/hub/2.1.4 linux/amd64'}
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
        # Check if validation / verification is required
        validation_url = None
        try:
            body = json.loads(exc.read().decode())
            if isinstance(body, dict):
                details = body.get('error', {}).get('details', [])
                for d in details:
                    if d.get('reason') == 'VALIDATION_REQUIRED':
                        validation_url = d.get('metadata', {}).get('validation_url')
                        break
        except Exception:
            pass
        if validation_url:
            raise ValidationRequiredError('Verify your account to continue.', validation_url=validation_url) from None
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
            resp = result.get('response', {})
            project = resp.get('cloudaicompanionProject') if isinstance(resp, dict) else None
            if isinstance(project, dict):
                project = project.get('id')
            return str(project) if project else "aicode-consumers"
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
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    if not query.get('code'):
        raise ValueError('No authorization code found in pasted URL')
    if query.get('error'):
        raise ValueError('Google authorization was denied or cancelled')
    # State validation: if present in URL, verify match; otherwise accept valid code
    qs_state = query.get('state', [''])[0]
    if qs_state and not secrets.compare_digest(qs_state, state):
        raise ValueError('OAuth state mismatch; use the link from this login session')
    code = query.get('code', [''])[0]
    if not code:
        raise ValueError('No valid authorization code found in URL')
    return code


def pending_file():
    store = Path(os.environ.get('AG_ACCOUNT_STORE', Path.home() / '.antigravity-accounts'))
    return store / 'pending-oauth.json'


def pending_slot():
    """Return the account slot a paused login was targeting, if the session is still valid."""
    path = pending_file()
    try:
        pending = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(pending, dict) or time.time() > pending.get('expires', 0):
        return None
    return pending.get('slot')


def save_pending(url, state, verifier, redirect, slot):
    pending = {'url':url, 'state':state, 'verifier':verifier, 'redirect':redirect, 'slot':slot,
               'created':time.time(), 'expires':time.time() + 3600}
    path = pending_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(prefix='.pending-oauth-', dir=path.parent)
        with os.fdopen(fd, 'w') as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(pending, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        # The panel still works; resume is simply unavailable on this filesystem.
        pass


def load_pending(resume):
    path = pending_file()
    if not resume:
        return None
    try:
        pending = json.loads(path.read_text())
    except (OSError, ValueError):
        raise ValueError('No pending Antigravity login to resume; start fresh with: ag add') from None
    if not isinstance(pending, dict) or time.time() > pending.get('expires', 0):
        try:
            path.unlink()
        except OSError:
            pass
        raise ValueError('The saved Antigravity login session has expired; start again with: ag add') from None
    required = ('url', 'state', 'verifier', 'redirect')
    if not all(isinstance(pending.get(k), str) and pending[k] for k in required):
        raise ValueError('Saved login session is corrupt; start again with: ag add') from None
    return pending


def clear_pending():
    try:
        pending_file().unlink()
    except OSError:
        pass


def login(resume=False, slot=None):
    """Enroll a Google account. Prints a continuation panel and keeps a resumable session."""
    port = int(os.environ.get('AG_OAUTH_PORT', '0'))
    pending = load_pending(resume)
    if pending:
        port = int(urllib.parse.urlparse(pending['redirect']).port or port)
        url, state, verifier, redirect = pending['url'], pending['state'], pending['verifier'], pending['redirect']
        if slot is None:
            slot = pending.get('slot')
    else:
        url, state, verifier, redirect = None, None, None, None
    received = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                received['code'] = callback_code('http://localhost:' + str(port) + self.path, state, redirect)
                body, status = b'Authorization received. Return to your terminal for validation.', 200
            except ValueError as exc:
                body, status = str(exc).encode(), 400
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
        raise ValueError('OAuth callback port is unavailable. Close the other login, free the port, or start fresh with: ag add') from None
    try:
        port = server.server_address[1]
        server.timeout = 0.25
        if url is None:
            redirect = 'http://localhost:' + str(port) + '/oauth-callback'
            url, state, verifier = authorization(redirect)
            save_pending(url, state, verifier, redirect, slot)
        CYAN = '\033[96m'
        GREEN = '\033[92m'
        YELLOW = '\033[93m'
        RED = '\033[91m'
        MAGENTA = '\033[95m'
        BOLD = '\033[1m'
        DIM = '\033[2m'
        RESET = '\033[0m'

        print(f"\n{CYAN}{'═' * 70}{RESET}")
        print(f"{BOLD}{MAGENTA} 🔐 GOOGLE ANTIGRAVITY / GEMINI OAUTH ENROLLMENT{RESET}")
        print(f"{CYAN}{'═' * 70}{RESET}\n")

        print(f" {BOLD}{GREEN}[STEP 1]{RESET} Click the URL below {DIM}(Ctrl + Click to open directly in browser){RESET}, or copy it:")
        print(f" {YELLOW}{url}{RESET}\n")

        print(f" {BOLD}{GREEN}[STEP 2]{RESET} Sign in with your Google Account & grant permissions.\n")

        print(f" {BOLD}{GREEN}[STEP 3]{RESET} Browser will redirect to: {DIM}http://localhost:{port}/...{RESET}")
        print(f"         {BOLD}{RED}⚠️  'Site cannot be reached' error is 100% NORMAL on remote VPS!{RESET}\n")

        print(f" {BOLD}{GREEN}[STEP 4]{RESET} {BOLD}{YELLOW}Copy the ENTIRE redirected URL from your browser address bar{RESET}")
        print(f"         {BOLD}{RED}and paste it right here in the terminal prompt 👇{RESET}")
        print(f"\n{CYAN}{'═' * 70}{RESET}")
        print(f"{YELLOW}⏳ Waiting for callback or pasted URL...{RESET} {DIM}(Ctrl+C to suspend session){RESET}\n", flush=True)
        if not os.environ.get('SSH_CONNECTION') and os.environ.get('AIPOOL_NO_BROWSER') != '1' and (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
            webbrowser.open(url)
        deadline = time.monotonic() + 600
        stdin_open = sys.stdin is not None
        last_tick = 0.0
        while time.monotonic() < deadline and not received.get('code'):
            server.handle_request()
            if stdin_open:
                try:
                    readable = select.select([sys.stdin], [], [], 0)[0]
                except (OSError, ValueError, io.UnsupportedOperation):
                    stdin_open = False
                else:
                    if readable:
                        line = sys.stdin.readline()
                        if not line:
                            stdin_open = False
                        elif line.strip():
                            try:
                                received['code'] = callback_code(line.strip(), state, redirect)
                            except ValueError as exc:
                                print(str(exc), flush=True)
            now = time.monotonic()
            if now - last_tick >= 30:
                print('Still waiting (' + str(max(0, int(deadline - now))) + 's left). Paste the redirected URL, or Ctrl+C and continue later with: ag add --resume', flush=True)
                last_tick = now
    except KeyboardInterrupt:
        raise ValueError('Login paused; the session is kept. Continue later with: ag add --resume') from None
    finally:
        server.server_close()
    if not received.get('code'):
        raise ValueError('OAuth login paused (timeout); the session is kept. Resume with: ag add --resume')
    client, secret = oauth_client()
    token = request(TOKEN_URL, {'client_id':client, 'client_secret':secret, 'grant_type':'authorization_code', 'code':received['code'], 'redirect_uri':redirect, 'code_verifier':verifier}, form=True)
    if not token.get('refresh_token'):
        raise ValueError('Google returned no refresh token; approve offline consent and run ag add again')
    clear_pending()
    return {'token':token}


def refresh(data):
    """Light refresh: fresh access token + verified identity. No Code Assist onboarding."""
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
    project = data.get('project_id') or token.get('project_id') or ''
    expiry = time.time() + int(fresh.get('expires_in', 3599))
    canonical = {'token':{**token, 'access_token':access, 'refresh_token':fresh.get('refresh_token') or token['refresh_token'], 'token_type':'Bearer', 'expiry':int(expiry * 1000)}, 'email':email}
    derived = {**canonical['token'], 'expires_at':expiry, 'email':email}
    if project:
        canonical['project_id'] = project
        derived['project_id'] = project
    return canonical, derived, {'identity':email.casefold(), 'email':email, 'project_id':project or None, 'status':'OK (light refresh)'}


def quota(data):
    access = data['token']['access_token']
    result = request(BASE + ':retrieveUserQuotaSummary', {'project':data['project_id']}, access)
    summary = result.get('quotaSummary')
    return summary if isinstance(summary, dict) else result
