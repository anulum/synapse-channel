# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — a peer hub's observed state as an advisory scope
"""A ``peer:<hub id>`` scope shows the observed peer alone, every row advisory."""

from __future__ import annotations

import json
from dataclasses import replace
from http import HTTPStatus
from pathlib import Path

import pytest

from dashboard_scope_fleet import scope_fleet
from synapse_channel.core.journal import EventKind
from synapse_channel.core.multihub_fold import fold_observed_state
from synapse_channel.core.multihub_merge import HubEvent
from synapse_channel.dashboard import DashboardSnapshot
from synapse_channel.dashboard_feed_scope import PEER_FEED_REASON, scoped_feed
from synapse_channel.dashboard_feed_serving import json_response
from synapse_channel.dashboard_peer_scope import NOT_OBSERVED
from synapse_channel.dashboard_scope import DashboardScope, parse_scope, scoped_document
from synapse_channel.observed_peers import ObservedPeerSnapshot

EAST = DashboardScope("peer", peer="east")


def _east_events() -> list[HubEvent]:
    """Return the east hub's log: two tasks, a claim on one, a note, and a released claim."""
    task = EventKind.LEDGER_TASK
    return [
        HubEvent("east", 1, 1.0, task, {"task_id": "E1", "title": "east one", "status": "open"}),
        HubEvent("east", 2, 2.0, task, {"task_id": "E2", "title": "east two", "status": "open"}),
        HubEvent(
            "east", 3, 3.0, EventKind.CLAIM, {"task_id": "E1", "owner": "EAST/api", "paths": ["a"]}
        ),
        HubEvent("east", 4, 4.0, EventKind.LEDGER_PROGRESS, {"task_id": "E1", "text": "started"}),
        HubEvent("east", 5, 5.0, EventKind.CLAIM, {"task_id": "E2", "owner": "EAST/web"}),
        HubEvent("east", 6, 6.0, EventKind.RELEASE, {"task_id": "E2", "owner": "EAST/web"}),
    ]


def _with_peer(peer: ObservedPeerSnapshot) -> DashboardSnapshot:
    """Return the seeded local fleet with ``peer`` as its one observed hub."""
    return replace(scope_fleet(), observed_peers=(peer,))


def _east(*, reachable: bool = True) -> ObservedPeerSnapshot:
    """Return the east hub as pulled; an unreachable pull keeps a fold to prove it is not shown."""
    return ObservedPeerSnapshot(
        hub_id="east",
        uri="ws://east",
        reachable=reachable,
        cursor=6,
        log_end_seq=9,
        state=fold_observed_state(_east_events()),
        error="" if reachable else "connection refused",
        clock_skew_seconds=-1.5,
    )


def test_a_peer_scope_is_parsed_named_and_holds_no_local_record() -> None:
    scope = parse_scope(["peer:east"])

    assert scope == EAST and scope.text == "peer:east"
    assert not any(scope.holds(namespace) for namespace in ("", "ALPHA", "east"))
    assert parse_scope(["peer:" + "h" * 256]).peer == "h" * 256


def test_the_peer_document_holds_the_peer_alone_and_says_advisory() -> None:
    document = scoped_document(_with_peer(_east()), EAST)

    assert document["advisory"] is True and document["hub_id"] == "east"
    assert [task["task_id"] for task in document["board"]["tasks"]] == ["E1", "E2"]
    claims = document["state"]["active_claims"]
    assert [(claim["task_id"], claim["owner"], claim["paths"]) for claim in claims] == [
        ("E1", "EAST/api", ["a"])
    ]
    for row in [*document["board"]["tasks"], *claims]:
        assert (row["advisory"], row["observed_from"]) == (True, "east")
    assert document["online_agents"] == [] and document["agent_roles"] == {}
    assert document["observed_peers"] == [] and document["manifest"] == []
    assert document["board"]["progress"] == [{"task_id": "E1", "text": "started"}]


def test_a_peer_s_seats_are_observed_and_counted_in_no_local_seat_state() -> None:
    document = scoped_document(_with_peer(_east()), EAST)
    local = scoped_document(_with_peer(_east()), parse_scope([]))

    assert {seat["identity"]: seat["state"] for seat in document["fleet"]["seats"]} == {
        "EAST/api": "observed"
    }
    seats = {
        key: document["headline"][key] for key in document["headline"] if key.startswith("seats_")
    }
    assert seats and set(seats.values()) == {0}
    assert "observed_from" not in document and "observed_from" not in local
    assert "observed" not in {seat["state"] for seat in local["fleet"]["seats"]}


def test_the_peer_block_states_its_pull_and_the_scope_block_its_counts() -> None:
    document = scoped_document(_with_peer(_east()), EAST)

    assert document["peer"] == {
        "hub_id": "east",
        "observed": True,
        "reachable": True,
        "cursor": 6,
        "log_end_seq": 9,
        "lag": 3,
        "clock_skew_seconds": -1.5,
        "observed_agents": ["EAST/api"],
        "board_conflicts": 0,
        "error": "",
    }
    scope = document["scope"]
    assert (scope["scope"], scope["applied"], scope["advisory"]) == ("peer:east", True, True)
    assert (scope["tasks_in_scope"], scope["tasks_total"]) == (2, 5)
    assert (scope["claims_in_scope"], scope["claims_total"]) == (1, 3)
    assert (scope["agents_in_scope"], scope["peers"]) == (0, ["east"])


def test_an_unreachable_or_unobserved_peer_shows_no_row_and_says_why() -> None:
    unreachable = scoped_document(_with_peer(_east(reachable=False)), EAST)
    unobserved = scoped_document(_with_peer(_east()), DashboardScope("peer", peer="west"))

    for document in (unreachable, unobserved):
        assert document["advisory"] is True
        assert document["board"]["tasks"] == [] and document["state"]["active_claims"] == []
        assert document["board"]["progress"] == []
    assert unreachable["peer"]["reachable"] is False
    assert unreachable["peer"]["error"] == "connection refused"
    assert unobserved["peer"] == {
        "hub_id": "west",
        "observed": False,
        "reachable": False,
        "error": NOT_OBSERVED,
    }


def test_the_local_document_lists_the_peers_and_keeps_the_a2a_summary(tmp_path: Path) -> None:
    missing = tmp_path / "absent-a2a.json"
    local = scoped_document(_with_peer(_east()), parse_scope([]), a2a_state_file=missing)
    peer = scoped_document(_with_peer(_east()), EAST, a2a_state_file=missing)

    assert local["scope"]["peers"] == ["east"] and local["scope"]["advisory"] is False
    assert "advisory" not in local and "peer" not in local
    assert local["fleet"]["a2a"] != peer["fleet"]["a2a"]
    assert peer["fleet"]["a2a"] == scoped_document(_with_peer(_east()), EAST)["fleet"]["a2a"]


@pytest.mark.parametrize("path", ["/events.json", "/operator-actions.json"])
def test_a_feed_answers_a_peer_scope_with_its_local_rows_and_says_so(
    path: str, tmp_path: Path
) -> None:
    rows = {"events": [{"seq": 1, "actor": "ALPHA/worker"}], "next_cursor": 1}
    answer = scoped_feed(path, json_response(rows), EAST, tmp_path / "hub.db")

    assert answer is not None and answer.status == HTTPStatus.OK
    assert json.loads(answer.body) == {
        **rows,
        "scope": {"scope": "peer:east", "applied": False, "reason": PEER_FEED_REASON},
    }
