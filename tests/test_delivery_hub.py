# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real-hub version-three delivery ingress tests
"""Exercise recipient capability negotiation through actual WebSocket sockets."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import ClientConnection, connect

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.core.acl import CLAIM, DELIVERY_CONTROL, MESSAGE, AclPolicy, AclRule
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.delivery_modes import parse_delivery_intent
from synapse_channel.core.handlers.delivery_modes import expire_due_deliveries
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType


async def _register(
    uri: str,
    name: str,
    *,
    version: int = 3,
    capabilities: dict[str, str] | None = None,
    session_token: str = "a" * 64,
    auth_token: str = "",
) -> tuple[ClientConnection, dict[str, Any] | None]:
    """Open one real hub socket and bind an optional recipient capability."""
    websocket = await connect(uri)
    if not auth_token:
        await read_until_type(websocket, MessageType.WELCOME)
    frame: dict[str, Any] = {
        "sender": name,
        "type": MessageType.HEARTBEAT,
        "target": "System",
        "payload": "online",
        "protocol_version": version,
    }
    if capabilities is not None:
        frame["delivery_session_token"] = session_token
        frame["delivery_capabilities"] = capabilities
    if auth_token:
        frame["token"] = auth_token
    await websocket.send(json.dumps(frame))
    if auth_token:
        await read_until_type(websocket, MessageType.WELCOME)
    session = (
        await read_until_type(websocket, MessageType.DELIVERY_SESSION)
        if capabilities is not None
        else None
    )
    return websocket, session


def _intent(incarnation: str, *, mode: str = "follow_up") -> dict[str, Any]:
    """Build one bounded request with an exact target session and deadline."""
    return {
        "sender": "P/author",
        "type": MessageType.DELIVERY_REQUEST,
        "target": "P/receiver",
        "protocol_version": 3,
        "request_id": "req-1",
        "idempotency_key": "idem-1",
        "target_incarnation": incarnation,
        "mode": mode,
        "allowed_fallbacks": ["next_turn"],
        "task_id": "T-1",
        "body": "Apply reviewed change.",
        "deadline": time.time() + 120,
    }


async def _stage(
    receiver: ClientConnection,
    *,
    msg_type: str,
    operation_key: str,
    mutation_id: str,
    evidence: dict[str, str],
    stage: str = "",
) -> None:
    """Report one correlated recipient transition over the live socket."""
    frame: dict[str, Any] = {
        "sender": "P/receiver",
        "type": msg_type,
        "target": "System",
        "protocol_version": 3,
        "operation_key": operation_key,
        "request_id": "req-1",
        "task_id": "T-1",
        "mutation_id": mutation_id,
        "evidence": evidence,
    }
    if stage:
        frame["stage"] = stage
    await receiver.send(json.dumps(frame))


@pytest.mark.real_hub
async def test_real_hub_queues_exact_mode_and_reports_distinct_stages(tmp_path: Path) -> None:
    """Socket delivery remains queued until the recipient reports a turn boundary."""
    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(journal=store, hub_id="hub-1")) as (hub, uri):
        receiver, session = await _register(
            uri, "P/receiver", capabilities={"next_turn": "emulated"}
        )
        sender, _ = await _register(uri, "P/author")
        try:
            assert session is not None
            request = _intent(session["incarnation"])
            await sender.send(json.dumps(request))
            status = await read_until_type(sender, MessageType.DELIVERY_STATUS)
            offer = await read_until_type(receiver, MessageType.DELIVERY_OFFER)
            assert status["stage"] == "queued"
            assert status["selected_mode"] == "next_turn"
            assert status["quality"] == "emulated"
            assert status["receiver_reachable"]
            assert status["active_session"]
            assert not status["boundary_delivered"]
            assert not status["explicitly_acknowledged"]
            assert not status["task_completed"]
            assert offer["selected_mode"] == "next_turn"
            assert offer["body"] == request["body"]
            assert offer["operation_key"] == status["operation_key"]
            assert len(tuple(store.iter_events())) == 2
            await sender.send(
                json.dumps(
                    {
                        "sender": "P/author",
                        "type": MessageType.DELIVERY_STATUS_REQUEST,
                        "target": "System",
                        "protocol_version": 3,
                        "operation_key": status["operation_key"],
                    }
                )
            )
            queried = await read_until_type(sender, MessageType.DELIVERY_STATUS)
            assert queried["stage"] == "queued"
            assert queried["latest_event_seq"] == status["latest_event_seq"]
            assert hub.clients.protocol_version_of("P/author") == 3
            await sender.send(
                json.dumps(
                    {
                        "sender": "P/author",
                        "type": MessageType.WHO_REQUEST,
                        "target": "System",
                    }
                )
            )
            roster = await read_until_type(sender, MessageType.WHO_SNAPSHOT)
            assert roster["delivery_sessions"]["P/receiver"] == {
                "incarnation": session["incarnation"],
                "capabilities": {"next_turn": "emulated"},
                "hub_id": "hub-1",
            }

            await _stage(
                receiver,
                msg_type=MessageType.DELIVERY_BOUNDARY,
                operation_key=offer["operation_key"],
                mutation_id="boundary-1",
                evidence={"boundary": "turn-1"},
            )
            boundary = await read_until_type(receiver, MessageType.DELIVERY_STATUS)
            await read_until_type(sender, MessageType.DELIVERY_STATUS)
            assert boundary["stage"] == "boundary_delivered"
            assert boundary["boundary_delivered"]
            assert not boundary["explicitly_acknowledged"]

            await _stage(
                receiver,
                msg_type=MessageType.DELIVERY_ACK,
                operation_key=offer["operation_key"],
                mutation_id="ack-1",
                evidence={"receipt_id": "local-1"},
            )
            acknowledged = await read_until_type(receiver, MessageType.DELIVERY_STATUS)
            await read_until_type(sender, MessageType.DELIVERY_STATUS)
            assert acknowledged["stage"] == "acknowledged"
            assert acknowledged["explicitly_acknowledged"]
            assert not acknowledged["task_completed"]

            await _stage(
                receiver,
                msg_type=MessageType.DELIVERY_OUTCOME,
                operation_key=offer["operation_key"],
                mutation_id="outcome-1",
                stage="completed",
                evidence={"executor_ref": "run-1", "outcome_code": "success"},
            )
            completed = await read_until_type(receiver, MessageType.DELIVERY_STATUS)
            await read_until_type(sender, MessageType.DELIVERY_STATUS)
            assert completed["stage"] == "completed"
            assert completed["task_completed"]
            assert len(tuple(store.iter_events())) == 5
            await _stage(
                receiver,
                msg_type=MessageType.DELIVERY_BOUNDARY,
                operation_key=offer["operation_key"],
                mutation_id="boundary-1",
                evidence={"boundary": "turn-1"},
            )
            repeated = await read_until_type(receiver, MessageType.DELIVERY_STATUS)
            assert repeated["stage"] == "completed"
            assert len(tuple(store.iter_events())) == 5
            await _stage(
                receiver,
                msg_type=MessageType.DELIVERY_BOUNDARY,
                operation_key=offer["operation_key"],
                mutation_id="boundary-1",
                evidence={"boundary": "different-turn"},
            )
            conflict = await read_until_type(receiver, MessageType.DELIVERY_REFUSED)
            assert conflict["reason_code"] == "id_conflict"
        finally:
            await sender.close()
            await receiver.close()


@pytest.mark.real_hub
async def test_malformed_delivery_mutations_leave_queued_work_unchanged(tmp_path: Path) -> None:
    """Malformed status and executor frames cannot advance a real queued request."""
    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(journal=store, hub_id="hub-1")) as (_hub, uri):
        receiver, session = await _register(uri, "P/receiver", capabilities={"follow_up": "native"})
        sender, _ = await _register(uri, "P/author")
        try:
            assert session is not None
            await sender.send(json.dumps(_intent(session["incarnation"])))
            queued = await read_until_type(sender, MessageType.DELIVERY_STATUS)
            await read_until_type(receiver, MessageType.DELIVERY_OFFER)
            key = queued["operation_key"]

            for bad_key in ("0" * 64, "g" * 64):
                await sender.send(
                    json.dumps(
                        {
                            "sender": "P/author",
                            "type": MessageType.DELIVERY_STATUS_REQUEST,
                            "target": "System",
                            "protocol_version": 3,
                            "operation_key": bad_key,
                        }
                    )
                )
                refusal = await read_until_type(sender, MessageType.DELIVERY_REFUSED)
                assert refusal["reason_code"] == (
                    "unknown_request" if bad_key[0] == "0" else "invalid_shape"
                )
            await sender.send(
                json.dumps(
                    {
                        "sender": "P/author",
                        "type": MessageType.DELIVERY_STATUS_REQUEST,
                        "target": "System",
                        "protocol_version": 3,
                        "operation_key": "short",
                    }
                )
            )
            assert (await read_until_type(sender, MessageType.DELIVERY_REFUSED))[
                "reason_code"
            ] == "invalid_shape"

            for unsafe_id in ("bad\nrequest", "x" * 129):
                await sender.send(
                    json.dumps(_intent(session["incarnation"]) | {"request_id": unsafe_id})
                )
                refusal = await read_until_type(sender, MessageType.DELIVERY_REFUSED)
                assert refusal["reason_code"] == "invalid_shape"
                assert refusal["request_id"] == ""

            for socket, principal, operation_key, reason in (
                (sender, "P/author", "0" * 64, "unknown_request"),
                (receiver, "P/receiver", key, "unauthorised_requester"),
            ):
                await socket.send(
                    json.dumps(
                        {
                            "sender": principal,
                            "type": MessageType.DELIVERY_CANCEL,
                            "target": "System",
                            "protocol_version": 3,
                            "operation_key": operation_key,
                            "mutation_id": "cancel-denied",
                        }
                    )
                )
                assert (await read_until_type(socket, MessageType.DELIVERY_REFUSED))[
                    "reason_code"
                ] == reason

            base = {
                "sender": "P/receiver",
                "type": MessageType.DELIVERY_OUTCOME,
                "target": "System",
                "protocol_version": 3,
                "operation_key": key,
                "request_id": "req-1",
                "task_id": "T-1",
                "mutation_id": "mutation-1",
                "stage": "completed",
                "evidence": {"executor_ref": "run-1", "outcome_code": "success"},
            }
            bad_mutations: tuple[tuple[dict[str, object], str], ...] = (
                ({"request_id": "other"}, "invalid_shape"),
                ({"stage": "unknown"}, "invalid_shape"),
                ({"evidence": {}}, "invalid_shape"),
                (
                    {
                        "evidence": {
                            "executor_ref": "run-1",
                            "outcome_code": "success",
                            "request_id": "other",
                        }
                    },
                    "invalid_shape",
                ),
                (
                    {
                        "evidence": {
                            "executor_ref": "run-1",
                            "outcome_code": "success",
                            "task_id": "other",
                        }
                    },
                    "invalid_shape",
                ),
                ({"mutation_id": "bad\nmutation"}, "invalid_shape"),
                ({"mutation_id": ""}, "invalid_shape"),
                ({"evidence": {"executor_ref": "run-1", "outcome_code": 1}}, "invalid_shape"),
                (
                    {
                        "evidence": {
                            "executor_ref": "run-1",
                            "outcome_code": "success",
                            "extra": "x",
                        }
                    },
                    "invalid_shape",
                ),
                (
                    {
                        "evidence": {
                            "executor_ref": "run-1",
                            "outcome_code": "success",
                            "reason_code": "bad\nvalue",
                        }
                    },
                    "invalid_shape",
                ),
                ({"type": MessageType.DELIVERY_BOUNDARY, "evidence": {}}, "invalid_shape"),
                ({"type": MessageType.DELIVERY_ACK, "evidence": {}}, "invalid_shape"),
            )
            for change, reason in bad_mutations:
                await receiver.send(json.dumps(base | change))
                refusal = await read_until_type(receiver, MessageType.DELIVERY_REFUSED)
                assert refusal["reason_code"] == reason

            assert len(tuple(store.iter_events())) == 2
            await sender.send(
                json.dumps(
                    {
                        "sender": "P/author",
                        "type": MessageType.DELIVERY_STATUS_REQUEST,
                        "target": "System",
                        "protocol_version": 3,
                        "operation_key": key,
                    }
                )
            )
            status = await read_until_type(sender, MessageType.DELIVERY_STATUS)
            assert status["stage"] == "queued"
        finally:
            await sender.close()
            await receiver.close()


@pytest.mark.real_hub
@pytest.mark.parametrize("version", [1, 2])
async def test_old_peer_and_unknown_mode_refuse_without_journal_growth(
    tmp_path: Path, version: int
) -> None:
    """Version-one/two peers and unknown modes never degrade to chat."""
    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(journal=store, hub_id="hub-1")) as (_hub, uri):
        receiver, session = await _register(uri, "P/receiver", capabilities={"follow_up": "native"})
        old_sender, _ = await _register(uri, "P/author", version=version)
        try:
            assert session is not None
            await old_sender.send(json.dumps(_intent(session["incarnation"])))
            old_refusal = await read_until_type(old_sender, MessageType.DELIVERY_REFUSED)
            assert old_refusal["reason_code"] == "unsupported_protocol"
            await old_sender.send(
                json.dumps(
                    {
                        "sender": "P/author",
                        "type": MessageType.WHO_REQUEST,
                        "target": "System",
                    }
                )
            )
            old_roster = await read_until_type(old_sender, MessageType.WHO_SNAPSHOT)
            assert "delivery_sessions" not in old_roster
            await old_sender.close()
            new_sender, _ = await _register(uri, "P/author")
            try:
                await new_sender.send(json.dumps(_intent(session["incarnation"], mode="unknown")))
                refusal = await read_until_type(new_sender, MessageType.DELIVERY_REFUSED)
                assert refusal["reason_code"] == "unsupported_mode"
                assert tuple(store.iter_events()) == ()
            finally:
                await new_sender.close()
        finally:
            await receiver.close()


@pytest.mark.real_hub
async def test_reconnect_replays_offer_and_offline_sender_status(tmp_path: Path) -> None:
    """A queued offer and committed notice survive real socket disconnections."""
    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(journal=store, hub_id="hub-1")) as (_hub, uri):
        receiver, session = await _register(uri, "P/receiver", capabilities={"follow_up": "native"})
        sender, _ = await _register(uri, "P/author")
        assert session is not None
        try:
            await sender.send(json.dumps(_intent(session["incarnation"])))
            queued = await read_until_type(sender, MessageType.DELIVERY_STATUS)
            offer = await read_until_type(receiver, MessageType.DELIVERY_OFFER)
            await receiver.close()
            await sender.send(
                json.dumps(
                    {
                        "sender": "P/author",
                        "type": MessageType.DELIVERY_STATUS_REQUEST,
                        "target": "System",
                        "protocol_version": 3,
                        "operation_key": queued["operation_key"],
                    }
                )
            )
            offline = await read_until_type(sender, MessageType.DELIVERY_STATUS)
            assert not offline["receiver_reachable"]
            assert not offline["active_session"]
            assert offline["stage"] == "queued"
            receiver, resumed = await _register(
                uri, "P/receiver", capabilities={"follow_up": "native"}
            )
            assert resumed is not None
            assert resumed["incarnation"] == session["incarnation"]
            replayed = await read_until_type(receiver, MessageType.DELIVERY_OFFER)
            assert replayed["notification_id"] == offer["notification_id"]
            await sender.close()
            await _stage(
                receiver,
                msg_type=MessageType.DELIVERY_BOUNDARY,
                operation_key=queued["operation_key"],
                mutation_id="boundary-after-reconnect",
                evidence={"boundary": "turn-2"},
            )
            await read_until_type(receiver, MessageType.DELIVERY_STATUS)
            sender, _ = await _register(uri, "P/author")
            notice = await read_until_type(sender, MessageType.DELIVERY_STATUS)
            assert notice["stage"] == "boundary_delivered"
            assert notice["notification_id"] == (f"delivery:{queued['operation_key']}:2")
        finally:
            await sender.close()
            await receiver.close()


@pytest.mark.real_hub
async def test_expired_queue_is_terminal_and_cannot_be_acknowledged(tmp_path: Path) -> None:
    """An elapsed deadline commits a distinct terminal outcome before executor ACK."""
    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(journal=store, hub_id="hub-1")) as (hub, uri):
        receiver, session = await _register(uri, "P/receiver", capabilities={"follow_up": "native"})
        sender, _ = await _register(uri, "P/author")
        try:
            assert session is not None
            request = _intent(session["incarnation"])
            request["deadline"] = time.time() + 0.2
            await sender.send(json.dumps(request))
            queued = await read_until_type(sender, MessageType.DELIVERY_STATUS)
            await read_until_type(receiver, MessageType.DELIVERY_OFFER)
            await asyncio.sleep(0.25)
            assert await expire_due_deliveries(hub) == 1
            expired = await read_until_type(sender, MessageType.DELIVERY_STATUS)
            assert expired["stage"] == "expired"
            await sender.send(
                json.dumps(
                    {
                        "sender": "P/author",
                        "type": MessageType.DELIVERY_STATUS_REQUEST,
                        "target": "System",
                        "protocol_version": 3,
                        "operation_key": queued["operation_key"],
                    }
                )
            )
            final_status = await read_until_type(sender, MessageType.DELIVERY_STATUS)
            assert not final_status["boundary_delivered"]
            assert not final_status["task_completed"]
            await _stage(
                receiver,
                msg_type=MessageType.DELIVERY_ACK,
                operation_key=queued["operation_key"],
                mutation_id="late-ack",
                evidence={"receipt_id": "late"},
            )
            refusal = await read_until_type(receiver, MessageType.DELIVERY_REFUSED)
            assert refusal["reason_code"] == "invalid_transition"
        finally:
            await sender.close()
            await receiver.close()


@pytest.mark.real_hub
async def test_new_process_supersedes_old_queued_intent(tmp_path: Path) -> None:
    """A new token under the same name cannot inherit an older process's offer."""
    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(journal=store, hub_id="hub-1")) as (_hub, uri):
        receiver, session = await _register(uri, "P/receiver", capabilities={"follow_up": "native"})
        sender, _ = await _register(uri, "P/author")
        try:
            assert session is not None
            await sender.send(json.dumps(_intent(session["incarnation"])))
            queued = await read_until_type(sender, MessageType.DELIVERY_STATUS)
            await read_until_type(receiver, MessageType.DELIVERY_OFFER)
            await receiver.close()
            receiver, replacement = await _register(
                uri,
                "P/receiver",
                capabilities={"follow_up": "native"},
                session_token="b" * 64,
            )
            assert replacement is not None
            assert replacement["incarnation"] != session["incarnation"]
            superseded = await read_until_type(sender, MessageType.DELIVERY_STATUS)
            assert superseded["stage"] == "superseded"
            record = store.delivery.get(queued["operation_key"])
            assert record is not None and record.stage == "superseded"
            stale = _intent(session["incarnation"])
            stale["request_id"] = "req-stale"
            stale["idempotency_key"] = "idem-stale"
            await sender.send(json.dumps(stale))
            refused = await read_until_type(sender, MessageType.DELIVERY_REFUSED)
            assert refused["reason_code"] == "stale_incarnation"
        finally:
            await sender.close()
            await receiver.close()


@pytest.mark.real_hub
async def test_duplicate_request_and_cancel_completion_race(tmp_path: Path) -> None:
    """Duplicates preserve identity; completion retains a racing cancel fact."""
    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(journal=store, hub_id="hub-1")) as (_hub, uri):
        receiver, session = await _register(uri, "P/receiver", capabilities={"follow_up": "native"})
        sender, _ = await _register(uri, "P/author")
        try:
            assert session is not None
            request = _intent(session["incarnation"])
            await sender.send(json.dumps(request))
            queued = await read_until_type(sender, MessageType.DELIVERY_STATUS)
            offer = await read_until_type(receiver, MessageType.DELIVERY_OFFER)
            await sender.send(json.dumps(request))
            replayed = await read_until_type(sender, MessageType.DELIVERY_STATUS)
            reoffered = await read_until_type(receiver, MessageType.DELIVERY_OFFER)
            assert replayed["operation_key"] == queued["operation_key"]
            assert reoffered["notification_id"] == offer["notification_id"]
            assert len(tuple(store.iter_events())) == 2
            altered = dict(request, body="Different body")
            await sender.send(json.dumps(altered))
            conflict = await read_until_type(sender, MessageType.DELIVERY_REFUSED)
            assert conflict["reason_code"] == "id_conflict"
            await _stage(
                receiver,
                msg_type=MessageType.DELIVERY_BOUNDARY,
                operation_key=offer["operation_key"],
                mutation_id="race-boundary",
                evidence={"boundary": "turn-1"},
            )
            await read_until_type(receiver, MessageType.DELIVERY_STATUS)
            await read_until_type(sender, MessageType.DELIVERY_STATUS)
            await _stage(
                receiver,
                msg_type=MessageType.DELIVERY_ACK,
                operation_key=offer["operation_key"],
                mutation_id="race-ack",
                evidence={"receipt_id": "local-1"},
            )
            await read_until_type(receiver, MessageType.DELIVERY_STATUS)
            await read_until_type(sender, MessageType.DELIVERY_STATUS)
            await sender.send(
                json.dumps(
                    {
                        "sender": "P/author",
                        "type": MessageType.DELIVERY_CANCEL,
                        "target": "System",
                        "protocol_version": 3,
                        "operation_key": offer["operation_key"],
                        "mutation_id": "race-cancel",
                    }
                )
            )
            cancel_status = await read_until_type(sender, MessageType.DELIVERY_STATUS)
            cancel_notice = await read_until_type(receiver, MessageType.DELIVERY_STATUS)
            assert cancel_status["cancel_requested"]
            assert cancel_status["stage"] == "acknowledged"
            assert cancel_notice["cancel_requested"]
            await _stage(
                receiver,
                msg_type=MessageType.DELIVERY_OUTCOME,
                operation_key=offer["operation_key"],
                mutation_id="race-outcome",
                stage="completed",
                evidence={"executor_ref": "run-1", "outcome_code": "success"},
            )
            completed = await read_until_type(receiver, MessageType.DELIVERY_STATUS)
            assert completed["stage"] == "completed"
            assert completed["cancel_requested"]
            assert len(tuple(store.iter_events())) == 6
        finally:
            await sender.close()
            await receiver.close()


@pytest.mark.real_hub
async def test_queued_cancellation_needs_recipient_terminal_confirmation(tmp_path: Path) -> None:
    """Sender cancellation stays pending until its exact executor confirms stop."""
    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(journal=store, hub_id="hub-1")) as (_hub, uri):
        receiver, session = await _register(uri, "P/receiver", capabilities={"follow_up": "native"})
        sender, _ = await _register(uri, "P/author")
        try:
            assert session is not None
            await sender.send(json.dumps(_intent(session["incarnation"])))
            queued = await read_until_type(sender, MessageType.DELIVERY_STATUS)
            await read_until_type(receiver, MessageType.DELIVERY_OFFER)
            await sender.send(
                json.dumps(
                    {
                        "sender": "P/author",
                        "type": MessageType.DELIVERY_CANCEL,
                        "target": "System",
                        "protocol_version": 3,
                        "operation_key": queued["operation_key"],
                        "mutation_id": "cancel-before-turn",
                    }
                )
            )
            pending = await read_until_type(sender, MessageType.DELIVERY_STATUS)
            await read_until_type(receiver, MessageType.DELIVERY_STATUS)
            assert pending["stage"] == "queued"
            assert pending["cancel_requested"]
            assert not pending["task_completed"]
            await _stage(
                receiver,
                msg_type=MessageType.DELIVERY_OUTCOME,
                operation_key=queued["operation_key"],
                mutation_id="cancel-confirmed",
                stage="cancelled",
                evidence={"reason_code": "sender_cancelled"},
            )
            terminal = await read_until_type(receiver, MessageType.DELIVERY_STATUS)
            assert terminal["stage"] == "cancelled"
            assert terminal["cancel_requested"]
            assert not terminal["boundary_delivered"]
        finally:
            await sender.close()
            await receiver.close()


@pytest.mark.real_hub
async def test_hub_restart_replays_committed_offer_before_ack(tmp_path: Path) -> None:
    """A fresh hub process recovers its queue and stable offer identity."""
    path = tmp_path / "hub.db"
    with EventStore(path) as first_store:
        async with running_hub(SynapseHub(journal=first_store, hub_id="hub-1")) as (_hub, uri):
            receiver, session = await _register(
                uri, "P/receiver", capabilities={"follow_up": "native"}
            )
            sender, _ = await _register(uri, "P/author")
            try:
                assert session is not None
                request = _intent(session["incarnation"])
                await sender.send(json.dumps(request))
                queued = await read_until_type(sender, MessageType.DELIVERY_STATUS)
                offered = await read_until_type(receiver, MessageType.DELIVERY_OFFER)
            finally:
                await sender.close()
                await receiver.close()
    with EventStore(path) as restored:
        async with running_hub(SynapseHub(journal=restored, hub_id="hub-1")) as (_hub, uri):
            receiver, resumed = await _register(
                uri, "P/receiver", capabilities={"follow_up": "native"}
            )
            try:
                assert resumed is not None
                assert resumed["incarnation"] == session["incarnation"]
                replayed = await read_until_type(receiver, MessageType.DELIVERY_OFFER)
                assert replayed["notification_id"] == offered["notification_id"]
                assert len(tuple(restored.iter_events())) == 2
                await _stage(
                    receiver,
                    msg_type=MessageType.DELIVERY_BOUNDARY,
                    operation_key=queued["operation_key"],
                    mutation_id="restart-boundary",
                    evidence={"boundary": "turn-after-restart"},
                )
                boundary = await read_until_type(receiver, MessageType.DELIVERY_STATUS)
                assert boundary["stage"] == "boundary_delivered"
                await _stage(
                    receiver,
                    msg_type=MessageType.DELIVERY_ACK,
                    operation_key=queued["operation_key"],
                    mutation_id="restart-ack",
                    evidence={"receipt_id": "local-after-restart"},
                )
                ack = await read_until_type(receiver, MessageType.DELIVERY_STATUS)
                assert ack["stage"] == "acknowledged"
                assert not ack["task_completed"]
            finally:
                await receiver.close()


@pytest.mark.real_hub
@pytest.mark.parametrize("grant_control", [False, True])
async def test_interrupt_requires_live_claim_and_explicit_control_grant(
    tmp_path: Path, grant_control: bool
) -> None:
    """Authenticated socket and ordinary message permission cannot imply steer authority."""
    rules = [
        AclRule(CLAIM, "claim", "*", "P", "task owner"),
        AclRule(CLAIM, "path", "*", "P", "task path"),
        AclRule(MESSAGE, "agent", "*", "P", "ordinary message"),
    ]
    if grant_control:
        rules.append(AclRule(DELIVERY_CONTROL, "agent", "P/receiver", "P", "task control"))
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(
        journal=store,
        hub_id="hub-1",
        authenticator=TokenAuthenticator(["test-token"]),
        acl_policy=AclPolicy(rules),
        require_acl=True,
    )
    async with running_hub(hub) as (_hub, uri):
        receiver, session = await _register(
            uri,
            "P/receiver",
            capabilities={"interrupt": "native"},
            auth_token="test-token",
        )
        sender, _ = await _register(uri, "P/author", auth_token="test-token")
        try:
            assert session is not None
            await receiver.send(
                json.dumps(
                    {
                        "sender": "P/receiver",
                        "type": MessageType.CLAIM,
                        "target": "System",
                        "task_id": "T-1",
                        "paths": ["src/task.py"],
                    }
                )
            )
            await read_until_type(receiver, MessageType.CLAIM_GRANTED)
            request = _intent(session["incarnation"], mode="interrupt")
            await sender.send(json.dumps(request))
            if grant_control:
                status = await read_until_type(sender, MessageType.DELIVERY_STATUS)
                offer = await read_until_type(receiver, MessageType.DELIVERY_OFFER)
                assert status["selected_mode"] == "interrupt"
                assert offer["quality"] == "native"
            else:
                refusal = await read_until_type(sender, MessageType.DELIVERY_REFUSED)
                assert refusal["reason_code"] == "unauthorised_requester"
                assert [event.kind for event in store.iter_events()] == ["claim"]
        finally:
            await sender.close()
            await receiver.close()


@pytest.mark.real_hub
async def test_third_party_cannot_read_cancel_or_confirm_another_delivery(tmp_path: Path) -> None:
    """Connection identity gates status, cancellation and recipient outcomes."""
    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(journal=store, hub_id="hub-1")) as (_hub, uri):
        receiver, session = await _register(uri, "P/receiver", capabilities={"follow_up": "native"})
        sender, _ = await _register(uri, "P/author")
        attacker, _ = await _register(uri, "P/attacker")
        try:
            assert session is not None
            await sender.send(json.dumps(_intent(session["incarnation"])))
            queued = await read_until_type(sender, MessageType.DELIVERY_STATUS)
            await read_until_type(receiver, MessageType.DELIVERY_OFFER)
            for msg_type, fields in (
                (MessageType.DELIVERY_STATUS_REQUEST, {}),
                (MessageType.DELIVERY_CANCEL, {"mutation_id": "attack-cancel"}),
                (
                    MessageType.DELIVERY_BOUNDARY,
                    {
                        "request_id": "req-1",
                        "task_id": "T-1",
                        "mutation_id": "attack-boundary",
                        "evidence": {"boundary": "forged"},
                    },
                ),
            ):
                await attacker.send(
                    json.dumps(
                        {
                            "sender": "P/attacker",
                            "type": msg_type,
                            "target": "System",
                            "protocol_version": 3,
                            "operation_key": queued["operation_key"],
                            **fields,
                        }
                    )
                )
                refused = await read_until_type(attacker, MessageType.DELIVERY_REFUSED)
                assert refused["reason_code"] == "unauthorised_requester"
            assert len(tuple(store.iter_events())) == 2
        finally:
            await attacker.close()
            await sender.close()
            await receiver.close()


@pytest.mark.real_hub
@pytest.mark.parametrize(
    "change",
    [
        {"operation_key": "bad"},
        {"request_id": "wrong"},
        {"task_id": "wrong"},
        {"mutation_id": ""},
        {"evidence": {}},
        {"evidence": {"unknown": "value"}},
        {"evidence": {"boundary": "invalid\nvalue"}},
        {"evidence": "not-an-object"},
        {"type": MessageType.DELIVERY_ACK, "evidence": {"boundary": "turn-1"}},
        {"type": MessageType.DELIVERY_OUTCOME, "stage": "unknown"},
        {
            "type": MessageType.DELIVERY_OUTCOME,
            "stage": "completed",
            "evidence": {"executor_ref": "run-1"},
        },
        {
            "type": MessageType.DELIVERY_OUTCOME,
            "stage": "completed",
            "evidence": {
                "executor_ref": "run-1",
                "outcome_code": "success",
                "request_id": "wrong",
            },
        },
    ],
)
async def test_malformed_recipient_evidence_cannot_advance_queue(
    tmp_path: Path, change: dict[str, Any]
) -> None:
    """Bad evidence never creates a boundary, ACK, or task outcome."""
    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(journal=store, hub_id="hub-1")) as (_hub, uri):
        receiver, session = await _register(uri, "P/receiver", capabilities={"follow_up": "native"})
        sender, _ = await _register(uri, "P/author")
        try:
            assert session is not None
            await sender.send(json.dumps(_intent(session["incarnation"])))
            queued = await read_until_type(sender, MessageType.DELIVERY_STATUS)
            await read_until_type(receiver, MessageType.DELIVERY_OFFER)
            malformed: dict[str, Any] = {
                "sender": "P/receiver",
                "type": MessageType.DELIVERY_BOUNDARY,
                "target": "System",
                "protocol_version": 3,
                "operation_key": queued["operation_key"],
                "request_id": "req-1",
                "task_id": "T-1",
                "mutation_id": "bad-evidence",
                "evidence": {"boundary": "turn-1"},
            }
            malformed.update(change)
            await receiver.send(json.dumps(malformed))
            refusal = await read_until_type(receiver, MessageType.DELIVERY_REFUSED)
            assert refusal["reason_code"] == "invalid_shape"
            assert len(tuple(store.iter_events())) == 2
        finally:
            await sender.close()
            await receiver.close()


@pytest.mark.real_hub
async def test_unavailable_recipient_and_unapproved_fallback_refuse(tmp_path: Path) -> None:
    """A missing process or capability cannot silently receive ordinary chat."""
    store = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(journal=store, hub_id="hub-1")) as (_hub, uri):
        sender, _ = await _register(uri, "P/author")
        try:
            await sender.send(json.dumps(_intent("a" * 64)))
            offline = await read_until_type(sender, MessageType.DELIVERY_REFUSED)
            assert offline["reason_code"] == "unavailable_recipient"
            receiver, session = await _register(
                uri, "P/receiver", capabilities={"next_turn": "emulated"}
            )
            try:
                assert session is not None
                request = _intent(session["incarnation"], mode="follow_up")
                request["allowed_fallbacks"] = []
                request["request_id"] = "req-unsupported"
                request["idempotency_key"] = "idem-unsupported"
                await sender.send(json.dumps(request))
                unsupported = await read_until_type(sender, MessageType.DELIVERY_REFUSED)
                assert unsupported["reason_code"] == "unsupported_mode"
                assert tuple(store.iter_events()) == ()
            finally:
                await receiver.close()
        finally:
            await sender.close()


@pytest.mark.real_hub
async def test_committed_queue_replays_when_hub_crashed_before_offer(tmp_path: Path) -> None:
    """An atomic pre-publication queue record is offered after a fresh hub start."""
    path = tmp_path / "hub.db"
    token = "a" * 64
    incarnation = hashlib.sha256(f"hub-1\0P/receiver\0{token}".encode()).hexdigest()
    intent = parse_delivery_intent(
        _intent(incarnation),
        sender="P/author",
        origin_hub="hub-1",
        now=time.time(),
    )
    offer = {
        "type": MessageType.DELIVERY_OFFER,
        "sender": "P/author",
        "origin_hub": "hub-1",
        "target": intent.target,
        "target_incarnation": incarnation,
        "operation_key": intent.operation_key,
        "notification_id": f"delivery:{intent.operation_key}:1",
        "request_id": intent.request_id,
        "task_id": intent.task_id,
        "selected_mode": "follow_up",
        "quality": "native",
        "deadline": intent.deadline,
        "body": intent.body,
        "protocol_version": 3,
    }
    with EventStore(path) as first:
        accepted = first.delivery.create(
            intent, selected_mode="follow_up", quality="native", offer=offer
        )
        assert accepted.record.stage == "queued"
    with EventStore(path) as restored:
        async with running_hub(SynapseHub(journal=restored, hub_id="hub-1")) as (_hub, uri):
            receiver, session = await _register(
                uri, "P/receiver", capabilities={"follow_up": "native"}
            )
            try:
                assert session is not None and session["incarnation"] == incarnation
                replayed = await read_until_type(receiver, MessageType.DELIVERY_OFFER)
                assert replayed["notification_id"] == offer["notification_id"]
                assert replayed["body"] == intent.body
                assert len(tuple(restored.iter_events())) == 2
            finally:
                await receiver.close()
