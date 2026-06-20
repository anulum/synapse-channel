# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — central registry for presence, task claims, and offered resources
"""Shared coordination state for the Synapse hub.

This module holds the authoritative, in-memory registry that the hub consults
to answer presence, claim, task-status, and resource-offer questions. All time
values are wall-clock seconds; task leases and resource offers expire on their
own so a crashed or disconnected agent never holds a claim forever.

The state object is deliberately transport-agnostic: it knows nothing about
WebSockets or message envelopes, which keeps it fully unit-testable with
injected timestamps via the ``now`` parameters.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

MINIMUM_TTL_SECONDS = 30.0
"""Floor applied to every requested lease/default TTL, in seconds."""

DEFAULT_RESOURCE_TTL_SECONDS = 300.0
"""Soft liveness window after which an un-refreshed resource offer is dropped."""


@dataclass
class TaskClaim:
    """A lease held by one agent over a named unit of work.

    The owner keeps the claim until it is explicitly released or its lease
    expires. While the lease is live, other agents are refused the same task.

    Attributes
    ----------
    task_id : str
        Stable identifier of the claimed task.
    owner : str
        Name of the agent currently holding the claim.
    note : str
        Free-form human-readable context for the claim.
    claimed_at : float
        Wall-clock time, in seconds, when the claim was last (re)acquired.
    lease_expires_at : float
        Wall-clock time, in seconds, after which the claim auto-expires.
    status : str
        Lifecycle marker: ``claimed``, ``in_progress``, ``blocked``, or
        ``completed``.
    data_ref : str
        Optional pointer to produced artefacts (e.g. a memory key or file path).
    """

    task_id: str
    owner: str
    note: str
    claimed_at: float
    lease_expires_at: float
    status: str = "claimed"
    data_ref: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of this claim.

        Returns
        -------
        dict[str, Any]
            A mapping with the claim's public fields, safe to embed in a
            wire message or state snapshot.
        """
        return {
            "task_id": self.task_id,
            "owner": self.owner,
            "note": self.note,
            "claimed_at": self.claimed_at,
            "lease_expires_at": self.lease_expires_at,
            "status": self.status,
            "data_ref": self.data_ref,
        }


@dataclass
class ResourceOffer:
    """A capability that an agent advertises to the rest of the team.

    Attributes
    ----------
    agent : str
        Name of the offering agent.
    kind : str
        Category of the resource, e.g. ``llm``, ``compute``, ``fs``, or
        ``memory``.
    name : str
        Concrete resource identifier, e.g. a model name or device handle.
    capacity : int
        How many concurrent consumers the offer can serve (minimum 1).
    meta : dict[str, Any]
        Arbitrary descriptive metadata about the offer.
    offered_at : float
        Wall-clock time, in seconds, when the offer was last refreshed.
    """

    agent: str
    kind: str
    name: str
    capacity: int = 1
    meta: dict[str, Any] = field(default_factory=dict)
    offered_at: float = field(default_factory=time.time)


class SynapseState:
    """Authoritative registry of presence, claims, tasks, and resources.

    The registry is single-threaded and synchronous; the hub owns one instance
    and mutates it from its event loop. Every mutating call refreshes the
    caller's heartbeat and lazily expires stale leases and offers, so liveness
    is maintained without a background timer.

    Parameters
    ----------
    default_ttl_seconds : float, optional
        Lease duration applied to a claim when the caller does not request an
        explicit TTL. Clamped up to :data:`MINIMUM_TTL_SECONDS`. Defaults to
        ``3600.0``.
    """

    def __init__(self, default_ttl_seconds: float = 3600.0) -> None:
        self.default_ttl_seconds = max(float(default_ttl_seconds), MINIMUM_TTL_SECONDS)
        self.last_seen: dict[str, float] = {}
        self.claims: dict[str, TaskClaim] = {}
        self.resources: dict[str, ResourceOffer] = {}

    def heartbeat(self, agent: str, now: float | None = None) -> None:
        """Record that ``agent`` is alive and expire anything now stale.

        Parameters
        ----------
        agent : str
            Name of the agent reporting liveness.
        now : float or None, optional
            Override for the current wall-clock time, in seconds. When ``None``
            the system clock is used. Primarily a testing seam.
        """
        ts = time.time() if now is None else float(now)
        self.last_seen[agent] = ts
        self._expire_claims(ts)
        self._expire_resources(ts)

    def claim(
        self,
        agent: str,
        task_id: str,
        note: str = "",
        ttl_seconds: float | None = None,
        now: float | None = None,
    ) -> tuple[bool, str]:
        """Acquire or renew a lease on a task.

        An owner may freely renew its own live claim, and any agent may take
        over a task whose lease has expired. A live claim held by another agent
        blocks the request.

        Parameters
        ----------
        agent : str
            Name of the agent attempting the claim.
        task_id : str
            Identifier of the task; surrounding whitespace is stripped.
        note : str, optional
            Human-readable context stored with the claim.
        ttl_seconds : float or None, optional
            Requested lease duration, clamped up to
            :data:`MINIMUM_TTL_SECONDS`. ``None`` uses ``default_ttl_seconds``.
        now : float or None, optional
            Override for the current wall-clock time, in seconds.

        Returns
        -------
        tuple[bool, str]
            ``(True, message)`` on success, ``(False, reason)`` when the task
            is missing an id or is held by another agent.
        """
        task = task_id.strip()
        if not task:
            return False, "Task ID is required."

        ts = time.time() if now is None else float(now)
        ttl = (
            self.default_ttl_seconds
            if ttl_seconds is None
            else max(float(ttl_seconds), MINIMUM_TTL_SECONDS)
        )
        self.heartbeat(agent, ts)

        existing = self.claims.get(task)
        if existing and existing.owner != agent and existing.lease_expires_at > ts:
            return False, f"Task '{task}' is already claimed by {existing.owner}."

        self.claims[task] = TaskClaim(
            task_id=task,
            owner=agent,
            note=note.strip(),
            claimed_at=ts,
            lease_expires_at=ts + ttl,
        )
        return True, f"Task '{task}' claimed by {agent}."

    def update_task(
        self,
        agent: str,
        task_id: str,
        *,
        status: str | None = None,
        note: str | None = None,
        data_ref: str | None = None,
        now: float | None = None,
    ) -> tuple[bool, str]:
        """Update the status, note, or artefact reference of an owned task.

        Only the claim owner may mutate it. Fields left as ``None`` are
        untouched; a non-empty ``status`` replaces the lifecycle marker.

        Parameters
        ----------
        agent : str
            Name of the agent issuing the update; must own the claim.
        task_id : str
            Identifier of the task to update.
        status : str or None, optional
            New lifecycle marker, applied only when truthy.
        note : str or None, optional
            Replacement note; stripped before storage.
        data_ref : str or None, optional
            Replacement artefact reference; stripped before storage.
        now : float or None, optional
            Override for the current wall-clock time, in seconds.

        Returns
        -------
        tuple[bool, str]
            ``(True, message)`` on success, ``(False, reason)`` when the task
            is unknown or owned by a different agent.
        """
        ts = time.time() if now is None else float(now)
        self.heartbeat(agent, ts)

        claim = self.claims.get(task_id)
        if claim is None:
            return False, f"Task '{task_id}' not found."
        if claim.owner != agent:
            return False, f"Task '{task_id}' owned by {claim.owner}, not {agent}."

        if status:
            claim.status = status
        if note is not None:
            claim.note = note.strip()
        if data_ref is not None:
            claim.data_ref = data_ref.strip()
        return True, f"Task '{task_id}' updated by {agent}."

    def release(self, agent: str, task_id: str, now: float | None = None) -> tuple[bool, str]:
        """Release a task held by ``agent``.

        Parameters
        ----------
        agent : str
            Name of the agent releasing the claim; must be the owner.
        task_id : str
            Identifier of the task; surrounding whitespace is stripped.
        now : float or None, optional
            Override for the current wall-clock time, in seconds.

        Returns
        -------
        tuple[bool, str]
            ``(True, message)`` on success, ``(False, reason)`` when the task
            id is empty, unclaimed, or owned by another agent.
        """
        task = task_id.strip()
        if not task:
            return False, "Task ID is required."

        ts = time.time() if now is None else float(now)
        self.heartbeat(agent, ts)
        existing = self.claims.get(task)
        if existing is None:
            return False, f"Task '{task}' is not currently claimed."
        if existing.owner != agent:
            return False, f"Task '{task}' is owned by {existing.owner}, not {agent}."
        del self.claims[task]
        return True, f"Task '{task}' released by {agent}."

    def offer_resource(
        self,
        agent: str,
        *,
        kind: str,
        name: str,
        capacity: int = 1,
        meta: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> str:
        """Advertise a resource the agent can provide, keyed by agent/kind/name.

        Re-offering the same triple refreshes the offer's liveness timestamp.

        Parameters
        ----------
        agent : str
            Name of the offering agent.
        kind : str
            Resource category, e.g. ``llm`` or ``compute``.
        name : str
            Concrete resource identifier.
        capacity : int, optional
            Concurrent-consumer capacity, clamped up to ``1``.
        meta : dict[str, Any] or None, optional
            Descriptive metadata; ``None`` becomes an empty mapping.
        now : float or None, optional
            Override for the current wall-clock time, in seconds.

        Returns
        -------
        str
            The registry key ``"{agent}:{kind}:{name}"`` of the stored offer.
        """
        ts = time.time() if now is None else float(now)
        self.heartbeat(agent, ts)
        key = f"{agent}:{kind}:{name}"
        self.resources[key] = ResourceOffer(
            agent=agent,
            kind=kind,
            name=name,
            capacity=max(1, int(capacity)),
            meta=meta or {},
            offered_at=ts,
        )
        return key

    def query_resources(self, kind: str | None = None) -> list[dict[str, Any]]:
        """List currently offered resources, optionally filtered by kind.

        Parameters
        ----------
        kind : str or None, optional
            When given, only offers of this category are returned.

        Returns
        -------
        list[dict[str, Any]]
            Offer mappings sorted by ``(agent, kind, name)``.
        """
        out: list[dict[str, Any]] = []
        for offer in self.resources.values():
            if kind is None or offer.kind == kind:
                out.append(
                    {
                        "agent": offer.agent,
                        "kind": offer.kind,
                        "name": offer.name,
                        "capacity": offer.capacity,
                        "meta": offer.meta,
                    }
                )
        return sorted(out, key=lambda r: (r["agent"], r["kind"], r["name"]))

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        """Return a consistent view of claims, agents, and resources.

        Stale claims and offers are expired before the view is built, so the
        snapshot never reports leases that have already lapsed.

        Parameters
        ----------
        now : float or None, optional
            Override for the current wall-clock time, in seconds.

        Returns
        -------
        dict[str, Any]
            Mapping with ``active_claims``, ``agents``, ``resources``, and the
            ``generated_at`` timestamp.
        """
        ts = time.time() if now is None else float(now)
        self._expire_claims(ts)
        self._expire_resources(ts)

        ordered = sorted(self.claims.values(), key=lambda c: c.task_id)
        claims = [claim.as_dict() for claim in ordered]
        agents = [
            {"agent": name, "last_seen": seen}
            for name, seen in sorted(self.last_seen.items(), key=lambda item: item[0])
        ]
        resources = self.query_resources()
        return {
            "active_claims": claims,
            "agents": agents,
            "resources": resources,
            "generated_at": ts,
        }

    def _expire_claims(self, now: float) -> None:
        """Drop every claim whose lease has reached or passed ``now``."""
        expired = [task for task, claim in self.claims.items() if claim.lease_expires_at <= now]
        for task in expired:
            del self.claims[task]

    def _expire_resources(
        self, now: float, ttl: float = DEFAULT_RESOURCE_TTL_SECONDS
    ) -> None:
        """Drop resource offers not refreshed within ``ttl`` seconds of ``now``."""
        stale = [k for k, r in self.resources.items() if (now - r.offered_at) > ttl]
        for k in stale:
            del self.resources[k]
