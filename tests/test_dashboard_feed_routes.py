# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard store-feed HTTP route tests

"""Tests for the dashboard's store-backed feed HTTP routes (read side)."""

from __future__ import annotations

import json
from pathlib import Path

from dashboard_helpers import _authorized_get, _feeds_server, _http_get
from synapse_channel.core.journal import EventKind, record_ledger_task, record_operator_relay
from synapse_channel.core.ledger import LedgerTask
from synapse_channel.core.merkle import proof_from_json, verify_inclusion
from synapse_channel.core.persistence import EventStore
from synapse_channel.dashboard import DashboardServer, start_dashboard_server
from synapse_channel.participants.session_metric_note import (
    SESSION_METRIC_NOTE_KIND,
    format_session_metric_note,
)
from synapse_channel.participants.session_telemetry import SessionMetrics


def _reliability_server(reliability_db: Path | None, **overrides: str) -> DashboardServer:
    """Start a dashboard against an unreachable hub — the reliability feed
    reads the durable store, so it must serve even when the hub is down."""
    return start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://127.0.0.1:1",
        name="SYNAPSE-CHANNEL/dashboard",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=5,
        allow_non_loopback=False,
        reliability_db=reliability_db,
        dashboard_token=overrides.get("dashboard_token"),
    )


def test_dashboard_reliability_endpoint_reports_absence_without_a_store() -> None:
    server = _reliability_server(None)
    try:
        status, content_type, body = _authorized_get(server, "/reliability.json")
    finally:
        server.close()

    assert status == 404
    assert content_type == "text/plain"
    assert "--reliability-db" in body


def test_dashboard_reliability_endpoint_serves_the_report_with_the_hub_down(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "T", "owner": "alice", "status": "claimed", "paths": [], "worktree": "w"},
        ts=1.0,
    )
    store.close()

    server = _reliability_server(db)
    try:
        status, content_type, body = _authorized_get(server, "/reliability.json")
    finally:
        server.close()

    assert status == 200
    assert content_type == "application/json"
    payload = json.loads(body)
    assert payload["note"] == "audit signals, not scores"
    assert "owners" in payload and "findings" in payload


_MALFORMED_FEED_QUERIES = (
    "seq=abc",
    "seq=",
    "seq=-1",
    "seq=1.5",
    "seq=99999999999999999999999999",
    "seq=0x10",
    "seq=%00",
    "seq[]=1",
    "seq=1&seq=2",
    "task=",
    "task=%ff%fe",
    "direction=sideways",
    "direction=",
    "limit=-5",
    "limit=abc",
    "limit=999999999999",
    "=noname",
    "&&&",
    "seq=" + "9" * 4096,
    "task=" + "A" * 8192,
)
"""Hostile query strings thrown at every store feed by the fuzz test below."""


def test_dashboard_feed_queries_never_crash_on_malformed_input(tmp_path: Path) -> None:
    # The store feeds parse an untrusted query string (?seq=, ?task=, ?direction=,
    # ?limit=). No input may crash the handler into a 500: every response must be a
    # deliberate status. This fuzzs each feed with hostile queries and asserts the
    # handler always answers 200 (parsed), 400 (rejected), 404 (absent anchor), or
    # 503 (store error) — never an unhandled 500.
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {
            "task_id": "T",
            "owner": "alice",
            "status": "claimed",
            "paths": [],
            "worktree": "w",
            "claimed_at": 1.0,
            "lease_expires_at": 61.0,
        },
        ts=1.0,
    )
    store.close()

    feeds = (
        "/state-at.json",
        "/merkle-proof.json",
        "/events.json",
        "/causality.json",
        "/receipts.json",
        "/postmortem.json",
    )
    server = _reliability_server(db)
    try:
        for feed in feeds:
            for raw_query in _MALFORMED_FEED_QUERIES:
                status, _, _ = _authorized_get(server, f"{feed}?{raw_query}")
                assert status in {200, 400, 404, 503}, f"{feed}?{raw_query} -> {status}"
    finally:
        server.close()


def test_dashboard_reliability_endpoint_fails_visible_on_a_missing_store(
    tmp_path: Path,
) -> None:
    server = _reliability_server(tmp_path / "absent.db")
    try:
        status, content_type, body = _authorized_get(server, "/reliability.json")
    finally:
        server.close()

    assert status == 503
    assert content_type == "text/plain"
    assert "missing event store" in body


def test_dashboard_reliability_endpoint_requires_the_dashboard_token(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hub.db"
    EventStore(db).close()

    server = _reliability_server(db, dashboard_token="secret")
    try:
        denied_status, _, _ = _authorized_get(server, "/reliability.json", unauthenticated=True)
        allowed_status, _, allowed_body = _http_get(
            server.url("/reliability.json"), authorization="Bearer secret"
        )
    finally:
        server.close()

    assert denied_status == 401
    assert allowed_status == 200
    assert json.loads(allowed_body)["note"] == "audit signals, not scores"


def _seed_feed_store(db: Path) -> None:
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "T", "owner": "alice", "status": "claimed", "paths": [], "worktree": "w"},
        ts=1.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "T"}, ts=2.0)
    store.close()


def test_events_feed_reports_absence_without_a_store() -> None:
    server = _feeds_server()
    try:
        status, _, body = _authorized_get(server, "/events.json")
    finally:
        server.close()

    assert status == 404
    assert "--feeds-db" in body


def test_events_feed_serves_the_tail_with_the_hub_down(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)

    server = _feeds_server(reliability_db=db)
    try:
        status, content_type, body = _authorized_get(server, "/events.json?since=1&limit=5")
    finally:
        server.close()

    assert status == 200
    assert content_type == "application/json"
    payload = json.loads(body)
    assert [event["seq"] for event in payload["events"]] == [2]
    assert payload["events"][0]["ts"] == 2.0
    assert payload["next_cursor"] == 2


def test_events_feed_refuses_malformed_numbers(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)

    server = _feeds_server(reliability_db=db)
    try:
        status, _, body = _authorized_get(server, "/events.json?since=abc")
        bad_history, _, history_body = _authorized_get(server, "/events.json?since=0&history=1")
    finally:
        server.close()

    assert status == 400
    assert "must be an integer or 'latest'" in body
    assert bad_history == 400
    assert "only with since=latest" in history_body


def test_events_feed_fails_visible_on_a_missing_store(tmp_path: Path) -> None:
    server = _feeds_server(reliability_db=tmp_path / "absent.db")
    try:
        status, _, body = _authorized_get(server, "/events.json")
    finally:
        server.close()

    assert status == 503
    assert "missing event store" in body


def test_postmortem_feed_serves_task_evidence_with_the_hub_down(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)

    server = _feeds_server(reliability_db=db)
    try:
        status, content_type, body = _authorized_get(server, "/postmortem.json?task=T")
    finally:
        server.close()

    assert status == 200
    assert content_type == "application/json"
    payload = json.loads(body)
    assert payload["present"] is True
    assert [event["kind"] for event in payload["timeline"]] == ["claim", "release"]


def _seed_session_note(db: Path) -> None:
    store = EventStore(db)
    note = format_session_metric_note(
        SessionMetrics(
            turns=3,
            errors=0,
            abstentions=0,
            input_tokens=200,
            output_tokens=40,
            cost_usd=0.25,
            total_latency_seconds=4.0,
            max_rate_limit_utilisation=None,
            last_input_tokens=120,
        )
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {"kind": SESSION_METRIC_NOTE_KIND, "text": note, "author": "alpha", "task_id": "s1"},
        ts=1.0,
    )
    store.close()


def test_sessions_feed_reports_absence_without_a_store() -> None:
    server = _feeds_server()
    try:
        status, _, body = _authorized_get(server, "/sessions.json")
    finally:
        server.close()

    assert status == 404
    assert "--feeds-db" in body


def test_sessions_feed_serves_the_report_with_the_hub_down(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_session_note(db)

    server = _feeds_server(reliability_db=db)
    try:
        status, content_type, body = _authorized_get(server, "/sessions.json")
    finally:
        server.close()

    assert status == 200
    assert content_type == "application/json"
    payload = json.loads(body)
    assert [record["agent"] for record in payload["sessions"]] == ["alpha"]
    assert payload["sessions"][0]["seq"] == 1  # the causality-join anchor
    assert payload["sessions"][0]["cost_usd"] == 0.25
    assert payload["totals"]["cost_usd"] == 0.25


def test_sessions_feed_is_honest_empty_on_a_log_without_notes(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)  # claims and releases, no session_metric notes

    server = _feeds_server(reliability_db=db)
    try:
        status, _, body = _authorized_get(server, "/sessions.json")
    finally:
        server.close()

    assert status == 200
    payload = json.loads(body)
    assert payload["sessions"] == []
    assert payload["totals"]["sessions"] == 0


def test_sessions_feed_fails_visible_on_a_missing_store(tmp_path: Path) -> None:
    server = _feeds_server(reliability_db=tmp_path / "absent.db")
    try:
        status, _, body = _authorized_get(server, "/sessions.json")
    finally:
        server.close()

    assert status == 503
    assert "missing event store" in body


def _seed_waits_store(db: Path) -> None:
    store = EventStore(db)
    record_ledger_task(
        store,
        LedgerTask(task_id="PENDING", title="prereq", created_at=1.0, updated_at=1.0),
    )
    record_ledger_task(
        store,
        LedgerTask(
            task_id="BLOCKED",
            title="ship it",
            created_at=2.0,
            updated_at=2.0,
            depends_on=("PENDING",),
            suggested_owner="amy",
        ),
    )
    store.close()


def test_waits_feed_reports_absence_without_a_store() -> None:
    server = _feeds_server()
    try:
        status, _, body = _authorized_get(server, "/waits.json")
    finally:
        server.close()

    assert status == 404
    assert "--feeds-db" in body


def test_waits_feed_serves_the_gates_with_the_hub_down(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_waits_store(db)

    server = _feeds_server(reliability_db=db)
    try:
        status, content_type, body = _authorized_get(server, "/waits.json")
    finally:
        server.close()

    assert status == 200
    assert content_type == "application/json"
    payload = json.loads(body)
    assert payload["wait_count"] == 1
    gate = payload["waits"][0]
    assert gate["task_id"] == "BLOCKED"
    assert gate["who"] == "amy"
    assert gate["on_what"] == ["PENDING"]


def test_waits_feed_fails_visible_on_a_missing_store(tmp_path: Path) -> None:
    server = _feeds_server(reliability_db=tmp_path / "absent.db")
    try:
        status, _, body = _authorized_get(server, "/waits.json")
    finally:
        server.close()

    assert status == 503
    assert "missing event store" in body


def test_causality_feed_mirrors_the_cli_shape(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)

    server = _feeds_server(reliability_db=db)
    try:
        by_seq_status, _, by_seq_body = _authorized_get(
            server, "/causality.json?seq=1&direction=effects"
        )
        by_task_status, _, by_task_body = _authorized_get(
            server, "/causality.json?task=T&direction=causes"
        )
    finally:
        server.close()

    assert (by_seq_status, by_task_status) == (200, 200)
    by_seq = json.loads(by_seq_body)
    assert by_seq["direction"] == "effects"
    assert by_seq["seq"] == 1
    assert by_seq["present"] is True
    by_task = json.loads(by_task_body)
    assert by_task["seq"] == 2  # the task's most recent event anchors the query


def test_causality_feed_maps_errors_to_honest_statuses(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)

    server = _feeds_server(reliability_db=db)
    try:
        ghost_status, _, ghost_body = _authorized_get(server, "/causality.json?task=GHOST")
        bad_seq_status, _, _ = _authorized_get(server, "/causality.json?seq=abc")
        bad_direction_status, _, _ = _authorized_get(
            server, "/causality.json?seq=1&direction=sideways"
        )
        unconfigured = _feeds_server()
        try:
            absent_status, _, _ = _authorized_get(unconfigured, "/causality.json?seq=1")
        finally:
            unconfigured.close()
    finally:
        server.close()

    assert ghost_status == 404
    assert "no recorded event for task" in ghost_body
    assert bad_seq_status == 400
    assert bad_direction_status == 400
    assert absent_status == 404


def test_federation_feed_serves_peerings_and_reports_absence(tmp_path: Path) -> None:
    from synapse_channel.core.federation import FederationPeer
    from synapse_channel.core.federation_store import (
        FederationRecord,
        PeerProvenance,
        save_store,
    )

    store = tmp_path / "federation.json"
    save_store(
        store,
        [
            FederationRecord(
                peer=FederationPeer(domain_id="atelier.example"),
                provenance=PeerProvenance(source="ws://a:1", imported_at=1.0, confirmed_by="ops"),
            )
        ],
    )

    configured = _feeds_server(federation_store=store)
    try:
        status, _, body = _authorized_get(configured, "/federation.json")
    finally:
        configured.close()
    unconfigured = _feeds_server()
    try:
        absent_status, _, absent_body = _authorized_get(unconfigured, "/federation.json")
    finally:
        unconfigured.close()

    assert status == 200
    payload = json.loads(body)
    assert payload["peerings"][0]["domain"] == "atelier.example"
    assert payload["peerings"][0]["state"] == "active"
    assert payload["namespaces"] == []
    assert absent_status == 404
    assert "--federation-store" in absent_body


def test_federation_feed_fails_visible_on_a_corrupt_store(tmp_path: Path) -> None:
    store = tmp_path / "federation.json"
    store.write_text("{not json", encoding="utf-8")

    server = _feeds_server(federation_store=store)
    try:
        status, _, _ = _authorized_get(server, "/federation.json")
    finally:
        server.close()

    assert status == 503


def test_feed_endpoints_sit_behind_the_dashboard_token(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)

    server = start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://127.0.0.1:1",
        name="SYNAPSE-CHANNEL/dashboard",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=5,
        allow_non_loopback=False,
        reliability_db=db,
        dashboard_token="secret",
    )
    try:
        denied, _, _ = _authorized_get(server, "/events.json", unauthenticated=True)
        allowed, _, _ = _http_get(server.url("/events.json"), authorization="Bearer secret")
    finally:
        server.close()

    assert denied == 401
    assert allowed == 200


def test_causality_feed_fails_visible_on_a_missing_store(tmp_path: Path) -> None:
    server = _feeds_server(reliability_db=tmp_path / "absent.db")
    try:
        status, _, body = _authorized_get(server, "/causality.json?seq=1")
    finally:
        server.close()

    assert status == 503
    assert "missing event store" in body


def test_events_feed_supports_the_latest_tail_shortcut(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)

    server = _feeds_server(reliability_db=db)
    try:
        status, _, body = _authorized_get(server, "/events.json?since=latest")
    finally:
        server.close()

    assert status == 200
    payload = json.loads(body)
    assert payload["events"] == []  # caught up instantly, no history walk
    assert payload["next_cursor"] == 2  # the log's end, ready for the next poll


def test_events_feed_bootstraps_bounded_latest_history_in_one_request(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)

    server = _feeds_server(reliability_db=db)
    try:
        status, _, body = _authorized_get(server, "/events.json?since=latest&limit=1&history=1")
    finally:
        server.close()

    assert status == 200
    payload = json.loads(body)
    assert [event["seq"] for event in payload["events"]] == [2]
    assert payload["next_cursor"] == 2
    assert payload["log_end_seq"] == 2
    assert payload["history_included"] is True


def test_metrics_feed_reports_absence_without_a_store() -> None:
    server = _feeds_server()
    try:
        status, _, body = _authorized_get(server, "/metrics.json")
    finally:
        server.close()

    assert status == 404
    assert "--feeds-db" in body


def test_metrics_feed_serves_log_metrics_with_the_hub_down(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)

    server = _feeds_server(reliability_db=db)
    try:
        status, content_type, body = _authorized_get(server, "/metrics.json")
    finally:
        server.close()

    assert status == 200
    assert content_type == "application/json"
    payload = json.loads(body)
    assert payload["log"]["total_events"] == 2
    assert payload["events_by_kind"] == {"claim": 1, "release": 1}
    assert payload["windows"]["last_hour"]["events"] == 2
    assert "hub's own /metrics" in payload["note"]


def test_metrics_feed_fails_visible_on_a_missing_store(tmp_path: Path) -> None:
    server = _feeds_server(reliability_db=tmp_path / "absent.db")
    try:
        status, _, body = _authorized_get(server, "/metrics.json")
    finally:
        server.close()

    assert status == 503
    assert "missing event store" in body


def test_metrics_feed_is_behind_the_dashboard_token(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)

    server = _feeds_server(reliability_db=db, dashboard_token="s3cret")
    try:
        denied, _, _ = _authorized_get(server, "/metrics.json", unauthenticated=True)
        allowed, _, body = _http_get(server.url("/metrics.json"), authorization="Bearer s3cret")
    finally:
        server.close()

    assert denied == 401
    assert allowed == 200
    assert json.loads(body)["log"]["total_events"] == 2


def _seed_receipts_store(db: Path) -> None:
    store = EventStore(db)
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "REL",
            "author": "owner",
            "kind": "assessment",
            "text": "release receipt: evidence=pytest; epistemic_status=supported",
        },
        ts=1.0,
    )
    record_operator_relay(
        store,
        {"action": "release", "task_id": "REMOTE", "operator": "ops", "applied": True},
    )
    store.close()


def test_receipts_feed_reports_absence_without_a_store() -> None:
    server = _feeds_server()
    try:
        status, _, body = _authorized_get(server, "/receipts.json")
    finally:
        server.close()

    assert status == 404
    assert "--feeds-db" in body


def test_receipts_feed_serves_universal_receipts_with_the_hub_down(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_receipts_store(db)

    server = _feeds_server(reliability_db=db)
    try:
        status, content_type, body = _authorized_get(server, "/receipts.json?since=0&limit=10")
    finally:
        server.close()

    assert status == 200
    assert content_type == "application/json"
    payload = json.loads(body)
    assert [receipt["kind"] for receipt in payload["receipts"]] == ["claim", "operator-relay"]
    assert payload["receipts"][0]["status"] == "supported"


def test_receipts_feed_refuses_malformed_numbers(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_receipts_store(db)

    server = _feeds_server(reliability_db=db)
    try:
        status, _, body = _authorized_get(server, "/receipts.json?since=abc")
    finally:
        server.close()

    assert status == 400
    assert "since and limit must be integers" in body


def test_receipts_feed_fails_visible_on_a_missing_store(tmp_path: Path) -> None:
    server = _feeds_server(reliability_db=tmp_path / "absent.db")
    try:
        status, _, body = _authorized_get(server, "/receipts.json")
    finally:
        server.close()

    assert status == 503
    assert "missing event store" in body


def test_receipts_feed_is_behind_the_dashboard_token(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_receipts_store(db)
    server = _feeds_server(reliability_db=db, dashboard_token="s3cret")
    try:
        denied, _, _ = _authorized_get(server, "/receipts.json", unauthenticated=True)
        allowed, _, _ = _http_get(server.url("/receipts.json"), authorization="Bearer s3cret")
    finally:
        server.close()

    assert denied == 401
    assert allowed == 200


def test_state_at_feed_reports_absence_without_a_store() -> None:
    server = _feeds_server()
    try:
        status, _, body = _authorized_get(server, "/state-at.json?seq=1")
    finally:
        server.close()
    assert status == 404
    assert "--feeds-db" in body


def _seed_replayable_store(db: Path) -> None:
    """A full claim payload (replayable) then a release — for state reconstruction."""
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {
            "task_id": "T",
            "owner": "alice",
            "note": "",
            "claimed_at": 1.0,
            "lease_expires_at": 1_000_000_000_000.0,
            "status": "claimed",
            "data_ref": "",
            "worktree": "w",
            "paths": [],
            "epoch": 1,
        },
        ts=1.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "T"}, ts=2.0)
    store.close()


def test_state_at_feed_reconstructs_state_at_a_seq(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_replayable_store(db)  # a claim then a release (2 events)

    server = _feeds_server(reliability_db=db)
    try:
        status, content_type, body = _authorized_get(server, "/state-at.json?seq=1")
    finally:
        server.close()

    assert status == 200
    assert content_type == "application/json"
    payload = json.loads(body)
    assert payload["as_of_seq"] == 1
    assert payload["log_end_seq"] == 2
    assert [c["task_id"] for c in payload["state"]["active_claims"]] == [
        "T"
    ]  # claimed, not yet released
    assert "presence/roster is not journalled" in payload["note"]


def test_state_at_feed_refuses_a_malformed_seq(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)
    server = _feeds_server(reliability_db=db)
    try:
        status, _, body = _authorized_get(server, "/state-at.json?seq=abc")
    finally:
        server.close()
    assert status == 400
    assert "seq must be an integer" in body


def test_state_at_feed_fails_visible_on_a_missing_store(tmp_path: Path) -> None:
    server = _feeds_server(reliability_db=tmp_path / "absent.db")
    try:
        status, _, body = _authorized_get(server, "/state-at.json?seq=1")
    finally:
        server.close()
    assert status == 503
    assert "missing event store" in body


def test_state_at_feed_is_behind_the_dashboard_token(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_replayable_store(db)
    server = _feeds_server(reliability_db=db, dashboard_token="s3cret")
    try:
        denied, _, _ = _authorized_get(server, "/state-at.json?seq=1", unauthenticated=True)
        allowed, _, _ = _http_get(server.url("/state-at.json?seq=1"), authorization="Bearer s3cret")
    finally:
        server.close()
    assert denied == 401
    assert allowed == 200


def test_merkle_proof_feed_reports_absence_without_a_store() -> None:
    server = _feeds_server()
    try:
        status, _, body = _authorized_get(server, "/merkle-proof.json?seq=1")
    finally:
        server.close()
    assert status == 404
    assert "--feeds-db" in body


def test_merkle_proof_feed_proves_inclusion_with_the_hub_down(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)  # two events; merkle hashes leaves, no replay needed

    server = _feeds_server(reliability_db=db)
    try:
        status, content_type, body = _authorized_get(server, "/merkle-proof.json?seq=1")
    finally:
        server.close()

    assert status == 200
    assert content_type == "application/json"
    payload = json.loads(body)
    assert payload["present"] is True
    assert payload["seq"] == 1
    assert payload["tree_size"] == 2
    # The served proof verifies through the same client-side check the cockpit's
    # per-row verify button runs — the row is committed to the attested root.
    assert verify_inclusion(proof_from_json(payload)) is True


def test_merkle_proof_feed_reports_an_absent_seq_without_fabricating(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)  # only seq 1..2 exist
    server = _feeds_server(reliability_db=db)
    try:
        status, _, body = _authorized_get(server, "/merkle-proof.json?seq=99")
    finally:
        server.close()
    assert status == 200
    payload = json.loads(body)
    assert payload["present"] is False
    assert "no event at that sequence" in payload["note"]


def test_merkle_proof_feed_refuses_a_malformed_seq(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)
    server = _feeds_server(reliability_db=db)
    try:
        status, _, body = _authorized_get(server, "/merkle-proof.json?seq=abc")
    finally:
        server.close()
    assert status == 400
    assert "seq must be an integer" in body


def test_merkle_proof_feed_fails_visible_on_a_missing_store(tmp_path: Path) -> None:
    server = _feeds_server(reliability_db=tmp_path / "absent.db")
    try:
        status, _, body = _authorized_get(server, "/merkle-proof.json?seq=1")
    finally:
        server.close()
    assert status == 503
    assert "missing event store" in body


def test_merkle_proof_feed_is_behind_the_dashboard_token(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)
    server = _feeds_server(reliability_db=db, dashboard_token="s3cret")
    try:
        denied, _, _ = _authorized_get(server, "/merkle-proof.json?seq=1", unauthenticated=True)
        allowed, _, _ = _http_get(
            server.url("/merkle-proof.json?seq=1"), authorization="Bearer s3cret"
        )
    finally:
        server.close()
    assert denied == 401
    assert allowed == 200


def test_health_anomalies_feed_reports_absence_without_a_store() -> None:
    server = _feeds_server()
    try:
        status, _, body = _authorized_get(server, "/health-anomalies.json")
    finally:
        server.close()
    assert status == 404
    assert "--feeds-db" in body


def test_health_anomalies_feed_flags_anomalies_with_the_hub_down(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "X", "owner": "bob", "status": "claimed", "paths": ["s"], "worktree": "w"},
        ts=1.0,
    )  # a claim that is its task's last event — orphaned
    store.close()

    server = _feeds_server(reliability_db=db)
    try:
        status, content_type, body = _authorized_get(server, "/health-anomalies.json")
    finally:
        server.close()

    assert status == 200
    assert content_type == "application/json"
    payload = json.loads(body)
    assert payload["present"] is True
    assert payload["anomaly_count"] >= 1
    assert [item["task_id"] for item in payload["orphaned"]] == ["X"]


def test_health_anomalies_feed_fails_visible_on_a_missing_store(tmp_path: Path) -> None:
    server = _feeds_server(reliability_db=tmp_path / "absent.db")
    try:
        status, _, body = _authorized_get(server, "/health-anomalies.json")
    finally:
        server.close()
    assert status == 503
    assert "missing event store" in body


def test_health_anomalies_feed_is_behind_the_dashboard_token(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed_feed_store(db)
    server = _feeds_server(reliability_db=db, dashboard_token="s3cret")
    try:
        denied, _, _ = _authorized_get(server, "/health-anomalies.json", unauthenticated=True)
        allowed, _, _ = _http_get(
            server.url("/health-anomalies.json"), authorization="Bearer s3cret"
        )
    finally:
        server.close()
    assert denied == 401
    assert allowed == 200
