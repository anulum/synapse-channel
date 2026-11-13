# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the bounded snapshot, its weak tag and 304 through real HTTP
"""Serve the bounded snapshot through the production dashboard server.

The fake fleet behind the server stamps every answer the way a real hub does:
``state.generated_at`` and the observer's ``last_seen`` move on each fetch
(measured against a running hub on 2026-11-13), so the tag must hold across
those stamps and move with anything else.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import synapse_channel.dashboard as dashboard_module
from dashboard_helpers import _feeds_server
from synapse_channel.dashboard import DashboardServer, DashboardSnapshot
from synapse_channel.dashboard_live_transport import snapshot_fingerprint

TOKEN = "bounded-snapshot-secret"


@dataclass
class _Fleet:
    """A fleet whose answers carry fresh per-fetch stamps, as a real hub's do."""

    online: list[str] = field(default_factory=lambda: ["BOUNDS/worker"])
    open_status: str = "open"
    clock: Iterator[int] = field(default_factory=lambda: itertools.count(1))

    async def fetch(self, **_kwargs: object) -> DashboardSnapshot:
        stamp = 1_783_000_000.0 + next(self.clock)
        return DashboardSnapshot(
            online_agents=list(self.online),
            state={
                "active_claims": [],
                "agents": [{"agent": name, "last_seen": stamp} for name in self.online],
                "generated_at": stamp,
            },
            board={
                "tasks": [
                    {
                        "task_id": "OPEN",
                        "title": "Open",
                        "status": self.open_status,
                        "depends_on": ["DONE"],
                    },
                    {"task_id": "DONE", "title": "Done", "status": "done", "depends_on": []},
                    {"task_id": "GONE", "title": "Gone", "status": "cancelled", "depends_on": []},
                ],
                "progress": [
                    {"task_id": "OPEN", "author": "BOUNDS/worker", "kind": "note", "text": "n"}
                ],
            },
            manifest=[],
            hub_id="hub-bounds",
        )


@pytest.fixture
def fleet() -> _Fleet:
    return _Fleet()


@pytest.fixture
def server(fleet: _Fleet, monkeypatch: pytest.MonkeyPatch) -> Iterator[DashboardServer]:
    monkeypatch.setattr(dashboard_module, "fetch_dashboard_snapshot", fleet.fetch)
    running = _feeds_server(dashboard_token=TOKEN)
    try:
        yield running
    finally:
        running.close()


def _get(
    server: DashboardServer, path: str, *, token: str | None = TOKEN, etag: str | None = None
) -> tuple[int, dict[str, str], bytes]:
    headers = {"Connection": "close"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if etag is not None:
        headers["If-None-Match"] = etag
    request = Request(server.url(path), headers=headers)
    try:
        with urlopen(request, timeout=5) as response:  # nosec B310 - owned loopback server
            return response.status, dict(response.headers), response.read()
    except HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_the_snapshot_is_bounded_and_carries_a_weak_tag(server: DashboardServer) -> None:
    status, headers, body = _get(server, "/snapshot.json")
    document = json.loads(body)

    assert status == 200
    assert headers["ETag"] == f'W/"{snapshot_fingerprint(document)}"'
    assert headers["Cache-Control"] == "no-store"
    assert [task["task_id"] for task in document["board"]["tasks"]] == ["OPEN"]
    assert document["bounds"]["closed_omitted"] == 2
    assert document["bounds"]["tasks_by_status"] == {"cancelled": 1, "done": 1, "open": 1}
    graph = document["fleet"]["task_graph"]
    assert [node["task_id"] for node in graph["nodes"]] == ["OPEN"]
    assert [(edge["from"], edge["to"], edge["satisfied"]) for edge in graph["edges"]] == [
        ("DONE", "OPEN", True)
    ]


def test_answers_differing_only_in_the_stamps_share_one_tag_and_304(
    server: DashboardServer,
) -> None:
    _, first_headers, first = _get(server, "/snapshot.json")
    _, second_headers, second = _get(server, "/snapshot.json")
    etag = first_headers["ETag"]
    one, two = json.loads(first), json.loads(second)

    assert one["state"]["generated_at"] != two["state"]["generated_at"]
    assert one["state"]["agents"][0]["last_seen"] != two["state"]["agents"][0]["last_seen"]
    assert second_headers["ETag"] == etag
    for offered in (etag, etag.removeprefix("W/"), "*", f'"other", {etag}'):
        status, repeat, body = _get(server, "/snapshot.json", etag=offered)
        assert (status, body, repeat["ETag"]) == (304, b"", etag)
        assert repeat["Cache-Control"] == "no-store"
    status, _, body = _get(server, "/snapshot.json", etag='W/"other"')
    assert status == 200 and json.loads(body)["hub_id"] == "hub-bounds"


@pytest.mark.parametrize("change", ["agent joins", "agent leaves", "task status"])
def test_any_other_change_moves_the_tag(
    server: DashboardServer, fleet: _Fleet, change: str
) -> None:
    _, headers, _ = _get(server, "/snapshot.json")
    if change == "agent joins":
        fleet.online.append("BOUNDS/reviewer")
    elif change == "agent leaves":
        fleet.online.clear()
    else:
        fleet.open_status = "in_progress"
    status, moved, body = _get(server, "/snapshot.json", etag=headers["ETag"])

    assert status == 200
    assert moved["ETag"] != headers["ETag"]
    assert moved["ETag"] == f'W/"{snapshot_fingerprint(json.loads(body))}"'


def test_the_token_check_runs_before_the_tag_comparison(server: DashboardServer) -> None:
    _, headers, _ = _get(server, "/snapshot.json")

    for token in (None, "wrong-secret"):
        status, denied, body = _get(server, "/snapshot.json", token=token, etag=headers["ETag"])
        assert status == 401
        assert "ETag" not in denied
        assert b"hub-bounds" not in body


def test_closed_tasks_come_on_request_with_their_own_tag(server: DashboardServer) -> None:
    _, open_headers, _ = _get(server, "/snapshot.json?closed=0")
    status, headers, body = _get(server, "/snapshot.json?closed=1")
    document = json.loads(body)

    assert status == 200
    assert headers["ETag"] != open_headers["ETag"]
    assert [task["task_id"] for task in document["board"]["tasks"]] == ["OPEN", "DONE", "GONE"]
    assert document["bounds"]["closed_included"] is True
    assert _get(server, "/snapshot.json?closed=1", etag=open_headers["ETag"])[0] == 200
    assert _get(server, "/snapshot.json?closed=1", etag=headers["ETag"])[0] == 304


@pytest.mark.parametrize("query", ["closed=2", "closed=yes", "closed=", "closed=1&closed=1"])
def test_a_closed_value_other_than_0_or_1_is_refused(server: DashboardServer, query: str) -> None:
    status, headers, body = _get(server, f"/snapshot.json?{query}")

    assert (status, body) == (400, b"closed must be 0 or 1\n")
    assert "ETag" not in headers


def test_studio_counts_the_closed_tasks_it_did_not_load(server: DashboardServer) -> None:
    status, _, body = _get(server, "/studio.json")
    projection = json.loads(body)["tasks"]["columns"]
    columns = {column["id"]: column for column in projection["columns"]}

    assert status == 200
    assert (columns["open"]["count"], columns["open"]["omitted"]) == (1, 0)
    assert columns["closed"]["tasks"] == []
    assert (columns["closed"]["count"], columns["closed"]["omitted"]) == (2, 2)
    assert (projection["declared_tasks"], projection["total_declared_tasks"]) == (1, 3)


def test_the_live_channel_sends_the_bounded_document_then_unchanged(
    server: DashboardServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    status, _, body = _get(server, "/live.ndjson?cycles=2")
    frames = [json.loads(line) for line in body.splitlines()]
    snapshots = [frame for frame in frames if frame.get("channel") == "snapshot"]

    assert status == 200
    assert [task["task_id"] for task in snapshots[0]["data"]["board"]["tasks"]] == ["OPEN"]
    assert snapshots[0]["data"]["bounds"]["closed_omitted"] == 2
    # The second fetch carried new stamps; the channel still reports no change.
    assert snapshots[1]["status"] == "unchanged"
    assert "data" not in snapshots[1]
