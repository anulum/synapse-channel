# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard store-feed builder regressions

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.causality import causality_to_json, run_causality
from synapse_channel.core.federation import FederationPeer
from synapse_channel.core.federation_store import (
    FederationRecord,
    FederationStoreError,
    PeerProvenance,
    save_store,
)
from synapse_channel.core.federation_wire import bundle_fingerprint
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.dashboard_store_feeds import (
    DEFAULT_EVENTS_LIMIT,
    MAX_EVENTS_LIMIT,
    build_causality_feed,
    build_events_tail,
    build_federation_feed,
    resolve_task_last_seq,
)


def _seed_log(db: Path) -> None:
    """Five events across two tasks: A claimed→released, X claimed twice."""
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "A", "owner": "alice", "status": "claimed", "paths": [], "worktree": "w"},
        ts=1.0,
    )
    store.append(
        EventKind.TASK_UPDATE,
        {"task_id": "A", "owner": "alice", "status": "working", "paths": [], "worktree": "w"},
        ts=2.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "A"}, ts=3.0)
    store.append(
        EventKind.CLAIM,
        {"task_id": "X", "owner": "bob", "status": "claimed", "paths": [], "worktree": "w"},
        ts=4.0,
    )
    store.append(
        EventKind.TASK_UPDATE,
        {"task_id": "X", "owner": "bob", "status": "working", "paths": [], "worktree": "w"},
        ts=5.0,
    )
    store.close()


class TestEventsTail:
    def test_tail_returns_events_past_the_cursor_with_real_seq_and_ts(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        document = build_events_tail(db, since=3)

        events = document["events"]
        assert isinstance(events, list)
        assert [event["seq"] for event in events] == [4, 5]
        assert [event["ts"] for event in events] == [4.0, 5.0]
        assert events[0]["kind"] == EventKind.CLAIM
        assert events[0]["payload"]["task_id"] == "X"
        assert document["next_cursor"] == 5

    def test_limit_bounds_the_batch_and_cursor_resumes_it(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        first = build_events_tail(db, since=0, limit=2)
        first_events = first["events"]
        assert isinstance(first_events, list)
        assert [event["seq"] for event in first_events] == [1, 2]
        assert first["next_cursor"] == 2

        second = build_events_tail(db, since=int(str(first["next_cursor"])), limit=2)
        second_events = second["events"]
        assert isinstance(second_events, list)
        assert [event["seq"] for event in second_events] == [3, 4]

    def test_caught_up_tail_is_empty_and_keeps_the_cursor(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        document = build_events_tail(db, since=99)

        assert document["events"] == []
        assert document["next_cursor"] == 99

    def test_limit_is_clamped_to_the_ceiling_and_floor(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        floored_events = build_events_tail(db, since=0, limit=0)["events"]
        assert isinstance(floored_events, list)
        assert len(floored_events) == 1

        ceiling_events = build_events_tail(db, since=0, limit=MAX_EVENTS_LIMIT * 100)["events"]
        assert isinstance(ceiling_events, list)
        assert len(ceiling_events) == 5

    def test_default_limit_is_the_documented_value(self) -> None:
        assert DEFAULT_EVENTS_LIMIT == 200
        assert MAX_EVENTS_LIMIT == 1000

    def test_missing_store_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing event store"):
            build_events_tail(tmp_path / "absent.db")


class TestTaskResolver:
    def test_resolves_the_tasks_most_recent_event(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        assert resolve_task_last_seq(db, "A") == 3
        assert resolve_task_last_seq(db, "X") == 5

    def test_unrecorded_task_resolves_to_none(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        assert resolve_task_last_seq(db, "GHOST") is None

    def test_missing_store_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing event store"):
            resolve_task_last_seq(tmp_path / "absent.db", "A")


class TestCausalityFeed:
    def test_seq_anchor_mirrors_the_cli_json_exactly(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        document = build_causality_feed(db, direction="causes", seq=3)

        assert document == causality_to_json(run_causality(db, "causes", 3))

    def test_task_anchor_resolves_to_the_last_event(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        by_task = build_causality_feed(db, direction="effects", task="A")
        by_seq = build_causality_feed(db, direction="effects", seq=3)

        assert by_task == by_seq

    def test_unknown_task_is_refused_not_invented(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        with pytest.raises(ValueError, match="no recorded event for task 'GHOST'"):
            build_causality_feed(db, direction="causes", task="GHOST")

    def test_exactly_one_anchor_is_required(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        with pytest.raises(ValueError, match="exactly one of seq and task"):
            build_causality_feed(db, direction="causes")
        with pytest.raises(ValueError, match="exactly one of seq and task"):
            build_causality_feed(db, direction="causes", seq=1, task="A")

    def test_only_causes_and_effects_are_served(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        with pytest.raises(ValueError, match="unknown causality direction"):
            build_causality_feed(db, direction="counterfactual", seq=1)


def _peer(domain: str, *, revoked: bool = False, expires_at: float | None = None) -> FederationPeer:
    return FederationPeer(
        domain_id=domain,
        namespaces=frozenset({f"{domain}/shared"}),
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
        assert peerings["atelier.example"]["confirmed_by"] == "ops"
        assert peerings["atelier.example"]["fingerprint"] == bundle_fingerprint(active)

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
