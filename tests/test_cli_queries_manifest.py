# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the read-only hub query commands (who/state/board/manifest/health)

from __future__ import annotations

import argparse

import pytest

from hub_e2e_helpers import AgentHandle, _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli_queries
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub


def test_print_manifest_renders_cards(capsys: pytest.CaptureFixture[str]) -> None:
    manifest = [
        {"agent": "FAST", "task_classes": ["chat"], "model": "m", "description": "quick"},
        {"agent": "BARE", "task_classes": [], "model": "", "description": ""},
    ]
    cli_queries._print_manifest(manifest)
    out = capsys.readouterr().out
    assert "FAST [chat] model=m: quick" in out
    assert "BARE [none] model=-:" in out


async def _advertise_manifest_agent(
    uri: str, name: str, *, token: str | None = None
) -> AgentHandle:
    handle = await connect_agent(name, uri, token=token)
    await handle.agent.advertise(description="q", task_classes=["chat"], model="m")
    await handle.recorder.wait_for(
        lambda message: (
            message.get("type") == "capability_advertised"
            and message.get("card", {}).get("agent") == name
        )
    )
    return handle


async def test_manifest_prints_snapshot(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        handle = await _advertise_manifest_agent(uri, "FAST")
        try:
            code = await cli_queries._manifest(uri=uri, name="USER")
        finally:
            await close_agents(handle)

    assert code == 0
    assert "FAST [chat] model=m: q" in capsys.readouterr().out


async def test_manifest_reports_unreachable_hub(capsys: pytest.CaptureFixture[str]) -> None:
    code = await cli_queries._manifest(
        uri=f"ws://127.0.0.1:{_free_port()}", name="USER", ready_timeout=0.1
    )
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_manifest_returns_quietly_when_no_snapshot(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        code = await cli_queries._query_hub(
            uri=uri,
            name="USER",
            token=None,
            response_type="not_a_real_snapshot_type",
            request=lambda agent: agent.request_manifest(),
            transform=lambda data: data.get("manifest", []),
            render=cli_queries._print_manifest,
            attempts=1,
        )

    assert code == 0
    assert "Agents" not in capsys.readouterr().out


async def test_manifest_threads_token_to_agent(capsys: pytest.CaptureFixture[str]) -> None:
    token = "s3cret"
    async with running_hub(SynapseHub(authenticator=TokenAuthenticator([token]))) as (_hub, uri):
        handle = await _advertise_manifest_agent(uri, "FAST", token=token)
        try:
            code = await cli_queries._manifest(uri=uri, name="USER", token=token)
        finally:
            await close_agents(handle)

    assert code == 0
    assert "FAST [chat] model=m: q" in capsys.readouterr().out


def test_cmd_manifest_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.cli_queries.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(uri="ws://h", name="USER", token=None)
    assert cli_queries._cmd_manifest(ns) == 0
