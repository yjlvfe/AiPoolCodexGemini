import json
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))

import codex_bridge
from stream_state import terminal_error_event, terminal_event


class IdleResponse:
    def readline(self):
        raise socket.timeout('idle')


def test_codex_events_raises_on_chunk_idle_timeout():
    with pytest.raises(TimeoutError, match='idle'):
        next(codex_bridge.events(IdleResponse(), idle_timeout=0.01))


def test_terminal_error_contains_correlation_and_done_sentinel():
    error = terminal_error_event('upstream stopped', 'request-123')
    payload = json.loads(error.removeprefix(b'data: ').split(b'\n\n', 1)[0])
    assert payload == {'error': {
        'message': 'upstream stopped',
        'type': 'stream_interrupted',
        'request_id': 'request-123',
    }}
    assert terminal_event() == b'data: [DONE]\n\n'
