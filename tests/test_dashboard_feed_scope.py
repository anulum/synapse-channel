# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the event-backed feeds under a scope, over a real durable log
"""Each scoped feed keeps exactly its rows; every other answer passes or says it was not scoped."""

from __future__ import annotations

import json
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest

from dashboard_feed_scope_store import seed_scoped_store
from synapse_channel.dashboard_feed_scope import feed_scope, scoped_feed
from synapse_channel.dashboard_feed_serving import FeedResponse, json_response, plain_response
from synapse_channel.dashboard_scope import WHOLE_HUB, DashboardScope
from synapse_channel.dashboard_store_feeds import (
    build_events_tail,
    build_findings_feed,
    build_receipts_feed,
    build_waits_feed,
    store_task_projects,
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "hub.db"
    seed_scoped_store(path)
    return path


def _scope(text: str) -> DashboardScope:
    return feed_scope(f"scope={text}")


def _scoped(path: str, document: dict[str, Any], text: str, db: Path | None) -> dict[str, Any]:
    response = scoped_feed(path, json_response(document), _scope(text), db)
    assert response is not None and response.status == HTTPStatus.OK
    result: dict[str, Any] = json.loads(response.body)
    return result


def test_each_declared_task_is_read_with_its_project(db: Path) -> None:
    assert store_task_projects(db) == {
        "A-1": "ALPHA",
        "A-2": "ALPHA",
        "B-1": "BETA",
        "B-2": "BETA",
        "B-3": "BETA",
        "U-1": "",
        "U-2": "",
    }


_FEEDS: dict[str, tuple[Callable[[Path], dict[str, Any]], str, Callable[[dict[str, Any]], Any]]] = {
    "/events.json": (build_events_tail, "events", lambda row: row["seq"]),
    "/receipts.json": (build_receipts_feed, "receipts", lambda row: row["seq"]),
    "/waits.json": (build_waits_feed, "waits", lambda row: row["task_id"]),
    "/findings.json": (build_findings_feed, "findings", lambda row: row["statement"]),
}


@pytest.mark.parametrize(
    ("path", "scope", "expected"),
    [
        ("/events.json", "project:ALPHA", [1, 2, 8]),
        ("/events.json", "project:BETA", [3, 4, 5, 9]),
        ("/events.json", "project:GAMMA", [5, 9]),
        ("/events.json", "none", [6, 7, 10]),
        ("/events.json", "project:ZETA", []),
        ("/receipts.json", "project:ALPHA", [8]),
        ("/receipts.json", "project:BETA", [9]),
        ("/receipts.json", "project:GAMMA", [9]),
        ("/receipts.json", "none", [10]),
        ("/receipts.json", "project:ZETA", []),
        ("/waits.json", "project:ALPHA", ["A-2"]),
        ("/waits.json", "project:BETA", ["B-2", "B-3"]),
        ("/waits.json", "none", ["U-2"]),
        ("/waits.json", "project:GAMMA", []),
        ("/findings.json", "project:ALPHA", ["alpha finding"]),
        ("/findings.json", "project:BETA", ["beta finding"]),
        ("/findings.json", "none", ["solo finding"]),
    ],
)
def test_a_scoped_feed_keeps_exactly_the_rows_of_its_scope(
    db: Path, path: str, scope: str, expected: list[Any]
) -> None:
    build, key, ident = _FEEDS[path]
    whole = build(db)
    scoped = _scoped(path, whole, scope, db)

    assert [ident(row) for row in scoped[key]] == expected
    assert scoped["scope"] == {
        "scope": scope,
        "applied": True,
        "rows_in_page": len(whole[key]),
        "rows_in_scope": len(expected),
    }


def test_a_feeds_own_count_follows_its_scoped_rows(db: Path) -> None:
    receipts = _scoped("/receipts.json", build_receipts_feed(db), "project:BETA", db)
    waits = _scoped("/waits.json", build_waits_feed(db), "none", db)

    assert (receipts["receipt_count"], waits["wait_count"]) == (1, 1)
    assert receipts["next_cursor"] == build_receipts_feed(db)["next_cursor"]


def test_an_attention_alert_counts_only_by_a_subject_that_is_a_task(db: Path) -> None:
    alerts = [{"subject": subject} for subject in ("A-1", "U-1", "sha256:abc", "ALPHA/x")]
    document = {"version": 1, "alerts": alerts, "remaining": 0}

    alpha = _scoped("/attention.json", document, "project:ALPHA", db)
    unscoped = _scoped("/attention.json", document, "none", db)

    assert [row["subject"] for row in alpha["alerts"]] == ["A-1"]
    assert [row["subject"] for row in unscoped["alerts"]] == ["U-1"]


def test_without_an_event_store_a_filtered_feed_says_it_was_not_scoped() -> None:
    document = {"version": 1, "alerts": [{"subject": "A-1"}], "remaining": 0}

    answer = _scoped("/attention.json", document, "project:ALPHA", None)

    assert answer["alerts"] == document["alerts"]
    assert answer["scope"] == {
        "scope": "project:ALPHA",
        "applied": False,
        "reason": "no event store to place a task by its project",
    }


def test_every_other_feed_keeps_its_body_and_says_it_was_not_scoped(db: Path) -> None:
    answer = _scoped("/sessions.json", {"sessions": [1, 2]}, "project:ALPHA", db)

    assert answer == {"sessions": [1, 2], "scope": {"scope": "project:ALPHA", "applied": False}}


@pytest.mark.parametrize(
    "response",
    [
        plain_response(HTTPStatus.NOT_FOUND, "events feed is off"),
        FeedResponse(HTTPStatus.OK, b"<html></html>", "text/html; charset=utf-8"),
        FeedResponse(HTTPStatus.OK, b"[1, 2]", "application/json"),
    ],
    ids=["refusal", "not-json", "json-array"],
)
def test_an_answer_that_is_no_json_object_passes_unchanged(
    db: Path, response: FeedResponse
) -> None:
    assert scoped_feed("/events.json", response, _scope("project:ALPHA"), db) is response


def test_a_path_that_no_feed_serves_passes_through(db: Path) -> None:
    assert scoped_feed("/nowhere.json", None, _scope("project:ALPHA"), db) is None


def test_the_whole_hub_leaves_every_feed_as_it_was(db: Path) -> None:
    response = json_response(build_events_tail(db))

    assert scoped_feed("/events.json", response, WHOLE_HUB, db) is response
    assert feed_scope("since=0") == WHOLE_HUB


def test_a_store_that_cannot_place_tasks_fails_visibly(tmp_path: Path) -> None:
    response = json_response({"events": []})

    answer = scoped_feed("/events.json", response, _scope("none"), tmp_path / "gone.db")

    assert answer is not None
    assert (answer.status, answer.body) == (
        HTTPStatus.SERVICE_UNAVAILABLE,
        b"feed scope unavailable\n",
    )


@pytest.mark.parametrize("query", ["scope=project:a/b", "scope=project:", "scope=all&scope=none"])
def test_a_malformed_scope_is_refused_with_the_allowed_forms(query: str) -> None:
    with pytest.raises(
        ValueError, match="all, none, project:<namespace>, group:<name> or peer:<hub id>"
    ):
        feed_scope(query)
