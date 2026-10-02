# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real released-journal ownership recovery
"""Recover actual journals emitted by the unmodified published Core 0.99.36."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.delivery_modes import DeliveryRefusal, DeliveryStage
from synapse_channel.core.event_row_mac import load_or_create_row_mac_key
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore

FIXTURE = Path(__file__).parent / "fixtures" / "delivery_legacy_core_09936.sql"


@pytest.fixture
def legacy_journal(tmp_path: Path) -> tuple[Path, str]:
    """Restore a hash-bound real release journal, including its refusal quarantine."""
    return _restore_legacy_journal(tmp_path, FIXTURE)


def _restore_legacy_journal(tmp_path: Path, fixture: Path) -> tuple[Path, str]:
    """Verify captured release provenance before restoring its exact SQLite dump."""
    provenance = json.loads(fixture.with_suffix(".json").read_text())
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == provenance["sql_sha256"]
    path = tmp_path / "hub.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(fixture.read_text())
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
    return path, str(provenance["operation_key"])


def test_local_legacy_recovery_keeps_the_proven_receiving_hub(tmp_path: Path) -> None:
    """Released local history refuses a new owner and retains immutable provenance."""
    fixture = FIXTURE.with_name("delivery_legacy_local_core_09936.sql")
    path, key = _restore_legacy_journal(tmp_path, fixture)
    with EventStore(path) as store:
        before = store.delivery.get(key)
        assert before is not None and before.receiving_hub == "workstation"
        assert before.storage_profile == 3
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                "SELECT receiving_hub FROM delivery_requests WHERE operation_key = ?", (key,)
            ).fetchone() == (None,)
        store.delivery.verify_origin_hub("workstation")
        with pytest.raises(DeliveryRefusal) as refusal:
            store.delivery.bind_legacy_receiving_hub(
                "different-hub", recovery_ref="operator-custody/local-wrong-owner"
            )
        assert refusal.value.code == "hub_identity_mismatch"
        assert store.delivery.get(key) == before and store.max_seq() == 2
        assert (
            store.delivery.bind_legacy_receiving_hub(
                "workstation", recovery_ref="operator-custody/local-proven-owner"
            )
            == 1
        )
    with EventStore(path) as recovered:
        recovered.delivery.verify_origin_hub("workstation")
        after = recovered.delivery.get(key)
        assert after is not None and after.receiving_hub == "workstation"
        assert after.storage_profile == 4 and after.stage == before.stage
        assert after.request == before.request and after.request_digest == before.request_digest
        assert after.operation_key == before.operation_key


def test_local_legacy_binding_cannot_change_owner_when_replayed(tmp_path: Path) -> None:
    """A tampered binding event cannot override the released local journal's origin."""
    fixture = FIXTURE.with_name("delivery_legacy_local_core_09936.sql")
    path, _ = _restore_legacy_journal(tmp_path, fixture)
    with EventStore(path) as store:
        assert (
            store.delivery.bind_legacy_receiving_hub(
                "workstation", recovery_ref="operator-custody/local-binding-tamper"
            )
            == 1
        )
    with sqlite3.connect(path) as connection:
        seq, raw = connection.execute(
            "SELECT seq, payload FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        payload = json.loads(raw)
        payload["receiving_hub"] = "different-hub"
        connection.execute(
            "UPDATE events SET payload = ? WHERE seq = ?", (json.dumps(payload), seq)
        )
    with pytest.raises(DeliveryRefusal) as refusal:
        EventStore(path)
    assert refusal.value.code == "replay_incompatible"


def test_recovery_refuses_origin_disagreement_admitted_by_the_old_release(
    tmp_path: Path,
) -> None:
    """The old public writer's inconsistent forwarded origin cannot gain ownership."""
    fixture = FIXTURE.with_name("delivery_legacy_bad_origin_core_09936.sql")
    path, key = _restore_legacy_journal(tmp_path, fixture)
    with EventStore(path) as store:
        before = store.delivery.get(key)
        assert before is not None and before.sender == "PROJ/alice@different-origin"
        assert before.request["origin_hub"] == "workstation"
        with pytest.raises(DeliveryRefusal) as refusal:
            store.delivery.bind_legacy_receiving_hub(
                "laptop", recovery_ref="operator-custody/inconsistent-origin-held"
            )
        assert refusal.value.code == "replay_incompatible"
        assert store.delivery.get(key) == before and store.max_seq() == 2
    with EventStore(path) as reopened:
        assert reopened.delivery.get(key) == before


def test_unbound_forwarded_legacy_journal_requires_explicit_recovery(
    legacy_journal: tuple[Path, str],
) -> None:
    """Neither the origin nor an arbitrary new receiver can claim legacy history."""
    path, key = legacy_journal
    with EventStore(path) as store:
        record = store.delivery.get(key)
        assert record is not None and record.receiving_hub is None
        for receiver in ("laptop", "workstation", "another-hub"):
            with pytest.raises(DeliveryRefusal) as refusal:
                store.delivery.verify_origin_hub(receiver)
            assert refusal.value.code == "receiving_hub_required"
        assert len(tuple(store.iter_events())) == 2


def test_binding_preserves_provenance_and_quarantine_across_reopen(
    legacy_journal: tuple[Path, str],
) -> None:
    """Offline binding adds evidence without silently retrying an old refusal."""
    path, key = legacy_journal
    with EventStore(path) as store:
        before = store.delivery.get(key)
        assert before is not None
        assert (
            store.delivery.bind_legacy_receiving_hub(
                "laptop", recovery_ref="operator-custody/recorded-two-hub-journal"
            )
            == 1
        )
        assert (
            store.delivery.bind_legacy_receiving_hub(
                "laptop", recovery_ref="operator-custody/recorded-two-hub-journal"
            )
            == 0
        )
        assert len(tuple(store.iter_events())) == 3
        assert store.delivery.due_for_expiry(float(before.request["deadline"]) + 1) == ()
    with EventStore(path) as recovered:
        recovered.delivery.verify_origin_hub("laptop")
        after = recovered.delivery.get(key)
        assert after is not None and after.storage_profile == 4 and after.receiving_hub == "laptop"
        assert after.request == before.request and after.request_digest == before.request_digest
        assert after.operation_key == before.operation_key and after.stage == before.stage
        with pytest.raises(DeliveryRefusal) as refusal:
            recovered.delivery.verify_origin_hub("workstation")
        assert refusal.value.code == "hub_identity_mismatch"


def test_recovery_cannot_rebind_an_existing_forwarded_owner(
    legacy_journal: tuple[Path, str],
) -> None:
    """A second recovery under another hub leaves the first binding unchanged."""
    path, key = legacy_journal
    with EventStore(path) as store:
        assert (
            store.delivery.bind_legacy_receiving_hub(
                "laptop", recovery_ref="operator-custody/original-receiver"
            )
            == 1
        )
        before = store.delivery.get(key)
        with pytest.raises(DeliveryRefusal) as refusal:
            store.delivery.bind_legacy_receiving_hub(
                "workstation", recovery_ref="operator-custody/wrong-receiver"
            )
        assert refusal.value.code == "hub_identity_mismatch"
        assert store.delivery.get(key) == before and store.max_seq() == 3


@pytest.mark.parametrize("refuse_write", [False, True])
def test_real_sqlite_cleanup_failure_preserves_the_committed_disposition(
    legacy_journal: tuple[Path, str],
    refuse_write: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An actual SQLite authorizer refusal preserves commit or rollback on reopen."""
    path, key = legacy_journal
    with EventStore(path) as store:

        def authorize(
            action: int,
            argument: str | None,
            value: str | None,
            database: str | None,
            trigger: str | None,
        ) -> int:
            """Reject the real cleanup PRAGMA and optionally the binding index write."""
            if action == sqlite3.SQLITE_PRAGMA and argument == "synchronous" and value == "NORMAL":
                return sqlite3.SQLITE_DENY
            if refuse_write and action == sqlite3.SQLITE_UPDATE and argument == "delivery_requests":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        store._conn.set_authorizer(authorize)
        try:
            if refuse_write:
                with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
                    store.delivery.bind_legacy_receiving_hub(
                        "laptop", recovery_ref="operator-custody/real-storage-refusal"
                    )
            else:
                assert (
                    store.delivery.bind_legacy_receiving_hub(
                        "laptop", recovery_ref="operator-custody/committed-cleanup-refusal"
                    )
                    == 1
                )
        finally:
            store._conn.set_authorizer(None)
        assert ("Could not restore SQLite synchronous=NORMAL" in caplog.text) is not refuse_write
    with EventStore(path) as reopened:
        record = reopened.delivery.get(key)
        assert record is not None
        assert record.receiving_hub == (None if refuse_write else "laptop")
        assert record.storage_profile == (3 if refuse_write else 4)
        assert reopened.max_seq() == (2 if refuse_write else 3)
        assert reopened.delivery.due_for_expiry(float(record.request["deadline"]) + 1) == ()
        reopened.delivery.verify_replay()


@pytest.mark.parametrize(
    "changes",
    [
        {"hub_id": ""},
        {"hub_id": "padded "},
        {"hub_id": "bad\nidentity"},
        {"hub_id": "x" * 513},
        {"hub_id": "\ud800"},
        {"recovery_ref": ""},
        {"recovery_ref": "x" * 1025},
        {"recovery_ref": "bad\nreference"},
        {"retry_authority_refusals": "true"},
    ],
)
def test_invalid_recovery_evidence_leaves_real_legacy_history_untouched(
    legacy_journal: tuple[Path, str],
    changes: dict[str, Any],
) -> None:
    """Malformed operator input refuses before any receiving ownership is written."""
    path, key = legacy_journal
    arguments: dict[str, Any] = {
        "hub_id": "laptop",
        "recovery_ref": "operator-custody/invalid-input-check",
        "retry_authority_refusals": False,
    }
    arguments.update(changes)
    with EventStore(path) as store:
        with pytest.raises(DeliveryRefusal) as refusal:
            store.delivery.bind_legacy_receiving_hub(**arguments)
        assert refusal.value.code == "invalid_shape"
        record = store.delivery.get(key)
        assert record is not None and record.receiving_hub is None
        assert len(tuple(store.iter_events())) == 2


@pytest.fixture
def authenticated_legacy_journal(legacy_journal: tuple[Path, str]) -> tuple[Path, str]:
    """Enable a real persisted row key after the recorded legacy event prefix."""
    path, key = legacy_journal
    with EventStore(path) as store:
        row_key = load_or_create_row_mac_key(
            path.with_suffix(".rowmac.key"),
            current_max_seq=store.max_seq(),
            log_has_macs=store.has_row_macs(),
        )
        assert store.enable_row_mac(row_key) == ()
        store.append(EventKind.CHAT, {"sender": "PROJ/operator", "payload": "Row key enabled."})
        assert store.has_row_macs()
    return path, key


def test_authenticated_legacy_recovery_requires_original_row_key(
    authenticated_legacy_journal: tuple[Path, str],
) -> None:
    """A restored journal must configure row authentication before binding writes."""
    path, _ = authenticated_legacy_journal
    with EventStore(path) as store:
        with pytest.raises(DeliveryRefusal) as refusal:
            store.delivery.bind_legacy_receiving_hub(
                "laptop", recovery_ref="operator-custody/mac-check"
            )
        assert refusal.value.code == "row_authentication_required"
        assert store.max_seq() == 3
        row_key = load_or_create_row_mac_key(
            path.with_suffix(".rowmac.key"),
            current_max_seq=store.max_seq(),
            log_has_macs=store.has_row_macs(),
        )
        assert store.enable_row_mac(row_key) == ()
        assert (
            store.delivery.bind_legacy_receiving_hub(
                "laptop", recovery_ref="operator-custody/mac-verified"
            )
            == 1
        )
    with EventStore(path) as recovered:
        assert recovered.enable_row_mac(row_key) == ()
        assert recovered.has_row_macs()
        recovered.delivery.verify_origin_hub("laptop")
        recovered.delivery.verify_replay()


@pytest.mark.parametrize("mac", [None, "0" * 64])
def test_legacy_recovery_refuses_unauthenticated_event_rows(
    authenticated_legacy_journal: tuple[Path, str],
    mac: str | None,
) -> None:
    """Corrupt row authentication cannot be cleared by delivery ownership recovery."""
    path, key = authenticated_legacy_journal
    with sqlite3.connect(path) as damaged:
        damaged.execute("UPDATE events SET mac = ? WHERE seq = 3", (mac,))
    with EventStore(path) as store:
        row_key = load_or_create_row_mac_key(
            path.with_suffix(".rowmac.key"),
            current_max_seq=store.max_seq(),
            log_has_macs=store.has_row_macs(),
        )
        assert store.enable_row_mac(row_key)
        with pytest.raises(DeliveryRefusal) as refusal:
            store.delivery.bind_legacy_receiving_hub(
                "laptop",
                recovery_ref="operator-custody/unauthenticated-history-held",
                retry_authority_refusals=True,
            )
        assert refusal.value.code == "journal_recovery_required"
        record = store.delivery.get(key)
        assert record is not None and record.receiving_hub is None
        assert store.max_seq() == 3


@pytest.mark.parametrize("stage", ["expired", "superseded"])
def test_origin_cannot_decide_or_replay_receiving_hub_transitions(
    legacy_journal: tuple[Path, str],
    stage: DeliveryStage,
) -> None:
    """Even a matching mutation retry must retain receiving-hub authority."""
    path, key = legacy_journal
    with EventStore(path) as store:
        store.delivery.bind_legacy_receiving_hub(
            "laptop", recovery_ref="operator-custody/actor-check"
        )
        for actor in ("workstation", "another-hub"):
            with pytest.raises(DeliveryRefusal) as refusal:
                store.delivery.advance(
                    key,
                    stage=stage,
                    mutation_id="receiving-decision",
                    mutation_digest="a" * 64,
                    actor=actor,
                    source="hub",
                    evidence={},
                )
            assert refusal.value.code == "unauthorised_requester"
        store.delivery.advance(
            key,
            stage=stage,
            mutation_id="receiving-decision",
            mutation_digest="a" * 64,
            actor="laptop",
            source="hub",
            evidence={},
        )
        with pytest.raises(DeliveryRefusal) as refusal:
            store.delivery.advance(
                key,
                stage=stage,
                mutation_id="receiving-decision",
                mutation_digest="a" * 64,
                actor="workstation",
                source="hub",
                evidence={},
            )
        assert refusal.value.code == "unauthorised_requester"
    with EventStore(path) as recovered:
        record = recovered.delivery.get(key)
        assert record is not None and record.stage == stage


@pytest.mark.real_hub
async def test_explicit_authority_retry_recovers_expiry_through_actual_hub_startup(
    legacy_journal: tuple[Path, str],
) -> None:
    """A cold-started receiving hub expires the recovered real legacy operation."""
    path, key = legacy_journal
    with EventStore(path) as store:
        assert (
            store.delivery.bind_legacy_receiving_hub(
                "laptop",
                recovery_ref="operator-custody/authority-refusal-reviewed",
                retry_authority_refusals=True,
            )
            == 1
        )
    with EventStore(path) as store:
        hub = SynapseHub(journal=store, hub_id="laptop")
        serving = asyncio.create_task(hub.serve("127.0.0.1", 0))
        try:
            await hub.wait_until_serving(timeout=5)
            for _ in range(250):
                record = store.delivery.get(key)
                assert record is not None
                if record.stage == "expired":
                    break
                await asyncio.sleep(0.02)
            assert record is not None and record.stage == "expired"
        finally:
            serving.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await serving
    with EventStore(path) as recovered:
        recovered.delivery.verify_origin_hub("laptop")
        record = recovered.delivery.get(key)
        assert record is not None and record.stage == "expired"
        assert record.request["origin_hub"] == "workstation"
        events = tuple(recovered.iter_events())
        assert events[2].payload["released_quarantine_reason"] == "unauthorised_requester"
        assert events[3].payload["actor"] == "laptop"


def test_other_quarantine_reasons_survive_explicit_authority_retry(
    legacy_journal: tuple[Path, str],
) -> None:
    """An authority recovery cannot release an unrelated integrity refusal."""
    path, key = legacy_journal
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE delivery_quarantine SET reason_code = 'replay_incompatible'")
    with EventStore(path) as store:
        assert (
            store.delivery.bind_legacy_receiving_hub(
                "laptop",
                recovery_ref="operator-custody/other-quarantine-held",
                retry_authority_refusals=True,
            )
            == 1
        )
        record = store.delivery.get(key)
        assert record is not None
        assert store.delivery.due_for_expiry(float(record.request["deadline"]) + 1) == ()
        assert tuple(store.iter_events())[-1].payload["released_quarantine_reason"] is None


def test_binding_storage_failure_rolls_back_owner_event_and_quarantine(
    legacy_journal: tuple[Path, str],
) -> None:
    """A real SQLite write refusal leaves the legacy recovery fully retryable."""
    path, key = legacy_journal
    with EventStore(path) as store:
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TRIGGER refuse_binding BEFORE UPDATE ON delivery_requests "
                "BEGIN SELECT RAISE(ABORT, 'storage refusal'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="storage refusal"):
            store.delivery.bind_legacy_receiving_hub(
                "laptop",
                recovery_ref="operator-custody/rollback-check",
                retry_authority_refusals=True,
            )
        record = store.delivery.get(key)
        assert record is not None and record.receiving_hub is None
        assert len(tuple(store.iter_events())) == 2
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT count(*) FROM delivery_quarantine").fetchone() == (1,)
            connection.execute("DROP TRIGGER refuse_binding")
        assert (
            store.delivery.bind_legacy_receiving_hub(
                "laptop",
                recovery_ref="operator-custody/rollback-retry",
                retry_authority_refusals=True,
            )
            == 1
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("receiving_hub", "changed-hub"),
        ("ordinal", 20),
        ("ordinal", 2.0),
        ("extra", "unexpected"),
        ("prior_stage", "completed"),
        ("recovery_ref", ""),
        ("profile", 3),
        ("released_quarantine_reason", "replay_incompatible"),
    ],
)
def test_replay_refuses_altered_binding_evidence(
    legacy_journal: tuple[Path, str],
    field: str,
    value: object,
) -> None:
    """The recovery event remains authoritative over the receiving-hub index."""
    path, _ = legacy_journal
    with EventStore(path) as store:
        store.delivery.bind_legacy_receiving_hub(
            "laptop", recovery_ref="operator-custody/tamper-check"
        )
    with sqlite3.connect(path) as connection:
        seq, raw = connection.execute(
            "SELECT seq, payload FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        payload = json.loads(raw)
        payload[field] = value
        connection.execute(
            "UPDATE events SET payload = ? WHERE seq = ?", (json.dumps(payload), seq)
        )
    with pytest.raises(DeliveryRefusal) as refusal:
        EventStore(path)
    assert refusal.value.code == "replay_incompatible"
