# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — immutable protected claim custody
"""Retain claim scope independently of author liveness.

These values are the claim-custody component of a reservation, not admission
permits. Complete effect expansion, reservation phases and journal reconstruction
must be integrated before any protected request is enabled.
"""

from __future__ import annotations

from dataclasses import dataclass

from synapse_channel.core.path_identity import ClaimScopeIdentity, claim_scopes_conflict
from synapse_channel.core.state_models import TaskClaim


@dataclass(frozen=True)
class ProtectedClaimCustody:
    """Immutable claim witness retained until explicit reservation settlement."""

    task_id: str
    owner: str
    epoch: int
    version: int
    lease_expires_at: float
    worktree: str
    paths: tuple[str, ...]
    path_identity: ClaimScopeIdentity | None

    @classmethod
    def capture(cls, claim: TaskClaim) -> ProtectedClaimCustody:
        """Copy an already validated authoritative claim, never renew its lease.

        Parameters
        ----------
        claim:
            Live authoritative claim validated inside the mutation actor.

        Returns
        -------
        ProtectedClaimCustody
            Immutable scope and epoch/version/lease witness.
        """
        return cls(
            claim.task_id,
            claim.owner,
            claim.epoch,
            claim.version,
            claim.lease_expires_at,
            claim.worktree,
            tuple(claim.paths),
            claim.path_identity,
        )

    def conflicts(
        self,
        task_id: str,
        worktree: str,
        paths: tuple[str, ...],
        path_identity: ClaimScopeIdentity | None,
    ) -> bool:
        """Check task or scope overlap without any same-owner exemption.

        Parameters
        ----------
        task_id:
            Incoming task identity.
        worktree:
            Incoming display worktree.
        paths:
            Normalized incoming display paths.
        path_identity:
            Optional incoming canonical identity.

        Returns
        -------
        bool
            Whether the exact task or its declared scope is held by custody.
        """
        return self.task_id == task_id or claim_scopes_conflict(
            worktree, paths, path_identity, self.worktree, self.paths, self.path_identity
        )
