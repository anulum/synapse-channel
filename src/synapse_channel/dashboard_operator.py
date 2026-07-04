# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard operator write relay
"""Relay operator actions from the dashboard into the hub.

The dashboard is a read-only observer by default. When it is armed with operator
mode it may relay a small, explicit set of write actions — sending a directed or
broadcast chat message, declaring a board task, and updating a task's status or
appending a progress note.

This module holds the relay itself, deliberately thin: it opens a short-lived,
authenticated client under an explicit operator identity, sends one frame, and
reports the hub's outcome. It reimplements neither authorisation nor auditing —
the hub already ACL-checks incoming frames (:func:`authorise_frame`) and records
every accepted frame in the durable event log, so an operator send is authorised
by the hub and audited by the log, appearing in replay, ``/state-at``, and the
signal stream like any other frame. The operator never impersonates an agent: the
sender identity is a distinct operator name.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.connect_failures import closed_after_ready
from synapse_channel.core.protocol import MessageType


class WriteRateLimiter:
    """A thread-safe sliding-window limiter for operator write actions.

    The dashboard server is threaded, so the limiter guards a shared timestamp
    window under a lock. It allows at most ``max_calls`` actions within any
    ``window_seconds`` interval; a refused action does not consume a slot.

    Parameters
    ----------
    max_calls : int
        Maximum actions permitted within the window.
    window_seconds : float
        Length of the sliding window in seconds.
    """

    def __init__(self, *, max_calls: int, window_seconds: float) -> None:
        self._max_calls = max(1, int(max_calls))
        self._window_seconds = max(0.0, float(window_seconds))
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self, now: float | None = None) -> bool:
        """Return whether an action is permitted now, consuming a slot if so.

        Parameters
        ----------
        now : float or None, optional
            Monotonic timestamp to judge against; the current monotonic time when
            omitted. Injectable so tests are deterministic.

        Returns
        -------
        bool
            ``True`` when the action is within the window's budget (a slot is then
            consumed), ``False`` when the budget is exhausted (no slot consumed).
        """
        moment = time.monotonic() if now is None else now
        with self._lock:
            horizon = moment - self._window_seconds
            while self._calls and self._calls[0] <= horizon:
                self._calls.popleft()
            if len(self._calls) >= self._max_calls:
                return False
            self._calls.append(moment)
            return True


#: A relayed frame reached a live recipient.
DELIVERED = "delivered"
#: The hub accepted the frame but no live recipient matched; it is recorded in the
#: dead-letter ledger. This is fleet state, not a relay failure.
UNDELIVERED = "undelivered"
#: The hub applied a board mutation (task declared or updated) and confirmed it.
ACCEPTED = "accepted"
#: The hub's ACL refused the frame; it was not routed.
DENIED = "denied"
#: The hub's blackboard refused the write on its own terms (unknown id, invalid
#: title, dependency cycle, unknown status). Authorised, but not applied.
REJECTED = "rejected"
#: The hub could not be reached, closed the connection, or returned no outcome in
#: time.
UNREACHABLE = "unreachable"

AgentFactory = Callable[..., SynapseAgent]

#: A sync resolver polled after sending: it returns a settled outcome, or ``None``
#: to keep waiting until the response bound expires.
OutcomeResolver = Callable[[], "RelayOutcome | None"]


def _task_confirms(message: dict[str, Any], task_id: str) -> bool:
    """Return whether a broadcast carries the confirmed board task ``task_id``."""
    task = message.get("task")
    return isinstance(task, dict) and task.get("task_id") == task_id


def _note_confirms(message: dict[str, Any], task_id: str, author: str) -> bool:
    """Return whether a broadcast carries this operator's progress note for the task."""
    note = message.get("note")
    return (
        isinstance(note, dict) and note.get("task_id") == task_id and note.get("author") == author
    )


@dataclass(frozen=True)
class RelayOutcome:
    """The outcome of relaying one operator action to the hub.

    Attributes
    ----------
    status : str
        One of :data:`DELIVERED`, :data:`UNDELIVERED`, :data:`ACCEPTED`,
        :data:`DENIED`, :data:`REJECTED`, or :data:`UNREACHABLE`.
    detail : str
        A short human-readable explanation, safe to surface to the operator.
    confirm : dict
        The hub message that decided the outcome (delivery receipt, board
        confirmation, or error), empty when none arrived.
    """

    status: str
    detail: str
    confirm: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Return whether the hub accepted the frame (delivered, dead-lettered, or applied)."""
        return self.status in (DELIVERED, UNDELIVERED, ACCEPTED)


class OperatorRelay:
    """Relay operator write actions to the hub over a short-lived client.

    One relay corresponds to one dashboard. Each action opens a fresh client under
    the operator identity, sends a single frame, awaits the hub's decision within
    the response bound, and tears the client down. Nothing is held open between
    actions, so the relay adds no persistent socket to the roster.

    Parameters
    ----------
    uri : str
        Hub URI to relay through.
    operator_name : str
        Sender identity for relayed frames. Distinct from any agent so operator
        actions are attributed, never spoofed.
    token : str or None, optional
        Shared-secret token for a secured hub.
    ready_timeout : float, optional
        Seconds to await hub connection readiness.
    response_timeout : float, optional
        Seconds to await the hub's outcome (delivery receipt or ACL error) after
        sending.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable so tests need no live hub.
    """

    def __init__(
        self,
        *,
        uri: str,
        operator_name: str,
        token: str | None = None,
        ready_timeout: float = 5.0,
        response_timeout: float = 2.0,
        agent_factory: AgentFactory = SynapseAgent,
    ) -> None:
        self.uri = uri
        self.operator_name = operator_name
        self.token = token
        self.ready_timeout = ready_timeout
        self.response_timeout = response_timeout
        self.agent_factory = agent_factory

    async def relay_message(self, to: str, text: str) -> RelayOutcome:
        """Relay one chat message to ``to`` and report the hub's outcome.

        Parameters
        ----------
        to : str
            Recipient identity, group, or ``all`` for a broadcast.
        text : str
            Message body.

        Returns
        -------
        RelayOutcome
            :data:`DELIVERED` when a live recipient received it, :data:`UNDELIVERED`
            when the hub accepted it but no recipient was online (dead-lettered),
            :data:`DENIED` when the ACL refused it, or :data:`UNREACHABLE` when the
            hub could not be reached or gave no outcome in time.
        """
        errors: list[dict[str, Any]] = []
        receipts: list[dict[str, Any]] = []

        async def collect(data: dict[str, Any]) -> None:
            message_type = data.get("type")
            if message_type == MessageType.ERROR and (
                data.get("acl_reason") or data.get("acl_decision")
            ):
                errors.append(data)
            elif message_type == MessageType.DELIVERY_RECEIPT and (
                data.get("target") == self.operator_name
            ):
                receipts.append(data)

        async def send(agent: SynapseAgent) -> None:
            await agent.send_message(
                MessageType.CHAT,
                target=to,
                payload=text,
                receipt_requested=True,
            )

        def resolve() -> RelayOutcome | None:
            if errors:
                return self._denial(errors[0])
            if receipts:
                receipt = receipts[0]
                if bool(receipt.get("delivered")):
                    return RelayOutcome(DELIVERED, "delivered to a live recipient", receipt)
                return RelayOutcome(
                    UNDELIVERED, "accepted; no live recipient (dead-lettered)", receipt
                )
            return None

        return await self._run(
            collect, send, resolve, timeout_detail="hub returned no delivery outcome in time"
        )

    async def relay_task(
        self, task_id: str, title: str, *, depends_on: tuple[str, ...] | list[str] = ()
    ) -> RelayOutcome:
        """Declare a board task on behalf of the operator and report the outcome.

        Parameters
        ----------
        task_id : str
            Identifier of the task to declare or re-declare.
        title : str
            Human title for the task.
        depends_on : tuple of str or list of str, optional
            Task ids this task depends on; the hub rejects a dependency cycle.

        Returns
        -------
        RelayOutcome
            :data:`ACCEPTED` when the hub declared the task and broadcast the
            confirmation, :data:`DENIED` when the ACL refused it, :data:`REJECTED`
            when the blackboard refused it (unknown id, empty title, cycle), or
            :data:`UNREACHABLE` when the hub could not be reached or gave no
            outcome in time.
        """
        task = task_id.strip()
        posted: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        async def collect(data: dict[str, Any]) -> None:
            message_type = data.get("type")
            if message_type == MessageType.LEDGER_TASK_POSTED and _task_confirms(data, task):
                posted.append(data)
            elif message_type == MessageType.ERROR and data.get("target") == self.operator_name:
                errors.append(data)

        async def send(agent: SynapseAgent) -> None:
            await agent.post_task(task, title, depends_on=tuple(depends_on))

        def resolve() -> RelayOutcome | None:
            if errors:
                return self._denial(errors[0])
            if posted:
                return RelayOutcome(ACCEPTED, f"task '{task}' declared on the board", posted[0])
            return None

        return await self._run(
            collect, send, resolve, timeout_detail="hub confirmed no task declaration in time"
        )

    async def relay_task_update(
        self, task_id: str, *, status: str | None = None, note: str | None = None
    ) -> RelayOutcome:
        """Update a board task's status and/or append a progress note.

        At least one of ``status`` or ``note`` must be supplied; both are sent when
        both are present, and the outcome settles only once every requested change
        is confirmed by the hub.

        Parameters
        ----------
        task_id : str
            Identifier of the task to update.
        status : str or None, optional
            New planning status (for example ``done`` or ``blocked``).
        note : str or None, optional
            Progress note text appended to the task's ledger.

        Returns
        -------
        RelayOutcome
            :data:`ACCEPTED` once every requested change is confirmed,
            :data:`DENIED` on ACL refusal, :data:`REJECTED` when the blackboard
            refuses the change (unknown id or status), or :data:`UNREACHABLE` when
            the hub could not be reached or gave no outcome in time.
        """
        task = task_id.strip()
        want_status = status is not None
        want_note = note is not None
        updated: list[dict[str, Any]] = []
        progressed: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        async def collect(data: dict[str, Any]) -> None:
            message_type = data.get("type")
            if message_type == MessageType.LEDGER_TASK_UPDATED and _task_confirms(data, task):
                updated.append(data)
            elif message_type == MessageType.LEDGER_PROGRESS_POSTED and _note_confirms(
                data, task, self.operator_name
            ):
                progressed.append(data)
            elif message_type == MessageType.ERROR and data.get("target") == self.operator_name:
                errors.append(data)

        async def send(agent: SynapseAgent) -> None:
            if want_status:
                await agent.update_ledger_task(task, status=status)
            if want_note:
                await agent.post_progress(task, note or "")

        def resolve() -> RelayOutcome | None:
            if errors:
                return self._denial(errors[0])
            if (not want_status or updated) and (not want_note or progressed):
                confirm = updated[0] if updated else progressed[0]
                return RelayOutcome(ACCEPTED, f"task '{task}' update applied on the board", confirm)
            return None

        return await self._run(
            collect, send, resolve, timeout_detail="hub confirmed no task update in time"
        )

    @staticmethod
    def _denial(error: dict[str, Any]) -> RelayOutcome:
        """Classify a hub error as an ACL denial or a blackboard rejection."""
        if error.get("acl_reason") or error.get("acl_decision"):
            reason = str(error.get("acl_reason") or error.get("payload") or "access denied")
            return RelayOutcome(DENIED, reason, error)
        reason = str(error.get("payload") or error.get("text") or "the hub refused the write")
        return RelayOutcome(REJECTED, reason, error)

    async def _run(
        self,
        collect: Callable[[dict[str, Any]], Awaitable[None]],
        send: Callable[[SynapseAgent], Awaitable[None]],
        resolve: OutcomeResolver,
        *,
        timeout_detail: str,
    ) -> RelayOutcome:
        """Open the operator client, send one frame, and resolve the outcome.

        The client is always torn down, even when readiness, the send, or the wait
        raises, so a relay never leaks a socket into the roster.
        """
        agent = self.agent_factory(
            self.operator_name, collect, uri=self.uri, verbose=False, token=self.token
        )
        conn_task = asyncio.create_task(agent.connect())
        try:
            if not await agent.wait_until_ready(timeout=self.ready_timeout):
                return RelayOutcome(UNREACHABLE, f"could not reach hub at {self.uri}")
            if await closed_after_ready(agent):
                reason = agent.last_close_reason or "connection closed after ready"
                return RelayOutcome(UNREACHABLE, str(reason))
            await send(agent)
            return await self._await_outcome(resolve, timeout_detail)
        finally:
            agent.running = False
            conn_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await conn_task

    async def _await_outcome(self, resolve: OutcomeResolver, timeout_detail: str) -> RelayOutcome:
        """Poll the resolver for a settled outcome within the response bound."""
        deadline = time.monotonic() + max(0.0, self.response_timeout)
        while time.monotonic() < deadline:
            outcome = resolve()
            if outcome is not None:
                return outcome
            await asyncio.sleep(0.02)
        return RelayOutcome(UNREACHABLE, timeout_detail)
