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
import json
import os
import shutil
import sys
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from pathlib import Path
from typing import Any, Protocol

from synapse_channel.cli_queries import AgentFactory, _query_hub
from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.client.diagnostics import (
    Diagnosis,
    check_disk_space,
    check_exposure,
    check_identity,
    check_reachable,
    check_send_identity,
    check_unread_addressees,
    check_waiter,
    summarise,
)
from synapse_channel.core.protocol import MessageType
from synapse_channel.ops_redeploy import build_redeploy_checklist, render_redeploy_checklist
from synapse_channel.service_setup import install_user_services, service_suggestions

RosterProbe = Callable[..., Awaitable[list[str] | None]]
"""Async callable that returns the live hub roster for doctor diagnostics."""

DiagnoseRunner = Callable[..., Coroutine[Any, Any, tuple[int, list[str], list[Diagnosis]]]]
"""Async callable used by the doctor CLI dispatcher."""

_LOCAL_DEFAULT_URIS = frozenset({"ws://localhost:8876", "ws://127.0.0.1:8876"})
"""The default local hub addresses the generated user services manage."""


def service_repairable_checks(diagnoses: list[Diagnosis], *, uri: str) -> list[str]:
    """Return the failed check names the local user services can actually repair.

    Installing and starting the hub, presence, and wake-arming units repairs
    exactly two findings: an unanswering hub and a missing ``-rx`` waiter — and
    only when the diagnosed ``uri`` is the default loopback hub those units
    manage. A remote or non-default hub cannot be repaired by writing local
    systemd units, so against such a URI nothing is auto-repairable.

    Parameters
    ----------
    diagnoses : list[Diagnosis]
        The verdicts a doctor run produced.
    uri : str
        The hub URI the run diagnosed.

    Returns
    -------
    list[str]
        The repairable check names (``"hub"``, ``"waiter"``), in report order;
        empty when nothing the service install fixes has failed.
    """
    if uri not in _LOCAL_DEFAULT_URIS:
        return []
    repairable: list[str] = []
    for diagnosis in diagnoses:
        if diagnosis.check == "hub" and diagnosis.status == "fail":
            repairable.append("hub")
        elif diagnosis.check == "waiter" and diagnosis.status in ("warn", "fail"):
            repairable.append("waiter")
    return repairable


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


FEED_TAIL_BYTES = 512 * 1024
"""How much of the feed's tail the addressee check reads — bounded, newest last."""


def _read_feed_tail(env: Mapping[str, str]) -> list[str]:
    """Return the trailing lines of the shared feed, empty when it is absent."""
    from synapse_channel.ergonomics import syn_home

    feed = syn_home(env) / "feed.ndjson"
    try:
        with feed.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - FEED_TAIL_BYTES))
            data = handle.read()
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()


def _read_cursor_names(env: Mapping[str, str]) -> list[str]:
    """Return the inbox cursor basenames present in the coordination home."""
    from synapse_channel.ergonomics import syn_home

    try:
        return [path.name.removesuffix(".cursor") for path in syn_home(env).glob("*.cursor")]
    except OSError:
        return []


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
    feed_tail_reader: Callable[[Mapping[str, str]], list[str]] | None = None,
    cursor_names_reader: Callable[[Mapping[str, str]], list[str]] | None = None,
) -> tuple[int, list[str], list[Diagnosis]]:
    """Resolve the identity, run every check, and return the summarised verdicts.

    ``send_name`` checks a specific send identity for project-routable replies
    (the ``<project>-<suffix>`` footgun); it defaults to the resolved identity.
    The structured diagnoses ride along with the exit code and report lines so
    ``--fix`` can decide which findings the local service install repairs.
    """
    from synapse_channel.ergonomics import resolve_identity

    env = os.environ if env is None else env
    feed_tail_reader = _read_feed_tail if feed_tail_reader is None else feed_tail_reader
    cursor_names_reader = _read_cursor_names if cursor_names_reader is None else cursor_names_reader
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
    diagnoses.append(
        check_unread_addressees(
            feed_lines=feed_tail_reader(env),
            cursor_names=cursor_names_reader(env),
            roster=roster,
        )
    )
    code, lines = summarise(diagnoses)
    return code, lines, diagnoses


def _resolve_service_identity(
    args: argparse.Namespace,
    *,
    env: Mapping[str, str] | None,
    cwd_basename: str | None,
    home_basename: str | None,
) -> tuple[str, str]:
    """Resolve ``(project, service identity)`` the way the ``syn`` wrappers do."""
    from synapse_channel.ergonomics import resolve_identity

    env = os.environ if env is None else env
    identity = resolve_identity(
        project=args.project,
        agent_id=args.id,
        env=env,
        cwd_basename=Path.cwd().name if cwd_basename is None else cwd_basename,
        home_basename=(
            Path(env.get("HOME", str(Path.home()))).name if home_basename is None else home_basename
        ),
    )
    return identity.project, getattr(args, "identity", None) or identity.identity


def doctor_report_to_json(code: int, diagnoses: list[Diagnosis]) -> dict[str, object]:
    """Return a doctor run as a stable JSON-compatible report.

    ``healthy`` mirrors the exit code (``0`` = no failing check), so a CI gate
    can read either signal; every verdict keeps its check name, status, detail,
    and remedy exactly as the text report prints them.
    """
    return {
        "healthy": code == 0,
        "diagnoses": [
            {
                "check": diagnosis.check,
                "status": diagnosis.status,
                "detail": diagnosis.detail,
                "remedy": diagnosis.remedy,
            }
            for diagnosis in diagnoses
        ],
    }


def _cmd_doctor(
    args: argparse.Namespace,
    *,
    diagnose_runner: DiagnoseRunner = _diagnose,
    async_runner: Callable[
        [Coroutine[Any, Any, tuple[int, list[str], list[Diagnosis]]]],
        tuple[int, list[str], list[Diagnosis]],
    ] = asyncio.run,
    service_installer: Callable[..., list[str]] = install_user_services,
    suggestion_builder: Callable[..., list[str]] = service_suggestions,
    env: Mapping[str, str] | None = None,
    cwd_basename: str | None = None,
    home_basename: str | None = None,
) -> int:
    """Dispatch ``doctor``: print the report, exit non-zero when a check fails.

    With ``--fix`` the safely auto-repairable findings — an unanswering default
    local hub or a missing waiter — are repaired by installing and starting the
    local user services, and the checks are then re-run so the exit code reports
    the state *after* the repair. Findings the services cannot repair (identity,
    exposure, disk, or any non-default hub) are never touched; their remedy
    stays printed guidance.

    With ``--json`` the run is a plain diagnostic — it refuses the mutating and
    checklist flags so stdout is exactly one JSON document — and prints every
    verdict (check, status, detail, remedy) plus the overall health, sized for a
    CI health gate. The exit code is unchanged.
    """

    def diagnose() -> tuple[int, list[str], list[Diagnosis]]:
        return async_runner(
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

    if getattr(args, "json", False):
        mutating = [
            flag
            for flag, present in (
                ("--fix", getattr(args, "fix", False)),
                ("--redeploy-checklist", getattr(args, "redeploy_checklist", False)),
                ("--install-user-services", getattr(args, "install_user_services", False)),
                ("--start-user-services", getattr(args, "start_user_services", False)),
            )
            if present
        ]
        if mutating:
            print(
                f"doctor --json is a plain diagnostic; drop {', '.join(mutating)}",
                file=sys.stderr,
            )
            return 2
        code, _, diagnoses = diagnose()
        print(json.dumps(doctor_report_to_json(code, diagnoses), sort_keys=True))
        return code

    code, lines, diagnoses = diagnose()
    for line in lines:
        print(line)
    if getattr(args, "redeploy_checklist", False):
        project, service_identity = _resolve_service_identity(
            args, env=env, cwd_basename=cwd_basename, home_basename=home_basename
        )
        for line in render_redeploy_checklist(
            build_redeploy_checklist(
                project=project,
                identity=service_identity,
                hub_uri=args.uri,
                db_path=getattr(args, "db_path", "~/synapse/hub.db"),
                synapse_bin=getattr(args, "synapse_bin", None),
            )
        ):
            print(line)
    install_requested = getattr(args, "install_user_services", False) or getattr(
        args, "start_user_services", False
    )
    if install_requested:
        project, service_identity = _resolve_service_identity(
            args, env=env, cwd_basename=cwd_basename, home_basename=home_basename
        )
        for line in service_installer(
            project=project,
            identity=service_identity,
            synapse_bin=getattr(args, "synapse_bin", None),
            start=getattr(args, "start_user_services", False),
        ):
            print(line)
    elif getattr(args, "fix", False):
        project, service_identity = _resolve_service_identity(
            args, env=env, cwd_basename=cwd_basename, home_basename=home_basename
        )
        repairable = service_repairable_checks(diagnoses, uri=args.uri)
        service_shaped = [
            d.check
            for d in diagnoses
            if (d.check == "hub" and d.status == "fail")
            or (d.check == "waiter" and d.status != "pass")
        ]
        if repairable:
            print(
                f"[fix] auto-repairing {', '.join(repairable)}: installing and starting "
                "the local user services"
            )
            for line in service_installer(
                project=project,
                identity=service_identity,
                synapse_bin=getattr(args, "synapse_bin", None),
                start=True,
            ):
                print(line)
            code, lines, _ = diagnose()
            print("[fix] re-check:")
            for line in lines:
                print(line)
        elif service_shaped:
            print(
                f"[fix] nothing auto-repaired: {args.uri} is not the default local hub "
                "the generated user services manage — repair that hub where it runs. "
                "Manual setup commands:"
            )
            for line in suggestion_builder(
                project=project,
                identity=service_identity,
                synapse_bin=getattr(args, "synapse_bin", None),
            ):
                print(line)
        else:
            print("[fix] nothing to auto-repair — no hub or waiter finding")
    return code


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``doctor`` subparser on the top-level CLI."""
    doctor = subparsers.add_parser(
        "doctor",
        help="Check for common coordination misconfigs (identity, exposure, hub, waiter).",
    )
    doctor.add_argument("--uri", default=default_hub_uri())
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
        help="Auto-repair the safely repairable findings: when the default local hub "
        "does not answer or the waiter is missing, install and start the local hub, "
        "presence, and wake services, then re-run the checks. Anything else (identity, "
        "exposure, disk, a non-default hub) is reported, never touched.",
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
        "--json",
        action="store_true",
        help="Emit every verdict plus overall health as one JSON document for CI "
        "health gates; refuses the mutating and checklist flags.",
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
