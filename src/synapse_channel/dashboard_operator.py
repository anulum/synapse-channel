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
broadcast chat message, and (in later slices) declaring or updating a board task.

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
#: The hub's ACL refused the frame; it was not routed.
DENIED = "denied"
#: The hub could not be reached, closed the connection, or returned no outcome in
#: time.
UNREACHABLE = "unreachable"

AgentFactory = Callable[..., SynapseAgent]


@dataclass(frozen=True)
class RelayOutcome:
    """The outcome of relaying one operator action to the hub.

    Attributes
    ----------
    status : str
        One of :data:`DELIVERED`, :data:`UNDELIVERED`, :data:`DENIED`, or
        :data:`UNREACHABLE`.
    detail : str
        A short human-readable explanation, safe to surface to the operator.
    confirm : dict
        The hub message that decided the outcome (delivery receipt or ACL error),
        empty when none arrived.
    """

    status: str
    detail: str
    confirm: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Return whether the hub accepted the frame (delivered or dead-lettered)."""
        return self.status in (DELIVERED, UNDELIVERED)


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

        return await self._run(collect, send, errors, receipts)

    async def _run(
        self,
        collect: Callable[[dict[str, Any]], Awaitable[None]],
        send: Callable[[SynapseAgent], Awaitable[None]],
        errors: list[dict[str, Any]],
        receipts: list[dict[str, Any]],
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
            return await self._await_outcome(errors, receipts)
        finally:
            agent.running = False
            conn_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await conn_task

    async def _await_outcome(
        self, errors: list[dict[str, Any]], receipts: list[dict[str, Any]]
    ) -> RelayOutcome:
        """Poll for the first ACL error or delivery receipt within the bound."""
        deadline = time.monotonic() + max(0.0, self.response_timeout)
        while time.monotonic() < deadline:
            if errors:
                error = errors[0]
                reason = str(error.get("acl_reason") or error.get("payload") or "access denied")
                return RelayOutcome(DENIED, reason, error)
            if receipts:
                receipt = receipts[0]
                if bool(receipt.get("delivered")):
                    return RelayOutcome(DELIVERED, "delivered to a live recipient", receipt)
                return RelayOutcome(
                    UNDELIVERED, "accepted; no live recipient (dead-lettered)", receipt
                )
            await asyncio.sleep(0.02)
        return RelayOutcome(UNREACHABLE, "hub returned no delivery outcome in time")
