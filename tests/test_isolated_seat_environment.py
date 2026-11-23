# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — ambient seat variables are isolated from the test suite
"""Prove the suite clears ambient seat-shell variables and restores them on teardown."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SEAT_VARIABLES: tuple[str, ...] = (
    "SYN_INBOX_SOURCE",
    "SYN_HOME",
    "SYN_IDENTITY",
    "SYN_PROJECT",
    "SYN_TMUX_PROVIDER",
    "SYNAPSE_WAITER_PIDFILE",
    "SYNAPSE_AUTO_CONNECT",
)
_REPO_ROOT = Path(__file__).resolve().parents[1]
_GUARD_NODE = (
    "tests/test_isolated_seat_environment.py::test_ambient_seat_variables_are_absent_unless_set"
)


def test_ambient_seat_variables_are_absent_unless_set() -> None:
    """None of the seat-shell variables is visible inside a test unless the test set it."""
    leaked = {name: os.environ[name] for name in _SEAT_VARIABLES if name in os.environ}

    assert leaked == {}


def test_monkeypatch_can_set_seat_variables_for_one_test(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Tests that exercise environment resolution keep overriding variables per test."""
    expected = {
        "SYN_INBOX_SOURCE": "hub",
        "SYN_HOME": str(tmp_path / "synapse-test-home"),
        "SYN_IDENTITY": "DEMO/test-agent",
        "SYN_PROJECT": "DEMO",
        "SYN_TMUX_PROVIDER": "1",
        "SYNAPSE_WAITER_PIDFILE": str(tmp_path / "synapse-test-waiter.pid"),
        "SYNAPSE_AUTO_CONNECT": "0",
    }
    for name, value in expected.items():
        monkeypatch.setenv(name, value)

    for name, value in expected.items():
        assert os.environ[name] == value


def test_conftest_clears_exported_seat_variables_and_restores_them(tmp_path: Path) -> None:
    """A pytest session launched with all seat variables exported clears and restores them."""
    exported = {
        "SYN_INBOX_SOURCE": "hub",
        "SYN_HOME": str(tmp_path / "seat-home"),
        "SYN_IDENTITY": "DEMO/seat-owner",
        "SYN_PROJECT": "DEMO",
        "SYN_TMUX_PROVIDER": "1",
        "SYNAPSE_WAITER_PIDFILE": str(tmp_path / "waiter.pid"),
        "SYNAPSE_AUTO_CONNECT": "0",
    }
    env = dict(os.environ)
    env.update(exported)
    env["SYNAPSE_TEST_HERMETIC_CANARY"] = "preserved"

    script = (
        "import json, os, sys, pytest\n"
        "code = pytest.main(['-q', '--no-header', '-o', 'addopts=', sys.argv[1]])\n"
        "names = json.loads(sys.argv[2])\n"
        "restored = {name: os.environ.get(name) for name in names}\n"
        "print(json.dumps({'code': code, 'restored': restored, "
        "'canary': os.environ.get('SYNAPSE_TEST_HERMETIC_CANARY')}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, _GUARD_NODE, json.dumps(list(_SEAT_VARIABLES))],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.splitlines()[-1])
    assert result["code"] == 0, completed.stdout
    assert result["restored"] == exported
    assert result["canary"] == "preserved"


def test_conftest_keeps_unset_seat_variables_absent_after_teardown() -> None:
    """Seat variables absent before the session remain absent after teardown."""
    env = dict(os.environ)
    for name in _SEAT_VARIABLES:
        env.pop(name, None)

    script = (
        "import json, os, sys, pytest\n"
        "code = pytest.main(['-q', '--no-header', '-o', 'addopts=', sys.argv[1]])\n"
        "names = json.loads(sys.argv[2])\n"
        "present = [name for name in names if name in os.environ]\n"
        "print(json.dumps({'code': code, 'present': present}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, _GUARD_NODE, json.dumps(list(_SEAT_VARIABLES))],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.splitlines()[-1])
    assert result["code"] == 0, completed.stdout
    assert result["present"] == []
