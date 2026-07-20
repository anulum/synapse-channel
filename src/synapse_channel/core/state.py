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
from typing import Any

from synapse_channel.core.lifecycle import can_transition
from synapse_channel.core.numeric_coercion import safe_float, safe_int
from synapse_channel.core.path_identity import ClaimScopeIdentity
from synapse_channel.core.scoping import (
    DEFAULT_WORKTREE,
    MAX_DECLARED_PATHS,
    normalize_paths,
)
from synapse_channel.core.state_leases import (
    LEASE_HEAP_COMPACT_FLOOR as LEASE_HEAP_COMPACT_FLOOR,
)
from synapse_channel.core.state_leases import (
    LeaseIndex,
)
from synapse_channel.core.state_models import GitContext, ResourceOffer, TaskClaim
from synapse_channel.core.state_resources import (
    DEFAULT_RESOURCE_TTL_SECONDS,
    MAX_OFFERS_PER_AGENT,
    ResourceRegistry,
)
from synapse_channel.core.state_scopes import find_scope_conflict

MINIMUM_TTL_SECONDS = 30.0
"""Floor applied to every requested lease/default TTL, in seconds."""

MAXIMUM_TTL_SECONDS = 2_592_000.0
"""Ceiling applied to every requested lease/default TTL, in seconds (30 days).

A lease is a liveness hint, not a durable reservation. Without a ceiling a caller
could request ``ttl_seconds=1e15`` and pin a task for longer than the process (or
the universe) will ever run, so the registry clamps every requested and default
TTL into ``[MINIMUM_TTL_SECONDS, MAXIMUM_TTL_SECONDS]``. Thirty days sits far
above any legitimate coordination lease (the default is one hour) while bounding
a runaway or hostile request. ``inf``/``nan`` are rejected earlier by
:func:`~synapse_channel.core.numeric_coercion.safe_float` (``finite=True``), so
the clamp only ever sees a finite value and never fails open on a non-finite one.
"""

MAX_CLAIMS_PER_AGENT = 128
"""Most live claims one agent may hold, so a runaway agent cannot exhaust state."""

__all__ = [
    "DEFAULT_RESOURCE_TTL_SECONDS",
    "GitContext",
    "LEASE_HEAP_COMPACT_FLOOR",
    "MAX_CLAIMS_PER_AGENT",
    "MAX_OFFERS_PER_AGENT",
    "MAXIMUM_TTL_SECONDS",
    "MINIMUM_TTL_SECONDS",
    "ResourceOffer",
    "SynapseState",
    "TaskClaim",
]


def _clamp_ttl(seconds: float) -> float:
    """Clamp a finite TTL into ``[MINIMUM_TTL_SECONDS, MAXIMUM_TTL_SECONDS]``.

    The caller must pass an already-finite value (every call site coerces through
    :func:`~synapse_channel.core.numeric_coercion.safe_float` with ``finite=True``
    first, so ``inf``/``nan`` become the finite default before reaching here). On a
    finite input the ``min``/``max`` window is total and never propagates a
    non-finite value, so the ceiling cannot be silently bypassed.
    """
    return min(max(seconds, MINIMUM_TTL_SECONDS), MAXIMUM_TTL_SECONDS)


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
        explicit TTL. Clamped into
        ``[MINIMUM_TTL_SECONDS, MAXIMUM_TTL_SECONDS]``. Defaults to ``3600.0``.
    max_claims_per_agent : int, optional
        Most live claims one agent may hold. Clamped up to ``1``. Defaults to
        :data:`MAX_CLAIMS_PER_AGENT`.
    max_offers_per_agent : int, optional
        Most live resource offers one agent may register. Clamped up to ``1``.
        Defaults to :data:`MAX_OFFERS_PER_AGENT`.
    max_paths_per_claim : int, optional
        Most distinct paths a single claim may declare before its scope is
        widened to the whole worktree. Clamped up to ``1``. Defaults to
        :data:`~synapse_channel.core.scoping.MAX_DECLARED_PATHS`.
    """

    def __init__(
        self,
        default_ttl_seconds: float = 3600.0,
        *,
        max_claims_per_agent: int = MAX_CLAIMS_PER_AGENT,
        max_offers_per_agent: int = MAX_OFFERS_PER_AGENT,
        max_paths_per_claim: int = MAX_DECLARED_PATHS,
    ) -> None:
        self.default_ttl_seconds = _clamp_ttl(safe_float(default_ttl_seconds, default=3600.0))
        self.max_claims_per_agent = safe_int(
            max_claims_per_agent, default=MAX_CLAIMS_PER_AGENT, min_value=1
        )
        self.max_offers_per_agent = safe_int(
            max_offers_per_agent, default=MAX_OFFERS_PER_AGENT, min_value=1
        )
        self.max_paths_per_claim = safe_int(
            max_paths_per_claim, default=MAX_DECLARED_PATHS, min_value=1
        )
        self.last_seen: dict[str, float] = {}
        self.claims: dict[str, TaskClaim] = {}
        self._resource_registry = ResourceRegistry(max_offers_per_agent=self.max_offers_per_agent)
        self.resources: dict[str, ResourceOffer] = self._resource_registry.resources
        self.max_offers_per_agent = self._resource_registry.max_offers_per_agent
        self.expired_checkpoints: dict[str, str] = {}
        self._epoch_seq = 0
        self._lease_index = LeaseIndex()

    @property
    def _lease_heap(self) -> list[tuple[float, str, int]]:
        """Compatibility view of the underlying lease-index entries."""
        return self._lease_index.entries

    @_lease_heap.setter
    def _lease_heap(self, entries: list[tuple[float, str, int]]) -> None:
        self._lease_index.entries = entries

    def _next_epoch(self) -> int:
        """Return the next strictly-increasing lease generation number."""
        self._epoch_seq += 1
        return self._epoch_seq

    def publish_from(self, candidate: SynapseState) -> None:
        """Atomically publish a privately mutated candidate state.

        Durable hub mutations are prepared on a deep copy while the current
        state remains visible to readers.  After the matching journal append
        commits, the event-loop mutation actor calls this synchronous method;
        because it contains no await, readers observe either the complete old
        state or the complete committed state, never a provisional mutation.

        ``last_seen`` is updated in place because the hub liveness view retains
        that mapping by reference.  The other registries are reached through
        ``hub.state`` on every read and can therefore be replaced wholesale.
        Configuration is immutable for a running hub and must agree between the
        authoritative state and its clone.
        """
        if (
            candidate.default_ttl_seconds != self.default_ttl_seconds
            or candidate.max_claims_per_agent != self.max_claims_per_agent
            or candidate.max_offers_per_agent != self.max_offers_per_agent
            or candidate.max_paths_per_claim != self.max_paths_per_claim
        ):
            raise ValueError("candidate state configuration does not match the live hub")

        self.last_seen.clear()
        self.last_seen.update(candidate.last_seen)
        self.claims = candidate.claims
        self._resource_registry = candidate._resource_registry
        self.resources = self._resource_registry.resources
        self.expired_checkpoints = candidate.expired_checkpoints
        self._epoch_seq = candidate._epoch_seq
        self._lease_index = candidate._lease_index

    def _track_lease(self, claim: TaskClaim) -> None:
        """Index a claim's lease for expiry, keeping the heap proportional to live claims.

        Called whenever a live lease is created or renewed. A renewal leaves its
        previous heap entry behind (lazy deletion reclaims it only when its expiry
        is reached), so a frequently-renewed claim can pile up future-dated stale
        entries; once the heap outgrows the live claims by a comfortable margin it
        is rebuilt (:meth:`reindex_leases`), an O(n) heapify that bounds the heap
        size to the active-claim count.
        """
        self._lease_index.track(claim, self.claims)

    def reindex_leases(self) -> None:
        """Rebuild the lease-expiry heap from the live claims.

        Discards every superseded heap entry in one pass. Used after a bulk load
        that assigns claims directly — a journal replay — and as the churn-bound
        rebuild in :meth:`_track_lease`.
        """
        self._lease_index.rebuild(self.claims)

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
        quota_principal: str | None = None,
        worktree: str = DEFAULT_WORKTREE,
        paths: tuple[str, ...] | list[str] = (),
        path_identity: ClaimScopeIdentity | None = None,
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
            Requested lease duration, clamped into
            ``[MINIMUM_TTL_SECONDS, MAXIMUM_TTL_SECONDS]``. A non-finite request
            falls back to ``default_ttl_seconds``. ``None`` uses
            ``default_ttl_seconds``.
        now : float or None, optional
            Override for the current wall-clock time, in seconds.
        worktree : str, optional
            Worktree label; claims in different worktrees never contend.
        paths : tuple[str, ...] or list[str], optional
            Declared file/directory paths; empty claims the whole worktree.
        path_identity : ClaimScopeIdentity or None, optional
            Client-derived filesystem-canonical identity aligned one-to-one with
            ``paths``.  Malformed alignment is refused rather than ignored.
        git : GitContext or None, optional
            Branch context to attach to the claim; ``None`` leaves it unset. A
            renewal replaces it with the supplied value, so a git-aware client
            keeps the branch current by passing it on every claim.
        quota_principal : str or None, optional
            Stable server-derived identity bucket charged for the claim. ``None``
            preserves the direct-call compatibility behaviour by using ``agent``.

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
        principal = str(quota_principal or agent).strip() or agent

        ts = time.time() if now is None else float(now)
        ttl = (
            self.default_ttl_seconds
            if ttl_seconds is None
            else _clamp_ttl(
                safe_float(ttl_seconds, default=self.default_ttl_seconds, allow_bool=False)
            )
        )
        norm_paths = normalize_paths(paths, self.max_paths_per_claim)
        if path_identity is not None and not path_identity.validates_display_scope(
            worktree, norm_paths
        ):
            return False, f"Task '{task}' path identity does not match its declared scope."
        self.heartbeat(agent, ts)

        existing = self.claims.get(task)
        if existing and existing.owner != agent and existing.lease_expires_at > ts:
            return False, f"Task '{task}' is already claimed by {existing.owner}."

        conflict = self._scope_conflict(
            task,
            agent,
            worktree,
            norm_paths,
            path_identity,
        )
        if conflict is not None:
            other_id, other_owner = conflict
            return (
                False,
                f"Task '{task}' file scope conflicts with '{other_id}' held by {other_owner}.",
            )

        # Cap the live claims one server-derived principal may hold so rotating an
        # asserted agent name cannot multiply the budget. A same-principal renewal
        # is free. If a task moved to this owner while still charged to a different
        # principal, its first renewal transfers the charge only when the new bucket
        # has capacity.
        owns_task = existing is not None and existing.owner == agent
        existing_principal = (
            (existing.quota_principal or existing.owner) if existing is not None else ""
        )
        same_principal = owns_task and existing_principal == principal
        if not same_principal and self._claims_owned_by(principal) >= self.max_claims_per_agent:
            return (
                False,
                f"Agent {agent} principal claim quota holds the maximum "
                f"{self.max_claims_per_agent} claims.",
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
            quota_principal=principal,
            worktree=worktree,
            paths=norm_paths,
            path_identity=path_identity,
            epoch=self._next_epoch(),
            checkpoint=checkpoint,
            git=git,
        )
        self.claims[task] = claim
        self._track_lease(claim)
        return True, f"Task '{task}' claimed by {agent}."

    def _claims_owned_by(self, quota_principal: str) -> int:
        """Return how many live claims are charged to ``quota_principal``."""
        return sum(
            1
            for claim in self.claims.values()
            if (claim.quota_principal or claim.owner) == quota_principal
        )

    def _offers_by(self, agent: str) -> int:
        """Return how many live resource offers ``agent`` currently holds."""
        return self._resource_registry.offers_by(agent)

    def _scope_conflict(
        self,
        task: str,
        agent: str,
        worktree: str,
        paths: tuple[str, ...],
        path_identity: ClaimScopeIdentity | None,
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
        path_identity : ClaimScopeIdentity or None
            Optional canonical identity aligned with ``paths``.

        Returns
        -------
        tuple[str, str] or None
            ``(other_task_id, other_owner)`` of the first conflicting claim, or
            ``None`` when the scope is free.
        """
        return find_scope_conflict(
            self.claims,
            task=task,
            agent=agent,
            worktree=worktree,
            paths=paths,
            path_identity=path_identity,
        )

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

    def force_release(self, task_id: str, *, by: str) -> tuple[bool, str]:
        """Release a task regardless of who holds it, on externally verified authority.

        Unlike :meth:`release`, this does not require the caller to be the lease owner:
        the authority to revoke another agent's claim is established *before* this is
        called — a governed cross-hub operator relay whose peer, scope, and namespace
        ownership have all been verified deny-closed. This method only executes the
        revocation the caller is already entitled to make, so it never checks ownership
        itself; it must not be reachable except behind that authorisation.

        The revocation is otherwise identical to a self-release: the lease is dropped and
        any retained checkpoint discarded, so a later unrelated claim of the same id does
        not resurrect stale resume state. It does not touch the operator's heartbeat — the
        operator is remote and holds no presence on this hub.

        Parameters
        ----------
        task_id : str
            Identifier of the task to revoke; surrounding whitespace is stripped.
        by : str
            The operator identity the revocation is attributed to, named in the message
            for the audit trail.

        Returns
        -------
        tuple[bool, str]
            ``(True, message)`` naming the operator and the previous holder on success;
            ``(False, reason)`` when the task id is empty or the task is not claimed.
        """
        task = task_id.strip()
        if not task:
            return False, "Task ID is required."
        existing = self.claims.get(task)
        if existing is None:
            return False, f"Task '{task}' is not currently claimed."
        previous_owner = existing.owner
        del self.claims[task]
        self.expired_checkpoints.pop(task, None)
        return True, (f"Task '{task}' released by operator {by} (was held by {previous_owner}).")

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
            owner, given no target, carries a stale epoch, or the recipient
            already holds the live-claim cap (the same invariant as direct
            :meth:`claim`).
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
        # Same live-claim cap as direct acquisition: a handoff must not grow the
        # recipient past max_claims_per_agent (BUG-7). Refuse before mutation so
        # ownership and journal stay consistent.
        if self._claims_owned_by(target) >= self.max_claims_per_agent:
            return (
                False,
                f"Agent {target} holds the maximum {self.max_claims_per_agent} claims.",
            )

        moved = TaskClaim(
            task_id=task,
            owner=target,
            note=note.strip() if note is not None else claim.note,
            claimed_at=ts,
            lease_expires_at=ts + self.default_ttl_seconds,
            # A handoff cannot assert the recipient's identity. Keep the claim
            # charged to the proven sender until the recipient renews through its
            # own admitted connection, at which point claim() transfers the bucket
            # subject to the recipient's quota.
            quota_principal=claim.quota_principal or claim.owner,
            status=claim.status,
            data_ref=claim.data_ref,
            worktree=claim.worktree,
            paths=claim.paths,
            path_identity=claim.path_identity,
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
    ) -> str | None:
        """Advertise a resource the agent can provide, keyed by agent/kind/name.

        Re-offering the same triple refreshes the offer's liveness timestamp. A new
        offer is refused once the agent already holds
        :data:`MAX_OFFERS_PER_AGENT` live offers, so a runaway agent cannot bloat
        the registry; refreshing an existing offer is always allowed.

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
        str or None
            The registry key ``"{agent}:{kind}:{name}"`` of the stored offer, or
            ``None`` when the agent is at its offer quota and this is a new offer.
        """
        ts = time.time() if now is None else float(now)
        self.heartbeat(agent, ts)
        return self._resource_registry.offer(
            agent,
            kind=kind,
            name=name,
            capacity=capacity,
            meta=meta,
            now=ts,
        )

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
        return self._resource_registry.query(kind=kind)

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
        for task, epoch in self._lease_index.pop_due(now):
            claim = self.claims.get(task)
            if claim is None or claim.epoch != epoch:
                continue  # superseded entry: released, already expired, or renewed
            if claim.checkpoint:
                self.expired_checkpoints[task] = claim.checkpoint
            del self.claims[task]

    def _expire_resources(self, now: float, ttl: float = DEFAULT_RESOURCE_TTL_SECONDS) -> None:
        """Drop resource offers not refreshed within ``ttl`` seconds of ``now``."""
        self._resource_registry.expire(now, ttl=ttl)
