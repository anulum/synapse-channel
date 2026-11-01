# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — native runner admission policy
"""Check runner admission order and execute its host refusal gates without privilege."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github/actions/native-user-session/action.yml"


def _steps() -> list[dict[str, Any]]:
    """Read the composite action that the actual Linux matrix invokes."""
    return cast(list[dict[str, Any]], yaml.safe_load(ACTION.read_text())["runs"]["steps"])


@pytest.mark.docs_contract
def test_native_admission_and_probe_precede_every_linux_shard() -> None:
    """Every matrix member uses the admission action before either shard command."""
    job = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]["test"]
    steps = job["steps"]
    admission = [
        index
        for index, step in enumerate(steps)
        if step.get("uses") == "./.github/actions/native-user-session"
    ]
    shards = [
        index
        for index, step in enumerate(steps)
        if "bash tools/run_delegated_tests.sh python -m pytest" in step.get("run", "")
    ]
    assert len(admission) == 1
    assert len(shards) == 2
    assert admission[0] < min(shards)
    assert "if" not in steps[admission[0]]
    assert all("continue-on-error" not in step for step in _steps())
    probe = _steps()[-1]["run"]
    assert "bash tools/run_delegated_tests.sh timeout 15 /usr/bin/bwrap" in probe
    assert "--unshare-user" in probe and "--unshare-pid --unshare-net" in probe
    assert "--ro-bind / / --proc /proc" in probe
    assert "os.ST_RDONLY" in probe and "socket.if_nameindex()" in probe


@pytest.mark.docs_contract
def test_namespace_admission_keeps_the_global_host_policy() -> None:
    """Only the runner's Bubblewrap executable receives an additive profile."""
    admission = _steps()[0]["run"]
    assert "apparmor_parser --add --skip-cache" in admission
    assert "profile synapse-ci-bwrap /usr/bin/bwrap flags=(unconfined)" in admission
    assert "userns," in admission
    assert "sysctl" not in admission
    assert "--replace" not in admission
    assert "--unshare-net" in _steps()[-1]["run"]
    assert '"$userns_policy"' in admission


def test_native_probe_executes_checks_even_with_empty_standard_input(tmp_path: Path) -> None:
    """An empty pipe cannot turn an unexecuted Python namespace probe into success."""
    probe = _steps()[-1]["run"]
    assert '/usr/bin/python3 -c "$probe"' in probe
    code = probe.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    result = subprocess.run(
        [sys.executable, "-c", code, "1000", "user", "mnt", "pid", "net"],
        input="",
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "Native user/mount/PID/network isolation" not in result.stdout


@pytest.mark.parametrize(
    "refusal",
    [
        {"GITHUB_ACTIONS": "false"},
        {"RUNNER_ENVIRONMENT": "self-hosted"},
        {"RUNNER_OS": "macOS"},
    ],
)
def test_admission_refuses_other_hosts_before_any_privileged_operation(
    tmp_path: Path, refusal: dict[str, str]
) -> None:
    """Run the real admission shell with each unsupported environment."""
    env = dict(
        os.environ,
        GITHUB_ACTIONS="true",
        RUNNER_ENVIRONMENT="github-hosted",
        RUNNER_OS="Linux",
    )
    env.update(refusal)
    result = subprocess.run(
        ["bash", "-c", _steps()[0]["run"]],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1, result.stderr
    assert not list(tmp_path.iterdir())
