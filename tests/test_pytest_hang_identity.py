# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — a hung test must name itself, and apt setup must fail fast

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
# The probe really sleeps longer than pytest's faulthandler timeout. The outer
# bound is that sleep, the faulthandler timeout, and room for a slow macOS
# runner to start pytest. A child that never exits still fails and is reaped.
_FAULT_TIMEOUT_SECONDS = 0.4
_HANG_SLEEP_SECONDS = 2.0
_OUTER_BOUND_SECONDS = _HANG_SLEEP_SECONDS + _FAULT_TIMEOUT_SECONDS + 45.0


def test_faulthandler_timeout_prints_the_running_node_id(tmp_path: Path) -> None:
    """A real hang prints its node id, then the child exits and is reaped.

    The macOS shard exceeded a fixed 20 second outer bound. The cause is not
    established: the old watcher join used ``timeout=2``, so that join cannot
    block for 20 seconds, and there is no macOS stack sample. Sleeping in
    slices instead of ``Event.wait`` is only a suspected way to stay clear of
    the faulthandler watchdog. The outer bound also has room for a slow
    runner, so a pass can come from that room alone. The hosted cross-os run
    on the pushed head is the macOS proof.
    """
    test_file = tmp_path / "test_hang_identity_probe.py"
    test_file.write_text(
        "import time\n\n"
        "def test_fast():\n"
        "    assert True\n\n"
        "def test_hangs():\n"
        f"    time.sleep({_HANG_SLEEP_SECONDS!r})\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    command = [
        sys.executable,
        "-m",
        "pytest",
        str(test_file),
        "--noconftest",
        "-p",
        "tools.pytest_hang_identity",
        "-p",
        "no:cacheprovider",
        "-o",
        f"faulthandler_timeout={_FAULT_TIMEOUT_SECONDS}",
        "-q",
    ]
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=_OUTER_BOUND_SECONDS)
    except subprocess.TimeoutExpired as expired:
        process.kill()
        stdout, stderr = process.communicate()
        output = (stdout or "") + (stderr or "")
        raise AssertionError(
            "inner pytest did not exit after the hang sleep\n" + output[-2500:]
        ) from expired
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    output = stdout + stderr
    hang_lines = [line for line in output.splitlines() if "TIMEOUT nodeid=" in line]
    assert hang_lines, output[-2500:]
    assert any(line.endswith("::test_hangs") or "::test_hangs" in line for line in hang_lines)
    assert all(not line.endswith("::test_fast") for line in hang_lines)
    assert process.returncode == 0


def test_watch_branches_are_deterministic() -> None:
    """Halt, idle, wait, and report are direct decisions of one session watcher.

    Stop and join the live session watcher before changing its arm. Clearing
    the thread pointer and joining None leaves that thread alive, and it can
    mark the due arm reported before this assertion reads it.
    """
    import tools.pytest_hang_identity as hang

    previous_stop = hang._session_stop.is_set()
    previous_arm = hang._arm
    previous_fd = hang._stderr_fd
    live = hang._session_thread
    was_alive = live is not None and live.is_alive()
    hang._stop_session_watcher()
    read_fd = -1
    write_fd = -1
    due: hang._Arm | None = None
    try:
        # _stop clears the global before join returns, so the global being
        # None does not prove the watcher stopped. Check the thread itself.
        assert live is None or not live.is_alive()
        hang._session_stop.set()
        assert hang._take_action(time.monotonic()) == ("halt", "")
        hang._session_stop.clear()
        hang._arm = None
        assert hang._take_action(time.monotonic()) == ("idle", "")
        stopped = hang._Arm("node::stopped", time.monotonic() + 5, stopped=True)
        hang._arm = stopped
        assert hang._take_action(time.monotonic()) == ("idle", "")
        reported = hang._Arm("node::reported", time.monotonic() - 1, reported=True)
        hang._arm = reported
        assert hang._take_action(time.monotonic()) == ("idle", "")
        waiting = hang._Arm("node::wait", time.monotonic() + 5)
        hang._arm = waiting
        assert hang._take_action(time.monotonic()) == ("wait", "")
        read_fd, write_fd = os.pipe()
        hang._stderr_fd = write_fd
        due = hang._Arm("node::due", time.monotonic() - 1)
        hang._arm = due
        assert hang._take_action(time.monotonic()) == ("report", "node::due")
        action, nodeid = hang._take_action(time.monotonic())
        assert (action, nodeid) == ("idle", "")
        hang._write_timeout("node::due")
        text = os.read(read_fd, 4096).decode()
        assert text == "TIMEOUT nodeid=node::due\n"
        assert due.reported is True
    finally:
        hang._arm = previous_arm
        hang._stderr_fd = previous_fd
        if write_fd >= 0:
            os.close(write_fd)
        if read_fd >= 0:
            os.close(read_fd)
        if previous_stop:
            hang._session_stop.set()
        else:
            hang._session_stop.clear()
        if was_alive:
            hang._ensure_session_watcher()


def test_idle_hooks_and_a_bad_timeout_do_not_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing config, a bad ini value, and a closed stderr do not name a test."""
    import tools.pytest_hang_identity as hang

    previous_config = hang._config
    previous_fd = hang._stderr_fd
    try:
        hang._config = None
        hang._stderr_fd = -1
        assert hang._limit() == 0.0
        hang._write_timeout("node::silent")
        hang.pytest_runtest_logstart("node::idle", ("f", 1, "t"))
        assert hang._arm is None or hang._arm.nodeid != "node::idle"
        hang.pytest_runtest_logfinish("node::absent", ("f", 1, "t"))

        class _BadIni:
            def getini(self, name: str) -> str:
                del name
                return "nope"

        hang._config = cast(pytest.Config, _BadIni())
        assert hang._limit() == 0.0

        class _EmptyIni:
            def getini(self, name: str) -> str:
                del name
                return ""

        hang._config = cast(pytest.Config, _EmptyIni())
        assert hang._limit() == 0.0

        monkeypatch.setattr(sys, "__stderr__", None)
        hang.pytest_configure(cast(pytest.Config, _EmptyIni()))
        assert hang._stderr_fd == -1

        def _boom(fd: int) -> int:
            del fd
            raise OSError("dup failed")

        monkeypatch.setattr(os, "dup", _boom)
        monkeypatch.setattr(sys, "__stderr__", sys.stderr)
        hang.pytest_configure(cast(pytest.Config, _EmptyIni()))
        assert hang._stderr_fd == -1
        hang.pytest_unconfigure(cast(pytest.Config, _EmptyIni()))
        assert hang._stderr_fd == -1

        class _NumericIni:
            def getini(self, name: str) -> str:
                del name
                return "0.4"

        monkeypatch.undo()
        hang._config = cast(pytest.Config, _NumericIni())
        hang._stderr_fd = -1
        hang.pytest_runtest_logstart("node::live", ("f", 1, "t"))
        assert hang._arm is not None and hang._arm.nodeid == "node::live"
        time.sleep(0.05)
        before = threading.active_count()
        for number in range(5):
            hang.pytest_runtest_logstart(f"node::live-{number}", ("f", 1, "t"))
            hang.pytest_runtest_logfinish(f"node::live-{number}", ("f", 1, "t"))
        time.sleep(0.05)
        assert threading.active_count() == before
        report_read, report_write = os.pipe()
        hang._stderr_fd = report_write
        with hang._arm_lock:
            hang._arm = hang._Arm("node::overdue", time.monotonic() - 1)
        time.sleep(0.15)
        os.close(report_write)
        reported = os.read(report_read, 4096).decode()
        os.close(report_read)
        assert reported == "TIMEOUT nodeid=node::overdue\n"
        hang.pytest_runtest_logfinish("node::overdue", ("f", 1, "t"))
        assert hang._arm is not None and hang._arm.stopped is True
        hang.pytest_configure(cast(pytest.Config, _NumericIni()))
        assert hang._stderr_fd >= 0
        hang.pytest_unconfigure(cast(pytest.Config, _NumericIni()))
        assert hang._stderr_fd == -1
        assert hang._session_thread is None or not hang._session_thread.is_alive()
    finally:
        hang._config = previous_config
        hang._stderr_fd = previous_fd
        if previous_config is not None and (
            hang._session_thread is None or not hang._session_thread.is_alive()
        ):
            hang._session_stop.clear()
            hang._ensure_session_watcher()


def test_softhsm_setup_names_itself_and_times_out() -> None:
    """The PKCS#11 package install cannot consume the whole shard job timeout."""
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "name: Install SoftHSM and the software TPM" in workflow
    assert "timeout 300 bash -c" in workflow
    inner = workflow.split("timeout 300 bash -c '", 1)[1].split("'", 1)[0]
    assert inner.lstrip().startswith("set -euo pipefail")
    assert 'python-version: ["3.11", "3.12", "3.13"]' in workflow
    assert "--python 3.11 3.12 3.13 --count 4" in workflow
    assert "3.10" not in workflow
    assert "Python 3.10 journeys" not in workflow
    conftest = (ROOT / "tests/conftest.py").read_text(encoding="utf-8")
    assert 'pytest_plugins = ["tools.pytest_hang_identity"]' not in conftest
    assert '"pytest_hang_identity.py"' in conftest
    assert "pluginmanager.register" in conftest


def test_bare_pytest_loads_hang_identity_without_pythonpath() -> None:
    """The console-script pytest used by the OpenCode workflows can collect.

    Those jobs invoke ``pytest`` with no ``PYTHONPATH``. The repository root is
    then absent from ``sys.path``, and a ``tools.*`` plugin import fails before
    collection. One file from each of those command lines is enough to prove
    the shared conftest loads the watcher.
    """
    pytest_bin = Path(sys.executable).with_name("pytest")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    commands = (
        [str(pytest_bin), "tests/test_opencode_compatibility_contract.py"],
        [str(pytest_bin), "tests/test_opencode_plugin.py"],
        [str(pytest_bin), "tests/test_opencode_editor_workflow_contract.py"],
    )
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        output = completed.stdout + completed.stderr
        assert "No module named tools" not in output, output[-2000:]
        assert completed.returncode == 0, output[-2000:]
