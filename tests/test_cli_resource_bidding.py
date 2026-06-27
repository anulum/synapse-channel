# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the advisory resource bidding CLI

from __future__ import annotations

import argparse
import json

import pytest

from hub_e2e_helpers import AgentHandle, _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli, cli_resource_bidding
from synapse_channel.core.hub import SynapseHub


async def _seed_bidding_hub(uri: str) -> AgentHandle:
    """Declare a task, capability card, and resource offer on ``uri``."""
    handle = await connect_agent("FAST", uri)
    await handle.agent.post_task(
        "TRAIN",
        title="GPU python training",
        description="Run cuda training on local a100 hardware with an 80GB short queue.",
    )
    await handle.recorder.wait_for(
        lambda message: (
            message.get("type") == "ledger_task_posted"
            and message.get("task", {}).get("task_id") == "TRAIN"
        )
    )
    await handle.agent.advertise(
        description="GPU python training worker",
        skills=["python", "cuda"],
        task_classes=["training"],
    )
    await handle.recorder.wait_for(
        lambda message: (
            message.get("type") == "capability_advertised"
            and message.get("card", {}).get("agent") == "FAST"
        )
    )
    await handle.agent.send_message(
        "resource",
        kind="gpu",
        name="a100",
        capacity=4,
        meta={"memory": "80GB", "queue": "short"},
    )
    await handle.recorder.wait_for(
        lambda message: message.get("type") == "resource_offered" and message.get("agent") == "FAST"
    )
    return handle


def test_resource_bids_parser_wires_command() -> None:
    args = cli.build_parser().parse_args(
        ["resource-bids", "TRAIN", "--resource-kind", "gpu", "--limit", "2", "--json"]
    )

    assert args.command == "resource-bids"
    assert args.task_id == "TRAIN"
    assert args.resource_kind == "gpu"
    assert args.limit == 2
    assert args.func is cli_resource_bidding._cmd_resource_bids


async def test_resource_bids_prints_live_recommendations(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        handle = await _seed_bidding_hub(uri)
        try:
            code = await cli_resource_bidding._resource_bids(
                uri=uri,
                name="BIDDER",
                task_id="TRAIN",
                resource_kind="gpu",
            )
        finally:
            await close_agents(handle)

    assert code == 0
    out = capsys.readouterr().out
    assert "Resource bids for TRAIN (1 candidates)" in out
    assert "FAST gpu/a100 score=51 capacity=4" in out
    assert "resource_kind:gpu" in out
    assert "Advisory only" in out


async def test_resource_bids_prints_json(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        handle = await _seed_bidding_hub(uri)
        try:
            code = await cli_resource_bidding._resource_bids(
                uri=uri,
                name="BIDDER",
                task_id="TRAIN",
                resource_kind="gpu",
                as_json=True,
            )
        finally:
            await close_agents(handle)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task_id"] == "TRAIN"
    assert payload["candidates"][0]["resource_id"] == "resource:FAST:gpu:a100"
    assert payload["candidates"][0]["reasons"][0] == "resource_kind:gpu"


async def test_resource_bids_reports_missing_task(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        handle = await _seed_bidding_hub(uri)
        try:
            code = await cli_resource_bidding._resource_bids(
                uri=uri,
                name="BIDDER",
                task_id="MISSING",
            )
        finally:
            await close_agents(handle)

    assert code == 1
    assert "Task MISSING is not on the board" in capsys.readouterr().out


async def test_resource_bids_reports_unreachable_hub(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = await cli_resource_bidding._resource_bids(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="BIDDER",
        task_id="TRAIN",
        ready_timeout=0.1,
    )

    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


def test_cmd_resource_bids_dispatches_real_query() -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="BIDDER",
        task_id="TRAIN",
        token=None,
        ready_timeout=0.1,
        response_timeout=0.1,
        resource_kind=None,
        limit=5,
        include_zero=False,
        json=False,
    )

    assert cli_resource_bidding._cmd_resource_bids(ns) == 1
