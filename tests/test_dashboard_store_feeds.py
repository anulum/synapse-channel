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
from synapse_channel.core.journal import EventKind, record_claim, record_release
from synapse_channel.core.merkle import proof_from_json, verify_inclusion
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import TaskClaim
from synapse_channel.dashboard_store_feeds import (
    DEFAULT_EVENTS_LIMIT,
    MAX_EVENTS_LIMIT,
    build_causality_feed,
    build_events_tail,
    build_federation_feed,
    build_health_anomalies_feed,
    build_merkle_proof_feed,
    build_metrics_feed,
    build_state_at_feed,
    latest_cursor,
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


class TestLatestCursor:
    def test_latest_cursor_is_the_logs_highest_sequence(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        assert latest_cursor(db) == 5

    def test_empty_log_starts_at_zero(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        EventStore(db).close()

        assert latest_cursor(db) == 0

    def test_missing_store_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing event store"):
            latest_cursor(tmp_path / "absent.db")


class TestCausalityAbsenceNotes:
    def test_recorded_but_graphless_event_says_so(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)
        store = EventStore(db)
        store.append("chat", {"sender": "P", "text": "hello"}, ts=6.0)
        store.close()

        document = build_causality_feed(db, direction="causes", seq=6)

        assert document["present"] is False
        assert "outside the coordination causal graph" in str(document["note"])

    def test_truly_absent_sequence_says_so(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        document = build_causality_feed(db, direction="causes", seq=999)

        assert document["present"] is False
        assert document["note"] == "no event recorded at this sequence"

    def test_present_answers_carry_no_note(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        document = build_causality_feed(db, direction="causes", seq=3)

        assert document["present"] is True
        assert "note" not in document


class TestMetricsFeed:
    def test_counts_totals_kinds_and_windows(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        base = 100000.0
        store.append(EventKind.CHAT, {"m": 1}, ts=base)  # outside both windows
        store.append(EventKind.CLAIM, {"m": 2}, ts=base + 90000.0)  # inside day only
        store.append(EventKind.CHAT, {"m": 3}, ts=base + 176000.0)  # inside hour
        store.append(EventKind.CHAT, {"m": 4}, ts=base + 176400.0)  # last event
        store.close()

        document = build_metrics_feed(db)

        log = document["log"]
        assert isinstance(log, dict)
        assert log["total_events"] == 4
        assert log["max_seq"] == 4
        assert log["first_ts"] == base
        assert log["last_ts"] == base + 176400.0
        assert document["events_by_kind"] == {"chat": 3, "claim": 1}
        windows = document["windows"]
        assert isinstance(windows, dict)
        assert windows["last_hour"] == {"events": 2, "by_kind": {"chat": 2}}
        assert windows["last_day"] == {"events": 3, "by_kind": {"chat": 2, "claim": 1}}
        assert "hub's own /metrics" in str(document["note"])

    def test_empty_store_is_all_zero_not_an_error(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        EventStore(db).close()

        document = build_metrics_feed(db)

        log = document["log"]
        assert isinstance(log, dict)
        assert log == {"total_events": 0, "max_seq": 0, "first_ts": None, "last_ts": None}
        assert document["events_by_kind"] == {}
        windows = document["windows"]
        assert isinstance(windows, dict)
        assert windows["last_hour"] == {"events": 0, "by_kind": {}}

    def test_missing_store_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing event store"):
            build_metrics_feed(tmp_path / "absent.db")

    def test_document_is_deterministic_over_a_given_log(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        store.append(EventKind.CHAT, {"m": 1}, ts=500.0)
        store.close()

        assert build_metrics_feed(db) == build_metrics_feed(db)


def _replayable_claim(**overrides: object) -> TaskClaim:
    base: dict[str, object] = {
        "task_id": "T1",
        "owner": "alice",
        "note": "",
        "claimed_at": 1000.0,
        "lease_expires_at": 1_000_000_000_000.0,
        "status": "claimed",
        "data_ref": "",
        "worktree": "w",
        "paths": (),
        "epoch": 1,
    }
    base.update(overrides)
    return TaskClaim(**base)  # type: ignore[arg-type]


def _task_ids(document: dict[str, object]) -> list[str]:
    state = document["state"]
    assert isinstance(state, dict)
    claims = state["active_claims"]
    assert isinstance(claims, list)
    task_ids: list[str] = []
    for claim in claims:
        assert isinstance(claim, dict)
        task_ids.append(str(claim["task_id"]))
    return sorted(task_ids)


class TestStateAtFeed:
    def _seed(self, db: Path) -> None:
        store = EventStore(db)
        record_claim(store, _replayable_claim(task_id="T1", owner="alice"))  # seq 1
        record_claim(store, _replayable_claim(task_id="T2", owner="bob"))  # seq 2
        record_release(store, "T1")  # seq 3
        store.close()

    def test_reconstructs_claims_as_of_a_seq(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        self._seed(db)

        at1 = build_state_at_feed(db, seq=1)
        at2 = build_state_at_feed(db, seq=2)
        at3 = build_state_at_feed(db, seq=3)

        assert _task_ids(at1) == ["T1"]  # only T1 claimed yet
        assert _task_ids(at2) == ["T1", "T2"]  # both claimed
        assert _task_ids(at3) == ["T2"]  # T1 released

    def test_carries_as_of_and_log_end(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        self._seed(db)

        doc = build_state_at_feed(db, seq=2)
        assert doc["as_of_seq"] == 2
        assert doc["log_end_seq"] == 3
        assert "board" in doc
        assert "presence/roster is not journalled" in str(doc["note"])

    def test_seq_is_clamped_into_range(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        self._seed(db)

        assert build_state_at_feed(db, seq=999)["as_of_seq"] == 3  # clamped to log end
        assert build_state_at_feed(db, seq=-5)["as_of_seq"] == 0  # clamped to 0
        assert _task_ids(build_state_at_feed(db, seq=0)) == []  # before any event

    def test_is_deterministic(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        self._seed(db)
        assert build_state_at_feed(db, seq=2) == build_state_at_feed(db, seq=2)

    def test_missing_store_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing event store"):
            build_state_at_feed(tmp_path / "absent.db", seq=1)


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
