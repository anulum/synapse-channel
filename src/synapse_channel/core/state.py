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

import heapq
import time
from dataclasses import dataclass, field
from typing import Any

from synapse_channel.core.lifecycle import TaskStatus, can_transition
from synapse_channel.core.scoping import DEFAULT_WORKTREE, normalize_paths, scopes_conflict

MINIMUM_TTL_SECONDS = 30.0
"""Floor applied to every requested lease/default TTL, in seconds."""

LEASE_HEAP_COMPACT_FLOOR = 16
"""Slack before the lease heap is rebuilt to shed superseded entries (see
:meth:`SynapseState._track_lease`)."""

DEFAULT_RESOURCE_TTL_SECONDS = 300.0
"""Soft liveness window after which an un-refreshed resource offer is dropped."""

AUTO_RELEASE_MODES = frozenset({"manual", "commit", "merge"})
"""Recognised auto-release triggers; an unknown value falls back to ``manual``."""


@dataclass(frozen=True)
class GitContext:
    """The git branch context a claim is scoped to.

    Opaque to the hub: it is stored on the claim, journalled, replayed, and shown
    in snapshots, but the hub never reads the filesystem or runs git. All git
    resolution happens client-side; this record only carries the result, so the
    hub can group and display claims by branch without ever touching a repository.

    Attributes
    ----------
    branch : str
        The branch the claiming agent is working on.
    base : str
        The branch the work will merge back into. Defaults to ``main``.
    auto_release_on : str
        When a client-side git hook should release the claim: ``manual`` (never
        automatically), ``commit``, or ``merge``. Defaults to ``merge``.
    """

    branch: str
    base: str = "main"
    auto_release_on: str = "merge"

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable mapping of the git context."""
        return {"branch": self.branch, "base": self.base, "auto_release_on": self.auto_release_on}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GitContext:
        """Rebuild a :class:`GitContext` from a wire or journal mapping.

        A defensive deserialiser, not an interpreter: an unrecognised
        ``auto_release_on`` falls back to ``manual`` and an empty ``base`` to
        ``main``, so a malformed field from a peer never crashes the hub.

        Parameters
        ----------
        data : dict[str, Any]
            The mapping carried on a claim message or replayed from the journal.

        Returns
        -------
        GitContext
            The reconstructed, normalised context.
        """
        mode = str(data.get("auto_release_on", "merge"))
        if mode not in AUTO_RELEASE_MODES:
            mode = "manual"
        return cls(
            branch=str(data.get("branch", "")),
            base=str(data.get("base") or "main"),
            auto_release_on=mode,
        )


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
    worktree : str
        Worktree label the work happens in; claims in different worktrees never
        contend for files.
    paths : tuple[str, ...]
        Declared file/directory paths the claim intends to touch; empty means the
        whole worktree.
    epoch : int
        Strictly-increasing lease generation. A mutation carrying a stale epoch is
        rejected, so a paused/expired agent cannot act on a superseded claim.
    version : int
        Optimistic-concurrency counter bumped on every field update, used for
        compare-and-swap so a stale update is rejected. Reset on (re)claim.
    checkpoint : str
        Opaque resume token the owner saves so the work can continue from where
        it stopped. It survives lease expiry: a later claimant of the same task
        inherits the last checkpoint instead of restarting.
    git : GitContext or None
        The branch context the claim is scoped to, set by a git-aware client;
        ``None`` for a plain claim. Opaque to the hub — stored and displayed but
        never acted on (the hub runs no git).
    """

    task_id: str
    owner: str
    note: str
    claimed_at: float
    lease_expires_at: float
    status: str = TaskStatus.CLAIMED
    data_ref: str = ""
    worktree: str = DEFAULT_WORKTREE
    paths: tuple[str, ...] = ()
    epoch: int = 0
    version: int = 0
    checkpoint: str = ""
    git: GitContext | None = None

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
            "worktree": self.worktree,
            "paths": list(self.paths),
            "epoch": self.epoch,
            "version": self.version,
            "checkpoint": self.checkpoint,
            "git": self.git.as_dict() if self.git is not None else None,
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
        self.expired_checkpoints: dict[str, str] = {}
        self._epoch_seq = 0
        # Min-heap of (lease_expires_at, task_id, epoch) so expiry pops only the
        # leases that have actually lapsed instead of scanning every claim on each
        # mutation. The epoch makes a heap entry self-validating: a renewal bumps
        # the claim's epoch, so a superseded entry is recognised and skipped when
        # popped (lazy deletion) rather than removed up front.
        self._lease_heap: list[tuple[float, str, int]] = []

    def _next_epoch(self) -> int:
        """Return the next strictly-increasing lease generation number."""
        self._epoch_seq += 1
        return self._epoch_seq

    def _track_lease(self, claim: TaskClaim) -> None:
        """Index a claim's lease for expiry, keeping the heap proportional to live claims.

        Called whenever a live lease is created or renewed. A renewal leaves its
        previous heap entry behind (lazy deletion reclaims it only when its expiry
        is reached), so a frequently-renewed claim can pile up future-dated stale
        entries; once the heap outgrows the live claims by a comfortable margin it
        is rebuilt (:meth:`reindex_leases`), an O(n) heapify that bounds the heap
        size to the active-claim count.
        """
        heapq.heappush(self._lease_heap, (claim.lease_expires_at, claim.task_id, claim.epoch))
        if len(self._lease_heap) > 2 * len(self.claims) + LEASE_HEAP_COMPACT_FLOOR:
            self.reindex_leases()

    def reindex_leases(self) -> None:
        """Rebuild the lease-expiry heap from the live claims.

        Discards every superseded heap entry in one pass. Used after a bulk load
        that assigns claims directly — a journal replay — and as the churn-bound
        rebuild in :meth:`_track_lease`.
        """
        self._lease_heap = [
            (claim.lease_expires_at, task, claim.epoch) for task, claim in self.claims.items()
        ]
        heapq.heapify(self._lease_heap)

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
        *,
        worktree: str = DEFAULT_WORKTREE,
        paths: tuple[str, ...] | list[str] = (),
        git: GitContext | None = None,
    ) -> tuple[bool, str]:
        """Acquire or renew a scoped lease on a task.

        An owner may freely renew its own live claim, and any agent may take over
        a task whose lease has expired. A live claim held by another agent blocks
        the request. Beyond the task id, a claim may declare a file scope
        (``worktree`` + ``paths``); the request is also refused when that scope
        contends with another agent's live claim, which is how the bus prevents
        two agents from editing the same files. Every successful claim or renewal
        is stamped with a fresh, strictly-increasing :attr:`TaskClaim.epoch`.

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
        worktree : str, optional
            Worktree label; claims in different worktrees never contend.
        paths : tuple[str, ...] or list[str], optional
            Declared file/directory paths; empty claims the whole worktree.
        git : GitContext or None, optional
            Branch context to attach to the claim; ``None`` leaves it unset. A
            renewal replaces it with the supplied value, so a git-aware client
            keeps the branch current by passing it on every claim.

        Returns
        -------
        tuple[bool, str]
            ``(True, message)`` on success, ``(False, reason)`` when the task is
            missing an id, held by another agent, or its file scope overlaps
            another agent's live claim.
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
        norm_paths = normalize_paths(paths)

        existing = self.claims.get(task)
        if existing and existing.owner != agent and existing.lease_expires_at > ts:
            return False, f"Task '{task}' is already claimed by {existing.owner}."

        conflict = self._scope_conflict(task, agent, worktree, norm_paths)
        if conflict is not None:
            other_id, other_owner = conflict
            return (
                False,
                f"Task '{task}' file scope conflicts with '{other_id}' held by {other_owner}.",
            )

        # Carry the checkpoint forward: the same owner renewing keeps its own; a
        # new owner taking over an expired task resumes the retained checkpoint.
        if existing is not None and existing.owner == agent:
            checkpoint = existing.checkpoint
        else:
            checkpoint = self.expired_checkpoints.pop(task, "")

        claim = TaskClaim(
            task_id=task,
            owner=agent,
            note=note.strip(),
            claimed_at=ts,
            lease_expires_at=ts + ttl,
            worktree=worktree,
            paths=norm_paths,
            epoch=self._next_epoch(),
            checkpoint=checkpoint,
            git=git,
        )
        self.claims[task] = claim
        self._track_lease(claim)
        return True, f"Task '{task}' claimed by {agent}."

    def _scope_conflict(
        self, task: str, agent: str, worktree: str, paths: tuple[str, ...]
    ) -> tuple[str, str] | None:
        """Return the first other live claim whose file scope contends, if any.

        Claims belonging to ``agent`` and the claim for ``task`` itself are
        skipped, so renewing a claim or holding several of one's own claims never
        self-conflicts. Stale claims are assumed already expired by the preceding
        heartbeat.

        Parameters
        ----------
        task : str
            The task id being claimed (skipped during the scan).
        agent : str
            The claiming agent (its own claims are skipped).
        worktree : str
            Worktree label of the incoming claim.
        paths : tuple[str, ...]
            Normalised declared paths of the incoming claim.

        Returns
        -------
        tuple[str, str] or None
            ``(other_task_id, other_owner)`` of the first conflicting claim, or
            ``None`` when the scope is free.
        """
        for other_id, other in self.claims.items():
            if other_id == task or other.owner == agent:
                continue
            if scopes_conflict(worktree, paths, other.worktree, other.paths):
                return other_id, other.owner
        return None

    def update_task(
        self,
        agent: str,
        task_id: str,
        *,
        status: str | None = None,
        note: str | None = None,
        data_ref: str | None = None,
        epoch: int | None = None,
        expected_version: int | None = None,
        now: float | None = None,
    ) -> tuple[bool, str]:
        """Update the status, note, or artefact reference of an owned task.

        Only the claim owner may mutate it. Fields left as ``None`` are
        untouched; a non-empty ``status`` must be a legal lifecycle transition
        (see :func:`synapse_channel.core.lifecycle.can_transition`). When ``epoch`` is
        supplied it must match the claim's current epoch (lease guard), and when
        ``expected_version`` is supplied it must match the claim's current version
        (optimistic-concurrency guard against lost updates). A successful update
        bumps the version.

        Parameters
        ----------
        agent : str
            Name of the agent issuing the update; must own the claim.
        task_id : str
            Identifier of the task to update.
        status : str or None, optional
            New lifecycle status, applied only when truthy and the transition is
            legal.
        note : str or None, optional
            Replacement note; stripped before storage.
        data_ref : str or None, optional
            Replacement artefact reference; stripped before storage.
        epoch : int or None, optional
            Expected lease generation; when given and stale, the update is refused.
        expected_version : int or None, optional
            Expected field version; when given and mismatched, the update is
            refused so a stale writer cannot clobber a newer value.
        now : float or None, optional
            Override for the current wall-clock time, in seconds.

        Returns
        -------
        tuple[bool, str]
            ``(True, message)`` on success, ``(False, reason)`` when the task is
            unknown, owned by a different agent, carries a stale epoch or version,
            or requests an illegal status transition.
        """
        ts = time.time() if now is None else float(now)
        self.heartbeat(agent, ts)

        claim = self.claims.get(task_id)
        if claim is None:
            return False, f"Task '{task_id}' not found."
        if claim.owner != agent:
            return False, f"Task '{task_id}' owned by {claim.owner}, not {agent}."
        if epoch is not None and epoch != claim.epoch:
            return False, f"Task '{task_id}' epoch is stale (current {claim.epoch})."
        if expected_version is not None and expected_version != claim.version:
            return False, f"Task '{task_id}' version conflict (current {claim.version})."
        if status and not can_transition(claim.status, status):
            return False, f"Task '{task_id}' cannot transition {claim.status} -> {status}."

        if status:
            claim.status = status
        if note is not None:
            claim.note = note.strip()
        if data_ref is not None:
            claim.data_ref = data_ref.strip()
        claim.version += 1
        return True, f"Task '{task_id}' updated by {agent}."

    def save_checkpoint(
        self,
        agent: str,
        task_id: str,
        checkpoint: str,
        *,
        epoch: int | None = None,
        now: float | None = None,
    ) -> tuple[bool, str]:
        """Save a resume token on an owned task so it can continue after expiry.

        Only the owner may save, and the checkpoint persists with the claim: if
        the lease later expires, a new claimant of the same task inherits it.

        Parameters
        ----------
        agent : str
            The owner saving the checkpoint.
        task_id : str
            Identifier of the owned task; whitespace is stripped.
        checkpoint : str
            Opaque resume token to store.
        epoch : int or None, optional
            Expected lease generation; a stale epoch is refused.
        now : float or None, optional
            Override for the current wall-clock time, in seconds.

        Returns
        -------
        tuple[bool, str]
            ``(True, message)`` on success, ``(False, reason)`` when the task is
            unknown, owned by another agent, or carries a stale epoch.
        """
        task = task_id.strip()
        ts = time.time() if now is None else float(now)
        self.heartbeat(agent, ts)
        claim = self.claims.get(task)
        if claim is None:
            return False, f"Task '{task}' not found."
        if claim.owner != agent:
            return False, f"Task '{task}' owned by {claim.owner}, not {agent}."
        if epoch is not None and epoch != claim.epoch:
            return False, f"Task '{task}' epoch is stale (current {claim.epoch})."
        claim.checkpoint = str(checkpoint)
        claim.version += 1
        return True, f"Checkpoint saved for '{task}' by {agent}."

    def release(
        self,
        agent: str,
        task_id: str,
        now: float | None = None,
        *,
        epoch: int | None = None,
    ) -> tuple[bool, str]:
        """Release a task held by ``agent``.

        Parameters
        ----------
        agent : str
            Name of the agent releasing the claim; must be the owner.
        task_id : str
            Identifier of the task; surrounding whitespace is stripped.
        now : float or None, optional
            Override for the current wall-clock time, in seconds.
        epoch : int or None, optional
            Expected lease generation; when given and stale, the release is refused
            so an agent cannot drop a lease that has since been superseded.

        Returns
        -------
        tuple[bool, str]
            ``(True, message)`` on success, ``(False, reason)`` when the task
            id is empty, unclaimed, owned by another agent, or carries a stale
            epoch.
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
        if epoch is not None and epoch != existing.epoch:
            return False, f"Task '{task}' epoch is stale (current {existing.epoch})."
        del self.claims[task]
        # The task is finished; drop any retained checkpoint so a later, unrelated
        # claim of the same id does not resurrect stale resume state.
        self.expired_checkpoints.pop(task, None)
        return True, f"Task '{task}' released by {agent}."

    def handoff(
        self,
        agent: str,
        task_id: str,
        to_agent: str,
        *,
        note: str | None = None,
        epoch: int | None = None,
        now: float | None = None,
    ) -> tuple[bool, str]:
        """Transfer an owned task to another agent in one atomic step.

        Ownership moves directly from the holder to ``to_agent`` with no
        release/re-claim window in which a third agent could grab the task. The
        task keeps its file scope, status, and artefact reference (its working
        context) and is stamped with a fresh epoch and a full lease, so the
        previous owner's epoch becomes stale and cannot act on the moved task.
        The version counter resets for the new owner.

        Parameters
        ----------
        agent : str
            The current owner requesting the handoff.
        task_id : str
            Identifier of the task to hand off; whitespace is stripped.
        to_agent : str
            The agent to receive the task; whitespace is stripped.
        note : str or None, optional
            Replacement note for the moved claim; the existing note is kept when
            ``None``.
        epoch : int or None, optional
            Expected lease generation; a stale epoch is refused.
        now : float or None, optional
            Override for the current wall-clock time, in seconds.

        Returns
        -------
        tuple[bool, str]
            ``(True, message)`` on success, ``(False, reason)`` when the task is
            missing an id, unclaimed, owned by another agent, handed to its own
            owner, given no target, or carries a stale epoch.
        """
        task = task_id.strip()
        if not task:
            return False, "Task ID is required."
        target = to_agent.strip()
        if not target:
            return False, "Handoff target is required."

        ts = time.time() if now is None else float(now)
        self.heartbeat(agent, ts)
        claim = self.claims.get(task)
        if claim is None:
            return False, f"Task '{task}' is not currently claimed."
        if claim.owner != agent:
            return False, f"Task '{task}' is owned by {claim.owner}, not {agent}."
        if target == agent:
            return False, f"Task '{task}' is already owned by {agent}."
        if epoch is not None and epoch != claim.epoch:
            return False, f"Task '{task}' epoch is stale (current {claim.epoch})."

        moved = TaskClaim(
            task_id=task,
            owner=target,
            note=note.strip() if note is not None else claim.note,
            claimed_at=ts,
            lease_expires_at=ts + self.default_ttl_seconds,
            status=claim.status,
            data_ref=claim.data_ref,
            worktree=claim.worktree,
            paths=claim.paths,
            epoch=self._next_epoch(),
            checkpoint=claim.checkpoint,
            git=claim.git,
        )
        self.claims[task] = moved
        self._track_lease(moved)
        self.last_seen[target] = ts
        return True, f"Task '{task}' handed from {agent} to {target}."

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
        """Drop every claim whose lease has reached or passed ``now``.

        Pops the lease heap while its earliest expiry is due, so the cost is
        proportional to the number of leases actually expiring, not the total
        number of claims. A popped entry is applied only when it is the live lease
        for its task: a missing claim (released or already expired) or an epoch
        mismatch (the lease was renewed, and a later entry covers the new expiry)
        means the entry is stale and is skipped. An expiring claim's checkpoint is
        retained so a later claimant of the same task can resume from it.
        """
        heap = self._lease_heap
        while heap and heap[0][0] <= now:
            _expires_at, task, epoch = heapq.heappop(heap)
            claim = self.claims.get(task)
            if claim is None or claim.epoch != epoch:
                continue  # superseded entry: released, already expired, or renewed
            if claim.checkpoint:
                self.expired_checkpoints[task] = claim.checkpoint
            del self.claims[task]

    def _expire_resources(self, now: float, ttl: float = DEFAULT_RESOURCE_TTL_SECONDS) -> None:
        """Drop resource offers not refreshed within ``ttl`` seconds of ``now``."""
        stale = [k for k, r in self.resources.items() if (now - r.offered_at) > ttl]
        for k in stale:
            del self.resources[k]
