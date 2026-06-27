# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the `synapse doctor` diagnostic CLI command
"""The ``synapse doctor`` subcommand: surface common coordination misconfigs.

It resolves the coordination identity the way the ``syn`` wrappers do, runs the
local identity/send-name/exposure checks, then queries the hub once to report
reachability and whether this identity's ``-rx`` waiter is live (presence is not
a wake). The check logic lives in :mod:`synapse_channel.client.diagnostics`; this
module only gathers the live inputs, renders the report, and sets the exit code.
``resolve_identity`` is imported lazily inside the handler because
:mod:`synapse_channel.ergonomics` imports :mod:`synapse_channel.cli`, which would
otherwise close an import cycle through this module.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from pathlib import Path
from typing import Any, Protocol

from synapse_channel.cli_queries import AgentFactory, _query_hub
from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.client.diagnostics import (
    Diagnosis,
    check_disk_space,
    check_exposure,
    check_identity,
    check_reachable,
    check_send_identity,
    check_waiter,
    summarise,
)
from synapse_channel.core.protocol import MessageType
from synapse_channel.ops_redeploy import build_redeploy_checklist, render_redeploy_checklist
from synapse_channel.service_setup import install_user_services, service_suggestions

RosterProbe = Callable[..., Awaitable[list[str] | None]]
"""Async callable that returns the live hub roster for doctor diagnostics."""

DiagnoseRunner = Callable[..., Coroutine[Any, Any, tuple[int, list[str]]]]
"""Async callable used by the doctor CLI dispatcher."""


class DiskUsage(Protocol):
    """Filesystem usage fields consumed by doctor diagnostics."""

    @property
    def total(self) -> int:
        """Total bytes on the filesystem."""

    @property
    def free(self) -> int:
        """Free bytes on the filesystem."""


DiskUsageProbe = Callable[
    [int | str | bytes | os.PathLike[str] | os.PathLike[bytes]],
    DiskUsage,
]
"""Callable that returns filesystem usage for a local path."""


def _disk_usage(
    path: int | str | bytes | os.PathLike[str] | os.PathLike[bytes],
) -> DiskUsage:
    """Return local filesystem usage for ``path``."""
    return shutil.disk_usage(path)


async def _fetch_roster(
    *,
    uri: str,
    name: str,
    token: str | None,
    agent_factory: AgentFactory,
    ready_timeout: float = 5.0,
) -> list[str] | None:
    """Return the live roster, or ``None`` when the hub is unreachable.

    Reuses the shared connect → request → poll flow; a non-zero return from the
    query means the hub never answered, which the caller reads as unreachable.
    """
    captured: list[list[str]] = []
    code = await _query_hub(
        uri=uri,
        name=name,
        token=token,
        response_type=MessageType.WHO_SNAPSHOT,
        transform=lambda data: [str(agent) for agent in data.get("online_agents", [])],
        request=lambda agent: agent.request_who(),
        render=lambda roster: captured.append(roster),
        agent_factory=agent_factory,
        ready_timeout=ready_timeout,
    )
    if code != 0:
        return None
    return captured[-1] if captured else []


async def _diagnose(
    *,
    uri: str,
    project: str | None,
    agent_id: str | None,
    token: str | None,
    send_name: str | None = None,
    agent_factory: AgentFactory = SynapseAgent,
    ready_timeout: float = 5.0,
    roster_probe: RosterProbe = _fetch_roster,
    env: Mapping[str, str] | None = None,
    cwd_basename: str | None = None,
    home_basename: str | None = None,
    disk_path: Path | None = None,
    disk_warn_used_percent: float = 95.0,
    disk_warn_free_mib: int = 1024,
    disk_usage_probe: DiskUsageProbe = _disk_usage,
) -> tuple[int, list[str]]:
    """Resolve the identity, run every check, and return ``(exit_code, report_lines)``.

    ``send_name`` checks a specific send identity for project-routable replies
    (the ``<project>-<suffix>`` footgun); it defaults to the resolved identity.
    """
    from synapse_channel.ergonomics import resolve_identity

    env = os.environ if env is None else env
    identity = resolve_identity(
        project=project,
        agent_id=agent_id,
        env=env,
        cwd_basename=Path.cwd().name if cwd_basename is None else cwd_basename,
        home_basename=(
            Path(env.get("HOME", str(Path.home()))).name if home_basename is None else home_basename
        ),
    )
    diagnoses: list[Diagnosis] = [
        check_identity(identity),
        check_send_identity(send_name or identity.identity, project=identity.project),
        check_exposure(uri, token),
    ]
    resolved_disk_path = Path(os.path.abspath(os.sep)) if disk_path is None else disk_path
    usage = disk_usage_probe(resolved_disk_path)
    diagnoses.append(
        check_disk_space(
            resolved_disk_path,
            total_bytes=usage.total,
            free_bytes=usage.free,
            warn_used_percent=disk_warn_used_percent,
            warn_free_mib=disk_warn_free_mib,
        )
    )
    roster = await roster_probe(
        uri=uri,
        name=f"{identity.identity}-doctor",
        token=token,
        agent_factory=agent_factory,
        ready_timeout=ready_timeout,
    )
    diagnoses.append(check_reachable(roster is not None, uri))
    diagnoses.append(check_waiter(roster, identity.waiter_name))
    return summarise(diagnoses)


def _cmd_doctor(
    args: argparse.Namespace,
    *,
    diagnose_runner: DiagnoseRunner = _diagnose,
    async_runner: Callable[[Coroutine[Any, Any, tuple[int, list[str]]]], tuple[int, list[str]]] = (
        asyncio.run
    ),
    service_installer: Callable[..., list[str]] = install_user_services,
    suggestion_builder: Callable[..., list[str]] = service_suggestions,
    env: Mapping[str, str] | None = None,
    cwd_basename: str | None = None,
    home_basename: str | None = None,
) -> int:
    """Dispatch ``doctor``: print the report, exit non-zero when a check fails."""
    code, lines = async_runner(
        diagnose_runner(
            uri=args.uri,
            project=args.project,
            agent_id=args.id,
            token=args.token,
            send_name=args.send_name,
            disk_path=Path(getattr(args, "disk_path", os.path.abspath(os.sep))),
            disk_warn_used_percent=getattr(args, "disk_warn_used_percent", 95.0),
            disk_warn_free_mib=getattr(args, "disk_warn_free_mib", 1024),
        )
    )
    for line in lines:
        print(line)
    if getattr(args, "redeploy_checklist", False):
        from synapse_channel.ergonomics import resolve_identity

        env = os.environ if env is None else env
        identity = resolve_identity(
            project=args.project,
            agent_id=args.id,
            env=env,
            cwd_basename=Path.cwd().name if cwd_basename is None else cwd_basename,
            home_basename=(
                Path(env.get("HOME", str(Path.home()))).name
                if home_basename is None
                else home_basename
            ),
        )
        service_identity = getattr(args, "identity", None) or identity.identity
        for line in render_redeploy_checklist(
            build_redeploy_checklist(
                project=identity.project,
                identity=service_identity,
                hub_uri=args.uri,
                db_path=getattr(args, "db_path", "~/synapse/hub.db"),
                synapse_bin=getattr(args, "synapse_bin", None),
            )
        ):
            print(line)
    if (
        getattr(args, "fix", False)
        or getattr(args, "install_user_services", False)
        or getattr(args, "start_user_services", False)
    ):
        from synapse_channel.ergonomics import resolve_identity

        env = os.environ if env is None else env
        identity = resolve_identity(
            project=args.project,
            agent_id=args.id,
            env=env,
            cwd_basename=Path.cwd().name if cwd_basename is None else cwd_basename,
            home_basename=(
                Path(env.get("HOME", str(Path.home()))).name
                if home_basename is None
                else home_basename
            ),
        )
        service_identity = getattr(args, "identity", None) or identity.identity
        if getattr(args, "install_user_services", False) or getattr(
            args, "start_user_services", False
        ):
            fix_lines = service_installer(
                project=identity.project,
                identity=service_identity,
                synapse_bin=getattr(args, "synapse_bin", None),
                start=getattr(args, "start_user_services", False),
            )
        else:
            fix_lines = [
                "[fix] exact local service setup commands:",
                *suggestion_builder(
                    project=identity.project,
                    identity=service_identity,
                    synapse_bin=getattr(args, "synapse_bin", None),
                ),
                f"[fix] immediate foreground waker: syn arm --project {identity.project}",
            ]
        for line in fix_lines:
            print(line)
    return code


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``doctor`` subparser on the top-level CLI."""
    doctor = subparsers.add_parser(
        "doctor",
        help="Check for common coordination misconfigs (identity, exposure, hub, waiter).",
    )
    doctor.add_argument("--uri", default=DEFAULT_HUB_URI)
    doctor.add_argument(
        "--project", default=None, help="Project identity (over $SYN_PROJECT and the CWD)."
    )
    doctor.add_argument("--id", default=None, help="Short id for a multi-agent identity.")
    doctor.add_argument(
        "--send-name",
        default=None,
        help="A send identity to check for project-routable replies (default: the "
        "resolved identity); flags a <project>-<suffix> name that misses the project inbox.",
    )
    doctor.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    doctor.add_argument(
        "--disk-path",
        default=os.path.abspath(os.sep),
        help="Filesystem path to check for local disk pressure; defaults to the root filesystem.",
    )
    doctor.add_argument(
        "--disk-warn-used-percent",
        type=float,
        default=95.0,
        help="Warn when --disk-path's filesystem is at or above this used percentage.",
    )
    doctor.add_argument(
        "--disk-warn-free-mib",
        type=int,
        default=1024,
        help="Warn when --disk-path's filesystem has less than this many MiB free.",
    )
    doctor.add_argument(
        "--fix",
        action="store_true",
        help="Print exact commands to install/start local hub, presence, and wake services.",
    )
    doctor.add_argument(
        "--install-user-services",
        action="store_true",
        help="Write systemd user units for hub, presence, and wake arming.",
    )
    doctor.add_argument(
        "--start-user-services",
        action="store_true",
        help="Install units, daemon-reload, and enable/start hub, presence, and wake arming.",
    )
    doctor.add_argument(
        "--identity",
        default=None,
        help="Worker identity to arm when fixing services; defaults to resolved identity.",
    )
    doctor.add_argument(
        "--synapse-bin",
        default=None,
        help="Synapse executable path baked into generated units; defaults to PATH lookup.",
    )
    doctor.add_argument(
        "--redeploy-checklist",
        action="store_true",
        help="Print post-release package, service, roster, replay, and git-hook checks.",
    )
    doctor.add_argument(
        "--db-path",
        default="~/synapse/hub.db",
        help="Hub SQLite event-store path to include in --redeploy-checklist.",
    )
    doctor.set_defaults(func=_cmd_doctor)
