# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — two-person approval ledger for governed cross-hub operator relays
"""Require two distinct verified federation principals before a governed relay applies.

A cross-hub operator relay force-releases a lease on a peer hub's authority, which is powerful
enough that a team or production hub may want it to need *two* principals, not one. This module owns
that quorum as a small stateful ledger, separate from the deny-by-default policy in
:mod:`synapse_channel.core.operator_relay`: the policy decides whether a single relay is authorised
at all, and this ledger — consulted only after that policy allows — decides whether enough distinct
verified principals have now asked for the same action to carry it out.

The rule is deliberately narrow. Two relays match when they target the same action, namespace, and
task; the ledger records the first as *pending* and applies the pair only when a *second, distinct*
principal submits the same target. A principal cannot approve its own request — a repeat or alias
from the same principal leaves the request pending, never self-approved. The ledger is in-memory: a
restart drops pending requests (they must be re-submitted, never auto-applied), and its capacity is
bounded so a flood of distinct pending requests evicts the oldest rather than growing without limit.

Distinctness is evaluated with the opaque principal derived from the peer's federation trust domain
after live mutual-TLS authentication. The asserted ``operator`` string remains human-readable audit
metadata only: aliases and key or certificate rotations within one domain cannot self-approve.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum

from synapse_channel.core.operator_relay_wire import RelayActionRequest

DEFAULT_APPROVAL_CAPACITY = 256
"""How many distinct relays may be pending at once before the oldest is evicted."""


class ApprovalStatus(Enum):
    """The outcome of submitting a relay to the two-person ledger."""

    RECORDED = "recorded"
    """The first request; recorded and awaiting a second, distinct verified principal."""

    AWAITING = "awaiting"
    """A repeat or alias from the first verified principal; still pending, no self-approval."""

    APPROVED = "approved"
    """A second verified principal matched a pending request; the action may now apply."""


@dataclass(frozen=True, slots=True)
class RelayApprovalKey:
    """What two relays must share to count as approving the same action.

    Attributes
    ----------
    action : str
        The relayable action id.
    namespace : str
        The namespace the action acts in.
    task_id : str
        The task the action targets (stripped, matching the applied identity).
    """

    action: str
    namespace: str
    task_id: str

    @classmethod
    def from_request(cls, request: RelayActionRequest) -> RelayApprovalKey:
        """Build the approval key a request contributes to."""
        return cls(
            action=request.action,
            namespace=request.namespace,
            task_id=request.task_id.strip(),
        )


@dataclass(frozen=True, slots=True)
class ApprovalOutcome:
    """The ledger's verdict on one submitted relay.

    Attributes
    ----------
    status : ApprovalStatus
        Whether the request was recorded, is still awaiting a distinct operator, or is approved.
    key : RelayApprovalKey
        The action/namespace/task the verdict concerns.
    requester : str
        The operator who first requested this action (the pending request's originator).
    approver : str
        On :attr:`ApprovalStatus.APPROVED`, the second, different operator who approved it;
        empty otherwise.
    requester_principal : str
        Opaque verified principal that recorded the pending request.
    approver_principal : str
        Opaque verified principal that completed the quorum; empty otherwise.
    """

    status: ApprovalStatus
    key: RelayApprovalKey
    requester: str
    approver: str = ""
    requester_principal: str = ""
    approver_principal: str = ""


@dataclass(frozen=True, slots=True)
class _Pending:
    """The recorded first request awaiting a second operator."""

    requester: str
    principal: str


class RelayApprovalLedger:
    """An in-memory quorum of two distinct verified principals per relayed action.

    Deny-by-default in spirit: a request never applies on its own submission. The first submission
    for an (action, namespace, task) is recorded pending; a submission from a *different* verified
    principal for the same target approves it and clears the pending record; a repeat or alias from
    the same principal leaves it pending. The ledger holds at most ``capacity`` pending records,
    evicting the oldest when a new distinct request would exceed it.
    """

    def __init__(self, *, capacity: int = DEFAULT_APPROVAL_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError("approval ledger capacity must be at least 1")
        self._capacity = capacity
        self._pending: OrderedDict[RelayApprovalKey, _Pending] = OrderedDict()

    @property
    def pending_count(self) -> int:
        """How many relays are currently awaiting a second operator."""
        return len(self._pending)

    def pending(self) -> list[dict[str, str]]:
        """Return the relays awaiting a second operator, oldest first.

        A JSON-serialisable view of the quorum's live state — the shape the hub's
        state snapshot carries to the dashboard, the cockpit, and the ``approvals``
        query so the enforced-but-otherwise-invisible quorum becomes operable. Each
        record names the pending action, its namespace and task, and the first
        operator who requested it — the requester a second, *different* operator
        must join to reach quorum. It holds only what the ledger holds (never a
        message body), and insertion order makes the oldest pending relay first.

        Returns
        -------
        list of dict
            One ``{"action", "namespace", "task_id", "requester"}`` record per
            pending relay, oldest first.
        """
        return [
            {
                "action": key.action,
                "namespace": key.namespace,
                "task_id": key.task_id,
                "requester": record.requester,
            }
            for key, record in self._pending.items()
        ]

    def submit(self, request: RelayActionRequest, *, principal: str) -> ApprovalOutcome:
        """Record or approve ``request`` and return the resulting verdict.

        Parameters
        ----------
        request : RelayActionRequest
            An already-authorised relay whose operator is a candidate approver.
        principal : str
            Opaque identity produced by the verified multi-hub authorisation. Blank principals
            are rejected fail-closed; asserted operator labels are never an identity boundary.

        Returns
        -------
        ApprovalOutcome
            :attr:`ApprovalStatus.RECORDED` for a new first request, :attr:`ApprovalStatus.AWAITING`
            for an alias from the same principal, or :attr:`ApprovalStatus.APPROVED` when a second,
            distinct principal completes the quorum (the pending record is then cleared).
        """
        if not principal:
            raise ValueError("verified relay principal is required")
        key = RelayApprovalKey.from_request(request)
        operator = request.operator
        existing = self._pending.get(key)
        if existing is None:
            self._record(key, operator, principal)
            return ApprovalOutcome(
                ApprovalStatus.RECORDED,
                key,
                requester=operator,
                requester_principal=principal,
            )
        if existing.principal == principal:
            return ApprovalOutcome(
                ApprovalStatus.AWAITING,
                key,
                requester=existing.requester,
                requester_principal=existing.principal,
            )
        del self._pending[key]
        return ApprovalOutcome(
            ApprovalStatus.APPROVED,
            key,
            requester=existing.requester,
            approver=operator,
            requester_principal=existing.principal,
            approver_principal=principal,
        )

    def withdraw(self, key: RelayApprovalKey) -> bool:
        """Drop a pending record without approving it. Returns whether one was removed."""
        return self._pending.pop(key, None) is not None

    def _record(self, key: RelayApprovalKey, operator: str, principal: str) -> None:
        """Store a first request pending, evicting the oldest if at capacity."""
        if len(self._pending) >= self._capacity:
            self._pending.popitem(last=False)
        self._pending[key] = _Pending(requester=operator, principal=principal)
