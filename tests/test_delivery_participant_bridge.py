# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — two actual provider adapters through durable delivery
"""Exercise OpenCode API and local Ollama participants behind a real hub."""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from fixtures.opencode.runtime import (
    TEST_MODEL,
    ScriptedLlmServer,
    find_opencode,
    isolated_environment,
    running_opencode_server,
)
from hub_e2e_helpers import running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.participants.delivery_bridge import DeliveryParticipantBridge
from synapse_channel.participants.envelope import TurnRequest, TurnResult, error_turn_result
from synapse_channel.participants.headless_ollama import OllamaParticipant
from synapse_channel.participants.opencode_api import OpenCodeApiParticipant
from synapse_channel.participants.participant import (
    Participant,
    ParticipantChannel,
    ParticipantHealth,
)


async def _until(
    queue: asyncio.Queue[dict[str, Any]], kind: str, *, stage: str = ""
) -> dict[str, Any]:
    """Wait for one callback frame with a type and optional lifecycle stage."""
    while True:
        frame = await asyncio.wait_for(queue.get(), 60)
        if frame.get("type") == kind and (not stage or frame.get("stage") == stage):
            return frame


async def _journey(
    tmp_path: Path,
    participant: Participant,
    *,
    capability: dict[str, str],
    mode: str,
    turns: int = 1,
    expected_stage: str = "completed",
) -> dict[str, Any]:
    """Drive an actual adapter from request through a hub outcome."""
    async with running_hub(SynapseHub(journal=EventStore(tmp_path / "hub.db"), hub_id="hub-1")) as (
        _hub,
        uri,
    ):
        sender_frames: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        receiver_frames: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def sender_callback(frame: dict[str, Any]) -> None:
            await sender_frames.put(frame)

        sender = SynapseAgent(
            "P/author", uri=uri, on_message_callback=sender_callback, machine_identity=False
        )
        receiver = SynapseAgent(
            participant.identity,
            uri=uri,
            delivery_capabilities=capability,
            machine_identity=False,
        )
        bridge = DeliveryParticipantBridge(
            receiver, participant, ledger_path=tmp_path / "bridge.db"
        )

        async def receiver_callback(frame: dict[str, Any]) -> None:
            await receiver_frames.put(frame)
            await bridge.on_message(frame)

        receiver.callback = receiver_callback
        bridge.start()
        sender_task = asyncio.create_task(sender.connect())
        receiver_task = asyncio.create_task(receiver.connect())
        try:
            await asyncio.wait_for(receiver.delivery_ready_event.wait(), 5)
            assert await sender.wait_until_ready()
            await sender.request_who()
            roster = await _until(sender_frames, MessageType.WHO_SNAPSHOT)
            incarnation = roster["delivery_sessions"][participant.identity]["incarnation"]
            for turn in range(turns):
                key = await sender.request_delivery(
                    target=participant.identity,
                    target_incarnation=incarnation,
                    mode=mode,
                    body=f"Reply with the word OK. Turn {turn + 1}.",
                    deadline=time.time() + 120,
                    request_id=f"provider-req-{turn}",
                    idempotency_key=f"provider-idem-{turn}",
                    task_id="provider-task",
                )
                accepted = await _until(sender_frames, MessageType.DELIVERY_STATUS, stage="queued")
                assert accepted["operation_key"] == key
                await _until(receiver_frames, MessageType.DELIVERY_OFFER)
                outcome = await _until(
                    sender_frames, MessageType.DELIVERY_STATUS, stage=expected_stage
                )
                await sender.request_delivery_status(key)
                status = await _until(
                    sender_frames, MessageType.DELIVERY_STATUS, stage=expected_stage
                )
                assert status["boundary_delivered"]
                assert status["explicitly_acknowledged"]
                assert status["task_completed"] is (expected_stage == "completed")
            return outcome
        finally:
            if sender.connection is not None:
                await sender.connection.close()
            if receiver.connection is not None:
                await receiver.connection.close()
            await asyncio.gather(sender_task, receiver_task)
            await bridge.close()


@pytest.mark.real_hub
async def test_local_ollama_emulates_next_turn(tmp_path: Path) -> None:
    """A real local Ollama CLI turn produces an emulated next-turn outcome."""
    if shutil.which("ollama") is None:
        pytest.skip("local Ollama binary is unavailable")
    models = subprocess.run(  # nosec B603
        ["ollama", "list"], capture_output=True, text=True, timeout=10, check=False
    )
    if models.returncode != 0 or "gemma3:1b" not in models.stdout:
        pytest.skip("local gemma3:1b is unavailable")
    outcome = await _journey(
        tmp_path,
        OllamaParticipant("P/ollama", model="gemma3:1b", timeout=60),
        capability={"next_turn": "emulated"},
        mode="next_turn",
    )
    assert outcome["stage"] == "completed"


@pytest.mark.real_hub
async def test_real_opencode_api_native_follow_up(tmp_path: Path) -> None:
    """A pinned real OpenCode server executes a native API follow-up turn."""
    binary = find_opencode()
    home = tmp_path / "home"
    home.mkdir()
    username = "opencode-test"
    password = "isolated-test-password"
    password_file = tmp_path / "server.password"
    password_file.write_text(password + "\n", encoding="utf-8")
    password_file.chmod(0o600)
    with ScriptedLlmServer() as llm:
        environment = isolated_environment(home, llm.url, pure=True, disable_project_config=True)
        with running_opencode_server(
            binary,
            cwd=tmp_path,
            env=environment,
            username=username,
            password=password,
        ) as server:
            llm.enqueue_text("OK")
            llm.enqueue_text("OK again")
            participant = OpenCodeApiParticipant(
                "P/opencode",
                directory=tmp_path,
                model=TEST_MODEL,
                endpoint=server.url,
                username=username,
                password_file=str(password_file),
            )
            outcome = await _journey(
                tmp_path,
                participant,
                capability={"follow_up": "native"},
                mode="follow_up",
                turns=2,
            )
            assert outcome["stage"] == "completed"
            assert len(llm.prompt_requests) == 2
            assert "Reply with the word OK" in json.dumps(llm.prompt_requests[0])
            assert "Turn 2" in json.dumps(llm.prompt_requests[1])


@pytest.mark.real_hub
async def test_real_opencode_bridge_denies_unapproved_shell_effect(tmp_path: Path) -> None:
    """A scripted bash tool call cannot create a file under an explicit deny rule."""
    binary = find_opencode()
    home = tmp_path / "home"
    home.mkdir()
    password_file = tmp_path / "server.password"
    password_file.write_text("isolated-test-password\n", encoding="utf-8")
    password_file.chmod(0o600)
    marker = tmp_path / "shell-effect.txt"
    with ScriptedLlmServer() as llm:
        environment = isolated_environment(home, llm.url, pure=True, disable_project_config=True)
        config = json.loads(environment["OPENCODE_CONFIG_CONTENT"])
        config["permission"] = {"bash": "deny", "edit": "deny"}
        environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
        with running_opencode_server(
            binary,
            cwd=tmp_path,
            env=environment,
            username="opencode-test",
            password="isolated-test-password",
        ) as server:
            llm.enqueue_tool(
                "bash",
                {"command": f"touch {marker}", "description": "unapproved shell effect"},
            )
            llm.enqueue_text("Shell command was refused.")
            participant = OpenCodeApiParticipant(
                "P/opencode",
                directory=tmp_path,
                model=TEST_MODEL,
                endpoint=server.url,
                username="opencode-test",
                password_file=str(password_file),
                timeout=20,
            )
            await _journey(
                tmp_path,
                participant,
                capability={"follow_up": "native"},
                mode="follow_up",
            )
            assert not marker.exists()
            assert len(llm.prompt_requests) >= 1


class _NeverRunParticipant:
    """Count actual provider invocations in an expiry refusal journey."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def identity(self) -> str:
        return "P/receiver"

    @property
    def channel(self) -> ParticipantChannel:
        return ParticipantChannel.HEADLESS

    def health(self) -> ParticipantHealth:
        return ParticipantHealth(self.identity, self.channel, True, "ready")

    async def take_turn(self, request: Any) -> Any:
        self.calls += 1
        raise AssertionError("expired delivery reached the provider")


async def test_terminal_status_unblocks_bridge_before_provider_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent hub expiry ends the queued bridge turn without retrying forever."""
    agent = SynapseAgent(
        "P/receiver", delivery_capabilities={"follow_up": "native"}, machine_identity=False
    )
    participant = _NeverRunParticipant()
    ledger_path = tmp_path / "bridge.db"
    bridge = DeliveryParticipantBridge(agent, participant, ledger_path=ledger_path)
    key = "a" * 64

    async def report_expired(*_args: Any, **_kwargs: Any) -> None:
        await bridge.on_message(
            {
                "type": MessageType.DELIVERY_STATUS,
                "target": agent.name,
                "operation_key": key,
                "stage": "expired",
            }
        )

    monkeypatch.setattr(agent, "report_delivery_stage", report_expired)
    bridge.start()
    try:
        await bridge.on_message(
            {
                "type": MessageType.DELIVERY_OFFER,
                "target": agent.name,
                "target_incarnation": agent.delivery_incarnation,
                "operation_key": key,
                "request_id": "req-1",
                "task_id": "task-1",
                "body": "Answer the reviewed question.",
                "deadline": time.time() + 30,
                "selected_mode": "follow_up",
            }
        )
        await asyncio.wait_for(bridge._queue.join(), 2)
        assert participant.calls == 0
    finally:
        await bridge.close()
    with sqlite3.connect(ledger_path) as connection:
        assert connection.execute(
            "SELECT stage, outcome_code FROM delivery_bridge WHERE operation_key = ?", (key,)
        ).fetchone() == ("hub_refused", "terminal_delivery")


class _DeterministicParticipant:
    """Return controlled provider outcomes through a real hub and bridge."""

    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.calls = 0

    @property
    def identity(self) -> str:
        return "P/receiver"

    @property
    def channel(self) -> ParticipantChannel:
        return ParticipantChannel.HEADLESS

    def health(self) -> ParticipantHealth:
        return ParticipantHealth(self.identity, self.channel, True, "ready")

    async def take_turn(self, request: TurnRequest) -> TurnResult:
        self.calls += 1
        if self.outcome == "exception":
            raise RuntimeError("provider process failed")
        result = error_turn_result(
            participant=self.identity,
            channel=self.channel,
            request=request,
            reason="provider did not complete",
        )
        if self.outcome == "abstained":
            result["is_error"] = False
            result["abstained"] = True
            result["reason"] = "no answer produced"
        return result


class _BlockingParticipant(_DeterministicParticipant):
    """Hold one accepted turn so a second queued offer can be cancelled."""

    def __init__(self) -> None:
        super().__init__("success")
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def take_turn(self, request: TurnRequest) -> TurnResult:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        result = error_turn_result(
            participant=self.identity,
            channel=self.channel,
            request=request,
            reason="",
        )
        result["is_error"] = False
        result["answer"] = "OK"
        result["stop_reason"] = "end_turn"
        return result


@pytest.mark.real_hub
@pytest.mark.parametrize(
    ("provider_outcome", "delivery_stage"),
    [("error", "failed"), ("abstained", "rejected"), ("exception", "failed")],
)
async def test_provider_failure_and_abstention_remain_distinct(
    tmp_path: Path, provider_outcome: str, delivery_stage: str
) -> None:
    """A real hub records a typed failure, abstention, or provider exception."""
    participant = _DeterministicParticipant(provider_outcome)
    outcome = await _journey(
        tmp_path,
        participant,
        capability={"next_turn": "emulated"},
        mode="next_turn",
        expected_stage=delivery_stage,
    )
    assert outcome["stage"] == delivery_stage
    assert participant.calls == 1


@pytest.mark.real_hub
async def test_cancelled_queued_offer_never_invokes_provider(tmp_path: Path) -> None:
    """A sender cancellation settles queued work after the active turn ends."""
    participant = _BlockingParticipant()
    async with running_hub(SynapseHub(journal=EventStore(tmp_path / "hub.db"), hub_id="hub-1")) as (
        _hub,
        uri,
    ):
        sender_frames: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def sender_callback(frame: dict[str, Any]) -> None:
            await sender_frames.put(frame)

        sender = SynapseAgent(
            "P/author", uri=uri, on_message_callback=sender_callback, machine_identity=False
        )
        receiver = SynapseAgent(
            participant.identity,
            uri=uri,
            delivery_capabilities={"next_turn": "emulated"},
            machine_identity=False,
        )
        bridge = DeliveryParticipantBridge(
            receiver, participant, ledger_path=tmp_path / "bridge.db"
        )
        receiver.callback = bridge.on_message
        bridge.start()
        sender_task = asyncio.create_task(sender.connect())
        receiver_task = asyncio.create_task(receiver.connect())
        try:
            await asyncio.wait_for(receiver.delivery_ready_event.wait(), 5)
            assert await sender.wait_until_ready()
            first = await sender.request_delivery(
                target=participant.identity,
                target_incarnation=receiver.delivery_incarnation,
                mode="next_turn",
                body="First turn holds the executor.",
                deadline=time.time() + 120,
                request_id="first-req",
                idempotency_key="first-idem",
            )
            await asyncio.wait_for(participant.started.wait(), 5)
            second = await sender.request_delivery(
                target=participant.identity,
                target_incarnation=receiver.delivery_incarnation,
                mode="next_turn",
                body="Second turn must be cancelled.",
                deadline=time.time() + 120,
                request_id="second-req",
                idempotency_key="second-idem",
            )
            await sender.cancel_delivery(second, mutation_id="cancel-second")
            await _until(sender_frames, MessageType.DELIVERY_STATUS, stage="queued")
            participant.release.set()
            await _until(sender_frames, MessageType.DELIVERY_STATUS, stage="completed")
            cancelled = await _until(sender_frames, MessageType.DELIVERY_STATUS, stage="cancelled")
            assert cancelled["operation_key"] == second
            await sender.request_delivery_status(second)
            cancelled_status = await _until(
                sender_frames, MessageType.DELIVERY_STATUS, stage="cancelled"
            )
            assert cancelled_status["cancel_requested"]
            assert not cancelled_status["task_completed"]
            assert participant.calls == 1
            await sender.request_delivery_status(first)
            first_status = await _until(
                sender_frames, MessageType.DELIVERY_STATUS, stage="completed"
            )
            assert first_status["operation_key"] == first
        finally:
            participant.release.set()
            if sender.connection is not None:
                await sender.connection.close()
            if receiver.connection is not None:
                await receiver.connection.close()
            await asyncio.gather(sender_task, receiver_task)
            await bridge.close()


@pytest.mark.real_hub
async def test_bridge_refuses_malformed_or_replayed_offer_before_provider_turn(
    tmp_path: Path,
) -> None:
    """Recipient callback accepts one exact hub offer and deduplicates its replay."""
    participant = _DeterministicParticipant("error")
    ledger_path = tmp_path / "bridge.db"
    async with running_hub(SynapseHub(journal=EventStore(tmp_path / "hub.db"), hub_id="hub-1")) as (
        _hub,
        uri,
    ):
        sender_frames: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        offers: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def sender_callback(frame: dict[str, Any]) -> None:
            await sender_frames.put(frame)

        sender = SynapseAgent(
            "P/author", uri=uri, on_message_callback=sender_callback, machine_identity=False
        )
        receiver = SynapseAgent(
            participant.identity,
            uri=uri,
            delivery_capabilities={"next_turn": "emulated"},
            machine_identity=False,
        )
        bridge = DeliveryParticipantBridge(receiver, participant, ledger_path=ledger_path)

        async def receiver_callback(frame: dict[str, Any]) -> None:
            if frame.get("type") == MessageType.DELIVERY_OFFER:
                await offers.put(frame)
            else:
                await bridge.on_message(frame)

        receiver.callback = receiver_callback
        sender_task = asyncio.create_task(sender.connect())
        receiver_task = asyncio.create_task(receiver.connect())
        try:
            await asyncio.wait_for(receiver.delivery_ready_event.wait(), 5)
            assert await sender.wait_until_ready()
            key = await sender.request_delivery(
                target=participant.identity,
                target_incarnation=receiver.delivery_incarnation,
                mode="next_turn",
                body="Only this exact offer may run.",
                deadline=time.time() + 120,
                request_id="guard-req",
                idempotency_key="guard-idem",
            )
            await _until(sender_frames, MessageType.DELIVERY_STATUS, stage="queued")
            offer = await asyncio.wait_for(offers.get(), 5)
            assert offer["operation_key"] == key
            for change in (
                {"target": "P/other"},
                {"target_incarnation": "b" * 64},
                {"operation_key": "invalid"},
                {"body": "x" * 8193},
                {"selected_mode": "interrupt"},
                {"deadline": True},
            ):
                await bridge.on_message(offer | change)
            with sqlite3.connect(ledger_path) as local:
                assert local.execute("SELECT COUNT(*) FROM delivery_bridge").fetchone() == (0,)
            await bridge.on_message(offer)
            await bridge.on_message(offer)
            await bridge.on_message(offer | {"body": "different"})
            bridge.start()
            outcome = await _until(sender_frames, MessageType.DELIVERY_STATUS, stage="failed")
            assert outcome["operation_key"] == key
            assert participant.calls == 1
            await bridge.on_message(offer)
            assert participant.calls == 1
            with sqlite3.connect(ledger_path) as local:
                assert local.execute("SELECT COUNT(*) FROM delivery_bridge").fetchone() == (1,)
        finally:
            if sender.connection is not None:
                await sender.connection.close()
            if receiver.connection is not None:
                await receiver.connection.close()
            await asyncio.gather(sender_task, receiver_task)
            await bridge.close()


async def test_bridge_requires_exact_supported_healthy_participant(tmp_path: Path) -> None:
    """A bridge refuses to advertise a mismatched or unavailable executor."""

    class OtherIdentity(_DeterministicParticipant):
        @property
        def identity(self) -> str:
            return "P/other"

    class Unavailable(_DeterministicParticipant):
        def health(self) -> ParticipantHealth:
            return ParticipantHealth(self.identity, self.channel, False, "offline")

    receiver = SynapseAgent(
        "P/receiver", delivery_capabilities={"next_turn": "emulated"}, machine_identity=False
    )
    with pytest.raises(ValueError, match="identities must match"):
        DeliveryParticipantBridge(receiver, OtherIdentity("error"), ledger_path=tmp_path / "a.db")
    unsupported = SynapseAgent(
        "P/receiver", delivery_capabilities={"steer": "native"}, machine_identity=False
    )
    with pytest.raises(ValueError, match="follow_up and next_turn only"):
        DeliveryParticipantBridge(
            unsupported, _DeterministicParticipant("error"), ledger_path=tmp_path / "b.db"
        )
    unavailable = DeliveryParticipantBridge(
        receiver, Unavailable("error"), ledger_path=tmp_path / "c.db"
    )
    with pytest.raises(RuntimeError, match="unavailable"):
        unavailable.start()
    await unavailable.close()
    healthy = DeliveryParticipantBridge(
        receiver, _DeterministicParticipant("error"), ledger_path=tmp_path / "d.db"
    )
    healthy.start()
    with pytest.raises(RuntimeError, match="already started"):
        healthy.start()
    await healthy.close()


@pytest.mark.real_hub
async def test_expired_offer_never_starts_provider_turn(tmp_path: Path) -> None:
    """A queued frame delayed past its deadline remains a hub expiry."""
    participant = _NeverRunParticipant()
    async with running_hub(SynapseHub(journal=EventStore(tmp_path / "hub.db"), hub_id="hub-1")) as (
        _hub,
        uri,
    ):
        sender_frames: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        delayed_offer = asyncio.Event()

        async def sender_callback(frame: dict[str, Any]) -> None:
            await sender_frames.put(frame)

        sender = SynapseAgent(
            "P/author", uri=uri, on_message_callback=sender_callback, machine_identity=False
        )
        receiver = SynapseAgent(
            "P/receiver",
            uri=uri,
            delivery_capabilities={"next_turn": "emulated"},
            machine_identity=False,
        )
        bridge = DeliveryParticipantBridge(
            receiver, participant, ledger_path=tmp_path / "bridge.db"
        )

        async def receiver_callback(frame: dict[str, Any]) -> None:
            if frame.get("type") == MessageType.DELIVERY_OFFER:
                await asyncio.sleep(0.25)
                delayed_offer.set()
            await bridge.on_message(frame)

        receiver.callback = receiver_callback
        bridge.start()
        sender_task = asyncio.create_task(sender.connect())
        receiver_task = asyncio.create_task(receiver.connect())
        try:
            await asyncio.wait_for(receiver.delivery_ready_event.wait(), 5)
            assert await sender.wait_until_ready()
            await sender.request_delivery(
                target="P/receiver",
                target_incarnation=receiver.delivery_incarnation,
                mode="next_turn",
                body="This expired before the provider could run.",
                deadline=time.time() + 0.15,
                request_id="expiry-req",
                idempotency_key="expiry-idem",
            )
            queued = await _until(sender_frames, MessageType.DELIVERY_STATUS, stage="queued")
            await asyncio.wait_for(delayed_offer.wait(), 3)
            await sender.request_delivery_status(queued["operation_key"])
            status = await _until(sender_frames, MessageType.DELIVERY_STATUS, stage="expired")
            assert status["stage"] == "expired"
            assert participant.calls == 0
        finally:
            if sender.connection is not None:
                await sender.connection.close()
            if receiver.connection is not None:
                await receiver.connection.close()
            await asyncio.gather(sender_task, receiver_task)
            await bridge.close()
