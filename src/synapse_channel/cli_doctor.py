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
import shlex
import shutil
import subprocess  # nosec B404
import sys
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from pathlib import Path
from typing import Any, Protocol

from synapse_channel.cli_cross_repo import NOTIFY_TIMEOUT_SECONDS
from synapse_channel.cli_doctor_federation import (
    DEFAULT_FEDERATION_CERT_WARN_DAYS,
    DEFAULT_FEDERATION_SKEW_WARN_SECONDS,
    diagnose_federation,
)
from synapse_channel.cli_doctor_mailbox import (
    DoctorRoster,
    diagnose_mailbox_pending,
    fetch_doctor_roster,
)
from synapse_channel.cli_queries import AgentFactory
from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.client.diagnostics import (
    Diagnosis,
    check_deaf_agents,
    check_disk_space,
    check_exposure,
    check_identity,
    check_multi_seat_posture,
    check_reachable,
    check_send_identity,
    check_sqlcipher_event_store,
    check_unread_addressees,
    check_waiter,
    summarise,
)
from synapse_channel.ops_redeploy import build_redeploy_checklist, render_redeploy_checklist
from synapse_channel.service_setup import install_user_services, service_suggestions

RosterProbe = Callable[..., Awaitable[DoctorRoster | list[str] | None]]
"""Async callable that returns the live hub roster for doctor diagnostics."""

FederationDiagnoseRunner = Callable[..., Awaitable[list[Diagnosis]]]
"""Async callable that returns opt-in federation doctor diagnoses."""

DiagnoseRunner = Callable[..., Coroutine[Any, Any, tuple[int, list[str], list[Diagnosis]]]]
"""Async callable used by the doctor CLI dispatcher."""

_LOCAL_DEFAULT_URIS = frozenset({"ws://localhost:8876", "ws://127.0.0.1:8876"})
"""The default local hub addresses the generated user services manage."""


def finding_lines(diagnoses: list[Diagnosis]) -> list[str]:
    """Render each non-pass verdict as one stable line for a notify sink.

    ``STATUS check: detail | remedy: …`` — the remedy rides along because the
    sink's reader acts on it directly (a pager message that says *what to run*
    beats one that says *something is wrong*). Healthy checks are omitted: a
    quiet fleet sends nothing.
    """
    return [
        f"{d.status} {d.check}: {d.detail} | remedy: {d.remedy}"
        for d in diagnoses
        if d.status != "pass"
    ]


def run_doctor_notify(command: str, findings: list[str], *, uri: str) -> None:
    """Run the operator's notify command with the findings on stdin.

    The same contract as ``cross-repo --notify-cmd``: the command is split
    with :func:`shlex.split` and executed without a shell (wrap in
    ``sh -c '…'`` for pipes), the checked hub URI is exposed as
    ``SYNAPSE_DOCTOR_URI``, and a failing or hanging sink is reported on
    stderr without changing the doctor's exit code — notification is
    best-effort, the report is the record.
    """
    payload = "\n".join(findings) + "\n"
    try:
        completed = subprocess.run(  # nosec B603
            shlex.split(command),
            input=payload,
            text=True,
            timeout=NOTIFY_TIMEOUT_SECONDS,
            env={**os.environ, "SYNAPSE_DOCTOR_URI": uri},
            check=False,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"notify command failed: {exc}", file=sys.stderr)
        return
    if completed.returncode != 0:
        print(f"notify command exited {completed.returncode}", file=sys.stderr)


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
    roster_probe: RosterProbe = fetch_doctor_roster,
    env: Mapping[str, str] | None = None,
    cwd_basename: str | None = None,
    home_basename: str | None = None,
    disk_path: Path | None = None,
    disk_warn_used_percent: float = 95.0,
    disk_warn_free_mib: int = 1024,
    disk_usage_probe: DiskUsageProbe = _disk_usage,
    feed_tail_reader: Callable[[Mapping[str, str]], list[str]] | None = None,
    cursor_names_reader: Callable[[Mapping[str, str]], list[str]] | None = None,
    federation_peers: tuple[str, ...] = (),
    federation_cursors: tuple[str, ...] = (),
    federation_paths: tuple[str, ...] = (),
    federation_store: Path | None = None,
    federation_token: str | None = None,
    federation_skew_warn_seconds: float = DEFAULT_FEDERATION_SKEW_WARN_SECONDS,
    federation_cert_warn_days: int = DEFAULT_FEDERATION_CERT_WARN_DAYS,
    federation_diagnose_runner: FederationDiagnoseRunner = diagnose_federation,
    multi_seat: bool = False,
    identity_trust: str | Path | None = None,
    role_grants: str | Path | None = None,
    event_store_path: str | Path | None = None,
    event_store_key_file: str | Path | None = None,
) -> tuple[int, list[str], list[Diagnosis]]:
    """Resolve the identity, run every check, and return the summarised verdicts.

    ``send_name`` checks a specific send identity for project-routable replies
    (the ``<project>-<suffix>`` footgun); it defaults to the resolved identity.
    Multi-seat trust materials (``--multi-seat``, identity-trust and role-grants
    paths) feed the team-secure checklist; deaf-agent detection uses the live
    roster. The structured diagnoses ride along with the exit code and report
    lines so ``--fix`` can decide which findings the local service install repairs.
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
    roster_result = await roster_probe(
        uri=uri,
        name=f"{identity.identity}-doctor",
        token=token,
        agent_factory=agent_factory,
        ready_timeout=ready_timeout,
    )
    if isinstance(roster_result, DoctorRoster):
        doctor_roster: DoctorRoster | None = roster_result
        roster: list[str] | None = list(roster_result.agents)
    else:
        doctor_roster = None
        roster = roster_result
    diagnoses.append(check_reachable(roster is not None, uri))
    diagnoses.append(check_waiter(roster, identity.waiter_name))
    diagnoses.append(check_deaf_agents(roster))
    if doctor_roster is not None:
        diagnoses.append(
            diagnose_mailbox_pending(
                doctor_roster.mailbox_pending,
                identity=identity.identity,
            )
        )
    diagnoses.append(check_sqlcipher_event_store(event_store_path, event_store_key_file))
    diagnoses.append(
        check_multi_seat_posture(
            roster=roster,
            token=token,
            identity_trust=identity_trust,
            role_grants=role_grants,
            force=multi_seat,
        )
    )
    diagnoses.append(
        check_unread_addressees(
            feed_lines=feed_tail_reader(env),
            cursor_names=cursor_names_reader(env),
            roster=roster,
        )
    )
    diagnoses.extend(
        await federation_diagnose_runner(
            peer_specs=federation_peers,
            cursor_specs=federation_cursors,
            path_specs=federation_paths,
            local_id=f"{identity.identity}-doctor",
            token=federation_token,
            store_path=federation_store,
            skew_warn_seconds=federation_skew_warn_seconds,
            cert_warn_days=federation_cert_warn_days,
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
    notify_runner: Callable[..., None] = run_doctor_notify,
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

    With ``--notify-cmd CMD`` any warn/fail findings are also piped to the
    operator's sink command, one line each with the remedy attached — the
    diagnostics become a proactive alert instead of a report someone must
    remember to read. A healthy run sends nothing; under ``--fix`` the sink
    receives the state *after* the repair; the sink composes with ``--json``
    (stdout stays one JSON document, the sink gets its own stream).
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
                federation_peers=tuple(getattr(args, "federation_peer", ())),
                federation_cursors=tuple(getattr(args, "federation_cursor", ())),
                federation_paths=tuple(getattr(args, "federation_path", ())),
                federation_store=(
                    None
                    if getattr(args, "federation_store", None) is None
                    else Path(args.federation_store)
                ),
                federation_token=getattr(args, "federation_token", None),
                federation_skew_warn_seconds=getattr(
                    args,
                    "federation_skew_warn_seconds",
                    DEFAULT_FEDERATION_SKEW_WARN_SECONDS,
                ),
                federation_cert_warn_days=getattr(
                    args,
                    "federation_cert_warn_days",
                    DEFAULT_FEDERATION_CERT_WARN_DAYS,
                ),
                multi_seat=bool(getattr(args, "multi_seat", False)),
                identity_trust=getattr(args, "identity_trust", None) or None,
                role_grants=getattr(args, "role_grants", None) or None,
                event_store_path=getattr(args, "db_path", None),
                event_store_key_file=getattr(args, "db_key_file", None),
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
        notify_cmd = getattr(args, "notify_cmd", None)
        findings = finding_lines(diagnoses)
        if notify_cmd and findings:
            notify_runner(notify_cmd, findings, uri=args.uri)
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
            code, lines, diagnoses = diagnose()
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
    notify_cmd = getattr(args, "notify_cmd", None)
    findings = finding_lines(diagnoses)
    if notify_cmd and findings:
        notify_runner(notify_cmd, findings, uri=args.uri)
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
        "--notify-cmd",
        default=None,
        metavar="CMD",
        help="Also pipe any warn/fail findings (one line each, remedy attached) "
        "to this command's stdin — split without a shell, hub URI in "
        "SYNAPSE_DOCTOR_URI, best-effort. A healthy run sends nothing.",
    )
    doctor.add_argument(
        "--federation-peer",
        action="append",
        default=[],
        metavar="PEER=URI",
        help="Probe a federated peer with a multi-hub log request. Repeat for each peer.",
    )
    doctor.add_argument(
        "--federation-cursor",
        action="append",
        default=[],
        metavar="PEER=SEQ",
        help="Local consumed cursor for a named federation peer; defaults to 0.",
    )
    doctor.add_argument(
        "--federation-path",
        action="append",
        default=[],
        metavar="PEER=MODE",
        help=(
            "Declare a peer network path for proxy/pinning diagnostics. MODE is "
            "direct-mtls, tls-passthrough, tailnet, or tls-terminating-proxy."
        ),
    )
    doctor.add_argument(
        "--federation-store",
        default=None,
        metavar="PATH",
        help="Inspect an imported federation store for revocation and bundle expiry state.",
    )
    doctor.add_argument(
        "--federation-token",
        default=None,
        help="Token sent only on federation peer log probes.",
    )
    doctor.add_argument(
        "--federation-skew-warn-seconds",
        type=float,
        default=DEFAULT_FEDERATION_SKEW_WARN_SECONDS,
        help="Warn when measured peer clock skew exceeds this threshold.",
    )
    doctor.add_argument(
        "--federation-cert-warn-days",
        type=int,
        default=DEFAULT_FEDERATION_CERT_WARN_DAYS,
        help="Warn when peer TLS certificates or federation bundles expire within this many days.",
    )
    doctor.add_argument(
        "--multi-seat",
        action="store_true",
        help="Force the multi-seat trust checklist even when the live roster looks "
        "single-seat (token, identity-trust, role-grants → --team-secure).",
    )
    doctor.add_argument(
        "--identity-trust",
        default="",
        metavar="FILE",
        help="Path to an identity trust bundle for the multi-seat checklist "
        "(same file as synapse hub --identity-trust).",
    )
    doctor.add_argument(
        "--role-grants",
        default="",
        metavar="FILE",
        help="Path to a role-grant store for the multi-seat checklist "
        "(same file as synapse hub --role-grants).",
    )
    doctor.add_argument(
        "--redeploy-checklist",
        action="store_true",
        help="Print post-release package, service, roster, replay, and git-hook checks.",
    )
    doctor.add_argument(
        "--db-path",
        default="~/synapse/hub.db",
        help=(
            "Hub SQLite event-store path for --redeploy-checklist and for "
            "SQLCipher doctor checks (same path as synapse hub --db)."
        ),
    )
    doctor.add_argument(
        "--db-key-file",
        default=None,
        help=(
            "Owner-only SQLCipher key file for the event store; when set, doctor "
            "opens --db-path with the key (requires synapse-channel[sqlcipher])."
        ),
    )
    doctor.set_defaults(func=_cmd_doctor)
