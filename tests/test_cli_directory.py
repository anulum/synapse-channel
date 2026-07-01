# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the read-only capability directory CLI

from __future__ import annotations

import argparse
import asyncio
import json

import pytest

from hub_e2e_helpers import AgentHandle, _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli, cli_directory
from synapse_channel.core.hub import SynapseHub


async def _seed_directory_agent(uri: str) -> AgentHandle:
    """Advertise one capability card and resource offer on a live hub."""
    handle = await connect_agent("FAST", uri)
    await handle.agent.advertise(
        description="quick worker",
        skills=["ollama"],
        task_classes=["chat"],
        model="gemma3:4b",
        contracts=[{"task_class": "chat", "input_schema": {"type": "object"}}],
    )
    await handle.recorder.wait_for(
        lambda message: (
            message.get("type") == "capability_advertised"
            and message.get("card", {}).get("agent") == "FAST"
        )
    )
    await handle.agent.send_message("resource", kind="llm", name="gemma3:4b", capacity=2)
    await handle.recorder.wait_for(
        lambda message: message.get("type") == "resource_offered" and message.get("agent") == "FAST"
    )
    return handle


def test_directory_parser_wires_command() -> None:
    args = cli.build_parser().parse_args(
        ["directory", "--task-class", "chat", "--resource-kind", "llm", "--json"]
    )

    assert args.command == "directory"
    assert args.task_class == "chat"
    assert args.resource_kind == "llm"
    assert args.json is True
    assert args.func is cli_directory._cmd_directory


async def test_directory_prints_live_capabilities_and_resources(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        handle = await _seed_directory_agent(uri)
        try:
            code = await cli_directory._directory(uri=uri, name="USER")
        finally:
            await close_agents(handle)

    assert code == 0
    out = capsys.readouterr().out
    assert "Directory (2 entries)" in out
    assert "agent FAST [chat] skills=ollama model=gemma3:4b contracts=1" in out
    assert "resource FAST llm/gemma3:4b capacity=2" in out
    assert "discovery-only" in out


async def test_directory_filters_and_prints_json(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        handle = await _seed_directory_agent(uri)
        try:
            code = await cli_directory._directory(
                uri=uri,
                name="USER",
                task_class="chat",
                as_json=True,
            )
        finally:
            await close_agents(handle)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [entry["id"] for entry in payload["entries"]] == ["agent:FAST"]
    assert payload["trust_boundary"].startswith("Capability directory entries are discovery")


async def test_directory_reports_unreachable_hub(capsys: pytest.CaptureFixture[str]) -> None:
    code = await cli_directory._directory(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="USER",
        ready_timeout=0.1,
    )

    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


def test_cmd_directory_dispatches_real_query() -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="USER",
        token=None,
        ready_timeout=0.1,
        response_timeout=0.1,
        agent=None,
        task_class=None,
        skill=None,
        resource_kind=None,
        json=False,
    )

    assert cli_directory._cmd_directory(ns) == 1


# --- malformed-snapshot extractors and silent-hub branch ------------------------


def test_extractors_drop_malformed_snapshot_shapes() -> None:
    """A hub reply with wrong-typed sections degrades to empty, never crashes."""
    assert cli_directory._cards({"manifest": "not-a-list"}) == []
    assert cli_directory._cards({"manifest": [{"agent": "A"}, 7]}) == [{"agent": "A"}]
    assert cli_directory._resources({"snapshot": "not-a-mapping"}) == []
    assert cli_directory._resources({"snapshot": {"resources": "junk"}}) == []
    assert cli_directory._resources({"snapshot": {"resources": [{"kind": "gpu"}, 3]}}) == [
        {"kind": "gpu"}
    ]


class _SilentDirectoryAgent:
    """Connects and reports ready, but never delivers a single snapshot."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.running = True

    async def connect(self) -> None:
        while self.running:
            await asyncio.sleep(0.01)

    async def wait_until_ready(self, *, timeout: float) -> bool:
        return True

    async def request_manifest(self) -> None:
        return None

    async def request_state(self) -> None:
        return None


async def test_directory_names_the_snapshots_that_never_arrived(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A ready hub that answers nothing yields the named missing snapshots."""
    code = await cli_directory._directory(
        uri="ws://unused",
        name="DIR",
        agent_factory=_SilentDirectoryAgent,  # type: ignore[arg-type]
        response_timeout=0.1,
    )
    assert code == 1
    assert "did not return capability directory snapshots" in capsys.readouterr().out
