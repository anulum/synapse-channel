# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard operator relay tests
"""Tests for the dashboard operator write relay and its rate limiter."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast

import pytest

from synapse_channel.core.protocol import MessageType
from synapse_channel.dashboard_operator import (
    DELIVERED,
    DENIED,
    UNDELIVERED,
    UNREACHABLE,
    AgentFactory,
    OperatorRelay,
    WriteRateLimiter,
)

OnSend = Callable[["_FakeAgent", str, str], Awaitable[None]]


class _FakeAgent:
    """A stand-in client that drives the relay's collect callback on send."""

    def __init__(
        self,
        name: str,
        collect: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        uri: str,
        verbose: bool = False,
        token: str | None = None,
        ready: bool,
        closed: bool,
        on_send: OnSend | None,
    ) -> None:
        self.name = name
        self.collect = collect
        self.uri = uri
        self.token = token
        self.running = True
        self.last_close_code: int | None = 4009 if closed else None
        self.last_close_reason: str | None = "name conflict" if closed else None
        self._ready = ready
        self._on_send = on_send
        self.sent: list[tuple[str, str, str, dict[str, Any]]] = []

    async def connect(self) -> None:
        while True:
            await asyncio.sleep(0.02)

    async def wait_until_ready(self, timeout: float) -> bool:
        return self._ready

    async def send_message(
        self, message_type: str, *, target: str, payload: str, **extra: Any
    ) -> None:
        self.sent.append((message_type, target, payload, extra))
        if self._on_send is not None:
            await self._on_send(self, target, payload)


def _factory(
    *, ready: bool = True, closed: bool = False, on_send: OnSend | None = None
) -> Callable[..., _FakeAgent]:
    """Build an agent factory yielding a configured :class:`_FakeAgent`."""

    holder: dict[str, _FakeAgent] = {}

    def make(
        name: str,
        collect: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        uri: str,
        verbose: bool = False,
        token: str | None = None,
    ) -> _FakeAgent:
        agent = _FakeAgent(
            name,
            collect,
            uri=uri,
            verbose=verbose,
            token=token,
            ready=ready,
            closed=closed,
            on_send=on_send,
        )
        holder["agent"] = agent
        return agent

    make.holder = holder  # type: ignore[attr-defined]
    return make


async def _deliver(agent: _FakeAgent, target: str, payload: str) -> None:
    await agent.collect(
        {"type": MessageType.DELIVERY_RECEIPT, "target": agent.name, "delivered": True}
    )


async def _dead_letter(agent: _FakeAgent, target: str, payload: str) -> None:
    await agent.collect(
        {"type": MessageType.DELIVERY_RECEIPT, "target": agent.name, "delivered": False}
    )


async def _deny(agent: _FakeAgent, target: str, payload: str) -> None:
    await agent.collect(
        {
            "type": MessageType.ERROR,
            "target": agent.name,
            "acl_reason": "no chat rule for team-b",
            "acl_decision": "deny",
        }
    )


def _relay(factory: Callable[..., _FakeAgent]) -> OperatorRelay:
    return OperatorRelay(
        uri="ws://hub.test",
        operator_name="operator:DASH",
        ready_timeout=0.5,
        response_timeout=0.5,
        agent_factory=cast(AgentFactory, factory),
    )


def test_relay_message_reports_delivered() -> None:
    factory = _factory(on_send=_deliver)
    outcome = asyncio.run(_relay(factory).relay_message("SC-NEUROCORE", "ship it"))

    assert outcome.status == DELIVERED
    assert outcome.ok is True
    agent = factory.holder["agent"]  # type: ignore[attr-defined]
    assert agent.sent == [
        (MessageType.CHAT, "SC-NEUROCORE", "ship it", {"receipt_requested": True})
    ]
    assert agent.running is False  # torn down


def test_relay_message_reports_undelivered_when_no_recipient() -> None:
    outcome = asyncio.run(_relay(_factory(on_send=_dead_letter)).relay_message("ghost", "hi"))

    assert outcome.status == UNDELIVERED
    assert outcome.ok is True
    assert "dead-lettered" in outcome.detail


def test_relay_message_reports_denied_on_acl_error() -> None:
    outcome = asyncio.run(_relay(_factory(on_send=_deny)).relay_message("team-b", "hi"))

    assert outcome.status == DENIED
    assert outcome.ok is False
    assert "team-b" in outcome.detail


def test_relay_message_unreachable_when_not_ready() -> None:
    outcome = asyncio.run(_relay(_factory(ready=False)).relay_message("x", "hi"))

    assert outcome.status == UNREACHABLE
    assert outcome.ok is False


def test_relay_message_unreachable_when_closed_after_ready() -> None:
    outcome = asyncio.run(_relay(_factory(closed=True)).relay_message("x", "hi"))

    assert outcome.status == UNREACHABLE
    assert "name conflict" in outcome.detail


def test_relay_message_unreachable_on_no_outcome_in_time() -> None:
    # on_send=None → the hub never answers → the bounded wait expires.
    outcome = asyncio.run(_relay(_factory(on_send=None)).relay_message("x", "hi"))

    assert outcome.status == UNREACHABLE
    assert "no delivery outcome" in outcome.detail


def test_rate_limiter_allows_up_to_the_budget_then_refuses() -> None:
    limiter = WriteRateLimiter(max_calls=3, window_seconds=60.0)

    assert [limiter.allow(now=0.0) for _ in range(3)] == [True, True, True]
    assert limiter.allow(now=1.0) is False


def test_rate_limiter_frees_slots_after_the_window() -> None:
    limiter = WriteRateLimiter(max_calls=2, window_seconds=10.0)

    assert limiter.allow(now=0.0) is True
    assert limiter.allow(now=1.0) is True
    assert limiter.allow(now=2.0) is False
    # The first two fall outside a 10s window measured from t=12.
    assert limiter.allow(now=12.0) is True


def test_rate_limiter_refused_call_consumes_no_slot() -> None:
    limiter = WriteRateLimiter(max_calls=1, window_seconds=100.0)

    assert limiter.allow(now=0.0) is True
    assert limiter.allow(now=1.0) is False
    assert limiter.allow(now=2.0) is False
    # After the window clears, exactly one slot is available again.
    assert limiter.allow(now=101.0) is True


@pytest.mark.parametrize("max_calls", [0, -5])
def test_rate_limiter_floors_max_calls_at_one(max_calls: int) -> None:
    limiter = WriteRateLimiter(max_calls=max_calls, window_seconds=10.0)

    assert limiter.allow(now=0.0) is True
    assert limiter.allow(now=0.0) is False
