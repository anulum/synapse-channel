# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard federation and integrity feed builder regressions

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard_store_feeds_helpers import _seed_log
from synapse_channel.core.federation import FederationPeer
from synapse_channel.core.federation_store import (
    FederationRecord,
    FederationStoreError,
    PeerProvenance,
    save_store,
)
from synapse_channel.core.federation_wire import bundle_fingerprint
from synapse_channel.core.journal import (
    EventKind,
)
from synapse_channel.core.merkle import proof_from_json, verify_inclusion
from synapse_channel.core.persistence import EventStore
from synapse_channel.dashboard_store_feeds import (
    build_federation_feed,
    build_health_anomalies_feed,
    build_merkle_proof_feed,
)


def _peer(domain: str, *, revoked: bool = False, expires_at: float | None = None) -> FederationPeer:
    return FederationPeer(
        domain_id=domain,
        namespaces=frozenset({f"{domain}/shared"}),
        certificate_pins=frozenset({f"sha256:{domain}"}),
        signing_key_ids=frozenset({f"{domain}-key"}),
        revoked=revoked,
        expires_at=expires_at,
    )


def _record(peer: FederationPeer, *, imported_at: float) -> FederationRecord:
    return FederationRecord(
        peer=peer,
        provenance=PeerProvenance(
            source=f"ws://{peer.domain_id}:8876",
            imported_at=imported_at,
            confirmed_by="ops",
        ),
    )


class TestFederationFeed:
    def test_peerings_carry_state_provenance_and_fingerprint(self, tmp_path: Path) -> None:
        store = tmp_path / "federation.json"
        active = _peer("atelier.example", expires_at=900.0)
        revoked = _peer("mallory.example", revoked=True)
        expired = _peer("stale.example", expires_at=50.0)
        save_store(
            store,
            [
                _record(active, imported_at=10.0),
                _record(revoked, imported_at=11.0),
                _record(expired, imported_at=12.0),
            ],
        )

        document = build_federation_feed(store, clock=lambda: 100.0)

        listed = document["peerings"]
        assert isinstance(listed, list)
        peerings = {item["domain"]: item for item in listed}
        assert peerings["atelier.example"]["state"] == "active"
        assert peerings["mallory.example"]["state"] == "revoked"
        assert peerings["stale.example"]["state"] == "expired"
        assert peerings["atelier.example"]["imported_at"] == 10.0
        assert peerings["atelier.example"]["imported_age_days"] == pytest.approx(90.0 / 86_400.0)
        assert peerings["atelier.example"]["confirmed_by"] == "ops"
        assert peerings["atelier.example"]["fingerprint"] == bundle_fingerprint(active)
        assert peerings["atelier.example"]["rotation_state"] == "steady"
        assert peerings["atelier.example"]["expires_in_days"] == pytest.approx(800.0 / 86_400.0)
        assert peerings["atelier.example"]["expiry_note"] == "in 0.0d"

    def test_namespace_outcomes_are_absent_with_the_reason_stated(self, tmp_path: Path) -> None:
        store = tmp_path / "federation.json"
        save_store(store, [_record(_peer("atelier.example"), imported_at=1.0)])

        document = build_federation_feed(store, clock=lambda: 100.0)

        assert document["namespaces"] == []
        assert "hub-runtime state" in str(document["note"])

    def test_empty_store_yields_an_empty_peering_list(self, tmp_path: Path) -> None:
        document = build_federation_feed(tmp_path / "absent.json", clock=lambda: 0.0)

        assert document["peerings"] == []

    def test_corrupt_store_is_refused(self, tmp_path: Path) -> None:
        store = tmp_path / "federation.json"
        store.write_text("{not json", encoding="utf-8")

        with pytest.raises(FederationStoreError):
            build_federation_feed(store, clock=lambda: 0.0)


class TestMerkleProofFeed:
    def test_proof_is_present_and_verifies(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)  # five events, seq 1..5

        document = build_merkle_proof_feed(db, seq=3)

        assert document["present"] is True
        assert document["seq"] == 3
        assert document["tree_size"] == 5
        assert isinstance(document["path"], list)
        # The proof round-trips through the client-side verifier the cockpit's
        # verify button uses: the row is committed to the attested tree root.
        assert verify_inclusion(proof_from_json(document)) is True

    def test_absent_seq_is_present_false_not_a_fabricated_proof(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)  # only seq 1..5 exist

        document = build_merkle_proof_feed(db, seq=99)

        assert document == {
            "present": False,
            "seq": 99,
            "note": "no event at that sequence in the committed log",
        }

    def test_missing_store_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing event store"):
            build_merkle_proof_feed(tmp_path / "absent.db", seq=1)

    def test_document_is_deterministic_over_a_given_log(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)
        assert build_merkle_proof_feed(db, seq=2) == build_merkle_proof_feed(db, seq=2)


def _seed_claim(db: Path, *, task: str, owner: str, ts: float) -> None:
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": task, "owner": owner, "status": "claimed", "paths": ["src/x"], "worktree": "w"},
        ts=ts,
    )
    store.close()


def _stale_task_ids(document: dict[str, object]) -> list[str]:
    stale = document["stale"]
    assert isinstance(stale, list)
    task_ids: list[str] = []
    for item in stale:
        assert isinstance(item, dict)
        task_ids.append(str(item["task_id"]))
    return task_ids


class TestHealthAnomaliesFeed:
    def test_flags_an_orphaned_claim(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_claim(db, task="X", owner="bob", ts=1.0)  # a claim that is its task's last event

        document = build_health_anomalies_feed(db)

        assert document["present"] is True
        assert isinstance(document["anomaly_count"], int)
        assert document["anomaly_count"] >= 1
        orphaned = document["orphaned"]
        assert isinstance(orphaned, list)
        assert [item["task_id"] for item in orphaned] == ["X"]

    def test_stale_after_controls_the_stale_signal(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        store.append(
            EventKind.CLAIM,
            {"task_id": "X", "owner": "bob", "status": "claimed", "paths": ["s"], "worktree": "w"},
            ts=1.0,
        )
        # A far-later event advances the log's final timestamp, so X has aged.
        store.append(
            EventKind.CLAIM, {"task_id": "Y", "owner": "amy", "status": "claimed"}, ts=5000.0
        )
        store.close()

        lenient = build_health_anomalies_feed(db, stale_after=10_000.0)
        strict = build_health_anomalies_feed(db, stale_after=100.0)

        assert _stale_task_ids(lenient) == []  # within the window
        assert "X" in _stale_task_ids(strict)  # aged past the window

    def test_is_deterministic(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_claim(db, task="X", owner="bob", ts=1.0)
        assert build_health_anomalies_feed(db) == build_health_anomalies_feed(db)

    def test_missing_store_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing event store"):
            build_health_anomalies_feed(tmp_path / "absent.db")
