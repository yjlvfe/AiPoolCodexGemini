"""Single source of truth for the Antigravity OAuth installed-application client.

Resolution order (never fall back to parsing Python source):
1. Environment: AG_OAUTH_CLIENT_ID + AG_OAUTH_CLIENT_SECRET
2. Local private JSON file (default: this directory/oauth-client.private.json,
   overridable with AIPOOL_OAUTH_CLIENT_FILE).

The private file is gitignored and must stay mode 0600. This module exists so
the bridge and the CLI share one loader instead of parsing constants out of
source files.
"""
import json
import os
from pathlib import Path

DEFAULT_FILE = Path(__file__).resolve().parent / 'oauth-client.private.json'


def oauth_credentials():
    client = os.environ.get('AG_OAUTH_CLIENT_ID', '').strip()
    secret = os.environ.get('AG_OAUTH_CLIENT_SECRET', '').strip()
    if client and secret:
        return client, secret
    path = Path(os.environ.get('AIPOOL_OAUTH_CLIENT_FILE', DEFAULT_FILE))
    if path.is_symlink():
        raise RuntimeError('Refusing OAuth client symlink')
    if path.is_file():
        try:
            if path.stat().st_mode & 0o077:
                raise RuntimeError('Antigravity OAuth client file must be mode 0600')
            data = json.loads(path.read_text())
        except RuntimeError:
            raise
        except (OSError, ValueError):
            data = {}
        client = str(data.get('client_id', '') or '').strip()
        secret = str(data.get('client_secret', '') or '').strip()
        if client and secret:
            return client, secret
    raise RuntimeError(
        'Antigravity OAuth client credentials are not configured. Set '
        'AG_OAUTH_CLIENT_ID/AG_OAUTH_CLIENT_SECRET or create ' + str(path))
