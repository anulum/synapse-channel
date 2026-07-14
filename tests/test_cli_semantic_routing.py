# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the semantic routing CLI

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import pytest

from hub_e2e_helpers import AgentHandle, _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli, cli_semantic_routing
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


async def _seed_routing_hub(uri: str) -> AgentHandle:
    """Declare a task and advertise a matching capability card on ``uri``."""
    handle = await connect_agent("FAST", uri)
    await handle.agent.post_task(
        "ROUTE-1",
        title="Repair websocket transport routing",
        description="Fix local hub websocket fallback.",
    )
    await handle.recorder.wait_for(
        lambda message: (
            message.get("type") == "ledger_task_posted"
            and message.get("task", {}).get("task_id") == "ROUTE-1"
        )
    )
    await handle.agent.advertise(
        description="Repairs local websocket routing and hub adapters.",
        skills=["websocket", "routing"],
        task_classes=["transport"],
        contracts=[{"task_class": "transport", "input_schema": {"type": "object"}}],
    )
    await handle.recorder.wait_for(
        lambda message: (
            message.get("type") == "capability_advertised"
            and message.get("card", {}).get("agent") == "FAST"
        )
    )
    return handle


def _seed_observation_store(path: Path) -> None:
    """Write one successful prior task for the live FAST agent."""
    store = EventStore(path)
    store.append(
        EventKind.LEDGER_TASK,
        {
            "task_id": "DONE",
            "title": "Websocket routing repair",
            "description": "Improved local hub fallback.",
            "depends_on": [],
            "status": "done",
            "suggested_owner": "",
            "created_by": "planner",
            "created_at": 1.0,
            "updated_at": 2.0,
        },
        ts=1.0,
        durable=True,
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "DONE",
            "author": "FAST",
            "kind": "assessment",
            "text": "release receipt: evidence=pytest -q; epistemic_status=supported",
            "posted_at": 3.0,
        },
        ts=3.0,
    )
    store.close()


def test_route_task_parser_wires_command() -> None:
    args = cli.build_parser().parse_args(
        ["route-task", "ROUTE-1", "--limit", "2", "--event-store", "events.db", "--json"]
    )

    assert args.command == "route-task"
    assert args.task_id == "ROUTE-1"
    assert args.limit == 2
    assert args.event_store == "events.db"
    assert args.json is True
    assert args.func is cli_semantic_routing._cmd_route_task


async def test_route_task_prints_live_recommendations(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        handle = await _seed_routing_hub(uri)
        try:
            code = await cli_semantic_routing._route_task(uri=uri, name="ROUTER", task_id="ROUTE-1")
        finally:
            await close_agents(handle)

    assert code == 0
    out = capsys.readouterr().out
    assert "Route recommendations for ROUTE-1" in out
    assert "FAST score=40" in out
    assert "task_class:transport" in out
    assert "Advisory only" in out


async def test_route_task_prints_json(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        handle = await _seed_routing_hub(uri)
        try:
            code = await cli_semantic_routing._route_task(
                uri=uri,
                name="ROUTER",
                task_id="ROUTE-1",
                as_json=True,
            )
        finally:
            await close_agents(handle)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task_id"] == "ROUTE-1"
    assert payload["candidates"][0]["agent"] == "FAST"
    assert payload["candidates"][0]["score"] == 40


async def test_route_task_uses_observed_event_store(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = tmp_path / "events.db"
    _seed_observation_store(db)
    async with running_hub(SynapseHub()) as (_, uri):
        handle = await _seed_routing_hub(uri)
        try:
            code = await cli_semantic_routing._route_task(
                uri=uri,
                name="ROUTER",
                task_id="ROUTE-1",
                event_store=str(db),
                as_json=True,
            )
        finally:
            await close_agents(handle)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["candidates"][0]["agent"] == "FAST"
    assert "observed:websocket" in payload["candidates"][0]["reasons"]
    assert payload["candidates"][0]["observed_evidence"] == [
        {
            "seq": 2,
            "task_id": "DONE",
            "tokens": ["fallback", "hub", "local", "repair", "routing", "websocket"],
        }
    ]


async def test_route_task_reports_missing_task(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        handle = await _seed_routing_hub(uri)
        try:
            code = await cli_semantic_routing._route_task(uri=uri, name="ROUTER", task_id="MISSING")
        finally:
            await close_agents(handle)

    assert code == 1
    assert "Task MISSING is not on the board" in capsys.readouterr().out


async def test_route_task_reports_unreachable_hub(capsys: pytest.CaptureFixture[str]) -> None:
    code = await cli_semantic_routing._route_task(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="ROUTER",
        task_id="ROUTE-1",
        ready_timeout=0.1,
    )

    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


def test_cmd_route_task_dispatches_real_query() -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="ROUTER",
        token=None,
        ready_timeout=0.1,
        response_timeout=0.1,
        task_id="ROUTE-1",
        limit=5,
        include_zero=False,
        event_store=None,
        json=False,
    )

    assert cli_semantic_routing._cmd_route_task(ns) == 1


# --- malformed-snapshot extractors and render fallback -------------------------


def test_extractors_drop_malformed_snapshot_shapes() -> None:
    """A hub reply with wrong-typed sections degrades to empty, never crashes."""
    assert cli_semantic_routing._cards({"manifest": "not-a-list"}) == []
    assert cli_semantic_routing._cards({"manifest": [{"agent": "A"}, "junk"]}) == [{"agent": "A"}]
    assert cli_semantic_routing._resources({"snapshot": "not-a-mapping"}) == []
    assert cli_semantic_routing._resources({"snapshot": {"resources": "junk"}}) == []
    assert cli_semantic_routing._resources({"snapshot": {"resources": [{"kind": "gpu"}, 3]}}) == [
        {"kind": "gpu"}
    ]
    assert cli_semantic_routing._board({"board": "not-a-mapping"}) == {}


def test_render_recommendation_prints_the_fallback_reason(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from synapse_channel.core.semantic_routing import RoutingRecommendation

    recommendation = RoutingRecommendation(
        task_id="T1",
        query="build",
        candidates=(),
        fallback_reason="no agent capability cards are available",
    )
    cli_semantic_routing._render_recommendation(recommendation)
    out = capsys.readouterr().out
    assert "Fallback: no agent capability cards are available" in out
    assert "Advisory only:" in out


def test_render_recommendation_makes_remote_controls_visible(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from synapse_channel.core.semantic_routing import RoutingCandidate, RoutingRecommendation

    hostile = "remote\x1b]52;c;YQ==\x07\nforged\u202e"
    recommendation = RoutingRecommendation(
        task_id=hostile,
        query="build",
        candidates=(
            RoutingCandidate(
                agent=hostile,
                score=1,
                reasons=(hostile,),
                task_classes=(hostile,),
                skills=(hostile,),
            ),
        ),
        fallback_reason=hostile,
        trust_boundary=hostile,
    )

    cli_semantic_routing._render_recommendation(recommendation)

    rendered = capsys.readouterr().out
    assert "remote\\x1b]52;c;YQ==\\x07\\nforged\\u202e" in rendered
    assert "\x1b" not in rendered
    assert "\x07" not in rendered
    assert "\u202e" not in rendered


async def test_route_task_reports_a_bad_observation_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """All snapshots answer, the task exists, but the observation store is absent."""
    async with running_hub(SynapseHub()) as (_, uri):
        handle = await _seed_routing_hub(uri)
        try:
            code = await cli_semantic_routing._route_task(
                uri=uri,
                name="ROUTER",
                task_id="ROUTE-1",
                event_store=str(tmp_path / "absent.db"),
            )
        finally:
            await close_agents(handle)
    assert code == 1
    assert "missing event store" in capsys.readouterr().out


class _SilentRoutingAgent:
    """Connects and reports ready, but never delivers a single snapshot."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.running = True

    async def connect(self) -> None:
        while self.running:
            await asyncio.sleep(0.01)

    async def wait_until_ready(self, *, timeout: float) -> bool:
        return True

    async def request_board(self) -> None:
        return None

    async def request_manifest(self) -> None:
        return None

    async def request_state(self) -> None:
        return None


async def test_route_task_names_the_snapshots_that_never_arrived(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A ready hub that answers nothing yields the named missing snapshots."""
    code = await cli_semantic_routing._route_task(
        uri="ws://unused",
        name="ROUTER",
        task_id="T1",
        agent_factory=_SilentRoutingAgent,  # type: ignore[arg-type]
        response_timeout=0.1,
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "did not return semantic routing snapshots" in out
    assert "board_snapshot" in out and "state_snapshot" in out
