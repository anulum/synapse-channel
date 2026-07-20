# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synapse_channel.core.aef_emission import AefReceiptLog
from synapse_channel.core.aef_legacy_mapping import AEF_MAPPED_EVENT_KINDS
from synapse_channel.core.aef_outbox import AefOutboxError, drain_aef_outbox
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.receipt_signing import ReceiptSigningKey, receipt_key_id


def _key() -> ReceiptSigningKey:
    private = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return ReceiptSigningKey(key_id=receipt_key_id(public), private_key=private)


def _claim(task_id: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "owner": "agent-1",
        "lease_expires_at": 1_783_944_000.0,
        "epoch": 1,
        "paths": [],
    }


def _store(path: Path) -> EventStore:
    return EventStore(path, aef_outbox_kinds=AEF_MAPPED_EVENT_KINDS)


def _append_claim(store: EventStore, task_id: str) -> int:
    return store.append(
        EventKind.CLAIM,
        _claim(task_id),
        ts=1_783_940_400.0,
        durable=True,
    )


def test_supported_rows_enqueue_atomically_and_unmapped_rows_do_not(tmp_path: Path) -> None:
    store = _store(tmp_path / "hub.db")
    claim_seq = _append_claim(store, "task-1")
    chat_seq = store.append(EventKind.CHAT, {"payload": "legacy only"})

    assert [event.seq for event in store.pending_aef_events()] == [claim_seq]
    assert store.aef_delivery(claim_seq) is None
    assert store.aef_delivery(chat_seq) is None
    store.close()


def test_batch_queues_supported_rows_in_legacy_order(tmp_path: Path) -> None:
    store = _store(tmp_path / "hub.db")
    sequences = store.append_batch(
        (
            (EventKind.CLAIM, _claim("task-1")),
            (EventKind.CHAT, {"payload": "legacy only"}),
            (EventKind.CLAIM, _claim("task-2")),
        ),
        durable=True,
    )

    assert [event.seq for event in store.pending_aef_events()] == [sequences[0], sequences[2]]
    store.close()


def test_drain_emits_and_marks_each_receipt(tmp_path: Path) -> None:
    path = tmp_path / "hub.db"
    store = _store(path)
    first = _append_claim(store, "task-1")
    second = _append_claim(store, "task-2")
    with AefReceiptLog(path, hub_id="hub.example", signing_key=_key()) as log:
        assert drain_aef_outbox(store, log) == 2
        receipts = log.read_all()

    assert store.pending_aef_events() == ()
    assert store.aef_delivery(first) == receipts[0]["receipt_id"]
    assert store.aef_delivery(second) == receipts[1]["receipt_id"]
    store.close()


def test_crash_after_emit_recovers_without_duplicate_receipt(tmp_path: Path) -> None:
    path = tmp_path / "hub.db"
    key = _key()
    store = _store(path)
    legacy_seq = _append_claim(store, "task-1")

    def crash_after_emit(_event: object, _receipt: object) -> None:
        raise RuntimeError("simulated process death boundary")

    with AefReceiptLog(path, hub_id="hub.example", signing_key=key) as log:
        with pytest.raises(RuntimeError, match="process death"):
            drain_aef_outbox(store, log, after_emit=crash_after_emit)
        assert log.count() == 1
        assert store.pending_aef_events()[0].seq == legacy_seq
    store.close()

    restarted = _store(path)
    with AefReceiptLog(path, hub_id="hub.example", signing_key=key) as log:
        assert drain_aef_outbox(restarted, log) == 1
        assert log.count() == 1
        receipt = log.receipt_for_legacy_seq(legacy_seq)
        assert receipt is not None
        assert restarted.aef_delivery(legacy_seq) == receipt["receipt_id"]
    restarted.close()


def test_existing_receipt_mismatch_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "hub.db"
    store = _store(path)
    legacy_seq = _append_claim(store, "task-1")
    with AefReceiptLog(path, hub_id="hub.example", signing_key=_key()) as log:
        log.append(
            receipt_type="lease",
            action="grant",
            actor_id="other-agent",
            subject={
                "task_id": "different",
                "epoch": 1,
                "lease_expires_at": 1_783_944_000_000,
            },
            issued_at=1_783_940_400_000,
            decision="allow",
            legacy_seq=legacy_seq,
        )
        with pytest.raises(AefOutboxError, match="conflicts"):
            drain_aef_outbox(store, log)

    assert store.pending_aef_events()[0].seq == legacy_seq
    store.close()


def test_queued_unmapped_kind_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path, aef_outbox_kinds={EventKind.CHAT})
    store.append(EventKind.CHAT, {"payload": "not an AEF mapping"})
    with AefReceiptLog(path, hub_id="hub.example", signing_key=_key()) as log:
        with pytest.raises(AefOutboxError, match="has no AEF mapping"):
            drain_aef_outbox(store, log)
    store.close()


def test_recovered_receipt_without_identity_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "hub.db"
    store = _store(path)
    legacy_seq = _append_claim(store, "task-1")

    def crash(_event: object, _receipt: object) -> None:
        raise RuntimeError("simulated crash")

    with AefReceiptLog(path, hub_id="hub.example", signing_key=_key()) as log:
        with pytest.raises(RuntimeError, match="simulated crash"):
            drain_aef_outbox(store, log, after_emit=crash)
        receipt = log.receipt_for_legacy_seq(legacy_seq)
        assert receipt is not None
        receipt.pop("receipt_id")
        monkeypatch.setattr(log, "receipt_for_legacy_seq", lambda _seq: receipt)
        with pytest.raises(AefOutboxError, match="stable identity"):
            drain_aef_outbox(store, log)
    store.close()


def test_delivery_binding_is_idempotent_but_not_rebindable(tmp_path: Path) -> None:
    store = _store(tmp_path / "hub.db")
    legacy_seq = _append_claim(store, "task-1")
    store.mark_aef_delivered(legacy_seq, "aef1:first")
    store.mark_aef_delivered(legacy_seq, "aef1:first")

    assert store.aef_delivery(legacy_seq) == "aef1:first"
    with pytest.raises(ValueError, match="another receipt"):
        store.mark_aef_delivered(legacy_seq, "aef1:second")
    with pytest.raises(KeyError, match="not queued"):
        store.mark_aef_delivered(legacy_seq + 100, "aef1:missing")
    store.close()


def test_compaction_cannot_delete_pending_source_but_may_delete_after_delivery(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hub.db"
    store = _store(path)
    legacy_seq = _append_claim(store, "task-1")

    assert store.delete([legacy_seq]) == 0
    assert [event.seq for event in store.pending_aef_events()] == [legacy_seq]

    with AefReceiptLog(path, hub_id="hub.example", signing_key=_key()) as log:
        assert drain_aef_outbox(store, log) == 1
        receipt = log.receipt_for_legacy_seq(legacy_seq)
    assert receipt is not None
    assert store.delete([legacy_seq]) == 1
    assert store.aef_delivery(legacy_seq) == receipt["receipt_id"]
    store.close()


def test_maintenance_reopen_without_route_flag_still_protects_pending_source(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hub.db"
    with _store(path) as writer:
        legacy_seq = _append_claim(writer, "task-1")

    with EventStore(path) as maintenance:
        assert [event.seq for event in maintenance.pending_aef_events()] == [legacy_seq]
        assert maintenance.delete([legacy_seq]) == 0

    with _store(path) as restarted:
        assert [event.seq for event in restarted.pending_aef_events()] == [legacy_seq]


@pytest.mark.parametrize("limit", [0, True, 10_001])
def test_pending_limit_is_bounded(tmp_path: Path, limit: object) -> None:
    store = _store(tmp_path / "hub.db")
    with pytest.raises(ValueError, match="outbox limit"):
        store.pending_aef_events(limit=limit)  # type: ignore[arg-type]
    store.close()


def test_disabled_outbox_is_a_noop(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    seq = _append_claim(store, "task-1")

    assert store.pending_aef_events() == ()
    assert store.aef_delivery(seq) is None
    store.close()
