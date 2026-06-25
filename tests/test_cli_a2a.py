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


async def test_a2a_card_uses_explicit_description(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    inbound = [{"type": "manifest_snapshot", "manifest": []}]

    rc = await cli_a2a._a2a_card(
        uri="ws://hub",
        name="A2A-TEST",
        endpoint_url="https://example.test/a2a/v1",
        description="Explicit bridge description.",
        agent_factory=_factory(holder, inbound=inbound, idle=False),
    )

    assert rc == 0
    card = json.loads(capsys.readouterr().out)
    assert card["description"] == "Explicit bridge description."


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


async def test_fetch_manifest_filters_non_card_entries() -> None:
    holder: list[FakeAgent] = []
    inbound = [
        {
            "type": "manifest_snapshot",
            "manifest": [{"agent": "A"}, "bad", {"agent": "B"}],
        }
    ]

    manifest = await cli_a2a._fetch_manifest(
        uri="ws://hub",
        name="A2A-BOOT",
        token="secret",
        agent_factory=_factory(holder, inbound=inbound, idle=False),
    )

    assert manifest == [{"agent": "A"}, {"agent": "B"}]
    assert holder[0].token == "secret"


async def test_fetch_manifest_returns_none_when_hub_never_becomes_ready() -> None:
    manifest = await cli_a2a._fetch_manifest(
        uri="ws://hub",
        name="A2A-BOOT",
        token=None,
        agent_factory=_factory([], ready=False, idle=False),
    )

    assert manifest is None


async def test_fetch_manifest_returns_empty_without_valid_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_sleep = asyncio.sleep

    async def no_sleep(_delay: float) -> None:
        await original_sleep(0)
        return None

    monkeypatch.setattr("synapse_channel.cli_a2a.asyncio.sleep", no_sleep)
    manifest = await cli_a2a._fetch_manifest(
        uri="ws://hub",
        name="A2A-BOOT",
        token=None,
        agent_factory=_factory(
            [],
            inbound=[
                {"type": "chat", "payload": "ignored"},
                {"type": "manifest_snapshot", "manifest": "bad"},
            ],
            idle=False,
        ),
    )

    assert manifest == []


async def test_inbound_handler_ignores_missing_bridge_and_forwards_when_present() -> None:
    frames: list[dict[str, Any]] = []

    class Bridge:
        def handle_synapse_frame(self, data: dict[str, Any]) -> None:
            frames.append(data)

    await cli_a2a._a2a_inbound_handler({}, {"type": "chat"})
    await cli_a2a._a2a_inbound_handler({"bridge": Bridge()}, {"type": "chat", "payload": "x"})

    assert frames == [{"type": "chat", "payload": "x"}]


def test_cmd_a2a_card_dispatches_async_query(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_card(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "coro"

    monkeypatch.setattr(cli_a2a, "_a2a_card", fake_card)
    monkeypatch.setattr("synapse_channel.cli_a2a.asyncio.run", lambda coro: 0)
    ns = cli.build_parser().parse_args(
        [
            "a2a-card",
            "--uri",
            "ws://hub",
            "--name",
            "A2A",
            "--token",
            "secret",
            "--endpoint-url",
            "https://example.test/a2a/v1",
            "--bridge-name",
            "Bridge",
            "--description",
            "desc",
            "--documentation-url",
            "https://docs.example.test",
            "--bearer-auth",
        ]
    )

    assert cli_a2a._cmd_a2a_card(ns) == 0
    assert captured["uri"] == "ws://hub"
    assert captured["name"] == "A2A"
    assert captured["token"] == "secret"
    assert captured["endpoint_url"] == "https://example.test/a2a/v1"
    assert captured["bridge_name"] == "Bridge"
    assert captured["description"] == "desc"
    assert captured["documentation_url"] == "https://docs.example.test"
    assert captured["bearer_auth"] is True


def test_cmd_a2a_serve_requires_bearer_token(capsys: pytest.CaptureFixture[str]) -> None:
    ns = cli.build_parser().parse_args(
        ["a2a-serve", "--endpoint-url", "https://example.test/a2a/v1", "--bearer-auth"]
    )

    assert cli_a2a._cmd_a2a_serve(ns) == 2
    assert "--a2a-token is required" in capsys.readouterr().err


def test_cmd_a2a_serve_refuses_unauthenticated_off_loopback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ns = cli.build_parser().parse_args(
        ["a2a-serve", "--endpoint-url", "https://example.test/a2a/v1", "--host", "0.0.0.0"]
    )

    assert cli_a2a._cmd_a2a_serve(ns) == 2
    assert "Refusing to bind A2A bridge" in capsys.readouterr().err


def test_cmd_a2a_serve_warns_when_off_loopback_override_is_explicit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def unavailable(**_: Any) -> None:
        return None

    monkeypatch.setattr(cli_a2a, "_fetch_manifest", unavailable)
    ns = cli.build_parser().parse_args(
        [
            "a2a-serve",
            "--endpoint-url",
            "https://example.test/a2a/v1",
            "--host",
            "0.0.0.0",
            "--insecure-off-loopback",
        ]
    )

    assert cli_a2a._cmd_a2a_serve(ns) == 1
    captured = capsys.readouterr()
    assert "WARNING: binding A2A bridge" in captured.err
    assert "Could not reach hub" in captured.err


def test_cmd_a2a_serve_starts_bridge_and_stops_runtime(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events: list[str] = []

    async def manifest(**_: Any) -> list[dict[str, Any]]:
        return [{"agent": "WORKER", "description": "worker", "skills": ["chat"]}]

    class Runtime:
        def __init__(self, agent: Any) -> None:
            self.agent = agent

        def start(self) -> bool:
            events.append("start")
            asyncio.run(self.agent.callback({"type": "chat", "payload": "early"}))
            return True

        def run(self, coro: Any) -> Any:
            coro.close()
            return None

        def stop(self) -> None:
            events.append("stop")

    def serve(**kwargs: Any) -> None:
        bridge = kwargs["bridge"]
        assert bridge.agent_card["capabilities"]["streaming"] is True
        assert bridge.agent_card["capabilities"]["pushNotifications"] is True
        assert bridge.agent_card["capabilities"]["extendedAgentCard"] is True
        assert bridge.auth_token == "a2a-secret"
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_a2a, "_fetch_manifest", manifest)
    monkeypatch.setattr(cli_a2a, "SynapseAgentRuntime", Runtime)
    monkeypatch.setattr(cli_a2a, "serve_a2a_http", serve)
    ns = cli.build_parser().parse_args(
        [
            "a2a-serve",
            "--endpoint-url",
            "https://example.test/a2a/v1",
            "--bearer-auth",
            "--a2a-token",
            "a2a-secret",
        ]
    )

    assert cli_a2a._cmd_a2a_serve(ns) == 0
    assert events == ["start", "stop"]
    assert "A2A bridge listening" in capsys.readouterr().out


def test_cmd_a2a_serve_tolerates_non_mapping_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def manifest(**_: Any) -> list[dict[str, Any]]:
        return []

    class Runtime:
        def __init__(self, agent: Any) -> None:
            self.agent = agent

        def start(self) -> bool:
            return True

        def run(self, coro: Any) -> Any:
            coro.close()
            return None

        def stop(self) -> None:
            return None

    def card_from_manifest(*_: Any, **__: Any) -> dict[str, Any]:
        return {"name": "Bridge", "capabilities": "bad"}

    def serve(**kwargs: Any) -> None:
        assert kwargs["bridge"].agent_card["capabilities"] == "bad"
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_a2a, "_fetch_manifest", manifest)
    monkeypatch.setattr(cli_a2a, "agent_card_from_manifest", card_from_manifest)
    monkeypatch.setattr(cli_a2a, "SynapseAgentRuntime", Runtime)
    monkeypatch.setattr(cli_a2a, "serve_a2a_http", serve)
    ns = cli.build_parser().parse_args(
        ["a2a-serve", "--endpoint-url", "https://example.test/a2a/v1"]
    )

    assert cli_a2a._cmd_a2a_serve(ns) == 0


def test_cmd_a2a_serve_reports_runtime_start_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events: list[str] = []

    async def manifest(**_: Any) -> list[dict[str, Any]]:
        return []

    class Runtime:
        def __init__(self, agent: Any) -> None:
            self.agent = agent

        def start(self) -> bool:
            return False

        def stop(self) -> None:
            events.append("stop")

    monkeypatch.setattr(cli_a2a, "_fetch_manifest", manifest)
    monkeypatch.setattr(cli_a2a, "SynapseAgentRuntime", Runtime)
    ns = cli.build_parser().parse_args(
        ["a2a-serve", "--endpoint-url", "https://example.test/a2a/v1"]
    )

    assert cli_a2a._cmd_a2a_serve(ns) == 1
    assert events == ["stop"]
    assert "Could not establish persistent hub connection" in capsys.readouterr().err
