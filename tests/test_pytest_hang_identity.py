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
    """Stop, deadline, and the short sleep are separate decisions."""
    import tools.pytest_hang_identity as hang

    stopped = threading.Event()
    stopped.set()
    hang._watch(stopped, time.monotonic() + 5, "node::stopped")
    read_fd, write_fd = os.pipe()
    previous_fd = hang._stderr_fd
    hang._stderr_fd = write_fd
    try:
        hang._watch(threading.Event(), time.monotonic() - 1, "node::due")
        hang._watch(threading.Event(), time.monotonic() + 0.06, "node::slept")
    finally:
        hang._stderr_fd = previous_fd
        os.close(write_fd)
    text = os.read(read_fd, 4096).decode()
    os.close(read_fd)
    assert "TIMEOUT nodeid=node::due" in text
    assert "TIMEOUT nodeid=node::slept" in text
    assert "node::stopped" not in text


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
        assert "node::idle" not in hang._active
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
        assert "node::live" in hang._active
        hang.pytest_runtest_logfinish("node::live", ("f", 1, "t"))
        assert "node::live" not in hang._active
        hang.pytest_configure(cast(pytest.Config, _NumericIni()))
        assert hang._stderr_fd >= 0
        hang.pytest_unconfigure(cast(pytest.Config, _NumericIni()))
        assert hang._stderr_fd == -1
    finally:
        hang._config = previous_config
        hang._stderr_fd = previous_fd


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
