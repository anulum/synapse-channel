# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — feed scopes through the production dashboard server
"""A scope on a feed route reaches the feed through the server's one feed dispatch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard_feed_scope_store import seed_scoped_store
from dashboard_helpers import _authorized_get, _feeds_server


def _get_all(db: Path | None, *paths: str, groups: Path | None = None) -> list[tuple[int, str]]:
    """GET each path on one dashboard over ``db`` and return each status and body."""
    server = _feeds_server(reliability_db=db, scope_groups_file=groups)
    try:
        return [_authorized_get(server, path)[0::2] for path in paths]
    finally:
        server.close()


def test_a_scoped_feed_request_is_narrowed_and_others_say_so(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    seed_scoped_store(db)

    (scoped, whole, waits, sessions) = _get_all(
        db,
        "/events.json?scope=project:ALPHA",
        "/events.json",
        "/waits.json?scope=none",
        "/sessions.json?scope=project:ALPHA",
    )

    assert scoped[0] == 200
    assert [event["seq"] for event in json.loads(scoped[1])["events"]] == [1, 2, 8]
    assert "scope" not in json.loads(whole[1])
    assert [row["task_id"] for row in json.loads(waits[1])["waits"]] == ["U-2"]
    assert json.loads(sessions[1])["scope"] == {"scope": "project:ALPHA", "applied": False}


def test_a_malformed_scope_is_refused_before_the_feed_is_built(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    seed_scoped_store(db)

    (refused, capabilities) = _get_all(
        db, "/receipts.json?scope=project:a/b", "/capabilities.json?scope=project:a/b"
    )

    assert refused[0] == 400
    assert (
        refused[1]
        == "scope must be all, none, project:<namespace>, group:<name> or peer:<hub id>\n"
    )
    assert capabilities[0] == 200


def test_an_absent_feed_stays_absent_under_a_scope() -> None:
    ((status, body),) = _get_all(None, "/attention.json?scope=project:ALPHA")

    assert status == 404
    assert "--attention-store" in body


def test_the_live_streams_feed_channels_take_its_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    db = tmp_path / "hub.db"
    seed_scoped_store(db)

    ((status, stream),) = _get_all(db, "/live.ndjson?cycles=1&scope=project%3AALPHA")
    frames = {
        frame.get("channel"): frame
        for frame in (json.loads(line) for line in stream.splitlines())
        if frame.get("kind") == "channel"
    }

    assert status == 200
    events = frames["events"]["data"]
    assert [event["seq"] for event in events["events"]] == [1, 2, 8]
    assert events["scope"]["applied"] is True
    unscoped = frames["operator_actions"]["data"]["scope"]
    assert unscoped == {"scope": "project:ALPHA", "applied": False}


def test_a_group_scope_narrows_the_feeds_to_its_namespaces(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    seed_scoped_store(db)
    groups = tmp_path / "groups.json"
    groups.write_text(json.dumps({"version": 1, "groups": {"alpha-beta": ["ALPHA", "BETA"]}}))
    groups.chmod(0o600)

    (group, alpha, beta) = _get_all(
        db,
        "/events.json?scope=group:alpha-beta",
        "/events.json?scope=project:ALPHA",
        "/events.json?scope=project:BETA",
        groups=groups,
    )
    (refused,) = _get_all(db, "/events.json?scope=group:alpha-beta")

    def seqs(body: str) -> set[int]:
        return {event["seq"] for event in json.loads(body)["events"]}

    assert group[0] == 200
    assert seqs(group[1]) == seqs(alpha[1]) | seqs(beta[1])
    assert json.loads(group[1])["scope"]["scope"] == "group:alpha-beta"
    assert refused == (
        400,
        "group scopes need a groups file: start the dashboard with --scope-groups\n",
    )
