# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for A2A bridge CLI commands

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from synapse_channel import cli, cli_a2a


class FakeAgent:
    """Stand-in for SynapseAgent used by the A2A card query flow."""

    def __init__(
        self,
        name: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        uri: str,
        verbose: bool,
        token: str | None = None,
        ready: bool = True,
        inbound: list[dict[str, Any]] | None = None,
        idle: bool = True,
    ) -> None:
        self.name = name
        self.callback = callback
        self.uri = uri
        self.verbose = verbose
        self.token = token
        self.running = True
        self._ready = ready
        self._inbound = inbound or []
        self._idle = idle

    async def connect(self) -> None:
        """Deliver the scripted inbound messages and optionally idle."""
        for message in self._inbound:
            await self.callback(message)
        if self._idle:
            await asyncio.Event().wait()

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        """Return the scripted readiness result."""
        return self._ready

    async def request_manifest(self) -> None:
        """Record-free stub for the manifest request."""
        return None


def _factory(
    holder: list[FakeAgent],
    *,
    ready: bool = True,
    inbound: list[dict[str, Any]] | None = None,
    idle: bool = True,
) -> Callable[..., Any]:
    """Build a fake-agent factory for the A2A query tests."""

    def make(
        name: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        uri: str,
        verbose: bool,
        token: str | None = None,
    ) -> FakeAgent:
        agent = FakeAgent(
            name,
            callback,
            uri=uri,
            verbose=verbose,
            token=token,
            ready=ready,
            inbound=inbound,
            idle=idle,
        )
        holder.append(agent)
        return agent

    return make


def test_parser_a2a_card() -> None:
    args = cli.build_parser().parse_args(
        ["a2a-card", "--endpoint-url", "https://example.test/a2a/v1", "--bearer-auth"]
    )

    assert args.endpoint_url == "https://example.test/a2a/v1"
    assert args.bearer_auth is True
    assert args.func is cli_a2a._cmd_a2a_card


def test_parser_a2a_serve() -> None:
    args = cli.build_parser().parse_args(
        [
            "a2a-serve",
            "--endpoint-url",
            "https://example.test/a2a/v1",
            "--host",
            "127.0.0.1",
            "--port",
            "8899",
            "--bearer-auth",
            "--a2a-token",
            "secret",
            "--state-file",
            "/tmp/synapse-a2a-state.json",
            "--task-timeout",
            "30",
            "--subscribe-timeout",
            "0.25",
        ]
    )

    assert args.endpoint_url == "https://example.test/a2a/v1"
    assert args.host == "127.0.0.1"
    assert args.port == 8899
    assert args.bearer_auth is True
    assert args.a2a_token == "secret"
    assert args.state_file == "/tmp/synapse-a2a-state.json"
    assert args.task_timeout == 30.0
    assert args.subscribe_timeout == 0.25
    assert args.func is cli_a2a._cmd_a2a_serve


async def test_a2a_card_prints_manifest_projection(
    capsys: pytest.CaptureFixture[str],
) -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        {
            "type": "manifest_snapshot",
            "manifest": [
                {
                    "agent": "FAST",
                    "description": "quick worker",
                    "skills": ["chat"],
                    "task_classes": ["rule"],
                }
            ],
        }
    ]

    rc = await cli_a2a._a2a_card(
        uri="ws://hub",
        name="A2A-TEST",
        token="secret",
        endpoint_url="https://example.test/a2a/v1",
        bridge_name="Synapse Bridge",
        bearer_auth=True,
        agent_factory=_factory(holder, inbound=inbound, idle=False),
    )

    assert rc == 0
    assert holder[0].name == "A2A-TEST"
    assert holder[0].token == "secret"
    card = json.loads(capsys.readouterr().out)
    assert card["name"] == "Synapse Bridge"
    assert card["supportedInterfaces"][0]["url"] == "https://example.test/a2a/v1"
    assert card["skills"][0]["id"] == "synapse-fast"
    assert card["securityRequirements"] == [{"synapseBearer": []}]


async def test_a2a_card_reports_unreachable_hub(
    capsys: pytest.CaptureFixture[str],
) -> None:
    holder: list[FakeAgent] = []

    rc = await cli_a2a._a2a_card(
        uri="ws://hub",
        name="A2A-TEST",
        endpoint_url="https://example.test/a2a/v1",
        agent_factory=_factory(holder, ready=False, idle=False),
    )

    assert rc == 1
    assert "Could not reach hub" in capsys.readouterr().out
