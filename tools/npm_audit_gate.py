# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — npm lock audit with expiring development-only advisory exceptions
"""Fail on every npm advisory in a committed lock unless an expiring exception covers it.

``npm audit --package-lock-only --json`` lists each vulnerable package of a lock
with the advisories it carries (``via`` objects), the vulnerable packages it
depends on (``via`` names) and the lock paths where it is installed (``nodes``).

A lock passes only when every advisory it carries is named, for that package, by
an exception that has not expired, and every reported package is installed in
that lock as a development dependency only. Any other advisory, an expired
exception, a production install path or an unreadable audit fails, as plain
``npm audit`` would. An exception lasts at most seven days and covers
development dependencies only; renewing it needs a new grant. An active
exception that none of the audited locks uses is a warning.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

MAX_EXCEPTION_DAYS = 7
AUDIT_COMMAND = ("npm", "audit", "--package-lock-only", "--json")
_FIELDS = frozenset({"advisory", "package", "scope", "granted", "expires", "reason"})
_GHSA = re.compile(r"GHSA(?:-[a-z0-9]{4}){3}")
_ADVISORY_URL = re.compile(r"https://github\.com/advisories/(GHSA(?:-[a-z0-9]{4}){3})")

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class AuditGateError(ValueError):
    """The audit cannot be judged: a malformed input or an invalid exception."""


@dataclass(frozen=True)
class AdvisoryException:
    """One granted, expiring exception for one advisory in one package."""

    advisory: str
    package: str
    granted: date
    expires: date
    reason: str


def parse_exception(item: object) -> AdvisoryException:
    """Validate one exception entry: development scope, one to seven days."""
    if not isinstance(item, dict) or set(item) != _FIELDS:
        raise AuditGateError(f"an exception needs exactly the fields {sorted(_FIELDS)}")
    advisory, package, reason = (
        str(item[key]).strip() for key in ("advisory", "package", "reason")
    )
    if not _GHSA.fullmatch(advisory):
        raise AuditGateError(f"{advisory!r} is not a GitHub advisory id")
    if not package or not reason:
        raise AuditGateError(f"{advisory}: an exception names its package and its reason")
    if item["scope"] != "dev":
        raise AuditGateError(f"{advisory}: an exception covers development dependencies only")
    granted = date.fromisoformat(str(item["granted"]))
    expires = date.fromisoformat(str(item["expires"]))
    span = (expires - granted).days
    if not 1 <= span <= MAX_EXCEPTION_DAYS:
        raise AuditGateError(
            f"{advisory}: an exception lasts 1 to {MAX_EXCEPTION_DAYS} days, not {span}"
        )
    return AdvisoryException(advisory, package, granted, expires, reason)


def load_exceptions(path: Path) -> tuple[AdvisoryException, ...]:
    """Read the exceptions file: ``{"schema": 1, "exceptions": [...]}``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or data.get("schema") != 1
        or not isinstance(data.get("exceptions"), list)
    ):
        raise AuditGateError("the exceptions file must be {schema: 1, exceptions: [...]}")
    return tuple(parse_exception(item) for item in data["exceptions"])


def run_audit(lock_dir: Path, runner: Runner = subprocess.run) -> dict[str, Any]:
    """Run npm's lock-only audit and return its version 2 JSON report."""
    completed = runner(
        list(AUDIT_COMMAND), cwd=lock_dir, capture_output=True, text=True, check=False, timeout=300
    )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AuditGateError(
            f"npm audit printed no JSON report (exit {completed.returncode})"
        ) from exc
    if not isinstance(report, dict) or "error" in report:
        raise AuditGateError(f"npm audit failed (exit {completed.returncode}): {str(report)[:300]}")
    found = bool(report.get("vulnerabilities"))
    if (completed.returncode, found) not in {(0, False), (1, True)}:
        raise AuditGateError(f"npm audit exit {completed.returncode} disagrees with its report")
    return cast(dict[str, Any], report)


def _advisory_id(via: Mapping[str, Any]) -> str | None:
    match = _ADVISORY_URL.fullmatch(str(via.get("url", "")))
    return match.group(1) if match else None


def _entry_failures(
    name: str,
    entry: Mapping[str, Any],
    listed: Mapping[str, Any],
    installs: Mapping[str, Any],
    active: Mapping[tuple[str, str], AdvisoryException],
    used: set[str],
) -> list[str]:
    failures: list[str] = []
    for via in entry.get("via", []):
        if isinstance(via, str):
            if via not in listed:
                failures.append(f"{name}: depends on {via}, which the report does not list")
            continue
        advisory = _advisory_id(via)
        if advisory is not None and (advisory, str(via.get("dependency"))) in active:
            used.add(advisory)
            continue
        failures.append(
            f"{name}: {advisory or via.get('url')} ({via.get('severity')}) has no active exception"
        )
    nodes = entry.get("nodes") or []
    if not nodes:
        failures.append(f"{name}: the report names no install path")
    for node in nodes:
        install = installs.get(node)
        if not isinstance(install, dict) or install.get("dev") is not True:
            failures.append(f"{name}: {node} is not a development-only install")
    return failures


def judge(
    report: Mapping[str, Any],
    lock: Mapping[str, Any],
    exceptions: Sequence[AdvisoryException],
    today: date,
) -> tuple[list[str], set[str]]:
    """Return the failures and the advisories accepted for one lock's report."""
    if report.get("auditReportVersion") != 2 or not isinstance(report.get("vulnerabilities"), dict):
        raise AuditGateError("npm audit did not return a version 2 report")
    installs = lock.get("packages")
    if not isinstance(installs, dict):
        raise AuditGateError("the lock has no packages map (lockfileVersion 2 or 3 is needed)")
    failures = [
        f"{item.advisory}: the exception expired on {item.expires}"
        for item in exceptions
        if today >= item.expires
    ]
    active = {(item.advisory, item.package): item for item in exceptions if today < item.expires}
    used: set[str] = set()
    listed = report["vulnerabilities"]
    for name, entry in sorted(listed.items()):
        failures.extend(_entry_failures(name, entry, listed, installs, active, used))
    return failures, used


def unused_exception_warnings(
    exceptions: Sequence[AdvisoryException], used: set[str], today: date
) -> list[str]:
    """Return warnings for active exceptions that none of the locks used.

    An expired exception is already a failure. A warning is only the case where
    the grant is still in date and no judged lock accepted that advisory.
    """
    return [
        f"npm audit gate: {item.advisory} ({item.package}) is an exception no lock used"
        for item in exceptions
        if today < item.expires and item.advisory not in used
    ]


def _check_lock(
    lock_path: Path, exceptions: Sequence[AdvisoryException], today: date, runner: Runner
) -> tuple[list[str], set[str] | None]:
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        return judge(run_audit(lock_path.parent, runner), lock, exceptions, today)
    except (OSError, ValueError) as exc:
        return [str(exc)], None


def main(
    argv: Sequence[str] | None = None, *, runner: Runner = subprocess.run, today: date | None = None
) -> int:
    """Audit each named lock; exit 1 on any failure, 2 on an unusable exceptions file."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--exceptions", type=Path, required=True)
    parser.add_argument("locks", nargs="+", type=Path)
    args = parser.parse_args(argv)
    day = today or datetime.now(UTC).date()
    try:
        exceptions = load_exceptions(args.exceptions)
    except (OSError, ValueError) as exc:
        print(f"npm audit gate: {exc}", file=sys.stderr)
        return 2
    expiry = {item.advisory: item.expires for item in exceptions}
    status = 0
    used_all: set[str] = set()
    judged = False
    for lock_path in args.locks:
        failures, used = _check_lock(lock_path, exceptions, day, runner)
        if used is None:
            accepted: set[str] = set()
        else:
            judged = True
            accepted = used
            used_all.update(used)
        for advisory in sorted(accepted):
            print(
                f"{lock_path}: {advisory} accepted in development dependencies only;"
                f" the exception expires on {expiry[advisory]}"
            )
        for failure in failures:
            print(f"{lock_path}: {failure}", file=sys.stderr)
        status = 1 if failures else status
        if not failures and not accepted:
            print(f"{lock_path}: found 0 vulnerabilities")
    if judged:
        for warning in unused_exception_warnings(exceptions, used_all, day):
            print(warning, file=sys.stderr)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
