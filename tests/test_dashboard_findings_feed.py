# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the findings feed from a real hub's event store
"""Serve the findings a running hub recorded, newest first, through real HTTP."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from dashboard_helpers import _feeds_server, _http_get
from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.dashboard import DashboardServer
from synapse_channel.dashboard_store_feeds import (
    FINDINGS_SCAN_SPAN,
    MAX_FINDINGS_LIMIT,
    build_findings_feed,
)

AUTHOR = "FINDINGS/author"
TOKEN = "findings-feed-secret"
BEARER = f"Bearer {TOKEN}"


def _rows(feed: dict[str, object]) -> list[dict[str, Any]]:
    """Return a feed page's rows, asserting the served shape."""
    rows = feed["findings"]
    assert isinstance(rows, list)
    return rows


async def _record_through_a_hub(db: Path) -> None:
    store = EventStore(db)
    try:
        async with running_hub(SynapseHub(hub_id="syn-test", journal=store)) as (_, uri):
            author = await connect_agent(AUTHOR, uri)
            try:
                await author.agent.record_finding(
                    "the poll downloads every closed task",
                    subkind="measurement",
                    idem_key="finding-poll",
                    evidence_kind="measured",
                    claim_status="reference-validated",
                    evidence_ref="evidence/01_live_measurement.txt",
                    checked_this_session=True,
                    source_ref="src/synapse_channel/dashboard.py",
                    project="SYNAPSE",
                )
                await author.agent.record_finding(
                    "the receipt list repeats every assessment note",
                    subkind="defect",
                    idem_key="finding-receipts",
                    evidence_ref="src/synapse_channel/dashboard_fleet.py:333",
                    project="SYNAPSE",
                )
                await author.agent.record_finding(
                    "an estimate that was later withdrawn",
                    subkind="estimate",
                    idem_key="finding-estimate",
                    lifecycle="retracted",
                )
                for _ in range(300):
                    recorded = [
                        message
                        for message in author.recorder.messages
                        if message.get("type") == MessageType.FINDING_RECORDED
                    ]
                    if len(recorded) == 3:
                        break
                    await asyncio.sleep(0.01)
                assert len(recorded) == 3
            finally:
                await close_agents(author)
    finally:
        store.close()


@pytest.fixture
def recorded(tmp_path: Path) -> Path:
    db = tmp_path / "hub.db"
    asyncio.run(_record_through_a_hub(db))
    return db


def test_findings_come_newest_first_with_the_hub_attested_author(recorded: Path) -> None:
    feed = build_findings_feed(recorded)
    rows = _rows(feed)

    assert feed["total"] == 3 and feed["next_before"] is None
    assert [row["statement"] for row in rows] == [
        "an estimate that was later withdrawn",
        "the receipt list repeats every assessment note",
        "the poll downloads every closed task",
    ]
    assert rows[0]["seq"] > rows[1]["seq"] > rows[2]["seq"]
    assert {row["provenance"]["actor"] for row in rows} == {AUTHOR}
    assert [row["provenance"]["project"] for row in rows][1:] == ["SYNAPSE", "SYNAPSE"]
    assert [row["freshness"] for row in rows] == [
        "untraceable",
        "traceable-unchecked",
        "verified-at-source",
    ]
    assert [row["lifecycle"] for row in rows] == ["retracted", "active", "active"]
    assert rows[2]["evidence_kind"] == "measured"
    assert rows[2]["claim_status"] == "reference-validated"
    assert rows[2]["evidence_ref"] == "evidence/01_live_measurement.txt"
    assert rows[0]["evidence_kind"] is None and rows[0]["evidence_ref"] is None
    # The hub stamps who and when on every finding; only the producer's claim says "checked".
    for row in rows:
        assert row["verified_at_source"]["by"] == AUTHOR
        assert row["verified_at_source"]["at"] == pytest.approx(row["ts"], abs=5.0)
    assert [row["verified_at_source"]["checked_this_session"] for row in rows] == [
        False,
        False,
        True,
    ]
    assert rows[2]["verified_at_source"]["source_ref"] == "src/synapse_channel/dashboard.py"


def test_the_cursor_pages_back_without_overlap(recorded: Path) -> None:
    first = build_findings_feed(recorded, limit=2)
    cursor = first["next_before"]
    assert isinstance(cursor, int) and cursor == _rows(first)[-1]["seq"]
    second = build_findings_feed(recorded, before=cursor, limit=2)

    assert [row["statement"] for row in _rows(second)] == ["the poll downloads every closed task"]
    assert second["next_before"] is None and second["total"] == 3
    assert _rows(build_findings_feed(recorded, before=1)) == []
    assert len(_rows(build_findings_feed(recorded, limit=0))) == 1
    # A page that holds exactly what is left names no older page.
    exact = build_findings_feed(recorded, limit=3)
    assert len(_rows(exact)) == 3 and exact["next_before"] is None


def test_a_missing_store_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing event store"):
        build_findings_feed(tmp_path / "absent.db")


def _store_with_spread_findings(db: Path, spots: tuple[int, ...], length: int) -> None:
    store = EventStore(db)
    try:
        store.append_batch(
            (
                (
                    (EventKind.FINDING, {"statement": f"finding at {index}", "subkind": "fact"})
                    if index in spots
                    else ("chat", {"sender": "A", "payload": f"line {index}"})
                )
                for index in range(1, length + 1)
            ),
            ts=1_783_000_000.0,
        )
    finally:
        store.close()


def test_the_walk_reads_backwards_window_by_window_and_stops_when_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "spread.db"
    length = 3 * FINDINGS_SCAN_SPAN
    _store_with_spread_findings(db, (5, FINDINGS_SCAN_SPAN + 7, length - 3), length)
    windows: list[tuple[int | None, int | None]] = []
    original = EventStore.read_window

    def counted(self: EventStore, **kwargs: Any) -> Any:
        windows.append((kwargs.get("min_seq"), kwargs.get("max_seq")))
        return original(self, **kwargs)

    monkeypatch.setattr(EventStore, "read_window", counted)
    newest = build_findings_feed(db, limit=1)

    assert [row["statement"] for row in _rows(newest)] == [f"finding at {length - 3}"]
    assert newest["total"] == 3 and newest["next_before"] == length - 3
    # One more finding than the page proves an older one exists; the walk stops there.
    assert windows == [
        (2 * FINDINGS_SCAN_SPAN + 1, length),
        (FINDINGS_SCAN_SPAN + 1, 2 * FINDINGS_SCAN_SPAN),
    ]
    windows.clear()
    rest = build_findings_feed(db, before=length - 3, limit=MAX_FINDINGS_LIMIT + 50)

    assert [row["statement"] for row in _rows(rest)] == [
        f"finding at {FINDINGS_SCAN_SPAN + 7}",
        "finding at 5",
    ]
    assert rest["next_before"] is None
    assert windows[0] == (length - 4 - FINDINGS_SCAN_SPAN + 1, length - 4)
    assert windows[-1][0] == 1


@pytest.fixture
def served(recorded: Path) -> Iterator[DashboardServer]:
    server = _feeds_server(reliability_db=recorded, dashboard_token=TOKEN)
    try:
        yield server
    finally:
        server.close()


def test_the_feed_is_served_behind_the_read_token(served: DashboardServer, recorded: Path) -> None:
    status, content_type, body = _http_get(served.url("/findings.json"), authorization=BEARER)

    assert (status, content_type) == (200, "application/json")
    assert json.loads(body) == json.loads(json.dumps(build_findings_feed(recorded)))
    status, _, body = _http_get(served.url("/findings.json?limit=1"), authorization=BEARER)
    page = json.loads(body)
    assert len(page["findings"]) == 1 and page["next_before"] == page["findings"][0]["seq"]
    assert _http_get(served.url("/findings.json"))[0] == 401
    assert _http_get(served.url("/findings.json"), authorization="Bearer wrong")[0] == 401


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("limit=many", "before and limit must be integers\n"),
        ("before=x", "before and limit must be integers\n"),
        ("before=0", "before must be at least 1\n"),
        ("before=-4", "before must be at least 1\n"),
    ],
)
def test_malformed_parameters_are_refused_by_name(
    served: DashboardServer, query: str, message: str
) -> None:
    status, content_type, body = _http_get(
        served.url(f"/findings.json?{query}"), authorization=BEARER
    )

    assert (status, content_type, body) == (400, "text/plain", message)


def test_without_an_event_store_the_feed_says_it_is_not_served() -> None:
    server = _feeds_server(dashboard_token=TOKEN)
    try:
        status, content_type, body = _http_get(server.url("/findings.json"), authorization=BEARER)
    finally:
        server.close()

    assert (status, content_type) == (404, "text/plain")
    assert body == "findings feed not configured; start the dashboard with --feeds-db\n"
