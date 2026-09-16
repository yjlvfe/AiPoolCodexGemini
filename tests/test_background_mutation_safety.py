"""C1-C6 acceptance checks for read-only background/account reporting paths."""
import ast
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cli"))


def _active_snapshot(store):
    path = Path(store) / "active"
    return path.read_bytes() if path.exists() else None


def _seed_codex(tmp_path):
    store = tmp_path / "codex-accounts"
    account = store / "3"
    account.mkdir(parents=True)
    data = {"tokens": {"access_token": "a.b.c", "refresh_token": "refresh"}, "email": "three@example.test"}
    (account / "auth.json").write_text(json.dumps(data))
    (store / "active").write_text("3\n")
    return store


def _assert_no_active_transition_calls(path, function_names):
    tree = ast.parse(Path(path).read_text())
    funcs = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in function_names]
    assert funcs, f"No target functions found in {path}"
    forbidden = {"add_or_switch", "compact", "remove", "relogin", "promote", "switch_account", "delete_account"}
    for func in funcs:
        for node in ast.walk(func):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden, f"{path}:{func.name} calls mutating {node.func.attr}"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden, f"{path}:{func.name} calls mutating {node.func.id}"


def test_c1_dashboard_refresh_has_no_active_transition():
    """Dashboard refresh worker only reports and persists dashboard cache."""
    _assert_no_active_transition_calls(ROOT / "dashboard/app.py", {"_update_all_background", "trigger_instant_refresh", "get_usage_logs_report"})


def test_c2_account_report_reads_active_without_writing_marker():
    """pool_report reads Manager.active and never switches/compacts/removes."""
    _assert_no_active_transition_calls(ROOT / "cli/account_reports.py", {"pool_report", "one"})


def test_c3_cusage_keeps_active_marker_unchanged(tmp_path, monkeypatch):
    """CLI usage polling may refresh credentials, but cannot move active slot."""
    store = _seed_codex(tmp_path)
    monkeypatch.setenv("CODEX_ACCOUNT_STORE", str(store))
    from account_manager import Manager
    manager = Manager("codex")
    before = _active_snapshot(store)
    with patch("usage_format.show_account", return_value="ok\n"):
        manager.usage()
    assert _active_snapshot(store) == before
    assert manager.active() == "3"


def test_c4_clist_and_cwho_are_marker_read_only(tmp_path, monkeypatch):
    """List/who paths only read the marker and credentials."""
    store = _seed_codex(tmp_path)
    monkeypatch.setenv("CODEX_ACCOUNT_STORE", str(store))
    from account_manager import Manager, read_json
    manager = Manager("codex")
    before = _active_snapshot(store)
    assert manager.ids() == ["3"]
    assert manager.active() == "3"
    assert read_json(manager.credential("3"))["email"] == "three@example.test"
    assert _active_snapshot(store) == before


def test_c5_health_worker_and_pool_runtime_do_not_transition_active():
    """Health/cooldown reporting code has no account transition call sites."""
    _assert_no_active_transition_calls(ROOT / "bridges/pool_runtime.py", {"candidates", "credentials", "_credentials", "_load_cooldown_record"})
    _assert_no_active_transition_calls(ROOT / "dashboard/app.py", {"_enrich_accounts_data", "get_all_status"})


def test_c6_multiple_background_tasks_keep_active_unchanged(tmp_path, monkeypatch):
    """Concurrent reporting tasks cannot mutate the persistent active marker."""
    from concurrent.futures import ThreadPoolExecutor
    store = _seed_codex(tmp_path)
    monkeypatch.setenv("CODEX_ACCOUNT_STORE", str(store))
    from account_manager import Manager
    manager = Manager("codex")
    before = _active_snapshot(store)
    with patch("usage_format.show_account", return_value="ok\n"):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: manager.usage("3", short=True), range(16)))
    assert _active_snapshot(store) == before
    assert manager.active() == "3"
