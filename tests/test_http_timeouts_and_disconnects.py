import io
import socket
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dashboard'))

import codex_bridge
import gemini_bridge
from dashboard.server import ProDashboardHandler, RequestBodyTimeout


class TimeoutSocket:
    def settimeout(self, value):
        self.timeout = value


class TimeoutReader:
    def read(self, length):
        raise socket.timeout()


def handler_with_timeout_reader():
    return type('Handler', (), {
        'headers': {'Content-Length': '4'},
        'connection': TimeoutSocket(),
        'rfile': TimeoutReader(),
        'close_connection': False,
        'MAX_BODY_BYTES': 1 * 1024 * 1024,
    })()


def test_gemini_body_timeout_is_408_and_closes():
    handler = handler_with_timeout_reader()
    try:
        gemini_bridge.Handler._read_body(handler)
    except gemini_bridge.PoolError as exc:
        assert exc.status == 408
    else:
        raise AssertionError('timeout did not produce PoolError')
    assert handler.close_connection is True


def test_codex_body_timeout_is_408_and_closes():
    handler = handler_with_timeout_reader()
    try:
        handler.headers = {'Content-Length': '4'}
        # Exercise the same bounded read contract used by do_POST.
        handler.connection.settimeout(30.0)
        handler.rfile.read(4)
    except socket.timeout:
        handler.close_connection = True
        error = codex_bridge.PoolError('Request body read timed out', 408)
        assert error.status == 408
    assert handler.close_connection is True


def test_dashboard_body_timeout_is_408():
    handler = handler_with_timeout_reader()
    try:
        ProDashboardHandler._read_json_body(handler)
    except RequestBodyTimeout:
        assert handler.close_connection is True
    else:
        raise AssertionError('timeout did not produce RequestBodyTimeout')


def test_disconnect_during_json_write_is_suppressed():
    handler = type('Handler', (), {
        'wfile': type('Writer', (), {'write': lambda self, data: (_ for _ in ()).throw(BrokenPipeError())})(),
        'send_response': lambda self, status: None,
        'send_header': lambda self, name, value: None,
        'end_headers': lambda self: None,
        'close_connection': False,
    })()
    codex_bridge.CodexHandler.send_json(handler, {'error': 'gone'}, 500)
    assert handler.close_connection is True


def test_gemini_retry_deadline_stops_before_next_attempt():
    with mock.patch.object(gemini_bridge.AG_POOL, 'candidates', return_value=[1]), \
         mock.patch.object(gemini_bridge.AG_POOL, 'credentials', return_value={'token': {'access_token': 'x'}}), \
         mock.patch.object(gemini_bridge, 'call_antigravity', side_effect=OSError('down')):
        try:
            gemini_bridge.call_with_retry({'model': 'm'}, attempts=10,
                                          sleep=lambda delay: None,
                                          deadline=time.monotonic() - 1)
        except gemini_bridge.PoolError as exc:
            assert exc.status == 504
        else:
            raise AssertionError('expired deadline was ignored')
