# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real-hub tests of the record-only native-message verb
"""Record native vendor messages on real hubs over real client connections."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.asyncio.client import connect

from hub_e2e_helpers import collect_available, read_until_type, running_hub, send_json
from synapse_channel.core.acl import EVIDENCE, AclPolicy, AclRule
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.handlers.native_message import native_message_quota
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind, record_claim, replay
from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageReplayCache,
    sign_event_frame,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import TaskClaim

SENDER = "GROUP-A/claude-aaaa"
RECIPIENT = "GROUP-B/claude-bbbb"
TEXT = "Direct message — správa č. 1.\nSecond line."
TOKEN = "native-record-token"


def _frame(*, recorder: str = SENDER, idem_key: str = "nm-1", **changes: Any) -> dict[str, Any]:
    encoded = TEXT.encode("utf-8")
    frame: dict[str, Any] = {
        "sender": recorder,
        "target": "System",
        "type": "native_message_record",
        "payload": "",
        "idem_key": idem_key,
        "channel": "claude_cross_session",
        "direction": "sent",
        "phase": "outcome",
        "outcome": "queued",
        "sender_seat": SENDER,
        "recipient_seat": RECIPIENT,
        "sender_native_session": "11111111-2222-4333-8444-555555555555",
        "native_message_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        "sent_at": "2026-10-05T09:50:22.130036Z",
        "text_sha256": hashlib.sha256(encoded).hexdigest(),
        "text_bytes": len(encoded),
        "text": TEXT,
    }
    frame.update(changes)
    return frame


def _events(store: EventStore, kind: str = EventKind.NATIVE_MESSAGE) -> list[dict[str, Any]]:
    return [event.payload for event in store.read_all() if event.kind == kind]


def _kinds(store: EventStore) -> set[str]:
    return {event.kind for event in store.read_all()}


async def _exchange(websocket: Any, frame: dict[str, Any]) -> dict[str, Any]:
    """Send one frame on an open connection and return the hub's verdict."""
    await websocket.send(json.dumps(frame))
    for _ in range(20):
        reply = json.loads(await websocket.recv())
        if reply.get("type") in {"native_message_recorded", "native_message_rejected", "error"}:
            return dict(reply)
    raise AssertionError("the hub did not answer the record")


async def _record(
    uri: str, *frames: dict[str, Any], token: str | None = None
) -> list[dict[str, Any]]:
    """Send the frames of one recorder over one connection; return the verdicts.

    The hub lets one connection own a name, so a recorder that writes several
    records keeps its connection instead of reconnecting under the same name.
    """
    async with connect(uri) as websocket:
        if token is not None:
            await send_json(
                websocket,
                sender=frames[0]["sender"],
                type="heartbeat",
                payload="online",
                token=token,
            )
        await read_until_type(websocket, "welcome")
        return [await _exchange(websocket, frame) for frame in frames]


async def test_open_durable_hub_records_the_message_and_delivers_nothing(tmp_path: Path) -> None:
    db = tmp_path / "native.db"
    store = EventStore(db)
    hub = SynapseHub(hub_id="native-test", journal=store)
    before = time.time()
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as recipient:
            await read_until_type(recipient, "welcome")
            await send_json(recipient, sender=RECIPIENT, type="heartbeat", payload="online")
            await collect_available(recipient)

            (recorded,) = await _record(uri, _frame())

            await send_json(recipient, sender=RECIPIENT, type="who_request")
            seen = [await read_until_type(recipient, "who_snapshot")]
            seen += await collect_available(recipient)

    assert recorded["type"] == "native_message_recorded"
    assert recorded["audit_seq"] > 0
    assert recorded["text_sha256"] == hashlib.sha256(TEXT.encode("utf-8")).hexdigest()
    assert (recorded["phase"], recorded["recorder_binding"]) == ("outcome", "socket_name")
    assert all(TEXT not in json.dumps(frame, ensure_ascii=False) for frame in seen)
    assert not [frame for frame in seen if str(frame.get("type", "")).startswith("native_")]

    (event,) = _events(store)
    assert event["recorder"] == SENDER
    assert event["recorder_binding"] == "socket_name"
    assert before <= event["recorded_at"] <= time.time()
    assert event["text"] == TEXT
    assert event["recipient_seat"] == RECIPIENT
    assert event["native_message_id"] == "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    assert "idem_key" not in event
    assert "sender" not in event
    assert _kinds(store) <= {EventKind.NATIVE_MESSAGE, EventKind.IDEMPOTENCY}
    stored_seq = next(e.seq for e in store.read_all() if e.kind == EventKind.NATIVE_MESSAGE)
    assert recorded["audit_seq"] == stored_seq
    store.close()

    reopened = EventStore(db)
    try:
        assert len(_events(reopened)) == 1
        result = replay(reopened)
        assert not result.state.claims
        restarted = SynapseHub(hub_id="native-test", journal=reopened)
        assert restarted.journal_corrupt_rows == ()
    finally:
        reopened.close()


async def test_repeated_key_replays_once_and_changed_content_conflicts(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "duplicate.db")
    hub = SynapseHub(hub_id="native-test", journal=store)
    async with running_hub(hub) as (_, uri):
        first, second, changed, other_key = await _record(
            uri,
            _frame(),
            _frame(),
            _frame(outcome="refused"),
            _frame(idem_key="nm-2", phase="attempt", outcome=None),
        )

    assert first["type"] == second["type"] == "native_message_recorded"
    assert second["audit_seq"] == first["audit_seq"]
    assert changed["type"] == "error"
    assert changed["error_code"] == "idempotency_conflict"
    assert other_key["type"] == "native_message_recorded"
    assert other_key["audit_seq"] != first["audit_seq"]
    assert [event["phase"] for event in _events(store)] == ["outcome", "attempt"]
    store.close()


async def test_only_the_seat_on_its_own_side_may_record(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "side.db")
    hub = SynapseHub(hub_id="native-test", journal=store)
    stranger = "GROUP-C/claude-cccc"
    async with running_hub(hub) as (_, uri):
        (foreign,) = await _record(uri, _frame(recorder=stranger))
        (receiver,) = await _record(uri, _frame(recorder=RECIPIENT, direction="received"))
        wrong_side, unresolved, partner_unknown = await _record(
            uri,
            _frame(direction="received"),
            _frame(sender_seat=None),
            _frame(idem_key="nm-3", recipient_seat=None),
        )

    for refused in (foreign, wrong_side, unresolved):
        assert refused["type"] == "native_message_rejected"
        assert refused["error_code"] == "native_record_not_own_side"
    assert receiver["type"] == partner_unknown["type"] == "native_message_recorded"
    assert [(e["recorder"], e["direction"]) for e in _events(store)] == [
        (RECIPIENT, "received"),
        (SENDER, "sent"),
    ]
    store.close()


async def test_schema_and_missing_key_are_refused_without_an_event(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "refused.db")
    hub = SynapseHub(hub_id="native-test", journal=store)
    async with running_hub(hub) as (_, uri):
        mismatch, vocabulary, no_key = await _record(
            uri,
            _frame(text=TEXT + " changed"),
            _frame(channel="telepathy"),
            _frame(idem_key=""),
        )

    assert mismatch["type"] == vocabulary["type"] == no_key["type"] == "native_message_rejected"
    assert mismatch["error_code"] == "native_record_text_mismatch"
    assert vocabulary["error_code"] == "native_record_invalid"
    assert no_key["error_code"] == "native_record_idem_key_required"
    assert _events(store) == []
    store.close()


async def test_hub_without_a_journal_refuses_the_record() -> None:
    async with running_hub(SynapseHub(hub_id="native-memory")) as (_, uri):
        (refused,) = await _record(uri, _frame())

    assert refused["type"] == "native_message_rejected"
    assert refused["error_code"] == "native_record_unavailable"
    assert refused["payload"] == "native message records require a durable hub"


async def test_failed_journal_write_is_reported_and_nothing_is_claimed(tmp_path: Path) -> None:
    db = tmp_path / "locked.db"
    store = EventStore(db)
    hub = SynapseHub(hub_id="native-test", journal=store)
    writer = sqlite3.connect(db, timeout=0.1)
    try:
        async with running_hub(hub) as (_, uri):
            async with connect(uri) as websocket:
                await read_until_type(websocket, "welcome")
                writer.execute("BEGIN IMMEDIATE")
                await websocket.send(json.dumps(_frame()))
                refused = await read_until_type(websocket, "native_message_rejected", timeout=30.0)
                writer.rollback()
    finally:
        writer.close()

    assert refused["error_code"] == "native_record_unavailable"
    assert _events(store) == []
    store.close()


async def test_token_authenticated_recorder_is_marked_auth_token(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "secured.db")
    hub = SynapseHub(
        hub_id="native-secured", authenticator=TokenAuthenticator([TOKEN]), journal=store
    )
    async with running_hub(hub) as (_, uri):
        (recorded,) = await _record(uri, _frame(), token=TOKEN)

    assert recorded["type"] == "native_message_recorded"
    assert recorded["recorder_binding"] == "auth_token"
    (event,) = _events(store)
    assert event["recorder_binding"] == "auth_token"
    assert TOKEN not in json.dumps(event)
    store.close()


async def test_signed_registration_is_marked_identity_proof(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    key = EventSignatureKey.from_private_key(
        key_id="k", private_key=private_key, senders=frozenset({SENDER})
    )
    bundle = EventSignatureTrustBundle(
        keys={"k": key},
        replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=64),
    )
    store = EventStore(tmp_path / "bound.db")
    hub = SynapseHub(
        hub_id="native-bound",
        identity_trust_bundle=bundle,
        require_identity_binding=True,
        journal=store,
    )
    registration = sign_event_frame(
        {"sender": SENDER, "type": "heartbeat", "target": "System", "payload": "online"},
        key_id="k",
        private_key=private_key,
        nonce="reg-1",
        sequence=1,
        signed_at=time.time(),
    )
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps(registration))
            await websocket.send(json.dumps(_frame()))
            recorded = await read_until_type(websocket, "native_message_recorded")

    assert recorded["recorder_binding"] == "identity_proof"
    (event,) = _events(store)
    assert event["recorder_binding"] == "identity_proof"
    store.close()


async def test_record_ingress_is_rate_limited_per_principal(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "quota.db")
    hub = SynapseHub(hub_id="native-test", journal=store)
    quota = native_message_quota(hub)
    assert native_message_quota(hub) is quota
    quota.max_events = 1
    async with running_hub(hub) as (_, uri):
        first, second = await _record(
            uri, _frame(), _frame(idem_key="nm-2", phase="attempt", outcome=None)
        )

    assert first["type"] == "native_message_recorded"
    assert second["type"] == "native_message_rejected"
    assert second["error_code"] == "native_record_rate_limited"
    assert len(_events(store)) == 1
    store.close()


async def test_retry_of_a_stored_record_is_replayed_without_spending_quota(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "retry.db")
    hub = SynapseHub(hub_id="native-test", journal=store)
    native_message_quota(hub).max_events = 1
    async with running_hub(hub) as (_, uri):
        first, retry, third = await _record(uri, _frame(), _frame(), _frame())

    assert first["type"] == retry["type"] == third["type"] == "native_message_recorded"
    assert retry["audit_seq"] == third["audit_seq"] == first["audit_seq"]
    assert len(_events(store)) == 1
    store.close()


async def test_enforced_acl_gates_the_verb_on_the_native_message_evidence_target(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "acl.db")
    policy = AclPolicy(
        [
            AclRule(EVIDENCE, "evidence", "native-message", "GROUP-A", "may record its messages"),
            AclRule(EVIDENCE, "evidence", "guard-denial", "GROUP-B", "another evidence target"),
        ]
    )
    hub = SynapseHub(hub_id="native-acl", journal=store, acl_policy=policy, require_acl=True)
    async with running_hub(hub) as (_, uri):
        (allowed,) = await _record(uri, _frame())
        (denied,) = await _record(uri, _frame(recorder=RECIPIENT, direction="received"))

    assert allowed["type"] == "native_message_recorded"
    assert denied["type"] == "error"
    assert denied["acl_decision"] == "would_deny"
    assert [event["recorder"] for event in _events(store)] == [SENDER]
    store.close()


async def test_record_is_refused_while_the_journal_needs_recovery(tmp_path: Path) -> None:
    db = tmp_path / "recovery.db"
    store = EventStore(db)
    record_claim(
        store,
        TaskClaim(
            task_id="SEED",
            owner="seed-owner",
            note="seed",
            claimed_at=1000.0,
            lease_expires_at=9_999_999_999.0,
            status="claimed",
            data_ref="",
            worktree="wt",
            paths=("src",),
            epoch=1,
        ),
    )
    damaged_seq = store.max_seq()
    store.close()
    writer = sqlite3.connect(db)
    writer.execute("UPDATE events SET payload = 'not json' WHERE seq = ?", (damaged_seq,))
    writer.commit()
    writer.close()
    reopened = EventStore(db)
    hub = SynapseHub(hub_id="native-recovery", journal=reopened)
    async with running_hub(hub) as (_, uri):
        (refused,) = await _record(uri, _frame())

    assert refused["type"] == "error"
    assert refused["journal_recovery_required"] is True
    assert refused["first_corrupt_seq"] == damaged_seq
    assert _events(reopened) == []
    reopened.close()
