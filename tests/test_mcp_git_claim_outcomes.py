# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real MCP Git claim recovery
"""Exercise MCP claim confirmation through the live translation bridge."""

from __future__ import annotations

from pathlib import Path

import pytest

from claim_outcome_helpers import ClaimProxy, claim_proxy
from cli_e2e_helpers import git_repo
from hub_e2e_helpers import running_hub
from mcp_server_helpers import start_bridge
from synapse_channel.mcp.server import build_mcp_server


async def test_mcp_recovers_lost_grant_then_confirms_without_renewal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MCP uses the same exact read-only confirmation and persists the fence."""
    monkeypatch.chdir(git_repo(tmp_path / "repo"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    async with running_hub() as (hub, upstream):
        proxy = ClaimProxy(upstream)
        async with claim_proxy(proxy) as uri:
            handle = await start_bridge(uri, request_timeout=0.01)
            try:
                result = await handle.bridge.git_claim("T", ["a.py"], reply_timeout=0.1)
                assert "claim confirmed" in result
                before = hub.state.claims["T"].as_dict()
                result = await handle.bridge.git_claim(
                    "T",
                    ["a.py"],
                    reply_timeout=0.1,
                    confirm_only=True,
                )
                assert "claim confirmed" in result
                assert hub.state.claims["T"].as_dict() == before
                assert proxy.requests.count("claim") == 1
                assert handle.bridge.agent.lease_epochs["T"] == before["epoch"]
                handle.bridge.request_timeout = 0.5
                assert "with receipt" in await handle.bridge.release("T")
            finally:
                await handle.close()


async def test_mcp_no_response_remains_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Losing the grant and confirmation cannot authorize work or imply denial."""
    monkeypatch.chdir(git_repo(tmp_path / "repo"))
    async with running_hub() as (_hub, upstream):
        proxy = ClaimProxy(upstream, drop_snapshots=True)
        async with claim_proxy(proxy) as uri:
            handle = await start_bridge(uri)
            try:
                result = await handle.bridge.git_claim("T", ["a.py"], reply_timeout=0.1)
                assert "claim outcome unknown" in result
                assert "confirm_only=true" in result
                assert proxy.requests.count("claim") == 1
            finally:
                await handle.close()


async def test_registered_mcp_tool_exposes_read_only_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registered tool schema reaches exact confirmation on the real hub."""
    monkeypatch.chdir(git_repo(tmp_path / "repo"))
    async with running_hub() as (hub, uri):
        handle = await start_bridge(uri)
        try:
            server = build_mcp_server(handle.bridge)
            await server.call_tool(
                "synapse_git_claim", {"task_id": "T", "paths": ["a.py"], "reply_timeout": 0.5}
            )
            before = hub.state.claims["T"].as_dict()
            confirmed = await server.call_tool(
                "synapse_git_claim",
                {"task_id": "T", "paths": ["a.py"], "reply_timeout": 0.5, "confirm_only": True},
            )
            assert "claim confirmed" in str(confirmed)
            assert hub.state.claims["T"].as_dict() == before
            unknown = await server.call_tool(
                "synapse_git_claim",
                {
                    "task_id": "ABSENT",
                    "paths": ["a.py"],
                    "reply_timeout": 0.1,
                    "confirm_only": True,
                },
            )
            assert "claim outcome unknown" in str(unknown)
        finally:
            await handle.close()


@pytest.mark.parametrize("deadline", [0.0, float("nan"), 301.0])
async def test_mcp_refuses_unbounded_deadline_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, deadline: float
) -> None:
    """Invalid MCP waits cannot become unbounded operations."""
    monkeypatch.chdir(git_repo(tmp_path / "repo"))
    async with running_hub() as (hub, uri):
        handle = await start_bridge(uri)
        try:
            response = await handle.bridge.git_claim("T", ["a.py"], reply_timeout=deadline)
            assert (
                response
                == "git claim refused: deadline must be finite, positive and at most 300 seconds"
            )
            assert not hub.state.claims
        finally:
            await handle.close()
