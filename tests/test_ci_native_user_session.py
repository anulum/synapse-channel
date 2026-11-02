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

from fixtures.capable_bwrap import ENVIRONMENT

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github/actions/native-user-session/action.yml"


ISOLATION_PROBE = "Require real user, mount, PID and network isolation before tests"
CAPABILITY_PROBE = "Prove which Bubblewrap may use capabilities inside its own user namespace"


def _steps() -> list[dict[str, Any]]:
    """Read the composite action that the actual Linux matrix invokes."""
    return cast(list[dict[str, Any]], yaml.safe_load(ACTION.read_text())["runs"]["steps"])


def _run(name: str) -> str:
    """Return the shell script of the one action step with ``name``."""
    (step,) = [step for step in _steps() if step["name"] == name]
    return cast(str, step["run"])


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
    probe = _run(ISOLATION_PROBE)
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
    assert "--unshare-net" in _run(ISOLATION_PROBE)
    assert '"$userns_policy"' in admission


@pytest.mark.docs_contract
def test_a_shipped_bwrap_profile_is_used_instead_of_a_conflicting_one() -> None:
    """Ubuntu's own bwrap profile is kept when loaded, and the probe checks its label.

    Loading a second profile for /usr/bin/bwrap beside Ubuntu's bwrap-userns-restrict
    leaves bwrap unconfined, so it cannot bring up loopback in a new network namespace.
    """
    admission = _steps()[0]["run"]
    shipped = admission.index("grep -qx 'unpriv_bwrap (enforce)'")
    assert admission.index("grep -qx 'bwrap (enforce)'") < shipped
    assert shipped < admission.index("apparmor_parser --add") < admission.index("fi\n")
    assert "bwrap_label='bwrap//&unpriv_bwrap (enforce)'" in admission
    assert "bwrap_label='synapse-ci-bwrap (unconfined)'" in admission
    assert "SYNAPSE_BWRAP_LABEL=%s" in admission and '"$GITHUB_ENV"' in admission
    probe = _run(ISOLATION_PROBE)
    assert "== sys.argv[1]" in probe
    assert '"$probe" "$SYNAPSE_BWRAP_LABEL" "$host_uid"' in probe


@pytest.mark.docs_contract
def test_only_a_root_owned_copy_gets_capabilities_and_only_on_the_shipped_profile_path() -> None:
    """On 26.04 a separate root-owned copy carries the 24.04 rule; Ubuntu's profiles stay.

    The namespace fixtures that mount inside their own user namespace use the copy;
    every other sandbox keeps Ubuntu's capability-free children. On 24.04 the
    system Bubblewrap already carries the rule, so the variable names it.
    """
    admission = _run("Admit only Bubblewrap to user namespaces on the disposable runner")
    shipped_start = admission.index("bwrap_label='bwrap//&unpriv_bwrap (enforce)'")
    other_start = admission.index("\nelse\n")
    shipped, other = admission[shipped_start:other_start], admission[other_start:]
    copy = "/usr/local/libexec/synapse-ci/bwrap"
    install = 'sudo -n install -o root -g root -m 0755 /usr/bin/bwrap "$capable_bwrap"'
    digest = 'test "$(sha256sum < /usr/bin/bwrap)" = "$(sha256sum < "$capable_bwrap")"'
    profile = (
        "abi <abi/4.0>,\n"
        f"profile synapse-ci-capable-bwrap {copy} flags=(unconfined) {{\n"
        "  userns,\n"
        "}\n"
    )
    assert f"capable_bwrap={copy}" in shipped
    assert "sudo -n install -d -o root -g root -m 0755 /usr/local/libexec/synapse-ci" in shipped
    assert shipped.index(install) < shipped.index(digest) < shipped.index("apparmor_parser --add")
    assert profile in shipped
    assert "synapse-ci-capable-bwrap" not in other and copy not in other
    assert "capable_bwrap=/usr/bin/bwrap" in other
    assert "profile synapse-ci-bwrap /usr/bin/bwrap flags=(unconfined)" in other
    assert f"{ENVIRONMENT}=%s" in admission
    assert admission.rstrip().endswith('= "$userns_policy"')
    assert "--replace" not in admission and "sysctl" not in admission


@pytest.mark.docs_contract
def test_capability_probes_use_an_actual_mount_and_fail_closed() -> None:
    """The copy must mount in its namespace; on 26.04 the system Bubblewrap must not."""
    names = [step["name"] for step in _steps()]
    assert names.index(ISOLATION_PROBE) < names.index(CAPABILITY_PROBE)
    run = _run(CAPABILITY_PROBE)
    assert f'run_probe "${ENVIRONMENT}" capable' in run
    guard = run.index(f'if [[ "${ENVIRONMENT}" != /usr/bin/bwrap ]]; then')
    assert guard < run.index("run_probe /usr/bin/bwrap restricted")
    assert "bash tools/run_delegated_tests.sh timeout 15" in run
    assert "--cap-add CAP_SYS_ADMIN" in run and "--tmpfs /tmp" in run
    code = run.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    assert "libc.mount(b'none', target.encode(), b'tmpfs', 0, None) == 0" in code
    assert "assert mounted and effective >> 21 & 1" in code
    assert "assert not mounted and error in (1, 13)" in code
    assert "if line.startswith('Cap'):" in code


@pytest.mark.parametrize("arguments", [[], ["bogus"]])
def test_capability_probe_refuses_a_missing_or_unknown_mode_before_any_effect(
    tmp_path: Path, arguments: list[str]
) -> None:
    """An unknown probe mode fails before it creates or mounts anything."""
    run = _run(CAPABILITY_PROBE)
    code = run.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    result = subprocess.run(
        [sys.executable, "-c", code, *arguments],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "verified by an actual mount" not in result.stdout
    assert not list(tmp_path.iterdir())


def test_the_delegated_runner_passes_the_capability_variable_to_the_tests() -> None:
    """The variable reaches the fixtures; the delegated runner drops unlisted variables."""
    runner = (ROOT / "tools/run_delegated_tests.sh").read_text()
    assert f"SYNAPSE_TEST_COVERAGE_DIR {ENVIRONMENT} \\\n" in runner


@pytest.mark.docs_contract
def test_push_runs_use_the_qualified_image_and_manual_runs_can_pick_the_old_one() -> None:
    """Push runs use the qualified ubuntu-26.04 image; a manual run may still pick 24.04."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    dispatch = workflow[True]["workflow_dispatch"]["inputs"]["test_runner"]
    assert dispatch["default"] == "ubuntu-26.04"
    assert dispatch["options"] == ["ubuntu-24.04", "ubuntu-26.04"]
    assert workflow["jobs"]["test"]["runs-on"] == "${{ inputs.test_runner || 'ubuntu-26.04' }}"


def test_native_probe_executes_checks_even_with_empty_standard_input(tmp_path: Path) -> None:
    """An empty pipe cannot turn an unexecuted Python namespace probe into success."""
    probe = _run(ISOLATION_PROBE)
    assert '/usr/bin/python3 -c "$probe"' in probe
    code = probe.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    result = subprocess.run(
        [sys.executable, "-c", code, "synapse-ci-bwrap (unconfined)", "1000"]
        + ["user", "mnt", "pid", "net"],
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
