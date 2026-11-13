# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one headline block, computed once on the whole board
"""Every headline counter has one definition, and every surface reads the block."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any
from urllib.request import Request, urlopen

import pytest

import synapse_channel.dashboard as dashboard_module
from dashboard_bounds_helpers import seeded_snapshot
from dashboard_helpers import _feeds_server
from synapse_channel.dashboard import DashboardServer, DashboardSnapshot
from synapse_channel.dashboard_bounds import bound_snapshot
from synapse_channel.dashboard_headline import HEADLINE_KEYS, build_headline
from synapse_channel.dashboard_live_transport import snapshot_fingerprint
from synapse_channel.dashboard_render import render_dashboard_html

TOKEN = "headline-secret"


def _blocked_identity(headline: dict[str, int]) -> None:
    assert headline["tasks_blocked"] == (
        headline["tasks_blocked_marked"] + headline["tasks_blocked_waiting"]
    )


def _plan_snapshot(*, lease_expires_at: float = 1e12) -> DashboardSnapshot:
    """A plan with every kind of task the definitions tell apart."""
    tasks = [
        {"task_id": "DONE", "status": "done", "depends_on": []},
        {"task_id": "GONE", "status": "cancelled", "depends_on": ["NOWHERE"]},
        {"task_id": "WORK", "status": "in_progress", "depends_on": []},
        {"task_id": "READY", "status": "open", "depends_on": ["DONE"]},
        {"task_id": "WAITS", "status": "open", "depends_on": ["WORK"]},
        {"task_id": "ORPHAN", "status": "open", "depends_on": ["NOWHERE"]},
        {"task_id": "MARKED", "status": "blocked", "depends_on": []},
        {"task_id": "MARKED-GATED", "status": "blocked", "depends_on": ["WORK"]},
        {"task_id": "WORK-GATED", "status": "in_progress", "depends_on": ["WAITS"]},
    ]
    return DashboardSnapshot(
        online_agents=["PLAN/worker", "PLAN/worker-rx", "PLAN/lonely"],
        state={
            "active_claims": [
                {
                    "task_id": "WORK",
                    "owner": "PLAN/worker",
                    "status": "working",
                    "lease_expires_at": lease_expires_at,
                    "paths": ["src/work.py"],
                }
            ]
        },
        board={"tasks": tasks, "ready": ["READY"]},
        manifest=[],
        hub_id="hub-headline",
    )


def test_each_counter_has_its_one_definition() -> None:
    headline = _plan_snapshot().to_dict()["headline"]

    assert list(headline) == list(HEADLINE_KEYS)
    assert headline["agents_live"] == 2  # the waiter sidecar is not an agent
    assert headline["waiters_missing"] == 1  # PLAN/lonely has no -rx
    assert (headline["claims_active"], headline["claims_stale"]) == (1, 0)
    assert headline["tasks_open"] == 7
    assert headline["tasks_closed"] == 2  # done and cancelled
    assert headline["tasks_in_progress"] == 2
    assert headline["tasks_ready"] == 1
    # cannot start: MARKED, MARKED-GATED (marked); WAITS, ORPHAN (open and waiting).
    # WORK-GATED is in progress: it has started, so its unfinished prerequisite does not count.
    assert headline["tasks_blocked_marked"] == 2
    assert headline["tasks_blocked_waiting"] == 2
    assert headline["tasks_blocked"] == 4
    # PLAN/worker (its waiter is connected) and PLAN/lonely are listening.
    assert (headline["seats_listening"], headline["seats_active_not_listening"]) == (2, 0)
    assert headline["seats_silent"] == 0
    assert headline["seats_deaf"] == 0  # the hub sent no judgement, so none is counted
    _blocked_identity(headline)


def test_risk_counters_split_the_signals_by_level() -> None:
    document = _plan_snapshot(lease_expires_at=1.0).to_dict()
    headline, signals = document["headline"], document["risk"]["signals"]

    assert headline["risk_signals"] == len(signals)
    assert headline["risk_signals_red"] == sum(1 for s in signals if s["level"] == "red") >= 1
    assert headline["risk_signals_amber"] == sum(1 for s in signals if s["level"] == "amber") >= 1
    assert headline["risk_signals"] == headline["risk_signals_red"] + headline["risk_signals_amber"]
    assert (headline["claims_active"], headline["claims_stale"]) == (0, 1)


def test_the_block_is_computed_on_the_whole_board_and_survives_the_bound() -> None:
    full = seeded_snapshot().to_dict()
    bounded = bound_snapshot(full)

    assert bounded["headline"] == full["headline"]
    assert full["headline"]["tasks_closed"] == 650  # never derived from what was sent
    assert len(bounded["board"]["tasks"]) == 350
    assert full["headline"]["tasks_open"] == 350
    assert full["headline"]["tasks_in_progress"] == 50
    assert full["headline"]["tasks_blocked_marked"] == 50
    assert full["headline"]["tasks_blocked_waiting"] == 0  # every prerequisite is done
    _blocked_identity(full["headline"])


def test_an_expiring_lease_moves_the_weak_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """No task changes; the lease alone moves claims_active to claims_stale."""
    snapshot = _plan_snapshot(lease_expires_at=1_783_000_000.0)
    monkeypatch.setattr(time, "time", lambda: 1_782_999_999.0)
    before = bound_snapshot(snapshot.to_dict())
    monkeypatch.setattr(time, "time", lambda: 1_783_000_001.0)
    after = bound_snapshot(snapshot.to_dict())

    assert (before["headline"]["claims_active"], after["headline"]["claims_active"]) == (1, 0)
    assert after["headline"]["claims_stale"] == 1
    assert before["board"] == after["board"]
    assert snapshot_fingerprint(before) != snapshot_fingerprint(after)


def test_missing_and_malformed_sources_count_zero() -> None:
    headline = build_headline(
        {"tasks": "nope"},
        {"agents": {"live": "nope"}, "claims": {"active": True}, "task_graph": {"edges": 3}},
        {"signals": [None, {"level": "green"}]},
        {"peers_total": 2, "peers_reachable": 1},
    )

    assert headline["agents_live"] == headline["claims_active"] == headline["tasks_open"] == 0
    assert headline["risk_signals"] == 1 and headline["risk_signals_red"] == 0
    assert (headline["peers_total"], headline["peers_reachable"]) == (2, 1)
    _blocked_identity(headline)


def test_the_classic_page_shows_the_same_block() -> None:
    snapshot = _plan_snapshot()
    page = render_dashboard_html(snapshot, refresh_seconds=5)
    headline = snapshot.to_dict()["headline"]

    assert "<h2>Counters</h2>" in page
    for name in HEADLINE_KEYS:
        assert f"<li><code>{name}</code>: {headline[name]}</li>" in page
    assert "<h2>Tasks marked blocked</h2>" in page
    assert "<h2>Connected names (3)</h2>" in page


async def _plan(**_kwargs: object) -> DashboardSnapshot:
    return _plan_snapshot()


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[DashboardServer]:
    monkeypatch.setattr(dashboard_module, "fetch_dashboard_snapshot", _plan)
    running = _feeds_server(dashboard_token=TOKEN)
    try:
        yield running
    finally:
        running.close()


def _json(server: DashboardServer, path: str) -> dict[str, Any]:
    request = Request(
        server.url(path), headers={"Authorization": f"Bearer {TOKEN}", "Connection": "close"}
    )
    with urlopen(request, timeout=10) as response:  # nosec B310 - owned loopback server
        document = json.loads(response.read())
    assert isinstance(document, dict)
    return document


def test_studio_and_the_snapshot_serve_one_block(server: DashboardServer) -> None:
    snapshot = _json(server, "/snapshot.json")
    studio = _json(server, "/studio.json")

    assert studio["headline"] == snapshot["headline"]
    assert snapshot["headline"]["tasks_blocked"] == 4
