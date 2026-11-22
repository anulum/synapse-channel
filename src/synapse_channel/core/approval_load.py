# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the live approval-request rate window and its journalled recount
"""Count admitted approval requests per approver and per role, and recount after a restart.

:class:`ApprovalLoad` is the hub's live state for an approval-load policy
(:mod:`synapse_channel.core.approval_load_policy`). For each limit a request
counts against it keeps the admission times inside that limit's window. A
request is checked and reserved in one step, before the hub appends it, so two
requests arriving together cannot both take the last place in a window; a
request the hub then does not append (refused, replayed, conflicting, or lost
to a journal failure) gives its place back.

Which limits a request counts against:

- one addressed to identity ``X`` counts against ``X``'s own rule, when the
  policy has one, and against the rule of every role ``X`` may claim under the
  hub's role grants;
- one addressed to role ``R`` counts against ``R``'s rule.

So spreading requests across a role's members cannot evade the role's rate.

The window survives a restart. Each admitted request is journalled, in the same
transaction as its note, with the limits it counted against
(``approval_load_admitted``), and at the first request after a start the window
is recounted from those rows. The recount
reads only the rows after the first one inside the longest window, found by a
binary search over the journal's sequence numbers, never by scanning the log.
The search assumes the hub's clock moved forward; a slack of
:data:`RECOUNT_SLACK_SECONDS` absorbs small steps, and a larger backward step can
leave rows before it uncounted, which can only admit more requests, never
refuse one wrongly.

The rate limit is a load control. It is not a presence check, and it never
refuses a decision: ``approved`` and ``rejected`` notes are outside it.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from synapse_channel.core.approvals import ApprovalAddressing
from synapse_channel.core.journal import EventKind, record_approval_load_policy

if TYPE_CHECKING:
    from synapse_channel.core.approval_load_policy import ApprovalLoadPolicy, ApprovalLoadRule
    from synapse_channel.core.persistence import EventStore, StoredEvent
    from synapse_channel.core.role_grants import RoleGrants

RECOUNT_SLACK_SECONDS = 300.0
"""Extra span read before the longest window at a recount, against small clock steps."""

LIMIT_APPROVER = "approver"
"""Limit kind of a rule keyed by one approver identity."""

LIMIT_ROLE = "role"
"""Limit kind of a rule keyed by an approver role."""

LimitKey = tuple[str, str]
"""A limit: its kind (:data:`LIMIT_APPROVER` or :data:`LIMIT_ROLE`) and its address."""


@dataclass(frozen=True)
class ApprovalLoadReservation:
    """One request's place in every window it counts against.

    Attributes
    ----------
    keys : tuple[LimitKey, ...]
        The limits the request counts against, in a stable order.
    at : float
        Admission time recorded in each of those windows.
    min_review_seconds : float
        Strictest review-time threshold among the matched rules.
    max_batch : int
        Strictest batch-size threshold among the matched rules.
    """

    keys: tuple[LimitKey, ...]
    at: float
    min_review_seconds: float
    max_batch: int


@dataclass(eq=False)
class ApprovalLoad:
    """The live rate windows for one hub's approval-load policy.

    It compares and hashes by identity, because it is one hub's mutable state
    carried inside the hub's frozen configuration.

    Parameters
    ----------
    policy : ApprovalLoadPolicy
        The validated, declared policy.
    clock : Callable[[], float], optional
        Wall clock in Unix seconds; the hub's own by default.
    """

    policy: ApprovalLoadPolicy
    clock: Callable[[], float] = time.time
    _windows: dict[LimitKey, deque[float]] = field(default_factory=dict, init=False, repr=False)
    _started: bool = field(default=False, init=False, repr=False)

    def matched_rules(
        self, addressing: ApprovalAddressing, role_grants: RoleGrants | None
    ) -> tuple[tuple[LimitKey, ApprovalLoadRule], ...]:
        """Return each limit the request counts against, with its rule, in a stable order."""
        matched: dict[LimitKey, ApprovalLoadRule] = {}
        approver = addressing.approver
        if approver:
            own = self.policy.approvers.get(approver)
            if own is not None:
                matched[(LIMIT_APPROVER, approver)] = own
            if role_grants is not None:
                for role in sorted(self.policy.roles):
                    if role_grants.may_claim(approver, role):
                        matched[(LIMIT_ROLE, role)] = self.policy.roles[role]
        role_rule = self.policy.roles.get(addressing.approver_role)
        if role_rule is not None:
            matched[(LIMIT_ROLE, addressing.approver_role)] = role_rule
        return tuple(sorted(matched.items()))

    def start(self, journal: EventStore) -> None:
        """Journal the policy and recount the windows, once per hub lifetime.

        Called at the first approval request after a start, before that
        request is counted, so nothing counted in this lifetime is counted twice.
        """
        if self._started:
            return
        record_approval_load_policy(
            journal, {"policy_sha256": self.policy.sha256, "policy": self.policy.to_json_obj()}
        )
        now = self.clock()
        floor = now - self.policy.longest_window_seconds - RECOUNT_SLACK_SECONDS
        first = _first_seq_at_or_after(journal, floor)
        if first is not None:
            for event in journal.iter_events(
                after_seq=first - 1, kinds=(EventKind.APPROVAL_LOAD_ADMITTED,)
            ):
                self._recount(event, now)
        # Marked only once the policy row is written and the window recounted, so a
        # journal failure leaves the next request to start over rather than count
        # against an empty window.
        self._started = True

    def reserve(
        self, matched: Iterable[tuple[LimitKey, ApprovalLoadRule]]
    ) -> tuple[ApprovalLoadReservation | None, str | None]:
        """Take one place in every matched window, or name the first limit that is full.

        Returns
        -------
        tuple[ApprovalLoadReservation | None, str | None]
            The reservation and ``None``, or ``None`` and the refusal reason.
        """
        rules = tuple(matched)
        if not rules:
            return None, None
        now = self.clock()
        for key, rule in rules:
            window = self._window(key, rule, now)
            if len(window) >= rule.request_rate:
                kind, address = key
                return None, (
                    f"approval_rate_exceeded: {kind} {address} allows {rule.request_rate} "
                    f"requests per {rule.window_seconds:g} s"
                )
        for key, _ in rules:
            self._windows[key].append(now)
        return (
            ApprovalLoadReservation(
                keys=tuple(key for key, _ in rules),
                at=now,
                min_review_seconds=max(rule.min_review_seconds for _, rule in rules),
                max_batch=min(rule.max_batch for _, rule in rules),
            ),
            None,
        )

    def release(self, reservation: ApprovalLoadReservation) -> None:
        """Give back a reservation whose request the hub did not append."""
        for key in reservation.keys:
            window = self._windows.get(key)
            if window is not None and reservation.at in window:
                window.remove(reservation.at)

    def admission_row(
        self,
        reservation: ApprovalLoadReservation,
        *,
        author: str,
        subject: str,
        text_sha256: str,
        addressing: ApprovalAddressing,
    ) -> dict[str, object]:
        """Return the ``approval_load_admitted`` row journalled with the request note.

        The handler writes it in the same transaction as the note, so a restart
        recounts exactly the requests that were appended.
        """
        return {
            "author": author,
            "subject": subject,
            "text_sha256": text_sha256,
            "limits": [[kind, address] for kind, address in reservation.keys],
            "min_review_seconds": reservation.min_review_seconds,
            "max_batch": reservation.max_batch,
            "policy_sha256": self.policy.sha256,
            **addressing.present(),
        }

    def _window(self, key: LimitKey, rule: ApprovalLoadRule, now: float) -> deque[float]:
        """Return ``key``'s window with every admission older than the rule's window dropped."""
        window = self._windows.setdefault(key, deque())
        while window and window[0] <= now - rule.window_seconds:
            window.popleft()
        return window

    def _recount(self, event: StoredEvent, now: float) -> None:
        """Put one journalled admission back into the windows it counted against."""
        limits = event.payload.get("limits")
        if not isinstance(limits, list):
            return
        for limit in limits:
            if not (isinstance(limit, list) and len(limit) == 2):
                continue
            key: LimitKey = (str(limit[0]), str(limit[1]))
            rule = self._rule_for(key)
            if rule is not None and event.ts > now - rule.window_seconds:
                self._windows.setdefault(key, deque()).append(event.ts)

    def _rule_for(self, key: LimitKey) -> ApprovalLoadRule | None:
        """Return the current rule for a journalled limit; a dropped rule recounts nothing."""
        kind, address = key
        rules = self.policy.approvers if kind == LIMIT_APPROVER else self.policy.roles
        return rules.get(address)


def _first_seq_at_or_after(journal: EventStore, floor_ts: float) -> int | None:
    """Return the first sequence whose row is at or after ``floor_ts``, by binary search.

    Each probe reads one row through the sequence key, so the cost grows with
    the logarithm of the log's length.
    """
    low, high = 1, journal.max_seq()
    found: int | None = None
    while low <= high:
        middle = (low + high) // 2
        row = _row_at_or_after(journal, middle)
        if row is not None and row.ts < floor_ts:
            low = row.seq + 1
        else:
            found = found if row is None else row.seq
            high = middle - 1
    return found


def _row_at_or_after(journal: EventStore, seq: int) -> StoredEvent | None:
    """Return the first stored row at or after ``seq``, read as one row through the sequence key."""
    rows = journal.read_since(seq - 1, limit=1)
    return rows[0] if rows else None
