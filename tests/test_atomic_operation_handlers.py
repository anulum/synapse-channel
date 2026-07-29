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
from synapse_channel.core.handlers import (
    guard_evidence,
    leasing,
    memory,
    offerings,
    operator_relay,
    planning,
)
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind, record_claim_denial, record_operator_relay
from synapse_channel.core.operator_relay_wire import RelayActionRequest
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.guard_evidence import guard_denial_digests


class _RecordingHub(SynapseHub):
    def __init__(
        self,
        store: EventStore,
        *,
        authenticated: bool = False,
        max_findings_per_agent: int = 200,
    ) -> None:
        super().__init__(
            hub_id="atomic-test",
            journal=store,
            authenticator=TokenAuthenticator(["token"]) if authenticated else None,
            anti_rollback_checkpoint=False,
            max_findings_per_agent=max_findings_per_agent,
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


def _finding_frame(key: str, statement: str) -> dict[str, Any]:
    """Return one emit-gate-admissible keyed finding frame."""
    return _frame(
        "A",
        MessageType.FINDING,
        key,
        statement=statement,
        subkind="codebase-fact",
        evidence_kind="measured",
        claim_status="bounded-support",
        provenance={"project": "SYNAPSE-CHANNEL"},
        validity={"valid_from": None, "valid_to": None},
        verified_at_source={
            "checked_this_session": True,
            "source_ref": "tests/test_atomic_operation_handlers.py",
        },
    )


async def test_memory_families_commit_once_conflict_and_resume(tmp_path: Path) -> None:
    db = tmp_path / "memory-families.db"
    store = EventStore(db)
    hub = _RecordingHub(store, max_findings_per_agent=1)
    socket = object()
    recall = _frame(
        "A",
        MessageType.RECALL_LOG,
        "recall-1",
        query_text="what changed?",
        returned_claim_ids=["finding-1"],
        was_used=True,
        abstained=False,
    )
    finding = _finding_frame("finding-1", "atomic finding")

    await memory.handle_recall_log(hub, "A", recall, socket)
    await memory.handle_finding(hub, "A", finding, socket)
    first_recall = hub.sent[-1]
    first_finding = hub.broadcasts[-1]
    assert len(store.read_operations()) == 2
    assert [event.kind for event in store.read_all()] == [
        EventKind.RECALL,
        EventKind.IDEMPOTENCY,
        EventKind.FINDING,
        EventKind.IDEMPOTENCY,
    ]
    assert first_recall["seq"] == 1
    assert first_finding["seq"] == 3
    assert hub.legacy_remembered == []

    await memory.handle_recall_log(hub, "A", recall, socket)
    await memory.handle_finding(hub, "A", finding, socket)
    assert hub.sent[-2:] == [first_recall, first_finding]
    assert len(hub.broadcasts) == 1
    assert len(store.read_all()) == 4

    await memory.handle_recall_log(hub, "A", {**recall, "query_text": "changed"}, socket)
    await memory.handle_finding(hub, "A", {**finding, "statement": "changed"}, socket)
    assert [message["error_code"] for message in hub.sent[-2:]] == [
        "idempotency_conflict",
        "idempotency_conflict",
    ]
    assert "recall-1" not in str(hub.sent[-2])
    assert "finding-1" not in str(hub.sent[-1])

    store.close()
    reopened = EventStore(db)
    restarted = _RecordingHub(reopened)
    await memory.handle_recall_log(restarted, "A", recall, socket)
    await memory.handle_finding(restarted, "A", finding, socket)
    assert restarted.sent == [first_recall, first_finding]
    assert restarted.broadcasts == []
    assert len(reopened.read_all()) == 4
    reopened.close()


async def test_finding_cancellation_after_commit_publishes_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EventStore(tmp_path / "finding-cancel.db")
    hub = _RecordingHub(store, max_findings_per_agent=1)
    started = threading.Event()
    finish = threading.Event()
    original = store.commit_operation

    def delayed(**kwargs: Any) -> Any:
        started.set()
        assert finish.wait(timeout=2)
        return original(**kwargs)

    monkeypatch.setattr(store, "commit_operation", delayed)
    frame = _finding_frame("finding-cancel-1", "committed before cancellation")
    task = asyncio.create_task(memory.handle_finding(hub, "A", frame, object()))
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    admitted, reason = hub.reserve_finding_slot("A")
    assert admitted is False
    assert "quota" in reason
    assert len(store.read_operations()) == 1
    assert [event.kind for event in store.read_all()] == [
        EventKind.FINDING,
        EventKind.IDEMPOTENCY,
    ]
    assert store.pending_operation_outbox_count() == 1
    assert hub.broadcasts == []
    store.close()


async def test_keyed_finding_quota_refusal_creates_no_operation(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "finding-quota.db")
    hub = _RecordingHub(store, max_findings_per_agent=1)
    socket = object()
    await memory.handle_finding(hub, "A", _finding_frame("finding-1", "first"), socket)
    await memory.handle_finding(hub, "A", _finding_frame("finding-2", "second"), socket)

    assert len(store.read_operations()) == 1
    assert [event.kind for event in store.read_all()] == [
        EventKind.FINDING,
        EventKind.IDEMPOTENCY,
    ]
    assert hub.sent[-1]["type"] == MessageType.FINDING_REJECTED
    assert "quota" in hub.sent[-1]["payload"]
    assert [message["finding"]["statement"] for message in hub.broadcasts] == ["first"]
    store.close()


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


async def test_board_families_commit_once_conflict_and_resume(tmp_path: Path) -> None:
    db = tmp_path / "board-families.db"
    store = EventStore(db)
    hub = _RecordingHub(store)
    socket = object()
    declare = _frame(
        "A",
        MessageType.LEDGER_TASK,
        "board-declare-1",
        task_id="PLAN-1",
        title="Atomic board",
        project="SYNAPSE-CHANNEL",
    )
    update = _frame(
        "A",
        MessageType.LEDGER_TASK_UPDATE,
        "board-update-1",
        task_id="PLAN-1",
        status="in_progress",
        expected_version=1,
    )
    progress = _frame(
        "A",
        MessageType.LEDGER_PROGRESS,
        "board-progress-1",
        task_id="PLAN-1",
        kind="assessment",
        text="atomic boundary active",
    )

    await planning.handle_ledger_task(hub, "A", declare, socket)
    await planning.handle_ledger_task_update(hub, "A", update, socket)
    await planning.handle_ledger_progress(hub, "A", progress, socket)

    assert hub.blackboard.tasks["PLAN-1"].status == "in_progress"
    assert hub.blackboard.tasks["PLAN-1"].version == 2
    assert [note.text for note in hub.blackboard.progress] == ["atomic boundary active"]
    assert len(store.read_operations()) == 3
    kinds = [event.kind for event in store.read_all()]
    assert kinds.count(EventKind.LEDGER_TASK) == 2
    assert kinds.count(EventKind.LEDGER_PROGRESS) == 1
    assert kinds.count(EventKind.IDEMPOTENCY) == 3

    event_count = len(store.read_all())
    broadcasts = tuple(hub.broadcasts)
    await planning.handle_ledger_task(hub, "A", declare, socket)
    await planning.handle_ledger_task_update(hub, "A", update, socket)
    await planning.handle_ledger_progress(hub, "A", progress, socket)
    assert len(store.read_all()) == event_count
    assert tuple(hub.broadcasts) == broadcasts
    assert [message["type"] for message in hub.sent[-3:]] == [
        MessageType.LEDGER_TASK_POSTED,
        MessageType.LEDGER_TASK_UPDATED,
        MessageType.LEDGER_PROGRESS_POSTED,
    ]

    await planning.handle_ledger_task(
        hub,
        "A",
        {**declare, "title": "Changed payload"},
        socket,
    )
    assert hub.sent[-1]["error_code"] == "idempotency_conflict"
    assert "board-declare-1" not in str(hub.sent[-1])
    assert hub.blackboard.tasks["PLAN-1"].title == "Atomic board"
    assert len(store.read_all()) == event_count

    store.close()
    reopened = EventStore(db)
    restarted = _RecordingHub(reopened)
    assert restarted.blackboard.tasks["PLAN-1"].status == "in_progress"
    assert [note.text for note in restarted.blackboard.progress] == ["atomic boundary active"]
    await planning.handle_ledger_task_update(restarted, "A", update, socket)
    assert restarted.sent[-1]["type"] == MessageType.LEDGER_TASK_UPDATED
    assert len(reopened.read_all()) == event_count
    reopened.close()


async def test_board_actor_serializes_unkeyed_write_behind_atomic_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EventStore(tmp_path / "board-serialization.db")
    hub = _RecordingHub(store)
    started = threading.Event()
    finish = threading.Event()
    original = store.commit_operation

    def delayed(**kwargs: Any) -> Any:
        started.set()
        assert finish.wait(timeout=2)
        return original(**kwargs)

    monkeypatch.setattr(store, "commit_operation", delayed)
    keyed = _frame("A", MessageType.LEDGER_TASK, "board-keyed", task_id="KEYED", title="Keyed")
    unkeyed = {"sender": "B", "type": MessageType.LEDGER_TASK, "task_id": "PLAIN", "title": "Plain"}
    keyed_task = asyncio.create_task(planning.handle_ledger_task(hub, "A", keyed, object()))
    assert await asyncio.to_thread(started.wait, 1)
    unkeyed_task = asyncio.create_task(planning.handle_ledger_task(hub, "B", unkeyed, object()))
    await asyncio.sleep(0)
    assert not unkeyed_task.done()

    finish.set()
    await asyncio.gather(keyed_task, unkeyed_task)
    assert set(hub.blackboard.tasks) == {"KEYED", "PLAIN"}
    assert [event.kind for event in store.read_all()].count(EventKind.LEDGER_TASK) == 2
    store.close()


async def test_operator_relay_commits_once_and_replays_exact_verdict(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "operator-relay.db")
    hub = _RecordingHub(store)
    assert hub.state.claim("holder", "T1")[0]
    request = RelayActionRequest(
        action="release",
        namespace="SYNAPSE-CHANNEL",
        task_id="T1",
        operator="ops",
        origin_hub_id="origin",
        reason="wedged",
        idem_key="relay-1",
    )
    frame = _frame(
        "peer",
        MessageType.OPERATOR_RELAY_REQUEST,
        "relay-1",
        action=request.action,
        namespace=request.namespace,
        task_id=request.task_id,
        operator=request.operator,
        origin_hub_id=request.origin_hub_id,
        reason=request.reason,
        break_glass=request.break_glass,
    )

    inserted = await operator_relay._apply_release_atomic_async(hub, "peer", request, frame)
    replayed = await operator_relay._apply_release_atomic_async(hub, "peer", request, frame)

    assert inserted is not None and inserted.outcome == "inserted"
    assert replayed is not None and replayed.outcome == "replayed"
    assert replayed.response == inserted.response
    assert "T1" not in hub.state.claims
    assert len(store.read_operations()) == 1
    assert [event.kind for event in store.read_all()] == [
        EventKind.RELEASE,
        EventKind.OPERATOR_RELAY,
        EventKind.IDEMPOTENCY,
    ]

    changed = {**frame, "reason": "changed"}
    conflict = await operator_relay._apply_release_atomic_async(hub, "peer", request, changed)
    assert conflict is not None and conflict.outcome == "conflict"
    assert conflict.response is not None
    assert conflict.response["error_code"] == "idempotency_conflict"
    assert "relay-1" not in str(conflict.response)
    assert len(store.read_all()) == 3
    store.close()


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


async def test_operator_relay_cancellation_after_commit_publishes_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EventStore(tmp_path / "operator-relay-cancel.db")
    hub = _RecordingHub(store)
    assert hub.state.claim("holder", "T1")[0]
    started = threading.Event()
    finish = threading.Event()
    original = store.commit_operation

    def delayed(**kwargs: Any) -> Any:
        started.set()
        assert finish.wait(timeout=2)
        return original(**kwargs)

    monkeypatch.setattr(store, "commit_operation", delayed)
    request = RelayActionRequest(
        action="release",
        namespace="SYNAPSE-CHANNEL",
        task_id="T1",
        operator="ops",
        origin_hub_id="origin",
        idem_key="relay-cancel-1",
    )
    frame = _frame(
        "peer",
        MessageType.OPERATOR_RELAY_REQUEST,
        request.idem_key,
        action=request.action,
        namespace=request.namespace,
        task_id=request.task_id,
        operator=request.operator,
        origin_hub_id=request.origin_hub_id,
    )
    task = asyncio.create_task(
        operator_relay._apply_release_atomic_async(hub, "peer", request, frame)
    )
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert "T1" not in hub.state.claims
    assert len(store.read_operations()) == 1
    assert store.pending_operation_outbox_count() == 1
    store.close()


async def test_two_person_relay_cancellation_publishes_quorum_and_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EventStore(tmp_path / "two-person-relay-cancel.db")
    hub = _RecordingHub(store)
    assert hub.state.claim("holder", "T1")[0]
    first = RelayActionRequest(
        action="release",
        namespace="SYNAPSE-CHANNEL",
        task_id="T1",
        operator="alice",
        origin_hub_id="origin",
        idem_key="relay-requester-1",
    )
    first_frame = _frame(
        "peer",
        MessageType.OPERATOR_RELAY_REQUEST,
        first.idem_key,
        action=first.action,
        namespace=first.namespace,
        task_id=first.task_id,
        operator=first.operator,
        origin_hub_id=first.origin_hub_id,
    )
    await operator_relay._apply_with_two_person_atomic_async(
        hub,
        "peer",
        first,
        "federation-peer:first",
        first_frame,
    )
    assert hub.relay_approvals.pending_count == 1

    started = threading.Event()
    finish = threading.Event()
    original = store.commit_operation

    def delayed(**kwargs: Any) -> Any:
        started.set()
        assert finish.wait(timeout=2)
        return original(**kwargs)

    monkeypatch.setattr(store, "commit_operation", delayed)
    second = RelayActionRequest(
        action="release",
        namespace="SYNAPSE-CHANNEL",
        task_id="T1",
        operator="bob",
        origin_hub_id="origin",
        idem_key="relay-approver-1",
    )
    second_frame = _frame(
        "peer-approver",
        MessageType.OPERATOR_RELAY_REQUEST,
        second.idem_key,
        action=second.action,
        namespace=second.namespace,
        task_id=second.task_id,
        operator=second.operator,
        origin_hub_id=second.origin_hub_id,
    )
    task = asyncio.create_task(
        operator_relay._apply_with_two_person_atomic_async(
            hub,
            "peer-approver",
            second,
            "federation-peer:second",
            second_frame,
        )
    )
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert "T1" not in hub.state.claims
    assert hub.relay_approvals.pending_count == 0
    assert len(store.read_operations()) == 1
    assert store.pending_operation_outbox_count() == 1
    store.close()


async def test_two_person_actor_serializes_unkeyed_approval_behind_keyed_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EventStore(tmp_path / "two-person-relay-serialization.db")
    hub = _RecordingHub(store)
    assert hub.state.claim("holder", "T1")[0]
    started = threading.Event()
    finish = threading.Event()
    original = record_operator_relay

    def delayed(*args: Any, **kwargs: Any) -> Any:
        started.set()
        assert finish.wait(timeout=2)
        return original(*args, **kwargs)

    monkeypatch.setattr(operator_relay, "record_operator_relay", delayed)
    first = RelayActionRequest(
        action="release",
        namespace="SYNAPSE-CHANNEL",
        task_id="T1",
        operator="alice",
        origin_hub_id="origin",
        idem_key="relay-requester-1",
    )
    first_frame = _frame(
        "peer",
        MessageType.OPERATOR_RELAY_REQUEST,
        first.idem_key,
        action=first.action,
        namespace=first.namespace,
        task_id=first.task_id,
        operator=first.operator,
        origin_hub_id=first.origin_hub_id,
    )
    keyed = asyncio.create_task(
        operator_relay._apply_with_two_person_atomic_async(
            hub,
            "peer",
            first,
            "federation-peer:first",
            first_frame,
        )
    )
    assert await asyncio.to_thread(started.wait, 1)

    second = RelayActionRequest(
        action="release",
        namespace="SYNAPSE-CHANNEL",
        task_id="T1",
        operator="bob",
        origin_hub_id="origin",
    )
    unkeyed = asyncio.create_task(
        operator_relay._apply_with_two_person_async(
            hub,
            "peer-approver",
            second,
            "federation-peer:second",
        )
    )
    await asyncio.sleep(0)
    assert not unkeyed.done()
    finish.set()
    first_result, second_result = await asyncio.gather(keyed, unkeyed)

    assert first_result is not None and first_result.outcome == "uncommitted"
    assert second_result.applied is True
    assert "T1" not in hub.state.claims
    assert hub.relay_approvals.pending_count == 0
    assert [event.kind for event in store.read_all()] == [
        EventKind.OPERATOR_RELAY,
        EventKind.RELEASE,
        EventKind.OPERATOR_RELAY,
    ]
    store.close()


@pytest.mark.parametrize("family", ["release", "handoff"])
async def test_cancellation_after_commit_publishes_cross_subject_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    family: str,
) -> None:
    store = EventStore(tmp_path / f"{family}-cross-subject.db")
    hub = _RecordingHub(store)
    assert hub.state.claim("A", "T1")[0]
    started = threading.Event()
    finish = threading.Event()
    original = store.commit_operation

    def delayed(**kwargs: Any) -> Any:
        started.set()
        assert finish.wait(timeout=2)
        return original(**kwargs)

    monkeypatch.setattr(store, "commit_operation", delayed)
    if family == "release":
        frame = _frame(
            "A",
            MessageType.RELEASE,
            "cancel-cross-subject",
            task_id="T1",
            evidence=["focused cancellation proof passed"],
        )
        task = asyncio.create_task(leasing.handle_release(hub, "A", frame, object()))
    else:
        hub.agent_sockets["B"] = object()
        frame = _frame(
            "A",
            MessageType.HANDOFF,
            "cancel-cross-subject",
            task_id="T1",
            to_agent="B",
            note="continue",
        )
        task = asyncio.create_task(leasing.handle_handoff(hub, "A", frame, object()))

    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    if family == "release":
        assert "T1" not in hub.state.claims
    else:
        assert hub.state.claims["T1"].owner == "B"
    assert len(hub.blackboard.progress) == 1
    assert hub.blackboard.progress[0].task_id == "T1"
    kinds = [event.kind for event in store.read_all()]
    assert EventKind.LEDGER_PROGRESS in kinds
    assert EventKind.IDEMPOTENCY in kinds
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
