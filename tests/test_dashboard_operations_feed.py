# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — /operations.json over HTTP, from what a real hub journalled
"""The operations feed lists and pages what the hub journalled, and serves one chain whole."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import synapse_channel.dashboard_operations_feed as operations_feed_module
from dashboard_helpers import _authorized_get, _feeds_server
from operation_report_store import journal_every_state
from synapse_channel.core.operation_report import (
    ChainLink,
    OperationReport,
    operation_report_to_json,
    run_operation_report,
)
from synapse_channel.dashboard import DashboardServer
from synapse_channel.dashboard_operations_feed import build_operations_feed

DECLARED = 12
"""The operations the shared journal declares."""


@pytest.fixture(scope="module")
def store_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One real hub journal holding an operation in every state the report gives."""
    path = tmp_path_factory.mktemp("operations-feed") / "hub.db"
    journal_every_state(path)
    return path


@pytest.fixture
def server(store_path: Path) -> Iterator[DashboardServer]:
    running = _feeds_server(reliability_db=store_path)
    try:
        yield running
    finally:
        running.close()


def _json(server: DashboardServer, path: str) -> dict[str, Any]:
    status, _content_type, body = _authorized_get(server, path)
    assert status == 200, body
    document = json.loads(body)
    assert isinstance(document, dict)
    return document


def test_the_list_holds_every_declared_operation_newest_first_in_the_reports_words(
    server: DashboardServer, store_path: Path
) -> None:
    feed = _json(server, f"/operations.json?limit={DECLARED}")
    rows = feed["operations"]

    assert feed["total"] == DECLARED
    assert feed["next_before"] is None
    assert feed["custody"] == "not recorded here"
    assert [row["declared_seq"] for row in rows] == sorted(
        (row["declared_seq"] for row in rows), reverse=True
    )
    for row in rows:
        report = run_operation_report(store_path, row["operation_id"])
        assert row["state"] == report.state
        assert (row["task_id"], row["declarer"]) == ("T1", "P/worker")
        assert (row["latest_seq"], row["latest_kind"]) == (
            report.chain[-1].seq,
            report.chain[-1].kind,
        )


def test_pages_follow_the_cursor_and_the_limit_is_held_in_its_bounds(
    server: DashboardServer,
) -> None:
    first = _json(server, "/operations.json?limit=5")
    second = _json(server, f"/operations.json?limit=5&before={first['next_before']}")
    third = _json(server, f"/operations.json?limit=5&before={second['next_before']}")
    seen = [row["operation_id"] for page in (first, second, third) for row in page["operations"]]

    assert [len(page["operations"]) for page in (first, second, third)] == [5, 5, 2]
    assert third["next_before"] is None
    assert len(set(seen)) == DECLARED
    assert first["next_before"] == first["operations"][-1]["declared_seq"]
    assert _json(server, "/operations.json?limit=0")["limit"] == 1
    assert _json(server, "/operations.json?limit=999")["limit"] == 200


def test_one_named_operation_is_served_whole_and_an_unknown_one_says_absent(
    server: DashboardServer, store_path: Path
) -> None:
    chain = _json(server, "/operations.json?operation=op-verified")
    absent = _json(server, "/operations.json?operation=op-never")

    expected = operation_report_to_json(run_operation_report(store_path, "op-verified"))
    assert chain == {**expected, "present": True}
    assert (absent["present"], absent["state"], absent["chain"]) == (False, "not recorded", [])


@pytest.mark.parametrize(
    ("query", "reason"),
    [
        ("limit=x", "before and limit must be integers"),
        ("before=0", "before must be at least 1"),
        ("operation=", "operation must be one non-empty identifier"),
        ("operation=a&operation=b", "operation must be one non-empty identifier"),
        ("operation=" + "x" * 513, "operation must be one non-empty identifier"),
    ],
)
def test_a_malformed_request_is_refused_naming_its_parameter(
    server: DashboardServer, query: str, reason: str
) -> None:
    status, _content_type, body = _authorized_get(server, f"/operations.json?{query}")

    assert status == 400
    assert reason in body


def test_without_an_event_store_the_feed_names_its_flag() -> None:
    bare = _feeds_server()
    try:
        status, _content_type, body = _authorized_get(bare, "/operations.json")
        capabilities = _json(bare, "/capabilities.json")
    finally:
        bare.close()

    assert status == 404
    assert "operations feed not configured; start the dashboard with --feeds-db" in body
    surface = next(item for item in capabilities["surfaces"] if item["name"] == "operations")
    assert (surface["path"], surface["flag"]) == ("/operations.json", "--feeds-db")


def test_a_scope_keeps_the_operations_of_its_declarer_and_a_named_chain_stays_whole(
    server: DashboardServer,
) -> None:
    mine = _json(server, f"/operations.json?limit={DECLARED}&scope=project:P")
    others = _json(server, f"/operations.json?limit={DECLARED}&scope=project:Q")
    chain = _json(server, "/operations.json?operation=op-verified&scope=project:Q")

    assert len(mine["operations"]) == DECLARED
    assert mine["scope"] == {
        "scope": "project:P",
        "applied": True,
        "rows_in_page": DECLARED,
        "rows_in_scope": DECLARED,
    }
    assert others["operations"] == []
    assert others["scope"]["rows_in_scope"] == 0
    assert chain["present"] is True
    assert chain["scope"] == {
        "scope": "project:Q",
        "applied": False,
        "reason": "one record is named, and it is shown whole",
    }


def test_an_approval_journalled_before_the_declaration_never_stands_for_the_declarer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    early = OperationReport(
        operation_id="op-early",
        task_id="T1",
        state="approved, no attempt",
        intent_sha256="a" * 64,
        digest_check="matches",
        attempts=(),
        chain=(
            ChainLink(4, 4.0, "approval", "P/approver", {"state": "approved"}),
            ChainLink(9, 9.0, "declared", "P/worker", {}),
        ),
        as_of_seq=9,
        as_of=9.0,
    )
    later = replace(
        early,
        operation_id="op-later",
        chain=(ChainLink(7, 7.0, "declared", "P/worker", {}),),
    )
    monkeypatch.setattr(
        operations_feed_module, "run_operation_reports", lambda *_a, **_k: (early, later)
    )

    first = build_operations_feed("unused.db", limit=1)
    cursor = first["next_before"]
    assert isinstance(cursor, int)
    second = build_operations_feed("unused.db", before=cursor, limit=1)
    later_rows = second["operations"]
    assert isinstance(later_rows, list)

    assert first["operations"] == [
        {
            "operation_id": "op-early",
            "task_id": "T1",
            "declarer": "P/worker",
            "declared_seq": 9,
            "state": "approved, no attempt",
            "latest_seq": 9,
            "latest_kind": "declared",
            "latest_at": 9.0,
        }
    ]
    assert cursor == 9
    assert [row["operation_id"] for row in later_rows] == ["op-later"]
    assert second["next_before"] is None
