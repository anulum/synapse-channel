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
from collections.abc import Coroutine
from typing import Any

import pytest

from hub_e2e_helpers import AgentHandle, _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli, cli_a2a
from synapse_channel.a2a_conformance import SPEC_VERSION
from synapse_channel.cli_a2a_conformance import _cmd_a2a_conformance, _status_filter
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MessageType


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


def test_parser_a2a_conformance() -> None:
    args = cli.build_parser().parse_args(["a2a-conformance", "--status", "partial", "--json"])

    assert args.status == "partial"
    assert args.json is True
    assert args.func is _cmd_a2a_conformance


def test_cmd_a2a_conformance_prints_markdown(capsys: pytest.CaptureFixture[str]) -> None:
    args = cli.build_parser().parse_args(["a2a-conformance", "--status", "unsupported"])

    assert _cmd_a2a_conformance(args) == 0
    captured = capsys.readouterr()
    assert f"A2A conformance matrix (spec {SPEC_VERSION})" in captured.out
    assert "| binding | gRPC | unsupported | none |" in captured.out


def test_cmd_a2a_conformance_prints_json(capsys: pytest.CaptureFixture[str]) -> None:
    args = cli.build_parser().parse_args(["a2a-conformance", "--status", "partial", "--json"])

    assert _cmd_a2a_conformance(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["spec_version"] == SPEC_VERSION
    assert {row["status"] for row in payload["rows"]} == {"partial"}
    assert "Independent interoperability" in {row["item"] for row in payload["rows"]}


def test_a2a_conformance_status_filter_accepts_none_and_rejects_unknown() -> None:
    assert _status_filter(None) is None
    with pytest.raises(ValueError, match="unsupported A2A conformance status"):
        _status_filter("bad")


async def test_a2a_card_prints_manifest_projection(
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = "secret"
    async with running_hub(SynapseHub(authenticator=TokenAuthenticator([token]))) as (_hub, uri):
        handle = await _advertise_agent(uri, "FAST", token=token)
        try:
            rc = await cli_a2a._a2a_card(
                uri=uri,
                name="A2A-TEST",
                token=token,
                endpoint_url="https://example.test/a2a/v1",
                bridge_name="Synapse Bridge",
                bearer_auth=True,
            )
        finally:
            await close_agents(handle)

    assert rc == 0
    card = json.loads(capsys.readouterr().out)
    assert card["name"] == "Synapse Bridge"
    assert card["supportedInterfaces"][0]["url"] == "https://example.test/a2a/v1"
    assert card["skills"][0]["id"] == "synapse-fast"
    assert card["securityRequirements"] == [{"synapseBearer": []}]


async def test_a2a_card_uses_explicit_description(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        rc = await cli_a2a._a2a_card(
            uri=uri,
            name="A2A-TEST",
            endpoint_url="https://example.test/a2a/v1",
            description="Explicit bridge description.",
        )

    assert rc == 0
    card = json.loads(capsys.readouterr().out)
    assert card["description"] == "Explicit bridge description."


async def test_a2a_card_reports_unreachable_hub(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = await cli_a2a._a2a_card(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="A2A-TEST",
        endpoint_url="https://example.test/a2a/v1",
        ready_timeout=0.1,
    )

    assert rc == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def _advertise_agent(uri: str, name: str, *, token: str | None = None) -> AgentHandle:
    handle = await connect_agent(name, uri, token=token)
    await handle.agent.advertise(
        description="quick worker",
        skills=["chat"],
        task_classes=["rule"],
    )
    await handle.recorder.wait_for(
        lambda message: (
            message.get("type") == "capability_advertised"
            and message.get("card", {}).get("agent") == name
        )
    )
    return handle


async def test_fetch_manifest_returns_live_manifest() -> None:
    token = "secret"
    async with running_hub(SynapseHub(authenticator=TokenAuthenticator([token]))) as (_hub, uri):
        handle = await _advertise_agent(uri, "FAST", token=token)
        try:
            manifest = await cli_a2a._fetch_manifest(
                uri=uri,
                name="A2A-BOOT",
                token=token,
            )
        finally:
            await close_agents(handle)

    assert manifest is not None
    assert manifest[0]["agent"] == "FAST"
    assert manifest[0]["skills"] == ["chat"]


async def test_fetch_manifest_returns_none_when_hub_never_becomes_ready() -> None:
    manifest = await cli_a2a._fetch_manifest(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="A2A-BOOT",
        token=None,
        ready_timeout=0.1,
        attempts=1,
    )

    assert manifest is None


async def test_fetch_manifest_returns_empty_for_empty_live_manifest() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        manifest = await cli_a2a._fetch_manifest(
            uri=uri,
            name="A2A-BOOT",
            token=None,
            attempts=1,
        )

    assert manifest == []


async def test_fetch_manifest_ignores_malformed_manifest_frame() -> None:
    class MalformedManifestAgent:
        def __init__(self, name: str, callback: Any, **_: Any) -> None:
            self.name = name
            self.callback = callback
            self.running = True

        async def connect(self) -> None:
            await self.callback(
                {
                    "type": MessageType.MANIFEST_SNAPSHOT,
                    "manifest": {"unexpected": "shape"},
                }
            )

        async def wait_until_ready(self, *, timeout: float) -> bool:
            return True

        async def request_manifest(self) -> None:
            return None

    manifest = await cli_a2a._fetch_manifest(
        uri="ws://not-used",
        name="A2A-BOOT",
        token=None,
        agent_factory=MalformedManifestAgent,
        attempts=1,
        poll_interval=0.0,
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


def test_cmd_a2a_card_dispatches_async_query() -> None:
    captured: dict[str, Any] = {}

    async def card_once(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    def run_once(coro: Coroutine[Any, Any, int]) -> int:
        return asyncio.run(coro)

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

    assert cli_a2a._cmd_a2a_card(ns, card_runner=card_once, async_runner=run_once) == 0
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
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def unavailable(**_: Any) -> None:
        return None

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

    assert cli_a2a._cmd_a2a_serve(ns, manifest_fetcher=unavailable) == 1
    captured = capsys.readouterr()
    assert "WARNING: binding A2A bridge" in captured.err
    assert "Could not reach hub" in captured.err


def test_cmd_a2a_serve_starts_bridge_and_stops_runtime(
    capsys: pytest.CaptureFixture[str],
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

    assert (
        cli_a2a._cmd_a2a_serve(
            ns,
            manifest_fetcher=manifest,
            runtime_factory=Runtime,
            server_runner=serve,
        )
        == 0
    )
    assert events == ["start", "stop"]
    assert "A2A bridge listening" in capsys.readouterr().out


def test_cmd_a2a_serve_tolerates_non_mapping_capabilities() -> None:
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

    ns = cli.build_parser().parse_args(
        ["a2a-serve", "--endpoint-url", "https://example.test/a2a/v1"]
    )

    assert (
        cli_a2a._cmd_a2a_serve(
            ns,
            manifest_fetcher=manifest,
            card_builder=card_from_manifest,
            runtime_factory=Runtime,
            server_runner=serve,
        )
        == 0
    )


def test_cmd_a2a_serve_reports_runtime_start_failure(
    capsys: pytest.CaptureFixture[str],
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

    ns = cli.build_parser().parse_args(
        ["a2a-serve", "--endpoint-url", "https://example.test/a2a/v1"]
    )

    assert cli_a2a._cmd_a2a_serve(ns, manifest_fetcher=manifest, runtime_factory=Runtime) == 1
    assert events == ["stop"]
    assert "Could not establish persistent hub connection" in capsys.readouterr().err
