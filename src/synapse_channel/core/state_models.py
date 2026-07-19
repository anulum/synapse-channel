# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — state data models
"""Data models stored by the in-memory coordination state."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from synapse_channel.core.lifecycle import TaskStatus
from synapse_channel.core.path_identity import ClaimScopeIdentity
from synapse_channel.core.scoping import DEFAULT_WORKTREE

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
    quota_principal : str
        Internal stable bucket charged for this claim. It is intentionally absent
        from public snapshots and wire grants; durable journal writers use
        :meth:`as_persisted_dict` so restarts cannot reset a principal's budget.
    status : str
        Exact claim lifecycle marker: ``claimed``, ``working``,
        ``input_required``, ``done``, or ``failed``.
    data_ref : str
        Optional pointer to produced artefacts (e.g. a memory key or file path).
    worktree : str
        Worktree label the work happens in; claims in different worktrees never
        contend for files.
    paths : tuple[str, ...]
        Declared file/directory paths the claim intends to touch; empty means the
        whole worktree.
    path_identity : ClaimScopeIdentity or None
        Optional client-derived canonical identity aligned with ``paths``.  The
        hub compares it but never resolves paths or reads the filesystem.
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
        ``None`` for a plain claim. Opaque to the hub: stored and displayed but
        never acted on because the hub runs no git.
    """

    task_id: str
    owner: str
    note: str
    claimed_at: float
    lease_expires_at: float
    quota_principal: str = ""
    status: str = TaskStatus.CLAIMED
    data_ref: str = ""
    worktree: str = DEFAULT_WORKTREE
    paths: tuple[str, ...] = ()
    path_identity: ClaimScopeIdentity | None = None
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
        snapshot: dict[str, Any] = {
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
        if self.path_identity is not None:
            snapshot["path_identity"] = self.path_identity.as_dict()
        return snapshot

    def as_persisted_dict(self) -> dict[str, Any]:
        """Return a durable snapshot including private quota accounting.

        Public state and protocol views use :meth:`as_dict`, which omits the
        credential-derived principal. The append-only journal needs the bucket so
        replay preserves quota enforcement across a hub restart.
        """
        snapshot = self.as_dict()
        if self.quota_principal:
            snapshot["quota_principal"] = self.quota_principal
        return snapshot


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
