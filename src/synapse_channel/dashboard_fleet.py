# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — derived fleet visibility snapshot
"""Derived fleet visibility for the read-only local dashboard."""

from __future__ import annotations

import html
import json
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.core.ledger import TERMINAL_LEDGER_STATUSES

if TYPE_CHECKING:
    from synapse_channel.dashboard import DashboardSnapshot

JsonDict = dict[str, Any]
"""JSON-compatible mapping returned by dashboard fleet projections."""


@dataclass(frozen=True)
class FleetAgents:
    """Live agent identities and their wake-loop status.

    Attributes
    ----------
    live : list[str]
        Online non-waiter agent identities.
    waiters : list[str]
        Online identities ending in ``-rx``.
    missing_waiters : list[str]
        Expected ``-rx`` waiter names missing for online non-waiter agents.
    """

    live: list[str]
    waiters: list[str]
    missing_waiters: list[str]

    def to_dict(self) -> JsonDict:
        """Return a JSON-compatible mapping."""
        return {
            "live": self.live,
            "waiters": self.waiters,
            "missing_waiters": self.missing_waiters,
        }


@dataclass(frozen=True)
class FleetClaims:
    """Active and stale lease summary for the fleet view.

    Attributes
    ----------
    active : int
        Number of claims whose lease expiry is after ``now``.
    stale : int
        Number of claims whose lease expiry is at or before ``now``.
    active_claims : list[dict[str, Any]]
        Bounded claim records still within their lease.
    stale_claims : list[dict[str, Any]]
        Bounded claim records past their lease.
    """

    active: int
    stale: int
    active_claims: list[JsonDict]
    stale_claims: list[JsonDict]

    def to_dict(self) -> JsonDict:
        """Return a JSON-compatible mapping."""
        return {
            "active": self.active,
            "stale": self.stale,
            "active_claims": self.active_claims,
            "stale_claims": self.stale_claims,
        }


@dataclass(frozen=True)
class FleetTasks:
    """Ready and blocked blackboard task summary.

    Attributes
    ----------
    ready : list[str]
        Task ids reported ready by the blackboard snapshot.
    blocked : list[dict[str, Any]]
        Blocked task ids with unmet dependency ids.
    """

    ready: list[str]
    blocked: list[JsonDict]

    def to_dict(self) -> JsonDict:
        """Return a JSON-compatible mapping."""
        return {"ready": self.ready, "blocked": self.blocked}


@dataclass(frozen=True)
class FleetA2A:
    """Persisted Agent2Agent task summary.

    Attributes
    ----------
    source : str
        ``none``, ``missing``, ``loaded``, or ``error``.
    total : int
        Number of persisted A2A tasks loaded.
    states : dict[str, int]
        Task counts by A2A status state.
    push_configs : int
        Persisted push-notification configuration count.
    error : str
        Error text when ``source`` is ``error``; empty otherwise.
    """

    source: str
    total: int
    states: dict[str, int]
    push_configs: int
    error: str = ""

    def to_dict(self) -> JsonDict:
        """Return a JSON-compatible mapping."""
        payload: JsonDict = {
            "source": self.source,
            "total": self.total,
            "states": self.states,
            "push_configs": self.push_configs,
        }
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True)
class FleetVisibility:
    """Derived dashboard fleet status.

    Attributes
    ----------
    agents : FleetAgents
        Live agent and waiter summary.
    claims : FleetClaims
        Lease summary split by freshness.
    tasks : FleetTasks
        Ready and blocked blackboard task summary.
    receipts : list[dict[str, Any]]
        Release receipt progress notes from the blackboard snapshot.
    a2a : FleetA2A
        Optional persisted A2A task summary.
    generated_at : float
        Wall-clock seconds used to derive lease freshness.
    """

    agents: FleetAgents
    claims: FleetClaims
    tasks: FleetTasks
    receipts: list[JsonDict]
    a2a: FleetA2A
    generated_at: float

    def to_dict(self) -> JsonDict:
        """Return a JSON-compatible mapping."""
        return {
            "agents": self.agents.to_dict(),
            "claims": self.claims.to_dict(),
            "tasks": self.tasks.to_dict(),
            "receipts": self.receipts,
            "a2a": self.a2a.to_dict(),
            "generated_at": self.generated_at,
        }


def _as_mappings(value: object) -> list[Mapping[str, object]]:
    """Return mapping items from an arbitrary JSON list value."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _as_strings(value: object) -> list[str]:
    """Return stringified values from an arbitrary JSON list value."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _sort_unique(values: Sequence[str]) -> list[str]:
    """Return non-empty unique strings in deterministic order."""
    return sorted({value for value in values if value})


def _fleet_agents(online_agents: Sequence[str]) -> FleetAgents:
    """Derive agent and waiter status from an online roster."""
    waiters = _sort_unique([agent for agent in online_agents if agent.endswith("-rx")])
    live = _sort_unique([agent for agent in online_agents if not agent.endswith("-rx")])
    waiter_set = set(waiters)
    missing = [f"{agent}-rx" for agent in live if f"{agent}-rx" not in waiter_set]
    return FleetAgents(live=live, waiters=waiters, missing_waiters=missing)


def _float_or_none(value: object) -> float | None:
    """Return ``value`` as a float when possible."""
    if not isinstance(value, str | bytes | int | float):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _claim_record(claim: Mapping[str, object], *, stale: bool) -> JsonDict:
    """Return a bounded claim record for fleet JSON."""
    paths = claim.get("paths")
    return {
        "task_id": str(claim.get("task_id", "")),
        "owner": str(claim.get("owner", "")),
        "lease_expires_at": claim.get("lease_expires_at"),
        "paths": _as_strings(paths),
        "stale": stale,
    }


def _fleet_claims(state: Mapping[str, object], *, now: float) -> FleetClaims:
    """Split state claims into active and stale lease buckets."""
    active: list[JsonDict] = []
    stale: list[JsonDict] = []
    for claim in _as_mappings(state.get("active_claims")):
        expires_at = _float_or_none(claim.get("lease_expires_at"))
        is_stale = expires_at is not None and expires_at <= now
        record = _claim_record(claim, stale=is_stale)
        if is_stale:
            stale.append(record)
        else:
            active.append(record)
    active.sort(key=lambda item: (str(item["task_id"]), str(item["owner"])))
    stale.sort(key=lambda item: (str(item["task_id"]), str(item["owner"])))
    return FleetClaims(
        active=len(active),
        stale=len(stale),
        active_claims=active,
        stale_claims=stale,
    )


def _task_index(tasks: list[Mapping[str, object]]) -> dict[str, Mapping[str, object]]:
    """Return a task-id keyed index for blackboard tasks."""
    return {str(task.get("task_id", "")): task for task in tasks if str(task.get("task_id", ""))}


def _unmet_dependencies(
    task: Mapping[str, object], by_id: Mapping[str, Mapping[str, object]]
) -> list[str]:
    """Return dependency ids that are not terminal in ``by_id``."""
    blocked_by: list[str] = []
    for dependency in _as_strings(task.get("depends_on")):
        status = str(by_id.get(dependency, {}).get("status", ""))
        if status not in TERMINAL_LEDGER_STATUSES:
            blocked_by.append(dependency)
    return blocked_by


def _fleet_tasks(board: Mapping[str, object]) -> FleetTasks:
    """Derive ready and blocked task lists from a board snapshot."""
    tasks = _as_mappings(board.get("tasks"))
    by_id = _task_index(tasks)
    blocked: list[JsonDict] = []
    for task in tasks:
        if str(task.get("status", "")) != "blocked":
            continue
        task_id = str(task.get("task_id", ""))
        blocked.append({"task_id": task_id, "blocked_by": _unmet_dependencies(task, by_id)})
    blocked.sort(key=lambda item: str(item["task_id"]))
    return FleetTasks(ready=_sort_unique(_as_strings(board.get("ready"))), blocked=blocked)


def _release_receipts(board: Mapping[str, object]) -> list[JsonDict]:
    """Return release receipt notes from board progress."""
    receipts: list[JsonDict] = []
    for note in _as_mappings(board.get("progress")):
        text = str(note.get("text", ""))
        if str(note.get("kind", "")) != "assessment" and not text.lower().startswith(
            "release receipt:"
        ):
            continue
        receipts.append(
            {
                "task_id": str(note.get("task_id", "")),
                "author": str(note.get("author", "")),
                "text": text,
                "posted_at": note.get("posted_at"),
            }
        )
    receipts.sort(key=lambda item: (str(item.get("posted_at", "")), str(item["task_id"])))
    return receipts


def _a2a_summary(path: Path | None) -> FleetA2A:
    """Load optional persisted A2A task counts."""
    if path is None:
        return FleetA2A(source="none", total=0, states={}, push_configs=0)
    if not path.exists():
        return FleetA2A(source="missing", total=0, states={}, push_configs=0)
    try:
        store = A2ATaskStore(path)
        tasks = store.list_tasks()
        state_counts: Counter[str] = Counter()
        push_configs = 0
        for task in tasks:
            task_id = str(task.get("id", ""))
            status = task.get("status")
            state = status.get("state") if isinstance(status, Mapping) else None
            state_counts[str(state or "unknown")] += 1
            if task_id:
                push_configs += len(store.list_push_configs(task_id))
    except (OSError, ValueError) as exc:
        return FleetA2A(source="error", total=0, states={}, push_configs=0, error=str(exc))
    return FleetA2A(
        source="loaded",
        total=len(tasks),
        states=dict(sorted(state_counts.items())),
        push_configs=push_configs,
    )


def build_fleet_visibility(
    snapshot: DashboardSnapshot,
    *,
    now: float | None = None,
    a2a_state_file: str | Path | None = None,
) -> FleetVisibility:
    """Build the derived fleet section for a dashboard snapshot.

    Parameters
    ----------
    snapshot : DashboardSnapshot
        Live read-side dashboard snapshot fetched from the hub.
    now : float or None, optional
        Wall-clock seconds for lease freshness. Defaults to ``time.time()``.
    a2a_state_file : str, pathlib.Path, or None, optional
        Persisted A2A bridge state file to summarise. ``None`` disables A2A
        state loading while still returning an explicit empty summary.

    Returns
    -------
    FleetVisibility
        JSON-compatible derived fleet visibility data.
    """
    timestamp = time.time() if now is None else float(now)
    path = Path(a2a_state_file) if a2a_state_file is not None else None
    return FleetVisibility(
        agents=_fleet_agents(snapshot.online_agents),
        claims=_fleet_claims(snapshot.state, now=timestamp),
        tasks=_fleet_tasks(snapshot.board),
        receipts=_release_receipts(snapshot.board),
        a2a=_a2a_summary(path),
        generated_at=timestamp,
    )


def _escape(value: object) -> str:
    """Return ``value`` escaped for HTML text nodes."""
    return html.escape(str(value), quote=True)


def _render_string_list(items: Sequence[str]) -> str:
    """Render escaped string list items or a single empty marker."""
    if not items:
        return '<li class="muted">None</li>'
    return "".join(f"<li>{_escape(item)}</li>" for item in items)


def _render_record_list(items: Sequence[JsonDict], *, empty: str) -> str:
    """Render derived fleet records as compact escaped JSON."""
    if not items:
        return f'<li class="muted">{_escape(empty)}</li>'
    return "".join(
        f"<li><small>{_escape(json.dumps(item, sort_keys=True))}</small></li>" for item in items
    )


def _render_key_counts(values: Mapping[str, int]) -> str:
    """Render key/count pairs for a dashboard section."""
    if not values:
        return '<li class="muted">None</li>'
    return "".join(
        f"<li><strong>{_escape(key)}</strong>: {_escape(count)}</li>"
        for key, count in sorted(values.items())
    )


def render_fleet_visibility_html(
    snapshot: DashboardSnapshot,
    *,
    a2a_state_file: str | Path | None = None,
) -> str:
    """Render the derived fleet visibility dashboard sections.

    Parameters
    ----------
    snapshot : DashboardSnapshot
        Read-side dashboard snapshot fetched from the live hub.
    a2a_state_file : str, pathlib.Path, or None, optional
        Optional persisted A2A bridge state file used to populate A2A task and
        push-configuration counts.

    Returns
    -------
    str
        Escaped HTML sections for embedding in the dashboard page.
    """
    fleet = build_fleet_visibility(snapshot, a2a_state_file=a2a_state_file)
    a2a = fleet.a2a
    return f"""
    <section>
      <h2>Fleet visibility</h2>
      <ul>
        <li>Live agents: {_escape(fleet.agents.live)}</li>
        <li>Waiters: {_escape(fleet.agents.waiters)}</li>
        <li>
          Active claims: {_escape(fleet.claims.active)}
          · stale claims: {_escape(fleet.claims.stale)}
        </li>
      </ul>
    </section>
    <section>
      <h2>Missing waiters</h2>
      <ul>{_render_string_list(fleet.agents.missing_waiters)}</ul>
    </section>
    <section>
      <h2>Blocked tasks</h2>
      <ul>{_render_record_list(fleet.tasks.blocked, empty="No blocked tasks")}</ul>
    </section>
    <section>
      <h2>Release receipts</h2>
      <ul>{_render_record_list(fleet.receipts[-10:], empty="No release receipts")}</ul>
    </section>
    <section>
      <h2>A2A tasks</h2>
      <ul>
        <li>Source: {_escape(a2a.source)}</li>
        <li>Total: {_escape(a2a.total)}</li>
        <li>Push configs: {_escape(a2a.push_configs)}</li>
        {_render_key_counts(a2a.states)}
      </ul>
    </section>
"""
