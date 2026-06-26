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

from dataclasses import dataclass
from os import PathLike
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

from synapse_channel.core.hub import is_loopback_host

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
    return Diagnosis(
        check="waiter",
        status="warn",
        detail=f"no waiter {waiter_name!r} on the bus — directed messages will not wake you",
        remedy=(
            f"arm one in the background: synapse wait --name {waiter_name} "
            "--for <project> --directed-only"
        ),
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
