# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard feed-serving unit tests (pure responses)

"""Direct tests for the read-side feed serving in dashboard_feed_serving.

The HTTP route tests prove the wire behaviour; this surface proves the module
contract itself — every function returns a plain :class:`FeedResponse`, the
honest-absence 404 names the enabling flag, a malformed query is a 400 naming
the parameter, and an unreadable store is a fail-visible 503 — against real
event stores and real files, no HTTP server in between.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path

import pytest

from synapse_channel.core.federation import FederationPeer
from synapse_channel.core.federation_store import (
    FederationRecord,
    PeerProvenance,
    save_store,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.dashboard_feed_serving import (
    FeedResponse,
    bounded_query_int,
    json_response,
    plain_response,
    serve_causality,
    serve_cockpit_dist,
    serve_events,
    serve_federation,
    serve_health_anomalies,
    serve_merkle_proof,
    serve_metrics_feed,
    serve_operator_actions,
    serve_receipts,
    serve_reliability,
    serve_sessions,
    serve_state_at,
    serve_waits,
)


def _seeded_db(tmp_path: Path) -> Path:
    """Create a real event store carrying one claim/release pair."""
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
            "lease_expires_at": 3600.0,
        },
        ts=1.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "T"}, ts=2.0)
    store.close()
    return db


def test_plain_response_appends_a_newline_and_sets_the_type() -> None:
    response = plain_response(HTTPStatus.NOT_FOUND, "gone")
    assert response == FeedResponse(HTTPStatus.NOT_FOUND, b"gone\n", "text/plain")


def test_json_response_is_deterministic_sorted_utf8() -> None:
    response = json_response({"b": 1, "a": "č"})
    assert response.status == HTTPStatus.OK
    assert response.content_type == "application/json"
    assert response.body == '{"a": "č", "b": 1}'.encode()


def test_bounded_query_int_accepts_the_64_bit_range() -> None:
    assert bounded_query_int("0") == 0
    assert bounded_query_int(str(2**63 - 1)) == 2**63 - 1
    assert bounded_query_int(str(-(2**63))) == -(2**63)


@pytest.mark.parametrize("raw", ["abc", "", "1.5", str(2**63), str(-(2**63) - 1)])
def test_bounded_query_int_rejects_malformed_or_oversize(raw: str) -> None:
    with pytest.raises(ValueError, match="."):
        bounded_query_int(raw)


@pytest.mark.parametrize(
    ("serve", "flag"),
    [
        (lambda: serve_reliability(None, None), "--reliability-db"),
        (lambda: serve_metrics_feed(None), "--feeds-db"),
        (lambda: serve_state_at(None, ""), "--feeds-db"),
        (lambda: serve_merkle_proof(None, ""), "--feeds-db"),
        (lambda: serve_health_anomalies(None), "--feeds-db"),
        (lambda: serve_sessions(None), "--feeds-db"),
        (lambda: serve_waits(None), "--feeds-db"),
        (lambda: serve_operator_actions(None, ""), "--feeds-db"),
        (lambda: serve_receipts(None, ""), "--feeds-db"),
        (lambda: serve_events(None, ""), "--feeds-db"),
        (lambda: serve_causality(None, ""), "--feeds-db"),
        (lambda: serve_federation(None), "--federation-store"),
        (lambda: serve_cockpit_dist(None, "/cockpit/", "/cockpit/"), "--cockpit-dist"),
    ],
)
def test_an_unconfigured_feed_is_an_honest_404_naming_the_flag(
    serve: Callable[[], FeedResponse], flag: str
) -> None:
    response = serve()
    assert response.status == HTTPStatus.NOT_FOUND
    assert "not configured" in response.body.decode()
    assert flag in response.body.decode()


@pytest.mark.parametrize(
    ("serve", "message"),
    [
        (lambda db: serve_state_at(db, "seq=abc"), "seq must be an integer"),
        (lambda db: serve_merkle_proof(db, "seq=abc"), "seq must be an integer"),
        (lambda db: serve_operator_actions(db, "since=abc"), "since and limit must be integers"),
        (lambda db: serve_receipts(db, "limit=abc"), "since and limit must be integers"),
        (lambda db: serve_events(db, "limit=abc"), "since must be an integer or 'latest'"),
        (lambda db: serve_causality(db, "seq=abc"), "seq must be an integer"),
    ],
)
def test_a_malformed_query_is_a_400_naming_the_parameter(
    tmp_path: Path, serve: Callable[[Path], FeedResponse], message: str
) -> None:
    response = serve(_seeded_db(tmp_path))
    assert response.status == HTTPStatus.BAD_REQUEST
    assert message in response.body.decode()


def test_a_missing_store_file_is_a_fail_visible_503(tmp_path: Path) -> None:
    absent = tmp_path / "never-created.db"
    response = serve_waits(absent)
    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert "missing event store" in response.body.decode()


def test_reliability_serves_the_report_from_a_real_store(tmp_path: Path) -> None:
    response = serve_reliability(_seeded_db(tmp_path), None)
    assert response.status == HTTPStatus.OK
    payload = json.loads(response.body)
    assert payload["note"] == "audit signals, not scores"


def test_events_tail_serves_seeded_events_and_the_latest_shortcut(tmp_path: Path) -> None:
    db = _seeded_db(tmp_path)
    tail = serve_events(db, "since=0&limit=10")
    latest = serve_events(db, "since=latest")
    assert tail.status == HTTPStatus.OK
    events = json.loads(tail.body)["events"]
    assert [event["kind"] for event in events] == ["claim", "release"]
    assert json.loads(latest.body)["events"] == []


@pytest.mark.parametrize(
    "serve",
    [
        lambda db: serve_metrics_feed(db),
        lambda db: serve_state_at(db, "seq=1"),
        lambda db: serve_merkle_proof(db, "seq=1"),
        lambda db: serve_health_anomalies(db),
        lambda db: serve_sessions(db),
        lambda db: serve_waits(db),
        lambda db: serve_operator_actions(db, ""),
        lambda db: serve_receipts(db, ""),
    ],
)
def test_every_store_feed_serves_a_document_from_a_real_store(
    tmp_path: Path, serve: Callable[[Path], FeedResponse]
) -> None:
    response = serve(_seeded_db(tmp_path))
    assert response.status == HTTPStatus.OK
    assert response.content_type == "application/json"
    assert isinstance(json.loads(response.body), dict)


def test_causality_is_fail_visible_when_the_store_file_is_missing(tmp_path: Path) -> None:
    response = serve_causality(tmp_path / "never-created.db", "seq=1")
    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert "missing event store" in response.body.decode()


def test_causality_maps_an_unknown_task_to_a_404_not_an_invention(tmp_path: Path) -> None:
    response = serve_causality(_seeded_db(tmp_path), "task=NEVER-DECLARED")
    assert response.status == HTTPStatus.NOT_FOUND
    assert "no recorded event for task" in response.body.decode()


def test_causality_answers_a_seq_anchor_from_the_log(tmp_path: Path) -> None:
    response = serve_causality(_seeded_db(tmp_path), "seq=1")
    assert response.status == HTTPStatus.OK
    document = json.loads(response.body)
    assert document["seq"] == 1
    assert document["present"] is True


def test_federation_serves_peerings_from_a_real_store(tmp_path: Path) -> None:
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
    response = serve_federation(store)
    assert response.status == HTTPStatus.OK
    assert json.loads(response.body)["peerings"][0]["domain"] == "atelier.example"


def test_federation_fails_visible_on_a_corrupt_store(tmp_path: Path) -> None:
    store = tmp_path / "federation.json"
    store.write_text("{not json")
    response = serve_federation(store)
    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE


def test_cockpit_dist_serves_the_index_and_named_assets(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html>")
    (tmp_path / "app.js").write_text("export {}")
    index = serve_cockpit_dist(tmp_path, "/cockpit/", "/cockpit/")
    asset = serve_cockpit_dist(tmp_path, "/cockpit/", "/cockpit/app.js")
    assert (index.status, index.content_type) == (HTTPStatus.OK, "text/html")
    assert index.body == b"<!doctype html>"
    assert (asset.status, asset.content_type) == (HTTPStatus.OK, "text/javascript")


@pytest.mark.parametrize(
    "path",
    [
        "/cockpit/../escape.txt",  # traversal out of the build directory
        "/cockpit/absent.js",  # file that does not exist
        "/cockpit/evil.exe",  # unrecognised suffix
    ],
)
def test_cockpit_dist_refuses_traversal_absence_and_odd_suffixes(tmp_path: Path, path: str) -> None:
    (tmp_path / "index.html").write_text("<!doctype html>")
    (tmp_path.parent / "escape.txt").write_text("secret")
    (tmp_path / "evil.exe").write_bytes(b"MZ")
    response = serve_cockpit_dist(tmp_path, "/cockpit/", path)
    assert response.status == HTTPStatus.NOT_FOUND
    assert response.body == b"not found\n"
