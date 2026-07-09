# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — local health checks behind the `synapse doctor` command
"""Local health checks behind ``synapse doctor``.

Each check produces a :class:`Diagnosis` — a named pass/warn/fail verdict with a
concrete remedy — so the dispatch can render them and exit non-zero when a check
fails. The checks are pure functions of their inputs (a resolved identity, the
configured URI and token, a reachability result, the live roster), so the whole
diagnosis is testable without a live hub; :mod:`synapse_channel.cli_doctor`
gathers the live inputs and renders the report.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

from synapse_channel.core.hub import is_loopback_host
from synapse_channel.waiter_identity import split_roster, waiter_name, waiter_owner

if TYPE_CHECKING:
    # Import-time-only: keeps the ``Identity`` type annotation without importing
    # ``ergonomics`` at runtime, which would close an ergonomics -> cli -> cli_doctor
    # -> diagnostics import cycle.
    from synapse_channel.ergonomics import Identity

DoctorStatus = Literal["pass", "warn", "fail"]
"""The three verdicts a check can return."""

_STATUS_OK_MARK = "ok"
"""Rendered marker for a passing doctor check."""

_STATUS_MARK: dict[DoctorStatus, str] = {
    "pass": _STATUS_OK_MARK,
    "warn": "warn",
    "fail": "FAIL",
}
"""How each status is marked in a rendered report line."""


@dataclass(frozen=True)
class Diagnosis:
    """One named check verdict with a remedy.

    Attributes
    ----------
    check : str
        Short check name shown in the report.
    status : {"pass", "warn", "fail"}
        ``pass`` healthy, ``warn`` a non-fatal footgun, ``fail`` a misconfiguration
        the user almost certainly wants to fix.
    detail : str
        One line on what was found.
    remedy : str
        What to do about it; empty when nothing is needed.
    """

    check: str
    status: DoctorStatus
    detail: str
    remedy: str = ""


def check_identity(identity: Identity) -> Diagnosis:
    """Verify the resolved coordination identity is deliberate, not accidental.

    A project that resolves to the home directory, a system path, or an empty
    string is almost always an accident, and one derived from the working
    directory is fragile (the CWD resets between harness tool calls), so each is
    surfaced with the fix.
    """
    if not identity.plausible:
        return Diagnosis(
            check="identity",
            status="fail",
            detail=(
                f"resolved project {identity.project!r} (from {identity.source}) looks "
                "accidental — the home directory, a system path, or empty"
            ),
            remedy="set $SYN_PROJECT or pass --project <repo> so the identity is explicit",
        )
    if identity.source == "cwd":
        return Diagnosis(
            check="identity",
            status="warn",
            detail=f"project {identity.project!r} was derived from the working directory",
            remedy="set $SYN_PROJECT or pass --project to pin it (the CWD drifts between calls)",
        )
    return Diagnosis(
        check="identity",
        status="pass",
        detail=(
            f"project {identity.project!r} (identity {identity.identity!r}) from {identity.source}"
        ),
    )


def check_send_identity(send_name: str, *, project: str) -> Diagnosis:
    """Flag a send name that would not receive project-routed replies.

    Project membership is the ``<project>/<seat>`` slash convention; a
    ``<project>-<suffix>`` hyphen name sits OUTSIDE its own project namespace, so a
    reply addressed to it never reaches the project inbox. The ``-rx``/``-keeper``
    hyphen suffixes are for connection/presence names, never a send identity.
    """
    if send_name == project or send_name.startswith(f"{project}/"):
        return Diagnosis(
            check="send-identity",
            status="pass",
            detail=f"send name {send_name!r} is project-routable",
        )
    if send_name.startswith(f"{project}-"):
        return Diagnosis(
            check="send-identity",
            status="warn",
            detail=(
                f"send name {send_name!r} is a hyphen child of project {project!r}, so it "
                "sits outside the project namespace and replies to it miss the project inbox"
            ),
            remedy=f"send as the bare project {project!r} or a slash seat {project}/<seat>",
        )
    return Diagnosis(
        check="send-identity",
        status="pass",
        detail=f"send name {send_name!r} is unrelated to project {project!r}",
    )


def check_exposure(uri: str, token: str | None) -> Diagnosis:
    """Warn when the hub URI points off loopback without a token.

    An off-loopback hub is reachable from the network; the hub itself refuses to
    bind such an address unauthenticated, so a client pointed there without a
    token would also fail to connect.
    """
    host = urlparse(uri).hostname or ""
    if is_loopback_host(host):
        return Diagnosis(
            check="exposure",
            status="pass",
            detail=f"hub URI {uri!r} is loopback-only",
        )
    if token:
        return Diagnosis(
            check="exposure",
            status="pass",
            detail=f"hub URI {uri!r} is off loopback but a token is set",
        )
    return Diagnosis(
        check="exposure",
        status="warn",
        detail=f"hub URI {uri!r} is off loopback with no token",
        remedy="set --token (the hub refuses to bind an unauthenticated off-loopback host)",
    )


def check_disk_space(
    path: str | PathLike[str],
    *,
    total_bytes: int,
    free_bytes: int,
    warn_used_percent: float,
    warn_free_mib: int,
) -> Diagnosis:
    """Report local filesystem pressure for the path Synapse should monitor.

    Parameters
    ----------
    path : str or PathLike[str]
        Filesystem path whose containing mount was measured.
    total_bytes : int
        Total bytes available on the containing filesystem.
    free_bytes : int
        Free bytes available on the containing filesystem.
    warn_used_percent : float
        Warn when used space is greater than or equal to this percentage.
    warn_free_mib : int
        Warn when free space is below this many MiB.
    """
    free_mib = free_bytes / (1024 * 1024)
    if total_bytes <= 0:
        return Diagnosis(
            check="disk",
            status="warn",
            detail=f"could not compute filesystem pressure for {str(path)!r}",
            remedy="verify the path exists and re-run `synapse doctor --disk-path <path>`",
        )
    used_percent = 100.0 * (1.0 - (free_bytes / total_bytes))
    detail = f"{str(path)!r} has {free_mib:.1f} MiB free ({used_percent:.1f}% used)"
    if used_percent >= warn_used_percent or free_mib < warn_free_mib:
        return Diagnosis(
            check="disk",
            status="warn",
            detail=detail,
            remedy=(
                "move build trees, caches, logs, or virtualenvs off the pressured "
                "filesystem before long-running Synapse sessions"
            ),
        )
    return Diagnosis(check="disk", status="pass", detail=detail)


def check_reachable(reachable: bool, uri: str) -> Diagnosis:
    """Report whether the hub answered a roster query."""
    if reachable:
        return Diagnosis(check="hub", status="pass", detail=f"hub at {uri} answered")
    return Diagnosis(
        check="hub",
        status="fail",
        detail=f"hub at {uri} did not answer",
        remedy="start it with `synapse hub`, or point --uri at a running hub",
    )


def check_waiter(roster: list[str] | None, waiter_name: str) -> Diagnosis:
    """Report whether this identity's ``-rx`` waiter is live on the bus.

    Presence (reachable) and a live waiter (promptly woken) are different: an
    agent whose ``-rx`` waiter is absent from the roster stays reachable yet is
    never woken — it goes dark — so a missing waiter is a warning, not a pass.
    """
    if roster is None:
        return Diagnosis(
            check="waiter",
            status="warn",
            detail="could not check the waiter — the hub is unreachable",
            remedy="bring the hub up, then re-run",
        )
    if waiter_name in roster:
        return Diagnosis(
            check="waiter",
            status="pass",
            detail=f"waiter {waiter_name!r} is live on the bus",
        )
    owner = waiter_owner(waiter_name)
    return Diagnosis(
        check="waiter",
        status="warn",
        detail=f"no waiter {waiter_name!r} on the bus — directed messages will not wake you",
        remedy=(
            f"arm one in the background: synapse wait --name {waiter_name} "
            f"--for {owner} --directed-only"
        ),
    )


def check_multi_seat_posture(
    *,
    roster: list[str] | None,
    token: str | None,
    identity_trust: str | PathLike[str] | None = None,
    role_grants: str | PathLike[str] | None = None,
    force: bool = False,
) -> Diagnosis:
    """Advise multi-seat trust materials when more than one seat is live.

    A multi-seat fleet (several agents, or several waiters, or ``force=True``)
    should use a connect token and the ``--team-secure`` materials (identity trust
    bundle + role-grant store). Solo loopback remains a pass so everyday single-
    agent use is not noisy. Missing materials are warnings, not failures — the
    operator may still be on open loopback deliberately.
    """
    if roster is None:
        return Diagnosis(
            check="multi-seat",
            status="warn",
            detail="could not assess multi-seat posture — the hub is unreachable",
            remedy="bring the hub up, then re-run (or pass --multi-seat to force the checklist)",
        )
    agents, waiters = split_roster(roster)
    multi = force or len(agents) >= 2 or (len(agents) >= 1 and len(waiters) >= 2)
    if not multi:
        return Diagnosis(
            check="multi-seat",
            status="pass",
            detail=(
                f"single-seat roster ({len(agents)} agent(s), {len(waiters)} waiter(s)); "
                "multi-seat trust checklist skipped"
            ),
            remedy="pass --multi-seat to force the team-secure checklist",
        )
    gaps: list[str] = []
    if not token:
        gaps.append("no connect token")
    trust_path = Path(identity_trust) if identity_trust else None
    roles_path = Path(role_grants) if role_grants else None
    if trust_path is None or not trust_path.is_file():
        gaps.append("identity trust bundle missing")
    if roles_path is None or not roles_path.is_file():
        gaps.append("role-grants store missing")
    detail = (
        f"multi-seat roster ({len(agents)} agent(s), {len(waiters)} waiter(s))"
        + (f": {'; '.join(gaps)}" if gaps else ": token + trust + role-grants present")
    )
    if gaps:
        return Diagnosis(
            check="multi-seat",
            status="warn",
            detail=detail,
            remedy=(
                "for multi-seat trust: set --token, enrol keys "
                "(`synapse identity keygen … --enroll`), grant roles "
                "(`synapse role grant …`), then start "
                "`synapse hub --team-secure --identity-trust … --role-grants …` "
                "(see docs/team-secure.md and the multi-seat golden path)"
            ),
        )
    return Diagnosis(
        check="multi-seat",
        status="pass",
        detail=detail,
        remedy=(
            "start the hub with --team-secure (and the trust/role paths) if it is "
            "not already; private directed messages require that profile"
        ),
    )


def check_deaf_agents(roster: list[str] | None) -> Diagnosis:
    """Warn when live agents have no matching ``-rx`` wake waiter on the bus.

    Presence without a waiter is the "online but deaf" failure: directed messages
    land in the feed and never wake the seat. Passive ``-rx`` names are ignored as
    agents (they are waiters themselves).
    """
    if roster is None:
        return Diagnosis(
            check="deaf-agents",
            status="warn",
            detail="could not check for deaf agents — the hub is unreachable",
            remedy="bring the hub up, then re-run",
        )
    agents, _waiters = split_roster(roster)
    live = set(roster)
    deaf = [agent for agent in agents if waiter_name(agent) not in live]
    if not deaf:
        return Diagnosis(
            check="deaf-agents",
            status="pass",
            detail=f"every live agent has a -rx waiter ({len(agents)} agent(s))",
        )
    listing = ", ".join(deaf[:3])
    more = f" and {len(deaf) - 3} more" if len(deaf) > 3 else ""
    sample = deaf[0]
    return Diagnosis(
        check="deaf-agents",
        status="warn",
        detail=f"agents present without a wake waiter: {listing}{more}",
        remedy=(
            f"arm waiters: synapse wait --name {waiter_name(sample)} "
            f"--for {sample} --directed-only (repeat per deaf agent)"
        ),
    )


def check_unread_addressees(
    *,
    feed_lines: Sequence[str],
    cursor_names: Collection[str],
    roster: list[str] | None,
) -> Diagnosis:
    """Warn when recent directed traffic addresses a name nobody reads.

    A message addressed to an identity whose inbox no cursor drains and
    whose waiter is not on the bus lands durably in the feed and wakes no
    one — the human ends up relaying, which is the exact failure the bus
    exists to remove. A target counts as read when its project's inbox
    cursor exists (the project-stable inbox covers ``project/...``
    sub-addresses), its own aliased cursor exists, or the name (or its
    ``-rx`` waiter) is live on the roster. Broadcasts and group globs are
    not directed traffic and are ignored.
    """
    targets: dict[str, int] = {}
    for raw in feed_lines:
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("ty") != "chat":
            continue
        target = str(record.get("to", "")).strip()
        if not target or target == "all" or "*" in target:
            continue
        targets[target] = targets.get(target, 0) + 1
    cursors = set(cursor_names)
    live = set(roster or [])
    unread: dict[str, int] = {}
    for target, count in targets.items():
        project = target.split("/", 1)[0]
        if project in cursors or target.replace("/", "__") in cursors:
            continue
        if target in live or f"{target}-rx" in live:
            continue
        unread[target] = count
    if not unread:
        return Diagnosis(
            check="addressees",
            status="pass",
            detail="every directed address in the recent feed has a reader",
        )
    worst = sorted(unread.items(), key=lambda item: (-item[1], item[0]))
    listing = ", ".join(f"{name} ({count} msg)" for name, count in worst[:3])
    more = f" and {len(worst) - 3} more" if len(worst) > 3 else ""
    return Diagnosis(
        check="addressees",
        status="warn",
        detail=f"directed messages nobody reads: {listing}{more}",
        remedy=f"drain them: syn inbox --as {worst[0][0]} (repeat --as per name)",
    )


def summarise(diagnoses: list[Diagnosis]) -> tuple[int, list[str]]:
    """Format the report lines and return ``(exit_code, lines)``.

    The exit code is ``1`` when any check failed, else ``0`` — warnings flag
    footguns but do not fail the command.
    """
    lines: list[str] = []
    fails = sum(1 for d in diagnoses if d.status == "fail")
    warns = sum(1 for d in diagnoses if d.status == "warn")
    for diagnosis in diagnoses:
        lines.append(f"[{_STATUS_MARK[diagnosis.status]}] {diagnosis.check}: {diagnosis.detail}")
        if diagnosis.remedy:
            lines.append(f"      → {diagnosis.remedy}")
    if fails:
        lines.append(f"synapse doctor: FAILED — {fails} issue(s), {warns} warning(s)")
    elif warns:
        lines.append(f"synapse doctor: {warns} warning(s), no failures")
    else:
        lines.append("synapse doctor: all clear")
    return (1 if fails else 0, lines)
