"""Codex active-account persistence and restart acceptance tests.

These tests deliberately cross the request, pool, account-store, and CLI seams.
They use temporary synthetic credentials and a local upstream, never production
accounts or external services.

Acceptance map:

* ``test_failover_persists_active_account_and_restart_starts_from_it`` covers
  A -> 429, B -> success, the 200 response, canonical ``active`` persistence,
  reinitialization, next-request ordering, and the synchronization log guard.
* ``test_two_quota_failures_promote_the_third_account`` covers A/B -> 429,
  C -> success and persistence of C.
* ``test_all_accounts_exhausted_keeps_the_last_valid_active_account`` covers
  bounded exhaustion with no invalid promotion.
* ``test_persistence_failure_rolls_back_without_corruption`` covers the
  transactional account-store failure path.
* ``test_cli_reads_the_active_account_written_by_rotation`` covers CLI/state
  compatibility after automatic rotation.
* The pool-isolation tests cover no cross-pool fallback and Gemini state
  isolation.

The restart case reloads ``codex_bridge`` and creates a new HTTP server after
shutting down the first one.  That is an in-process equivalent of restarting
``codex.service`` while keeping the canonical account store and runtime DB.
"""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridges"))
sys.path.insert(0, str(ROOT / "cli"))

import account_manager
from account_manager import Manager, read_json
import client_identity
import codex_bridge
from pool_runtime import AccountPool


MODEL = "gpt-5.6-luna"


class _FakeCodexUpstream:
    """Local upstream that returns a scripted result per account header."""

    def __init__(self, actions: dict[str, list[object]]):
        self.actions = {account: deque(values) for account, values in actions.items()}
        self.calls: list[str] = []
        self.payloads: list[dict] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_POST(self):  # noqa: N802 - stdlib handler API
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                payload = json.loads(raw.decode("utf-8"))
                account = self.headers.get("ChatGPT-Account-Id", "")
                with outer._lock:
                    outer.calls.append(account)
                    outer.payloads.append(payload)
                    action = outer.actions.get(account, deque()).popleft() if outer.actions.get(account) else "success"

                if action == 429:
                    self.send_response(429)
                    self.send_header("Retry-After", "0")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                if isinstance(action, int) and action != 200:
                    self.send_response(action)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                response = {
                    "id": "resp-" + str(len(outer.calls)),
                    "model": payload.get("model"),
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "POOL_OK"}],
                        }
                    ],
                }
                events = [{"type": "response.completed", "response": response}]
                body = b"".join(
                    ("data: " + json.dumps(event) + "\n\n").encode("utf-8")
                    for event in events
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


@contextmanager
def _running_codex_bridge(upstream: _FakeCodexUpstream, pool: AccountPool):
    """Run one isolated bridge instance against the fake upstream."""
    bridge = ThreadingHTTPServer(("127.0.0.1", 0), codex_bridge.CodexHandler)
    thread = threading.Thread(target=bridge.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.object(
            codex_bridge,
            "UPSTREAM_URL",
            f"http://127.0.0.1:{upstream.server.server_port}/v1/responses",
        ), patch.object(codex_bridge, "POOL", pool), patch.object(
            client_identity, "authorize", return_value=True
        ), patch.object(codex_bridge.time, "sleep"):
            yield bridge
    finally:
        bridge.shutdown()
        bridge.server_close()
        thread.join(timeout=2)


def _request(port: int, model: str = MODEL) -> tuple[int, dict]:
    payload = {"model": model, "input": "hello", "stream": False}
    request = Request(
        f"http://127.0.0.1:{port}/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        with exc:
            return exc.code, json.load(exc)


def _codex_env(directory: Path) -> dict[str, str]:
    return {
        "CODEX_ACCOUNT_STORE": str(directory / "codex-accounts"),
        "CODEX_SHARED_HOME": str(directory / "codex-home"),
        "AUTH_DB_PATH": str(directory / "runtime" / "auth.db"),
        # Prevent the repository's developer config from changing subprocess
        # behavior while exercising the installed-compatible CLI wrapper.
        "AIPOOL_CONFIG_ENV": str(directory / "empty-config.env"),
    }


def _seed_codex(directory: Path, labels: tuple[str, ...] = ("A", "B"), active: str = "1") -> Path:
    store = Path(_codex_env(directory)["CODEX_ACCOUNT_STORE"])
    store.mkdir(parents=True)
    for number, label in enumerate(labels, start=1):
        account = store / str(number)
        account.mkdir()
        data = {
            "email": f"{label.lower()}@example.test",
            "tokens": {
                "access_token": f"access-{label}",
                "refresh_token": f"refresh-{label}",
                "account_id": f"account-{label}",
            },
        }
        (account / "auth.json").write_text(json.dumps(data) + "\n")
    (store / "active").write_text(active + "\n")
    live = Path(_codex_env(directory)["CODEX_SHARED_HOME"]) / "auth.json"
    live.parent.mkdir(parents=True)
    live.write_text((store / active / "auth.json").read_text())
    return store


def _seed_gemini(directory: Path, number: str = "7") -> Path:
    store = Path(_codex_env(directory)["CODEX_ACCOUNT_STORE"]).parent / "gemini-accounts"
    account = store / number
    account.mkdir(parents=True)
    (account / "antigravity-oauth-token").write_text(
        json.dumps(
            {
                "email": "gemini@example.test",
                "project_id": "test-project",
                "token": {"access_token": "gemini-access", "refresh_token": "gemini-refresh"},
            }
        )
        + "\n"
    )
    (store / "active").write_text(number + "\n")
    return store


class CodexPersistenceRestartTests(unittest.TestCase):
    def test_failover_persists_active_account_and_restart_starts_from_it(self):
        """The primary acceptance flow survives a real bridge reinitialization."""
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            _seed_codex(directory)
            env = _codex_env(directory)
            upstream = _FakeCodexUpstream(
                {"account-A": [429], "account-B": ["success", "success"]}
            ).start()
            try:
                with patch.dict(os.environ, env, clear=False):
                    first_pool = AccountPool("codex")
                    with _running_codex_bridge(upstream, first_pool) as bridge:
                        stderr = io.StringIO()
                        with redirect_stderr(stderr):
                            status, body = _request(bridge.server_port)
                        self.assertEqual(status, 200)
                        self.assertEqual(body["id"], "resp-2")
                        self.assertEqual(upstream.calls[:2], ["account-A", "account-B"])
                        self.assertNotIn(
                            "active-account synchronization failed",
                            stderr.getvalue().lower(),
                        )

                    # The response is successful, but the durable source of
                    # truth is the account store, not the in-memory POOL.
                    manager = Manager("codex")
                    self.assertEqual(manager.active(), "2")
                    self.assertEqual(
                        (Path(env["CODEX_ACCOUNT_STORE"]) / "active").read_text(), "2\n"
                    )

                    # Simulate a service restart: discard the old pool and
                    # reload the module that owns the HTTP handler/global pool.
                    importlib.reload(codex_bridge)
                    restarted_pool = AccountPool("codex")
                    self.assertEqual(
                        restarted_pool.candidates(MODEL, include_cooldown=True)[:2],
                        ["2", "1"],
                    )
                    self.assertEqual(restarted_pool.candidates(MODEL)[0], "2")

                    with _running_codex_bridge(upstream, restarted_pool) as restarted_bridge:
                        stderr = io.StringIO()
                        with redirect_stderr(stderr):
                            status, body = _request(restarted_bridge.server_port)
                        self.assertEqual(status, 200)
                        self.assertEqual(body["id"], "resp-3")
                        self.assertEqual(upstream.calls[-1], "account-B")
                        self.assertNotIn(
                            "active-account synchronization failed",
                            stderr.getvalue().lower(),
                        )
                        self.assertEqual(Manager("codex").active(), "2")
            finally:
                upstream.close()

    def test_rotation_persists_under_systemd_readonly_live_sandbox(self):
        """When self.live is read-only (systemd ProtectSystem=strict), failover persists active account."""
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            _seed_codex(directory)
            env = _codex_env(directory)
            live_path = Path(env["CODEX_SHARED_HOME"]) / "auth.json"
            upstream = _FakeCodexUpstream(
                {"account-A": [429], "account-B": ["success", "success"]}
            ).start()
            try:
                real_atomic_bytes = account_manager.atomic_bytes
                def sandbox_atomic_bytes(path, blob):
                    if Path(path) == live_path:
                        raise OSError(30, "Read-only file system", str(path))
                    return real_atomic_bytes(path, blob)

                with patch.dict(os.environ, env, clear=False), patch.object(
                    account_manager, "atomic_bytes", side_effect=sandbox_atomic_bytes
                ):
                    first_pool = AccountPool("codex")
                    with _running_codex_bridge(upstream, first_pool) as bridge:
                        stderr = io.StringIO()
                        with redirect_stderr(stderr):
                            status, body = _request(bridge.server_port)
                        self.assertEqual(status, 200)
                        self.assertEqual(body["id"], "resp-2")
                        self.assertEqual(upstream.calls[:2], ["account-A", "account-B"])
                        self.assertNotIn(
                            "active-account synchronization failed",
                            stderr.getvalue().lower(),
                        )

                    manager = Manager("codex")
                    self.assertEqual(manager.active(), "2")
                    self.assertEqual(
                        (Path(env["CODEX_ACCOUNT_STORE"]) / "active").read_text(), "2\n"
                    )

                    # Restart/reload bridge and pool
                    importlib.reload(codex_bridge)
                    restarted_pool = AccountPool("codex")
                    self.assertEqual(restarted_pool.candidates(MODEL)[0], "2")

                    with _running_codex_bridge(upstream, restarted_pool) as restarted_bridge:
                        stderr = io.StringIO()
                        with redirect_stderr(stderr):
                            status, body = _request(restarted_bridge.server_port)
                        self.assertEqual(status, 200)
                        self.assertEqual(body["id"], "resp-3")
                        self.assertEqual(upstream.calls[-1], "account-B")
                        self.assertNotIn(
                            "active-account synchronization failed",
                            stderr.getvalue().lower(),
                        )

                    # Verify cwho and clist reflect account 2
                    cli_env = os.environ.copy()
                    cli_env.update(env)
                    res_who = subprocess.run(
                        [sys.executable, str(ROOT / "cli" / "cx"), "who"],
                        cwd=ROOT,
                        env=cli_env,
                        text=True,
                        capture_output=True,
                        timeout=10,
                    )
                    self.assertEqual(res_who.returncode, 0)
                    self.assertIn("Active Codex account: 2", res_who.stdout)

                    res_list = subprocess.run(
                        [sys.executable, str(ROOT / "cli" / "cx"), "list"],
                        cwd=ROOT,
                        env=cli_env,
                        text=True,
                        capture_output=True,
                        timeout=10,
                    )
                    self.assertEqual(res_list.returncode, 0)
                    self.assertIn("Account 2 [ACTIVE]", res_list.stdout)
                    self.assertNotIn("Account 1 [ACTIVE]", res_list.stdout)
            finally:
                upstream.close()

    def test_two_quota_failures_promote_the_third_account(self):
        """A and B can fail before C succeeds and becomes canonical."""
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            _seed_codex(directory, labels=("A", "B", "C"))
            upstream = _FakeCodexUpstream(
                {"account-A": [429], "account-B": [429], "account-C": ["success"]}
            ).start()
            try:
                with patch.dict(os.environ, _codex_env(directory), clear=False):
                    with _running_codex_bridge(upstream, AccountPool("codex")) as bridge:
                        status, body = _request(bridge.server_port)
                    self.assertEqual(status, 200)
                    self.assertEqual(body["id"], "resp-3")
                    self.assertEqual(
                        upstream.calls[:3], ["account-A", "account-B", "account-C"]
                    )
                    self.assertEqual(Manager("codex").active(), "3")
                    self.assertEqual(
                        AccountPool("codex").candidates(MODEL)[0], "3"
                    )
            finally:
                upstream.close()

    def test_all_accounts_exhausted_keeps_the_last_valid_active_account(self):
        """Exhaustion fails closed and never writes an unknown active slot."""
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            store = _seed_codex(directory)
            upstream = _FakeCodexUpstream({"account-A": [429], "account-B": [429]}).start()
            try:
                with patch.dict(os.environ, _codex_env(directory), clear=False):
                    with _running_codex_bridge(upstream, AccountPool("codex")) as bridge:
                        status, body = _request(bridge.server_port)
                    self.assertEqual(status, 429)
                    self.assertIn("error", body)
                    self.assertEqual(upstream.calls, ["account-A", "account-B"])
                    self.assertEqual(Manager("codex").active(), "1")
                    self.assertIn(
                        (store / "active").read_text().strip(),
                        {str(number) for number in (1, 2)},
                    )
                    self.assertEqual(
                        read_json(store / "1" / "auth.json")["tokens"]["account_id"],
                        "account-A",
                    )
            finally:
                upstream.close()

    def test_persistence_failure_rolls_back_without_corruption(self):
        """A failed active-marker write leaves every canonical file intact."""
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            store = _seed_codex(directory)
            env = _codex_env(directory)
            live = Path(env["CODEX_SHARED_HOME"]) / "auth.json"
            paths = [
                store / "1" / "auth.json",
                store / "2" / "auth.json",
                live,
                store / "active",
            ]
            before = {path: path.read_bytes() for path in paths}
            failed = False
            real_atomic_bytes = account_manager.atomic_bytes

            def fail_active_once(path, blob):
                nonlocal failed
                if Path(path).name == "active" and not failed:
                    failed = True
                    raise OSError("synthetic persistence failure")
                return real_atomic_bytes(path, blob)

            with patch.dict(os.environ, env, clear=False), patch.object(
                account_manager, "atomic_bytes", side_effect=fail_active_once
            ):
                with self.assertRaises(OSError):
                    AccountPool("codex").promote("2")

                self.assertTrue(failed)
                self.assertEqual(Manager("codex").active(), "1")
                for path, original in before.items():
                    self.assertEqual(path.read_bytes(), original, str(path))
                for number in ("1", "2"):
                    read_json(store / number / "auth.json")

    def test_cli_reads_the_active_account_written_by_rotation(self):
        """The installed-compatible CLI sees the bridge's automatic switch."""
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            _seed_codex(directory)
            upstream = _FakeCodexUpstream({"account-A": [429], "account-B": ["success"]}).start()
            try:
                env = _codex_env(directory)
                with patch.dict(os.environ, env, clear=False):
                    with _running_codex_bridge(upstream, AccountPool("codex")) as bridge:
                        status, _ = _request(bridge.server_port)
                    self.assertEqual(status, 200)

                cli_env = os.environ.copy()
                cli_env.update(env)
                result = subprocess.run(
                    [sys.executable, str(ROOT / "cli" / "cx"), "list"],
                    cwd=ROOT,
                    env=cli_env,
                    text=True,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Account 2 [ACTIVE]", result.stdout)
                self.assertNotIn("Account 1 [ACTIVE]", result.stdout)
            finally:
                upstream.close()

    def test_codex_candidates_never_fall_back_to_gemini_accounts(self):
        """A Codex pool reads only CODEX_ACCOUNT_STORE."""
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            _seed_codex(directory, labels=("A",))
            gemini_store = _seed_gemini(directory)
            env = _codex_env(directory)
            env["AG_ACCOUNT_STORE"] = str(gemini_store)
            with patch.dict(os.environ, env, clear=False):
                candidates = AccountPool("codex").candidates(MODEL)
                self.assertEqual(candidates, ["1"])
                self.assertEqual(Manager("codex").active(), "1")
                self.assertEqual(Manager("gemini").active(), "7")

    def test_codex_rotation_does_not_change_gemini_state(self):
        """Automatic Codex promotion leaves Gemini's store and marker untouched."""
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            _seed_codex(directory)
            gemini_store = _seed_gemini(directory)
            env = _codex_env(directory)
            env["AG_ACCOUNT_STORE"] = str(gemini_store)
            gemini_active = (gemini_store / "active").read_bytes()
            gemini_token = (gemini_store / "7" / "antigravity-oauth-token").read_bytes()
            upstream = _FakeCodexUpstream({"account-A": [429], "account-B": ["success"]}).start()
            try:
                with patch.dict(os.environ, env, clear=False):
                    with _running_codex_bridge(upstream, AccountPool("codex")) as bridge:
                        status, _ = _request(bridge.server_port)
                    self.assertEqual(status, 200)
                    self.assertEqual(Manager("codex").active(), "2")
                    self.assertEqual((gemini_store / "active").read_bytes(), gemini_active)
                    self.assertEqual(
                        (gemini_store / "7" / "antigravity-oauth-token").read_bytes(),
                        gemini_token,
                    )
                    self.assertEqual(Manager("gemini").active(), "7")
            finally:
                upstream.close()


if __name__ == "__main__":
    unittest.main()
