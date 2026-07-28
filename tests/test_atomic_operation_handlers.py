# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — all covered keyed mutation families use one operation commit

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.handlers import guard_evidence, leasing, offerings
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind, record_claim_denial
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.guard_evidence import guard_denial_digests


class _RecordingHub(SynapseHub):
    def __init__(self, store: EventStore, *, authenticated: bool = False) -> None:
        super().__init__(
            hub_id="atomic-test",
            journal=store,
            authenticator=TokenAuthenticator(["token"]) if authenticated else None,
            anti_rollback_checkpoint=False,
        )
        self.sent: list[dict[str, Any]] = []
        self.broadcasts: list[dict[str, Any]] = []
        self.legacy_remembered: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def _send_json(self, _websocket: Any, data: dict[str, Any]) -> None:
        self.sent.append(data)

    async def _broadcast(self, data: dict[str, Any]) -> None:
        self.broadcasts.append(data)

    def _remember(self, data: dict[str, Any], response: dict[str, Any]) -> None:
        self.legacy_remembered.append((data, response))


def _frame(sender: str, msg_type: str, key: str, **extra: Any) -> dict[str, Any]:
    return {"sender": sender, "type": msg_type, "idem_key": key, **extra}


async def test_all_state_families_commit_once_replay_and_resume(tmp_path: Path) -> None:
    db = tmp_path / "families.db"
    store = EventStore(db)
    hub = _RecordingHub(store)
    socket = object()

    claim = _frame("A", MessageType.CLAIM, "claim-1", task_id="T1", paths=["src/a.py"])
    await leasing.handle_claim(hub, "A", claim, socket)
    update = _frame(
        "A", MessageType.TASK_UPDATE, "update-1", task_id="T1", status="working", note="n"
    )
    await leasing.handle_task_update(hub, "A", update, socket)
    checkpoint = _frame(
        "A", MessageType.CHECKPOINT, "checkpoint-1", task_id="T1", checkpoint="resume"
    )
    await leasing.handle_checkpoint(hub, "A", checkpoint, socket)
    hub.agent_sockets["B"] = object()
    handoff = _frame(
        "A", MessageType.HANDOFF, "handoff-1", task_id="T1", to_agent="B", note="continue"
    )
    await leasing.handle_handoff(hub, "A", handoff, socket)
    release = _frame("B", MessageType.RELEASE, "release-1", task_id="T1")
    await leasing.handle_release(hub, "B", release, socket)
    resource = _frame("A", MessageType.RESOURCE, "resource-1", kind="gpu", name="local", capacity=2)
    await offerings.handle_resource(hub, "A", resource, socket)

    assert "T1" not in hub.state.claims
    assert "A:gpu:local" in hub.state.resources
    assert len(store.read_operations()) == 6
    assert hub.legacy_remembered == []
    assert store.pending_operation_outbox_count() == 0
    kinds = [event.kind for event in store.read_all()]
    for kind in (
        EventKind.CLAIM,
        EventKind.TASK_UPDATE,
        EventKind.CHECKPOINT,
        EventKind.HANDOFF,
        EventKind.RELEASE,
        EventKind.RESOURCE,
    ):
        assert kinds.count(kind) == 1
    assert kinds.count(EventKind.IDEMPOTENCY) == 6
    assert kinds.count(EventKind.LEDGER_PROGRESS) == 1

    before = len(store.read_all())
    await offerings.handle_resource(hub, "A", resource, socket)
    assert len(store.read_all()) == before
    assert hub.sent[-1]["type"] == MessageType.RESOURCE_OFFERED

    changed = {**resource, "capacity": 7}
    await offerings.handle_resource(hub, "A", changed, socket)
    assert len(store.read_all()) == before
    assert hub.sent[-1]["error_code"] == "idempotency_conflict"
    assert "resource-1" not in str(hub.sent[-1])

    store.close()
    reopened = EventStore(db)
    restarted = _RecordingHub(reopened)
    assert "T1" not in restarted.state.claims
    assert "A:gpu:local" in restarted.state.resources
    assert len(restarted.blackboard.progress) == 1
    await offerings.handle_resource(restarted, "A", resource, socket)
    assert restarted.sent[-1]["type"] == MessageType.RESOURCE_OFFERED
    assert len(reopened.read_all()) == before
    reopened.close()


async def test_guard_denial_response_sequence_is_exact_across_restart(tmp_path: Path) -> None:
    db = tmp_path / "guard.db"
    store = EventStore(db)
    hub = _RecordingHub(store, authenticated=True)
    socket = object()
    hub.clients.set_quota_principal(socket, "auth-token:test-principal")
    actor, call, scope = guard_denial_digests(
        provider="codex",
        identity="actor",
        session_id="session",
        tool_use_id="call",
        paths=["private/path.py"],
    )
    frame = _frame(
        "A",
        MessageType.GUARD_DENIAL,
        "guard-1",
        actor_sha256=actor,
        call_sha256=call,
        scope_sha256=scope,
        provider="codex",
        reason_code="GUARD_NO_CLAIM",
        path_count=1,
    )

    await guard_evidence.handle_guard_denial(hub, "A", frame, socket)
    first = hub.sent[-1]
    await guard_evidence.handle_guard_denial(hub, "A", frame, socket)
    assert hub.sent[-1] == first
    assert first["audit_seq"] == 1
    assert hub.legacy_remembered == []
    assert [event.kind for event in store.read_all()] == [
        EventKind.GUARD_DENIAL,
        EventKind.IDEMPOTENCY,
    ]

    store.close()
    reopened = EventStore(db)
    restarted = _RecordingHub(reopened, authenticated=True)
    restarted.clients.set_quota_principal(socket, "auth-token:test-principal")
    await guard_evidence.handle_guard_denial(restarted, "A", frame, socket)
    assert restarted.sent[-1] == first
    assert len(reopened.read_all()) == 2
    reopened.close()


async def test_cancellation_after_atomic_commit_publishes_the_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EventStore(tmp_path / "cancel.db")
    hub = _RecordingHub(store)
    started = threading.Event()
    finish = threading.Event()
    original = store.commit_operation

    def delayed(**kwargs: Any) -> Any:
        started.set()
        assert finish.wait(timeout=2)
        return original(**kwargs)

    monkeypatch.setattr(store, "commit_operation", delayed)
    frame = _frame("A", MessageType.CLAIM, "cancel-1", task_id="T1", paths=["src/a.py"])
    task = asyncio.create_task(leasing.handle_claim(hub, "A", frame, object()))
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert "T1" in hub.state.claims
    assert len(store.read_operations()) == 1
    assert store.pending_operation_outbox_count() == 1
    store.close()


async def test_cancellation_waits_for_keyed_denial_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EventStore(tmp_path / "deny-cancel.db")
    hub = _RecordingHub(store)
    assert hub.state.claim("B", "HELD", paths=["src/a.py"])[0]
    started = threading.Event()
    finish = threading.Event()
    original = record_claim_denial

    def delayed(*args: Any, **kwargs: Any) -> int:
        started.set()
        assert finish.wait(timeout=2)
        return int(original(*args, **kwargs))

    monkeypatch.setattr(leasing, "record_claim_denial", delayed)
    frame = _frame("A", MessageType.CLAIM, "deny-1", task_id="T1", paths=["src/a.py"])
    task = asyncio.create_task(leasing.handle_claim(hub, "A", frame, object()))
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert set(hub.state.claims) == {"HELD"}
    assert [event.kind for event in store.read_all()] == [EventKind.CLAIM_DENIAL]
    assert store.read_operations() == ()
    store.close()
