# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real-hub prevention test for the socket-up but consume-dead incident
"""Prove stale directed delivery and pin recovery across real WebSockets."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel.cli_identity import _identity_reclaim
from synapse_channel.cli_messaging_send import _send
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.acl import PIN_RECLAIM, AclPolicy, AclRule
from synapse_channel.core.handlers.identity_pins import PIN_RECLAIM_CLOSE_CODE
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.machine_identity import machine_identity_agent_kwargs

OPERATOR = "OPS/operator"
PIN_NAME = "PROJ/wedged"


class SteppingClock:
    """Monotonic hub clock advanced across reaction and recovery windows."""

    def __init__(self) -> None:
        self.now = 5_000.0

    def __call__(self) -> float:
        return self.now


def _machine(tmp_path: Path, label: str) -> dict[str, Any]:
    """Return isolated explicit machine-identity arguments for one client."""
    kwargs = machine_identity_agent_kwargs(base=tmp_path / label)
    assert kwargs
    return kwargs


def _policy() -> AclPolicy:
    """Grant the test operator exact pin-reclaim authority for the wedged target."""
    return AclPolicy(
        [
            AclRule(
                PIN_RECLAIM,
                "agent",
                PIN_NAME,
                "OPS",
                "designated stale-pin operator",
            )
        ]
    )


async def _start(agent: SynapseAgent) -> asyncio.Task[None]:
    """Start one real client and require a completed welcome handshake."""
    task = asyncio.create_task(agent.connect())
    assert await agent.wait_until_ready(timeout=3.0)
    return task


async def _stop(agent: SynapseAgent, task: asyncio.Task[None]) -> None:
    """Stop one client task without leaking it into a later test phase."""
    agent.running = False
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _await_offline(hub: SynapseHub, name: str, *, timeout: float = 3.0) -> None:
    """Wait until ``name`` leaves the live client registry."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if name not in hub.clients.agent_sockets:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(f"{name} stayed online")


async def _await_online(hub: SynapseHub, name: str, *, timeout: float = 3.0) -> None:
    """Wait until ``name`` enters the live client registry."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if name in hub.clients.agent_sockets:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(f"{name} stayed offline")


async def _await_reaction(hub: SynapseHub, name: str, *, timeout: float = 3.0) -> float:
    """Return ``name``'s first recorded reaction after registration."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        reaction = hub._recipient_liveness.last_reaction_at(name)
        if reaction is not None:
            return reaction
        await asyncio.sleep(0.01)
    raise TimeoutError(f"{name} never seeded consume liveness")


async def test_socket_keepalives_cannot_hide_a_dead_letter_or_pin_forever(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A heartbeat-only pinned agent fails delivery and becomes reclaimable on TTL."""
    clock = SteppingClock()
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(
        hub_id="syn-consume-liveness",
        clock=clock,
        recipient_liveness_window=5.0,
        lease_offline_ttl=10.0,
        identity_pin_path=tmp_path / "pins.json",
        journal=store,
        acl_policy=_policy(),
        require_acl=False,
        private_directed_messages=True,
    )
    target_key = _machine(tmp_path, "target")
    operator_key = _machine(tmp_path, "operator")
    reporter_key = _machine(tmp_path, "reporter")

    def operator_factory(name: str, callback: Any, **kwargs: Any) -> SynapseAgent:
        kwargs.update(operator_key)
        return SynapseAgent(name, callback, **kwargs)

    def reporter_factory(name: str, callback: Any, **kwargs: Any) -> SynapseAgent:
        kwargs.update(reporter_key)
        return SynapseAgent(name, callback, **kwargs)

    async with running_hub(hub) as (_, uri):
        target = SynapseAgent(
            PIN_NAME,
            None,
            uri=uri,
            verbose=False,
            request_lease=True,
            heartbeat_interval=3_600.0,
            **target_key,
        )
        target_task = await _start(target)
        await _await_online(hub, PIN_NAME)
        assert await _await_reaction(hub, PIN_NAME) == 5_000.0
        pin = hub._identity_pins.pinned(PIN_NAME)
        assert pin is not None

        clock.now = 5_006.0
        await target.send_message(
            MessageType.HEARTBEAT,
            target="System",
            payload="alive",
        )
        assert hub._recipient_liveness.last_reaction_at(PIN_NAME) == 5_000.0

        code = await _send(
            uri=uri,
            name="REPORTER",
            target=PIN_NAME,
            message="respond if alive",
            wait_seconds=0.0,
            receipt_timeout=0.5,
            agent_factory=reporter_factory,
        )
        assert code == 1
        assert "no live recipient matched" in capsys.readouterr().out
        assert hub.dead_letters.snapshot()[0]["target"] == PIN_NAME

        # Another transport keepalive proves the socket remains up, but must neither
        # refresh consume liveness nor erase the blackhole signal.
        await target.send_message(
            MessageType.HEARTBEAT,
            target="System",
            payload="alive",
        )
        assert hub.dead_letters.snapshot()[0]["target"] == PIN_NAME

        refused = await _identity_reclaim(
            uri=uri,
            operator=OPERATOR,
            pin_name=PIN_NAME,
            expected_key_id=pin.key_id,
            reason="heartbeat-only holder does not consume directed work",
            break_glass=False,
            token=None,
            ready_timeout=3.0,
            result_timeout=3.0,
            json_output=False,
            agent_factory=operator_factory,
        )
        assert refused == 1
        assert "online" in capsys.readouterr().out
        assert target.last_close_code is None

        clock.now = 5_015.0
        await target.send_message(
            MessageType.HEARTBEAT,
            target="System",
            payload="alive",
        )
        applied = await _identity_reclaim(
            uri=uri,
            operator=OPERATOR,
            pin_name=PIN_NAME,
            expected_key_id=pin.key_id,
            reason="heartbeat-only holder exceeded the governed recovery TTL",
            break_glass=False,
            token=None,
            ready_timeout=3.0,
            result_timeout=3.0,
            json_output=False,
            agent_factory=operator_factory,
        )
        assert applied == 0
        await _await_offline(hub, PIN_NAME)
        assert target.last_close_code == PIN_RECLAIM_CLOSE_CODE
        assert hub._identity_pins.pinned(PIN_NAME) is None

        replacement = SynapseAgent(
            PIN_NAME,
            None,
            uri=uri,
            verbose=False,
            heartbeat_interval=3_600.0,
            **_machine(tmp_path, "replacement"),
        )
        replacement_task = await _start(replacement)
        await _await_online(hub, PIN_NAME)
        replacement_pin = hub._identity_pins.pinned(PIN_NAME)
        assert replacement_pin is not None and replacement_pin.key_id != pin.key_id
        await _stop(replacement, replacement_task)
        await _stop(target, target_task)

    immediate = [
        event for event in store.read_all() if event.kind == EventKind.DELIVERY_RECEIPT_IMMEDIATE
    ]
    audits = [event for event in store.read_all() if event.kind == EventKind.IDENTITY_PIN_RECLAIM]
    store.close()
    assert immediate[-1].payload["reason"] == "no_live_recipient"
    assert immediate[-1].payload["matched_recipients"] == [PIN_NAME]
    assert immediate[-1].payload["stale_recipients"] == [PIN_NAME]
    assert immediate[-1].payload["dead_lettered"] is True
    assert [event.payload["status"] for event in audits] == ["approved", "applied"]
    assert audits[-1].payload["break_glass"] is False
    assert audits[-1].payload["stale_owner_reclaimable"] is True
    assert audits[-1].payload["evicted_live_socket"] is True
