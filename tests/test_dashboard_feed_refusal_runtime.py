# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable dashboard feed failure and recovery HTTP tests

"""Exercise storage refusal, authored queries and recovery over real HTTP."""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from dashboard_helpers import _authorized_get, _feeds_server
from synapse_channel import dashboard_feed_serving
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore

_UNAVAILABLE = "dashboard feed unavailable; check server diagnostics\n"
_ROUTES = (
    "/reliability.json",
    "/events.json",
    "/metrics.json",
    "/state-at.json?seq=1",
    "/merkle-proof.json?seq=1",
    "/health-anomalies.json",
    "/sessions.json",
    "/waits.json",
    "/operator-actions.json",
    "/receipts.json",
    "/postmortem.json?task=TASK",
    "/causality.json?seq=1",
)


def _seed(path: Path) -> None:
    """Create a real claim and release journal for repair/readback checks."""
    store = EventStore(path)
    try:
        store.append(
            EventKind.CLAIM,
            {
                "task_id": "TASK",
                "owner": "probe",
                "status": "claimed",
                "paths": [],
                "worktree": "probe",
                "claimed_at": 1.0,
                "lease_expires_at": 61.0,
            },
            ts=1.0,
        )
        store.append(EventKind.RELEASE, {"task_id": "TASK"}, ts=2.0)
    finally:
        store.close()


def _damage(path: Path, condition: str) -> None:
    """Prepare missing, invalid SQLite or incompatible actual schema files."""
    if condition == "not-sqlite":
        path.write_bytes(b"PRIVATE-STORAGE-CANARY is not a SQLite database")
    elif condition == "wrong-schema":
        with contextlib.closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY)")


@pytest.mark.real_hub
@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("condition", ("missing", "not-sqlite", "wrong-schema"))
def test_store_failure_is_private_and_the_same_server_recovers(
    tmp_path: Path, route: str, condition: str
) -> None:
    db = tmp_path / "PRIVATE-STORAGE-CANARY.db"
    replacement = tmp_path / "replacement.db"
    _seed(replacement)
    _damage(db, condition)
    server = _feeds_server(reliability_db=db)
    try:
        denied, _, _ = _authorized_get(server, route, unauthenticated=True)
        assert denied == 401
        status, media_type, body = _authorized_get(server, route)
        assert (status, media_type, body) == (503, "text/plain", _UNAVAILABLE)
        assert "PRIVATE-STORAGE-CANARY" not in body
        assert str(tmp_path) not in body
        assert "Traceback" not in body

        if condition == "wrong-schema":
            # Repair the SQLite database through SQLite, including its WAL;
            # swapping just its main file would retain the old journal.
            with contextlib.closing(sqlite3.connect(db)) as connection:
                connection.execute("DROP TABLE events")
            _seed(db)
        else:
            replacement.replace(db)
        repaired_digest = hashlib.sha256(db.read_bytes()).hexdigest()
        status, media_type, body = _authorized_get(server, route)
        assert (status, media_type) == (200, "application/json")
        assert isinstance(json.loads(body), dict)
        assert hashlib.sha256(db.read_bytes()).hexdigest() == repaired_digest
        _, _, tail = _authorized_get(server, "/events.json")
        events = json.loads(tail)["events"]
        assert [event["seq"] for event in events] == [1, 2]
        assert [event["kind"] for event in events] == ["claim", "release"]
    finally:
        server.close()
    assert not server.thread.is_alive()


@pytest.mark.real_hub
def test_live_feed_channels_refuse_privately_then_report_repaired_evidence(
    tmp_path: Path,
) -> None:
    db = tmp_path / "PRIVATE-STORAGE-CANARY.db"
    server = _feeds_server(reliability_db=db)
    try:
        status, media_type, body = _authorized_get(server, "/live.ndjson?cycles=1")
        assert (status, media_type) == (200, "application/x-ndjson")
        frames = [json.loads(line) for line in body.splitlines()]
        selected = [
            frame
            for frame in frames
            if frame.get("channel") in {"events", "receipts", "operator_actions"}
        ]
        assert len(selected) == 3
        assert all(frame["status"] == "error" for frame in selected)
        assert all(frame["detail"] == _UNAVAILABLE.strip() for frame in selected)
        assert "PRIVATE-STORAGE-CANARY" not in body
        assert frames[-1]["kind"] == "close"

        _seed(db)
        status, _, body = _authorized_get(server, "/live.ndjson?cycles=1")
        assert status == 200
        repaired = [json.loads(line) for line in body.splitlines()]
        for channel in ("events", "receipts", "operator_actions"):
            frame = next(frame for frame in repaired if frame.get("channel") == channel)
            assert frame["status"] == "live"
            assert frame["data"]["log_end_seq"] == 2
        assert [frame["sequence"] for frame in repaired] == list(range(1, len(repaired) + 1))
        assert repaired[-1]["kind"] == "close"
    finally:
        server.close()
    assert not server.thread.is_alive()


@pytest.mark.real_hub
def test_federation_store_failure_is_private_and_repair_is_observed(tmp_path: Path) -> None:
    path = tmp_path / "PRIVATE-FEDERATION-CANARY.json"
    path.write_text("{not json", encoding="utf-8")
    server = _feeds_server(federation_store=path)
    try:
        status, media_type, body = _authorized_get(server, "/federation.json")
        assert (status, media_type, body) == (503, "text/plain", _UNAVAILABLE)
        assert str(path) not in body
        path.write_text('{"version":1,"records":[]}', encoding="utf-8")
        status, _, body = _authorized_get(server, "/federation.json")
        assert status == 200
        assert json.loads(body)["peerings"] == []
    finally:
        server.close()


@pytest.mark.real_hub
@pytest.mark.parametrize(
    ("query", "status", "message"),
    (
        ("seq=PRIVATE-QUERY-CANARY", 400, "seq must be an integer\n"),
        (
            "direction=sideways&seq=1",
            400,
            "unknown causality direction 'sideways'; expected one of causes/effects\n",
        ),
        ("", 400, "exactly one of seq and task selects the anchor event\n"),
        ("seq=1&task=TASK", 400, "exactly one of seq and task selects the anchor event\n"),
        ("task=UNKNOWN", 404, "no recorded event for task 'UNKNOWN'\n"),
    ),
)
def test_causal_authored_refusals_preserve_status_and_exact_text(
    tmp_path: Path, query: str, status: int, message: str
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    digest = hashlib.sha256(db.read_bytes()).hexdigest()
    server = _feeds_server(reliability_db=db)
    try:
        actual_status, media_type, body = _authorized_get(server, "/causality.json?" + query)
        assert (actual_status, media_type, body) == (status, "text/plain", message)
        assert "PRIVATE-QUERY-CANARY" not in body
        assert "invalid literal" not in body
        assert hashlib.sha256(db.read_bytes()).hexdigest() == digest
        accepted, _, document = _authorized_get(server, "/causality.json?task=TASK")
        assert accepted == 200
        assert json.loads(document)["present"] is True
    finally:
        server.close()


@pytest.mark.real_hub
def test_invalid_journal_json_remains_quarantined_in_http_and_live_frames(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE events SET payload = ? WHERE seq = 1", ("{PRIVATE-ROW-CANARY",))
    digest = hashlib.sha256(db.read_bytes()).hexdigest()
    server = _feeds_server(reliability_db=db)
    try:
        status, _, body = _authorized_get(server, "/events.json")
        assert status == 200
        document = json.loads(body)
        assert [event["seq"] for event in document["events"]] == [1, 2]
        assert document["events"][0]["kind"] == "corrupt_event"
        assert document["events"][0]["payload"]["reasons"] == ["invalid_json"]
        assert "PRIVATE-ROW-CANARY" not in body
        status, _, body = _authorized_get(server, "/live.ndjson?cycles=1")
        assert status == 200
        frames = [json.loads(line) for line in body.splitlines()]
        event_frame = next(frame for frame in frames if frame.get("channel") == "events")
        assert event_frame["status"] == "live"
        assert event_frame["data"]["events"][0]["kind"] == "corrupt_event"
        assert frames[-1]["kind"] == "close"
        assert "PRIVATE-ROW-CANARY" not in body
        assert hashlib.sha256(db.read_bytes()).hexdigest() == digest
    finally:
        server.close()


@pytest.mark.real_hub
@pytest.mark.parametrize(
    "fault",
    (
        ValueError("no recorded event for task PRIVATE-BUILDER-CANARY"),
        ValueError("missing event store PRIVATE-BUILDER-CANARY"),
        TypeError("PRIVATE-BUILDER-CANARY"),
        OSError("PRIVATE-BUILDER-CANARY"),
        KeyError("PRIVATE-BUILDER-CANARY"),
        RuntimeError("PRIVATE-BUILDER-CANARY"),
    ),
)
def test_injected_foreign_builder_fault_cannot_impersonate_an_authored_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    fault: Exception,
) -> None:
    """Inject classification faults separately from real damaged-file cases."""
    db = tmp_path / "hub.db"
    _seed(db)
    digest = hashlib.sha256(db.read_bytes()).hexdigest()

    def fail(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise fault

    monkeypatch.setattr(dashboard_feed_serving, "build_causality_feed", fail)
    server = _feeds_server(reliability_db=db)
    try:
        status, media_type, body = _authorized_get(server, "/causality.json?seq=1")
        assert (status, media_type, body) == (503, "text/plain", _UNAVAILABLE)
        assert "PRIVATE-BUILDER-CANARY" not in body
        assert "PRIVATE-BUILDER-CANARY" in caplog.text
        assert hashlib.sha256(db.read_bytes()).hexdigest() == digest
    finally:
        server.close()


@pytest.mark.real_hub
@pytest.mark.parametrize("condition", ("unserializable", "invalid-history-cursor"))
def test_injected_encoding_and_cursor_faults_are_private_http_refusals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    condition: str,
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    digest = hashlib.sha256(db.read_bytes()).hexdigest()

    def invalid_document(*_args: object, **_kwargs: object) -> dict[str, object]:
        if condition == "unserializable":
            return {"payload": Path("PRIVATE-ENCODING-CANARY")}
        return {"next_cursor": "PRIVATE-CURSOR-CANARY"}

    monkeypatch.setattr(dashboard_feed_serving, "build_events_tail", invalid_document)
    server = _feeds_server(reliability_db=db)
    try:
        route = (
            "/events.json"
            if condition == "unserializable"
            else "/events.json?since=latest&history=1"
        )
        status, media_type, body = _authorized_get(server, route)
        assert (status, media_type, body) == (503, "text/plain", _UNAVAILABLE)
        assert "CANARY" not in body
        assert "Dashboard store feed could not be served" in caplog.text
        assert hashlib.sha256(db.read_bytes()).hexdigest() == digest
    finally:
        server.close()
