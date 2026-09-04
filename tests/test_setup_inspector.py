# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only setup inspector tests

from __future__ import annotations

from collections.abc import Awaitable
from subprocess import CompletedProcess
from typing import Any

import pytest

from synapse_channel.client.diagnostics import Diagnosis
from synapse_channel.setup_contract import setup_schema
from synapse_channel.setup_inspector import (
    PlatformSnapshot,
    current_platform,
    inspect_setup,
    systemd_user_manager_available,
)
from synapse_channel.setup_profiles import get_setup_profile


def _which(name: str) -> str | None:
    return {"synapse": "/opt/synapse/bin/synapse", "systemctl": "/usr/bin/systemctl"}.get(name)


async def _healthy_diagnose(**_kwargs: Any) -> tuple[int, list[str], list[Diagnosis]]:
    return (
        0,
        [],
        [
            Diagnosis("identity", "pass", "Identity resolves."),
            Diagnosis("hub", "pass", "Hub answers."),
            Diagnosis("waiter", "pass", "Waiter is live."),
        ],
    )


@pytest.mark.asyncio
async def test_inspection_is_ready_deterministic_and_schema_valid() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    profile = get_setup_profile("local-single-user")
    assert profile is not None
    kwargs = {
        "uri": "ws://localhost:8876",
        "project": "DEMO",
        "agent_id": "codex-1",
        "env": {},
        "executable_probe": _which,
        "platform_probe": lambda: PlatformSnapshot("Linux", "6.8", "x86_64"),
        "service_manager_probe": lambda _executable: True,
        "diagnose_runner": _healthy_diagnose,
    }

    first = await inspect_setup(profile, **kwargs)  # type: ignore[arg-type]
    second = await inspect_setup(profile, **kwargs)  # type: ignore[arg-type]

    assert first == second
    assert first["ready"] is True
    assert first["read_only"] is True
    assert first["target"] == {
        "uri": "ws://localhost:8876",
        "project": "DEMO",
        "identity": "DEMO/claude-codex-1",
    }
    jsonschema.validate(first, setup_schema())


@pytest.mark.asyncio
async def test_probe_failure_is_bounded_and_never_echoes_secret() -> None:
    async def fail(**_kwargs: Any) -> tuple[int, list[str], list[Diagnosis]]:
        raise RuntimeError("Bearer super-secret")

    profile = get_setup_profile("local-single-user")
    assert profile is not None
    document = await inspect_setup(
        profile,
        uri="ws://localhost:8876",
        project="DEMO",
        agent_id="codex-1",
        env={"SYNAPSE_TOKEN": "super-secret"},
        executable_probe=_which,
        platform_probe=lambda: PlatformSnapshot("Linux", "6.8", "x86_64"),
        service_manager_probe=lambda _executable: True,
        diagnose_runner=fail,
    )

    assert document["ready"] is False
    assert "super-secret" not in str(document)
    checks = document["checks"]
    assert isinstance(checks, list)
    by_id = {check["id"]: check for check in checks}
    assert by_id["hub"]["status"] == "unavailable"
    assert by_id["waiter"]["status"] == "unavailable"


def test_diagnose_runner_shape_remains_awaitable() -> None:
    result = _healthy_diagnose()
    assert isinstance(result, Awaitable)
    result.close()


def test_current_platform_returns_nonempty_standard_library_facts() -> None:
    snapshot = current_platform()
    assert snapshot.system
    assert snapshot.release
    assert snapshot.machine


def test_systemd_probe_reports_success_nonzero_and_execution_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "synapse_channel.setup_inspector.subprocess.run",
        lambda *_args, **_kwargs: CompletedProcess([], 0),
    )
    assert systemd_user_manager_available("/usr/bin/systemctl") is True
    monkeypatch.setattr(
        "synapse_channel.setup_inspector.subprocess.run",
        lambda *_args, **_kwargs: CompletedProcess([], 1),
    )
    assert systemd_user_manager_available("/usr/bin/systemctl") is False

    def unavailable(*_args: Any, **_kwargs: Any) -> CompletedProcess[str]:
        raise OSError("not executable")

    monkeypatch.setattr("synapse_channel.setup_inspector.subprocess.run", unavailable)
    assert systemd_user_manager_available("/usr/bin/systemctl") is False
