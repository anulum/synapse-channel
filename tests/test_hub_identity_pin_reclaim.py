# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — governed identity-pin reclaim over real hub sockets
"""The stale-pin recovery contract, exercised end to end on real websockets."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel.cli_identity import _identity_reclaim
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
    """Monotonic hub clock advanced explicitly across the offline lease TTL."""

    def __init__(self) -> None:
        self.now = 5_000.0

    def __call__(self) -> float:
        return self.now


def _machine(tmp_path: Path, label: str) -> dict[str, Any]:
    """Return explicit client kwargs for one isolated machine identity."""
    kwargs = machine_identity_agent_kwargs(base=tmp_path / label)
    assert kwargs
    return kwargs


def _policy() -> AclPolicy:
    """Grant only ``OPS/operator`` permission to reclaim the one test name."""
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
    """Start ``agent`` and require a completed hub welcome."""
    task = asyncio.create_task(agent.connect())
    assert await agent.wait_until_ready(timeout=3.0)
    return task


async def _stop(agent: SynapseAgent, task: asyncio.Task[None]) -> None:
    """Stop a client task without leaking it into the next real-hub phase."""
    agent.running = False
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _await_registry(
    hub: SynapseHub, name: str, *, online: bool, timeout: float = 3.0
) -> None:
    """Wait until ``name`` has the requested live-registry state."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if (name in hub.clients.agent_sockets) is online:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(f"{name} did not become {'online' if online else 'offline'}")


async def _await_result(
    replies: list[dict[str, Any]], previous: int, *, timeout: float = 3.0
) -> dict[str, Any]:
    """Return the next typed reclaim result appended after ``previous``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        typed = [
            frame
            for frame in replies[previous:]
            if frame.get("type") == MessageType.IDENTITY_PIN_RECLAIM_RESULT
        ]
        if typed:
            return typed[-1]
        await asyncio.sleep(0.01)
    raise TimeoutError("hub returned no identity-pin reclaim result")


async def test_normal_reclaim_waits_for_the_ownership_ttl_then_allows_repin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The safe path cannot skip a live lease and leaves a two-phase durable audit."""
    clock = SteppingClock()
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(
        hub_id="syn-pin-reclaim",
        clock=clock,
        lease_offline_ttl=10.0,
        identity_pin_path=tmp_path / "pins.json",
        journal=store,
        acl_policy=_policy(),
        require_acl=True,
    )
    target_key = _machine(tmp_path, "target")
    operator_key = _machine(tmp_path, "operator")

    def operator_factory(name: str, callback: Any, **kwargs: Any) -> SynapseAgent:
        kwargs.update(operator_key)
        return SynapseAgent(name, callback, **kwargs)

    async with running_hub(hub) as (_, uri):
        target = SynapseAgent(
            PIN_NAME,
            None,
            uri=uri,
            verbose=False,
            request_lease=True,
            **target_key,
        )
        target_task = await _start(target)
        await _await_registry(hub, PIN_NAME, online=True)
        pin = hub._identity_pins.pinned(PIN_NAME)
        assert pin is not None
        expected_key_id = pin.key_id
        await _stop(target, target_task)
        await _await_registry(hub, PIN_NAME, online=False)

        refused = await _identity_reclaim(
            uri=uri,
            operator=OPERATOR,
            pin_name=PIN_NAME,
            expected_key_id=expected_key_id,
            reason="recover wedged holder",
            break_glass=False,
            token=None,
            ready_timeout=3.0,
            result_timeout=3.0,
            json_output=False,
            agent_factory=operator_factory,
        )
        assert refused == 1
        assert hub._identity_pins.pinned(PIN_NAME) is not None
        await _await_registry(hub, OPERATOR, online=False)

        clock.now += 10.0
        applied = await _identity_reclaim(
            uri=uri,
            operator=OPERATOR,
            pin_name=PIN_NAME,
            expected_key_id=expected_key_id,
            reason="recover wedged holder",
            break_glass=False,
            token=None,
            ready_timeout=3.0,
            result_timeout=3.0,
            json_output=False,
            agent_factory=operator_factory,
        )
        assert applied == 0
        assert "ownership lease is still live" in capsys.readouterr().out
        assert hub._identity_pins.pinned(PIN_NAME) is None

        replacement = SynapseAgent(
            PIN_NAME, None, uri=uri, verbose=False, **_machine(tmp_path, "replacement")
        )
        replacement_task = await _start(replacement)
        await _await_registry(hub, PIN_NAME, online=True)
        replacement_pin = hub._identity_pins.pinned(PIN_NAME)
        assert replacement_pin is not None and replacement_pin.key_id != expected_key_id
        await _stop(replacement, replacement_task)

    audits = [event for event in store.read_all() if event.kind == EventKind.IDENTITY_PIN_RECLAIM]
    store.close()
    assert [event.payload["status"] for event in audits] == ["approved", "applied"]
    assert audits[-1].payload["reason"] == "recover wedged holder"
    assert audits[-1].payload["break_glass"] is False


async def test_break_glass_is_required_to_evict_a_live_exact_pin(tmp_path: Path) -> None:
    """A live holder survives ordinary/wrong-key attempts and is visibly evicted only once."""
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(
        hub_id="syn-pin-break-glass",
        identity_pin_path=tmp_path / "pins.json",
        journal=store,
        acl_policy=_policy(),
        require_acl=True,
    )
    replies: list[dict[str, Any]] = []

    async def collect(frame: dict[str, Any]) -> None:
        replies.append(frame)

    async with running_hub(hub) as (_, uri):
        operator = SynapseAgent(
            OPERATOR, collect, uri=uri, verbose=False, **_machine(tmp_path, "operator")
        )
        target = SynapseAgent(
            PIN_NAME,
            None,
            uri=uri,
            verbose=False,
            request_lease=True,
            **_machine(tmp_path, "target"),
        )
        operator_task = await _start(operator)
        target_task = await _start(target)
        await _await_registry(hub, PIN_NAME, online=True)
        pin = hub._identity_pins.pinned(PIN_NAME)
        assert pin is not None

        previous = len(replies)
        await operator.send_message(
            MessageType.IDENTITY_PIN_RECLAIM,
            pin_name=PIN_NAME,
            expected_key_id=pin.key_id,
            reason="holder does not consume mail",
            break_glass=False,
        )
        ordinary = await _await_result(replies, previous)
        assert ordinary["applied"] is False
        assert "online" in ordinary["payload"]
        assert target.last_close_code is None

        previous = len(replies)
        await operator.send_message(
            MessageType.IDENTITY_PIN_RECLAIM,
            pin_name=PIN_NAME,
            expected_key_id="machine-stale-observation",
            reason="holder does not consume mail",
            break_glass=True,
        )
        mismatch = await _await_result(replies, previous)
        assert mismatch["applied"] is False
        assert "expected key" in mismatch["payload"]
        assert hub._identity_pins.pinned(PIN_NAME) is not None

        previous = len(replies)
        await operator.send_message(
            MessageType.IDENTITY_PIN_RECLAIM,
            pin_name=PIN_NAME,
            expected_key_id=pin.key_id,
            reason="holder does not consume mail",
            break_glass=True,
        )
        applied = await _await_result(replies, previous)
        assert applied["applied"] is True
        assert applied["break_glass"] is True
        await _await_registry(hub, PIN_NAME, online=False)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 3.0
        while loop.time() < deadline and target.last_close_code is None:
            await asyncio.sleep(0.01)
        assert target.last_close_code == PIN_RECLAIM_CLOSE_CODE
        assert target.last_close_reason == "identity pin reclaimed"
        assert hub._identity_pins.pinned(PIN_NAME) is None
        assert not hub.clients.ownership.is_leased(PIN_NAME)
        await _stop(operator, operator_task)
        await _stop(target, target_task)

    audits = [event for event in store.read_all() if event.kind == EventKind.IDENTITY_PIN_RECLAIM]
    store.close()
    assert [event.payload["status"] for event in audits] == ["approved", "applied"]
    assert audits[-1].payload["evicted_live_socket"] is True


async def test_reclaim_acl_is_enforced_even_when_general_acl_enforcement_is_off(
    tmp_path: Path,
) -> None:
    """Loading an empty policy cannot expose pin removal on a compatibility hub."""
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(
        hub_id="syn-pin-deny",
        identity_pin_path=tmp_path / "pins.json",
        journal=store,
        acl_policy=AclPolicy([]),
        require_acl=False,
    )
    replies: list[dict[str, Any]] = []

    async def collect(frame: dict[str, Any]) -> None:
        replies.append(frame)

    async with running_hub(hub) as (_, uri):
        operator = SynapseAgent(
            OPERATOR, collect, uri=uri, verbose=False, **_machine(tmp_path, "operator")
        )
        target = SynapseAgent(
            PIN_NAME, None, uri=uri, verbose=False, **_machine(tmp_path, "target")
        )
        operator_task = await _start(operator)
        target_task = await _start(target)
        await _await_registry(hub, PIN_NAME, online=True)
        pin = hub._identity_pins.pinned(PIN_NAME)
        assert pin is not None
        await _stop(target, target_task)
        await _await_registry(hub, PIN_NAME, online=False)

        previous = len(replies)
        await operator.send_message(
            MessageType.IDENTITY_PIN_RECLAIM,
            pin_name=PIN_NAME,
            expected_key_id=pin.key_id,
            reason="try without grant",
        )
        denied = await _await_result(replies, previous)
        assert denied["applied"] is False
        assert "ACL grant" in denied["payload"]
        assert hub._identity_pins.pinned(PIN_NAME) is not None

        hub.acl_policy = None
        previous = len(replies)
        await operator.send_message(
            MessageType.IDENTITY_PIN_RECLAIM,
            pin_name=PIN_NAME,
            expected_key_id=pin.key_id,
            reason="try without any policy",
        )
        no_policy = await _await_result(replies, previous)
        assert no_policy["applied"] is False
        assert "ACL grant" in no_policy["payload"]
        await _stop(operator, operator_task)

    assert not [event for event in store.read_all() if event.kind == EventKind.IDENTITY_PIN_RECLAIM]
    store.close()


async def test_reclaim_storage_failure_and_cas_race_stay_not_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An approved request records either storage failure without dropping the pin."""
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(
        hub_id="syn-pin-failures",
        identity_pin_path=tmp_path / "pins.json",
        journal=store,
        acl_policy=_policy(),
        require_acl=True,
    )
    replies: list[dict[str, Any]] = []

    async def collect(frame: dict[str, Any]) -> None:
        replies.append(frame)

    async with running_hub(hub) as (_, uri):
        operator = SynapseAgent(
            OPERATOR, collect, uri=uri, verbose=False, **_machine(tmp_path, "operator")
        )
        target = SynapseAgent(
            PIN_NAME, None, uri=uri, verbose=False, **_machine(tmp_path, "target")
        )
        operator_task = await _start(operator)
        target_task = await _start(target)
        await _await_registry(hub, PIN_NAME, online=True)
        pin = hub._identity_pins.pinned(PIN_NAME)
        assert pin is not None
        await _stop(target, target_task)
        await _await_registry(hub, PIN_NAME, online=False)

        def fail_reclaim(_name: str, *, expected_key_id: str) -> None:
            del expected_key_id
            raise OSError("disk unavailable")

        monkeypatch.setattr(hub._identity_pins, "reclaim", fail_reclaim)
        previous = len(replies)
        await operator.send_message(
            MessageType.IDENTITY_PIN_RECLAIM,
            pin_name=PIN_NAME,
            expected_key_id=pin.key_id,
            reason="storage failure drill",
        )
        failed = await _await_result(replies, previous)
        assert failed["applied"] is False
        assert "disk unavailable" in failed["payload"]

        monkeypatch.setattr(
            hub._identity_pins,
            "reclaim",
            lambda _name, *, expected_key_id: None,
        )
        previous = len(replies)
        await operator.send_message(
            MessageType.IDENTITY_PIN_RECLAIM,
            pin_name=PIN_NAME,
            expected_key_id=pin.key_id,
            reason="compare-and-swap race drill",
        )
        raced = await _await_result(replies, previous)
        assert raced["applied"] is False
        assert "changed before" in raced["payload"]
        assert hub._identity_pins.pinned(PIN_NAME) is not None
        await _stop(operator, operator_task)

    audits = [event for event in store.read_all() if event.kind == EventKind.IDENTITY_PIN_RECLAIM]
    store.close()
    assert [event.payload["status"] for event in audits] == [
        "approved",
        "not_applied",
        "approved",
        "not_applied",
    ]
