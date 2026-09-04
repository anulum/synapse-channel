# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only host and coordination inspection
"""Collect bounded setup evidence without changing the inspected host."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess  # nosec B404
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from synapse_channel import __version__
from synapse_channel.cli_doctor import _diagnose
from synapse_channel.client.diagnostics import Diagnosis
from synapse_channel.setup_contract import SETUP_SCHEMA_VERSION, SetupCheck
from synapse_channel.setup_profiles import SetupProfile

DiagnoseRunner = Callable[..., Awaitable[tuple[int, list[str], list[Diagnosis]]]]
ExecutableProbe = Callable[[str], str | None]
ServiceManagerProbe = Callable[[str], bool]
ServicePidProbe = Callable[[str], int | None]


@dataclass(frozen=True, slots=True)
class PlatformSnapshot:
    """Stable operating-system facts exposed by setup inspection."""

    system: str
    release: str
    machine: str


PlatformProbe = Callable[[], PlatformSnapshot]


def current_platform() -> PlatformSnapshot:
    """Return the current host platform through the standard-library API."""
    return PlatformSnapshot(
        system=platform.system() or "unknown",
        release=platform.release() or "unknown",
        machine=platform.machine() or "unknown",
    )


def systemd_user_manager_available(executable: str) -> bool:
    """Return whether the current user's systemd manager answers read-only."""
    try:
        # ``executable`` is resolved from PATH and every remaining argv token is fixed.
        completed = subprocess.run(  # nosec B603
            [executable, "--user", "show-environment"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def systemd_user_hub_pid(executable: str) -> int | None:
    """Return the active user hub PID through one bounded read-only probe."""
    try:
        completed = subprocess.run(  # nosec B603
            [
                executable,
                "--user",
                "show",
                "--property=MainPID",
                "--value",
                "--",
                "synapse-hub.service",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=2.0,
        )
        value = int(completed.stdout.strip())
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return value if completed.returncode == 0 and value > 1 else None


def _doctor_check(
    diagnoses: list[Diagnosis],
    check_id: str,
    *,
    value: object,
) -> SetupCheck:
    diagnosis = next((item for item in diagnoses if item.check == check_id), None)
    if diagnosis is None:
        return SetupCheck(
            check_id,
            "unavailable",
            True,
            value,
            "The diagnostic probe returned no verdict for this requirement.",
            "Run synapse doctor for a detailed diagnostic report.",
        )
    return SetupCheck(
        check_id,
        diagnosis.status,
        True,
        value,
        diagnosis.detail,
        diagnosis.remedy,
    )


async def inspect_setup(
    profile: SetupProfile,
    *,
    uri: str,
    project: str | None,
    agent_id: str | None,
    env: Mapping[str, str] | None = None,
    executable_probe: ExecutableProbe = shutil.which,
    platform_probe: PlatformProbe = current_platform,
    service_manager_probe: ServiceManagerProbe = systemd_user_manager_available,
    hub_service_pid_probe: ServicePidProbe = systemd_user_hub_pid,
    diagnose_runner: DiagnoseRunner = _diagnose,
) -> dict[str, object]:
    """Inspect one setup profile using only read-only local and hub probes."""
    from synapse_channel.ergonomics import resolve_identity

    environment = os.environ if env is None else env
    identity = resolve_identity(
        project=project,
        agent_id=agent_id,
        env=environment,
        cwd_basename=Path.cwd().name,
        home_basename=Path(environment.get("HOME", str(Path.home()))).name,
    )
    snapshot = platform_probe()
    system = snapshot.system.lower()
    executable = executable_probe("synapse")
    systemctl = executable_probe("systemctl") if system == "linux" else None
    service_manager_available = bool(systemctl is not None and service_manager_probe(systemctl))
    hub_service_pid = (
        hub_service_pid_probe(systemctl)
        if systemctl is not None and service_manager_available
        else None
    )

    checks = [
        SetupCheck(
            "package",
            "pass",
            True,
            {"name": "synapse-channel", "version": __version__},
            "The installed package is importable.",
        ),
        SetupCheck(
            "python",
            "pass" if sys.version_info >= (3, 10) else "fail",
            True,
            {
                "executable": sys.executable,
                "version": platform.python_version(),
            },
            "The running interpreter satisfies Python >=3.10."
            if sys.version_info >= (3, 10)
            else "The running interpreter is older than Python 3.10.",
            "" if sys.version_info >= (3, 10) else "Use Python 3.10 or newer.",
        ),
        SetupCheck(
            "platform",
            "pass" if system in {"linux", "darwin", "windows"} else "warn",
            True,
            {
                "system": snapshot.system,
                "release": snapshot.release,
                "machine": snapshot.machine,
            },
            "The host platform is supported."
            if system in {"linux", "darwin", "windows"}
            else "The host platform has no documented persistent-service adapter.",
            ""
            if system in {"linux", "darwin", "windows"}
            else "Run hub and waiter explicitly, or provide a compatible host adapter.",
        ),
        SetupCheck(
            "executable",
            "pass" if executable is not None else "fail",
            True,
            executable or "",
            "The synapse entry point is discoverable on PATH."
            if executable is not None
            else "The synapse entry point is not discoverable on PATH.",
            ""
            if executable is not None
            else "Activate the package environment or add its scripts directory to PATH.",
        ),
    ]

    try:
        _code, _lines, diagnoses = await diagnose_runner(
            uri=uri,
            project=project,
            agent_id=agent_id,
            token=environment.get("SYNAPSE_TOKEN") or None,
            env=environment,
        )
    except Exception:  # noqa: BLE001 - inspection converts all probe failures to stable evidence
        diagnoses = []

    checks.extend(
        (
            _doctor_check(
                diagnoses,
                "identity",
                value={"project": identity.project, "identity": identity.identity},
            ),
            _doctor_check(diagnoses, "hub", value={"uri": uri}),
            _doctor_check(
                diagnoses,
                "waiter",
                value={"identity": identity.waiter_name},
            ),
            SetupCheck(
                "service_manager",
                "pass" if service_manager_available else "unavailable",
                False,
                {
                    "kind": "systemd-user",
                    "executable": systemctl or "",
                    "hub_pid": hub_service_pid or 0,
                },
                "The systemd user manager answers for optional persistent services."
                if service_manager_available
                else "No supported persistent user service manager answered.",
                ""
                if service_manager_available
                else "Run hub and waiter explicitly in foreground on this host.",
            ),
        )
    )

    required_checks = [check for check in checks if check.required]
    summary = {
        status: sum(check.status == status for check in checks)
        for status in ("pass", "warn", "fail", "unavailable")
    }
    return {
        "schema_version": SETUP_SCHEMA_VERSION,
        "document_kind": "inspection",
        "profile": profile.profile_id,
        "profile_version": profile.version,
        "read_only": True,
        "ready": all(check.status == "pass" for check in required_checks),
        "target": {
            "uri": uri,
            "project": identity.project,
            "identity": identity.identity,
        },
        "summary": summary,
        "checks": [check.as_dict() for check in checks],
    }
