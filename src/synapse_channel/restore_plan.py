# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the restore plan: which cut seats start again, and with what
"""Which seats ``synapse ledger restore`` starts again, decided from the ledger.

For each identity, its last row is judged:

* **started again** when a reboot cut it (``boot_cut``, or a row of an
  earlier boot that never ended), whoever launched it, or when its launcher
  (``seat-hold`` or ``worker-session``) vanished without writing its end
  (``observed_gone``);
* **never** when it has a real end (``exit`` or ``signal``): it was ended on
  purpose, or its holder ended it;
* **only when the run names it** with ``--only`` when the collector alone saw
  it end (an ``observed`` row's ``observed_gone``, where the owner's ``/exit``
  and a crash look the same), or when its last two restores could not start
  the agent.

A row on another host is elsewhere and is never started here. By default the
rows of the previous boot are read, with this boot's launcher rows that
vanished; a seat cut in an older boot was most likely left down on purpose
and needs ``--boot``. A row with no identity is never a candidate: it can be
neither fenced nor addressed.

A row that would start is still held back when it cannot come back as the
same session: no native session id, an id carried over rather than read, an
id that is not a plain token, or a vendor without a resume form ("no resume
handle"); a permission profile the restore cannot apply yet ("needs
profile"); a working directory that is gone; or a tmux session name in use.
A fresh, unresumed session would not be the seat that was lost.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from synapse_channel.reap import safe_key
from synapse_channel.session_ledger import END_KINDS, UNOBSERVED_END_KINDS, Row

RESUME_FORMS: Mapping[str, tuple[str, ...]] = {
    "claude": ("claude", "--resume"),
    "codex": ("codex", "resume"),
    "grok": ("grok", "--resume"),
    "agy": ("agy", "--conversation"),
}
"""Each vendor's resume command, to which the native session id is appended.

No form carries a permission flag: a restored seat gets exactly the
permissions its vendor's own configuration gives it.
"""

LAUNCHERS = frozenset({"seat-hold", "worker-session"})
"""Launchers that write a real end; one that vanished without it crashed or was killed."""

FAILURES_BEFORE_ATTENTION = 2
"""Restores in a row that could not start the agent before a row needs attention."""

SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
"""A native session id the restore passes on: a plain token, never a flag."""

START = "start"
LIVE = "live"
ELSEWHERE = "elsewhere"
ENDED = "ended"
CAUSE_UNKNOWN = "ended, cause unknown"
OTHER_BOOT = "cut in another boot"
NEEDS_ATTENTION = "needs attention"
NO_HANDLE = "no resume handle"
NEEDS_PROFILE = "needs profile"
CWD_GONE = "cwd gone"
TMUX_IN_USE = "tmux name in use"


@dataclass(frozen=True)
class Scope:
    """Which rows a run reads.

    ``boot`` is the boot whose cut rows are read (``None`` when the ledger
    holds no earlier boot); ``by_default`` says it was chosen as the previous
    boot, so this boot's launcher rows that vanished are read too. ``only``
    restricts the run to the named identities and lets it start their rows
    that need a name.
    """

    host: str
    current_boot: str
    boot: str | None
    by_default: bool = True
    only: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Entry:
    """One identity's last row, and what the restore does with it."""

    identity: str
    row_id: str
    state: str
    vendor: str | None = None
    project: str | None = None
    command: tuple[str, ...] = ()
    cwd: str | None = None
    session: str = ""
    priority: int | None = None
    started_at: str = ""
    failures: int = 0

    def as_dict(self) -> dict[str, Any]:
        """Return the entry as plain data for ``--json``."""
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def row_boot(row: Row) -> str:
    """Return the boot a row belongs to, the first part of its key."""
    return row.row_id.partition(":")[0]


def previous_boot(rows: Iterable[Row], current: str) -> str | None:
    """Return the last boot other than ``current`` that the ledger has rows of."""
    found = None
    for row in rows:
        if row_boot(row) != current:
            found = row_boot(row)
    return found


def last_rows(rows: Iterable[Row]) -> dict[str, Row]:
    """Return each identity's last row, in the order the rows were recorded."""
    last: dict[str, Row] = {}
    for row in rows:
        identity = row.start.get("identity")
        if isinstance(identity, str) and identity:
            last.pop(identity, None)
            last[identity] = row
    return last


def latest(row: Row, name: str) -> Any:
    """Return a field as last observed, or as the row's start recorded it."""
    return row.observed[name] if name in row.observed else row.start.get(name)


def end_kind(row: Row, current_boot: str, alive: Callable[[Row], bool]) -> str | None:
    """Return how a row ended, or ``None`` while its process still runs.

    An open row of an earlier boot was cut by the reboot. An open row of this
    boot whose process is gone, by its PID and start ticks, vanished.
    """
    if row.end is not None:
        return str(row.end.get("end_kind") or "")
    if row_boot(row) != current_boot:
        return "boot_cut"
    return None if alive(row) else "observed_gone"


def judged(row: Row, scope: Scope, kind: str | None) -> str:
    """Return whether the row is a candidate (``START``) or why it is not, by its end."""
    if row.start.get("host") != scope.host:
        return ELSEWHERE
    if kind is None:
        return LIVE
    if kind in END_KINDS or kind not in UNOBSERVED_END_KINDS:
        return ENDED
    boot, vanished = row_boot(row), kind == "observed_gone"
    this_boot = vanished and scope.by_default and boot == scope.current_boot
    if boot != scope.boot and not this_boot:
        return OTHER_BOOT
    named = row.start.get("identity") in scope.only
    if vanished and row.start.get("launcher") not in LAUNCHERS and not named:
        return CAUSE_UNKNOWN
    if failures(row) >= FAILURES_BEFORE_ATTENTION and not named:
        return NEEDS_ATTENTION
    return START


def failures(row: Row) -> int:
    """Return how many restores in a row could not start this row's agent."""
    restore = row.observed.get("restore")
    count = restore.get("failures") if isinstance(restore, Mapping) else 0
    return count if isinstance(count, int) else 0


def resume_command(row: Row) -> tuple[str, ...]:
    """Return the vendor's resume command for the row, or ``()`` without a usable handle."""
    form = RESUME_FORMS.get(str(row.start.get("vendor")))
    native = latest(row, "native_session_id")
    if (
        form is None
        or latest(row, "native_session_id_source") == "carried"
        or not isinstance(native, str)
        or not SESSION_ID.fullmatch(native)
    ):
        return ()
    return (*form, native)


def session_name(row: Row, identity: str) -> str:
    """Return the tmux session the row ran in, or the name a restore gives the identity."""
    tmux = latest(row, "tmux")
    name = tmux.get("session") if isinstance(tmux, Mapping) else None
    return name if isinstance(name, str) and name else f"synapse-{safe_key(identity)}"


def held_back(
    entry: Entry,
    row: Row,
    *,
    cwd_exists: Callable[[str], bool],
    tmux_taken: Callable[[str], bool],
) -> str:
    """Return why a candidate cannot start as the same session, or ``START``."""
    if not entry.command:
        return NO_HANDLE
    if latest(row, "permission_profile"):
        return NEEDS_PROFILE
    if entry.cwd is None or not cwd_exists(entry.cwd):
        return CWD_GONE
    if tmux_taken(entry.session):
        return TMUX_IN_USE
    return START


def entry_for(identity: str, row: Row, state: str) -> Entry:
    """Return the entry for one identity's last row, with the state it was judged."""
    priority, cwd, project = (
        row.start.get("restore_priority"),
        row.start.get("cwd"),
        row.start.get("project"),
    )
    return Entry(
        identity=identity,
        row_id=row.row_id,
        state=state,
        vendor=row.start.get("vendor"),
        project=project if isinstance(project, str) else None,
        command=resume_command(row),
        cwd=cwd if isinstance(cwd, str) and cwd else None,
        session=session_name(row, identity),
        priority=priority if isinstance(priority, int) else None,
        started_at=str(row.start.get("started_at") or ""),
        failures=failures(row),
    )


def plan(
    rows: Mapping[str, Row],
    scope: Scope,
    *,
    live: Collection[str],
    alive: Callable[[Row], bool],
    cwd_exists: Callable[[str], bool],
    tmux_taken: Callable[[str], bool],
) -> list[Entry]:
    """Return one entry per identity, those that start first, in the order they start.

    ``live`` holds the identities the current snapshot sees running; ``alive``
    proves a row's process runs by its PID and start ticks (same boot only).
    Entries that start are ordered by ``restore_priority`` (lower first), then
    rows without one in the order they started.
    """
    entries: list[Entry] = []
    for identity, row in last_rows(rows.values()).items():
        if scope.only and identity not in scope.only:
            continue
        state = (
            LIVE
            if identity in live
            else judged(row, scope, end_kind(row, scope.current_boot, alive))
        )
        entry = entry_for(identity, row, state)
        if state == START:
            held = held_back(entry, row, cwd_exists=cwd_exists, tmux_taken=tmux_taken)
            entry = replace(entry, state=held)
        entries.append(entry)
    return sorted(entries, key=_order)


def _order(entry: Entry) -> tuple[bool, bool, int, str]:
    return (entry.state != START, entry.priority is None, entry.priority or 0, entry.started_at)
