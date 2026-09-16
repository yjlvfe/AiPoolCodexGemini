import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]


_WORKER = r'''
import os
import sys
import time
from pathlib import Path

from account_manager import Manager

store = Path(os.environ["CODEX_ACCOUNT_STORE"])
role = os.environ["LOCK_WORKER_ROLE"]
manager = Manager("codex")

if role == "holder":
    with manager.locked(timeout=2):
        Path(os.environ["LOCK_READY"]).write_text("ready")
        while not Path(os.environ["LOCK_RELEASE"]).exists():
            time.sleep(0.01)
elif role == "contender":
    try:
        with manager.locked(timeout=0.2):
            Path(os.environ["LOCK_ENTERED"]).write_text("entered")
    except TimeoutError as error:
        Path(os.environ["LOCK_ERROR"]).write_text(str(error))
elif role == "successor":
    with manager.locked(timeout=1):
        Path(os.environ["LOCK_SUCCEEDED"]).write_text("succeeded")
'''


def _start_worker(store, role, **markers):
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "cli"),
        "CODEX_ACCOUNT_STORE": str(store),
        "LOCK_WORKER_ROLE": role,
        **{name: str(path) for name, path in markers.items()},
    }
    return subprocess.Popen(
        [sys.executable, "-c", _WORKER],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for(path, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)
    raise AssertionError(f"Timed out waiting for {path}")


def test_lock_timeout_does_not_enter_critical_section_and_recovers(tmp_path):
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    entered = tmp_path / "entered"
    error = tmp_path / "error"
    succeeded = tmp_path / "succeeded"

    holder = _start_worker(
        tmp_path,
        "holder",
        LOCK_READY=ready,
        LOCK_RELEASE=release,
    )
    try:
        _wait_for(ready)
        contender = _start_worker(
            tmp_path,
            "contender",
            LOCK_ENTERED=entered,
            LOCK_ERROR=error,
        )
        stdout, stderr = contender.communicate(timeout=3)
        assert contender.returncode == 0, (stdout, stderr)
        assert not entered.exists()
        assert "Failed to acquire store lock within timeout" in error.read_text()

        release.write_text("release")
        holder_stdout, holder_stderr = holder.communicate(timeout=3)
        assert holder.returncode == 0, (holder_stdout, holder_stderr)

        successor = _start_worker(tmp_path, "successor", LOCK_SUCCEEDED=succeeded)
        successor_stdout, successor_stderr = successor.communicate(timeout=3)
        assert successor.returncode == 0, (successor_stdout, successor_stderr)
        assert succeeded.read_text() == "succeeded"
    finally:
        release.touch()
        if holder.poll() is None:
            holder.kill()
            holder.wait()
