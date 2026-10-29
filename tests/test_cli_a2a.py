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
from pathlib import Path
from typing import Any

import pytest

from _platform_caps import requires_posix_mode_bits
from hub_e2e_helpers import AgentHandle, _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli, cli_a2a
from synapse_channel.a2a_conformance import SPEC_VERSION
from synapse_channel.a2a_server import SynapseAgentRuntime
from synapse_channel.cli_a2a_conformance import _cmd_a2a_conformance, _status_filter
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MessageType


class _StartedRuntime:
    """Hub runtime stand-in for ``a2a-serve``: starts, closes submitted work, records stop."""

    def __init__(self, agent: Any, events: list[str] | None = None) -> None:
        self.agent = agent
        self.events = [] if events is None else events

    def start(self) -> bool:
        return True

    def run(self, coro: Any) -> Any:
        coro.close()
        return None

    def stop(self) -> None:
        self.events.append("runtime-stop")


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
            "--a2a-token-file",
            "/run/secrets/a2a",
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
    assert args.a2a_token_file == "/run/secrets/a2a"
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
    # gRPC binding is partial (optional SendMessage/GetTask); filter partial rows.
    args = cli.build_parser().parse_args(["a2a-conformance", "--status", "partial"])

    assert _cmd_a2a_conformance(args) == 0
    captured = capsys.readouterr()
    assert f"A2A conformance matrix (spec {SPEC_VERSION})" in captured.out
    assert "| Area | Item | Status | SYNAPSE surface | Evidence | Limitation |" in captured.out
    assert "| binding | gRPC | partial |" in captured.out


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
    assert "--a2a-token or --a2a-token-file is required" in capsys.readouterr().err


def test_cmd_a2a_serve_refuses_unauthenticated_off_loopback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ns = cli.build_parser().parse_args(
        ["a2a-serve", "--endpoint-url", "https://example.test/a2a/v1", "--host", "0.0.0.0"]
    )

    assert cli_a2a._cmd_a2a_serve(ns) == 2
    err = capsys.readouterr().err
    assert "Refusing to bind A2A bridge" in err
    assert "without --bearer-auth" in err


def test_cmd_a2a_serve_refuses_plaintext_bearer_off_loopback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """R4 parity: bearer over cleartext HTTP off loopback is refuse, not allow."""
    ns = cli.build_parser().parse_args(
        [
            "a2a-serve",
            "--endpoint-url",
            "https://example.test/a2a/v1",
            "--host",
            "0.0.0.0",
            "--bearer-auth",
            "--a2a-token",
            "a2a-secret",
        ]
    )

    assert cli_a2a._cmd_a2a_serve(ns) == 2
    err = capsys.readouterr().err
    assert "Refusing to bind A2A bridge" in err
    assert "plaintext HTTP" in err


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
    assert "WARNING:" in captured.err
    assert "without --bearer-auth" in captured.err
    assert "Could not reach hub" in captured.err


def test_cmd_a2a_serve_warns_when_plaintext_bearer_override_is_explicit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Explicit --insecure-off-loopback downgrades plaintext-bearer refuse to warn."""

    async def unavailable(**_: Any) -> None:
        return None

    ns = cli.build_parser().parse_args(
        [
            "a2a-serve",
            "--endpoint-url",
            "https://example.test/a2a/v1",
            "--host",
            "0.0.0.0",
            "--bearer-auth",
            "--a2a-token",
            "a2a-secret",
            "--insecure-off-loopback",
        ]
    )

    assert cli_a2a._cmd_a2a_serve(ns, manifest_fetcher=unavailable) == 1
    captured = capsys.readouterr()
    assert "WARNING:" in captured.err
    assert "plaintext HTTP" in captured.err
    assert "Could not reach hub" in captured.err


def test_cmd_a2a_serve_starts_bridge_and_stops_runtime(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    events: list[str] = []

    async def manifest(**_: Any) -> list[dict[str, Any]]:
        return [{"agent": "WORKER", "description": "worker", "skills": ["chat"]}]

    class Runtime(_StartedRuntime):
        def start(self) -> bool:
            self.events.append("start")
            asyncio.run(self.agent.callback({"type": "chat", "payload": "early"}))
            return True

    def serve(**kwargs: Any) -> None:
        bridge = kwargs["bridge"]
        assert bridge.agent_card["capabilities"]["streaming"] is True
        assert bridge.agent_card["capabilities"]["pushNotifications"] is True
        assert bridge.agent_card["capabilities"]["extendedAgentCard"] is True
        assert bridge.auth_token == "a2a-secret"
        bindings = [
            item.get("protocolBinding") for item in bridge.agent_card["supportedInterfaces"]
        ]
        assert "GRPC" not in bindings, "default a2a-serve must not start gRPC"
        raise KeyboardInterrupt

    token_file = tmp_path / "a2a.token"
    token_file.write_text("a2a-secret\n", encoding="utf-8")
    token_file.chmod(0o600)
    ns = cli.build_parser().parse_args(
        [
            "a2a-serve",
            "--endpoint-url",
            "https://example.test/a2a/v1",
            "--bearer-auth",
            "--a2a-token-file",
            str(token_file),
        ]
    )

    assert (
        cli_a2a._cmd_a2a_serve(
            ns,
            manifest_fetcher=manifest,
            runtime_factory=lambda agent: Runtime(agent, events),
            server_runner=serve,
        )
        == 0
    )
    assert events == ["start", "runtime-stop"]
    out = capsys.readouterr().out
    assert "A2A bridge listening" in out
    assert "A2A gRPC binding" not in out


@requires_posix_mode_bits
def test_cmd_a2a_serve_redacts_rejected_token_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token_file = tmp_path / "a2a.token"
    token_file.write_text("never-print-this-a2a-secret\n", encoding="utf-8")
    token_file.chmod(0o644)
    ns = cli.build_parser().parse_args(
        [
            "a2a-serve",
            "--endpoint-url",
            "https://example.test/a2a/v1",
            "--bearer-auth",
            "--a2a-token-file",
            str(token_file),
        ]
    )

    assert cli_a2a._cmd_a2a_serve(ns) == 2
    error = capsys.readouterr().err
    assert "--a2a-token-file" in error
    assert "never-print-this-a2a-secret" not in error


def test_cmd_a2a_serve_composes_grpc_policy_and_closes_listener(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, Any] = {}
    events: list[str] = []

    async def manifest(**_: Any) -> list[dict[str, Any]]:
        return []

    class GrpcServer:
        def stop(self, *, grace: None) -> None:
            assert grace is None
            events.append("grpc-stop")

    def start_grpc(bridge: Any, **kwargs: Any) -> tuple[GrpcServer, object]:
        captured["bridge"] = bridge
        captured.update(kwargs)
        return GrpcServer(), object()

    def serve(**kwargs: Any) -> None:
        interfaces = kwargs["bridge"].agent_card["supportedInterfaces"]
        assert interfaces[-1]["url"] == "grpc://127.0.0.1:50051"
        raise KeyboardInterrupt

    token_file = tmp_path / "grpc-a2a.token"
    token_file.write_text("a2a-secret\n", encoding="utf-8")
    token_file.chmod(0o600)
    ns = cli.build_parser().parse_args(
        [
            "a2a-serve",
            "--endpoint-url",
            "http://127.0.0.1:8877",
            "--bearer-auth",
            "--a2a-token-file",
            str(token_file),
            "--grpc-port",
            "50051",
            "--max-concurrent-requests",
            "3",
            "--request-read-timeout",
            "2.5",
        ]
    )
    assert (
        cli_a2a._cmd_a2a_serve(
            ns,
            manifest_fetcher=manifest,
            runtime_factory=lambda agent: _StartedRuntime(agent, events),
            server_runner=serve,
            grpc_server_starter=start_grpc,
        )
        == 0
    )
    policy = captured["policy"]
    assert policy.bearer_token == "a2a-secret"
    assert policy.max_concurrent_rpcs == 3
    assert policy.max_rpc_seconds == pytest.approx(2.5)
    assert policy.max_receive_message_bytes == 1024 * 1024
    assert policy.max_send_message_bytes == 1024 * 1024
    assert captured["server_credentials"] is None
    assert events == ["grpc-stop", "runtime-stop"]
    out = capsys.readouterr().out
    assert "bearer=required" in out
    assert "max-rpcs=3" in out
    assert "deadline=2.5s" in out


def test_cmd_a2a_serve_grpc_start_failure_closes_runtime(
    capsys: pytest.CaptureFixture[str],
) -> None:
    events: list[str] = []

    async def manifest(**_: Any) -> list[dict[str, Any]]:
        return []

    def fail_grpc(_bridge: Any, **_: Any) -> tuple[Any, Any]:
        raise RuntimeError("listener bind failed")

    ns = cli.build_parser().parse_args(
        [
            "a2a-serve",
            "--endpoint-url",
            "http://127.0.0.1:8877",
            "--grpc-port",
            "50051",
        ]
    )
    assert (
        cli_a2a._cmd_a2a_serve(
            ns,
            manifest_fetcher=manifest,
            runtime_factory=lambda agent: _StartedRuntime(agent, events),
            grpc_server_starter=fail_grpc,
        )
        == 2
    )
    assert events == ["runtime-stop"]
    assert "A2A gRPC unavailable: listener bind failed" in capsys.readouterr().err


def test_cmd_a2a_serve_refuses_non_finite_read_timeout_before_runtime(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ns = cli.build_parser().parse_args(
        [
            "a2a-serve",
            "--endpoint-url",
            "http://127.0.0.1:8877",
            "--request-read-timeout",
            "nan",
        ]
    )

    created: list[_StartedRuntime] = []

    assert (
        cli_a2a._cmd_a2a_serve(
            ns, runtime_factory=lambda agent: created.append(_StartedRuntime(agent))
        )
        == 2
    )
    assert created == [], "invalid timeout must fail before runtime creation"
    assert "--request-read-timeout must be finite and > 0" in capsys.readouterr().err


def test_cmd_a2a_serve_tolerates_non_mapping_capabilities() -> None:
    async def manifest(**_: Any) -> list[dict[str, Any]]:
        return []

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
            runtime_factory=_StartedRuntime,
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


def test_cmd_a2a_serve_refuses_a_zero_request_ceiling(capsys: pytest.CaptureFixture[str]) -> None:
    """A concurrency ceiling below one is refused before the hub is contacted."""
    ns = cli.build_parser().parse_args(
        [
            "a2a-serve",
            "--endpoint-url",
            "http://127.0.0.1:8877",
            "--max-concurrent-requests",
            "0",
        ]
    )

    assert cli_a2a._cmd_a2a_serve(ns) == 2
    assert "--max-concurrent-requests must be >= 1" in capsys.readouterr().err


def test_cmd_a2a_serve_reports_an_unusable_mutual_tls_configuration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Missing certificate files for mutual TLS end in a configuration refusal."""
    ns = cli.build_parser().parse_args(
        [
            "a2a-serve",
            "--endpoint-url",
            "https://127.0.0.1:8877",
            "--tls-certfile",
            str(tmp_path / "absent-cert.pem"),
            "--tls-keyfile",
            str(tmp_path / "absent-key.pem"),
            "--mtls-client-ca-file",
            str(tmp_path / "absent-ca.pem"),
        ]
    )

    assert cli_a2a._cmd_a2a_serve(ns) == 2
    assert "A2A TLS configuration error" in capsys.readouterr().err


def test_cmd_a2a_serve_bounds_the_real_runtime_and_stops_it_when_the_hub_is_absent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The real runtime receives the read timeout; an unreachable hub stops it cleanly."""
    created: list[SynapseAgentRuntime] = []

    async def manifest(**_: Any) -> list[dict[str, Any]]:
        return []

    def runtime_factory(agent: Any) -> SynapseAgentRuntime:
        runtime = SynapseAgentRuntime(agent)
        created.append(runtime)
        return runtime

    port = _free_port()
    ns = cli.build_parser().parse_args(
        [
            "a2a-serve",
            "--endpoint-url",
            "http://127.0.0.1:8877",
            "--uri",
            f"ws://127.0.0.1:{port}",
            "--request-read-timeout",
            "4.5",
        ]
    )

    assert (
        cli_a2a._cmd_a2a_serve(ns, manifest_fetcher=manifest, runtime_factory=runtime_factory) == 1
    )
    assert created[0].operation_timeout_seconds == pytest.approx(4.5)
    assert "Could not establish persistent hub connection" in capsys.readouterr().err


def test_cmd_a2a_serve_keeps_a_non_list_interface_field_untouched_for_grpc(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A card whose interface field is not a list is not rewritten by the gRPC binding."""
    seen: dict[str, Any] = {}
    events: list[str] = []

    async def manifest(**_: Any) -> list[dict[str, Any]]:
        return []

    def card_builder(_manifest: Any, **_: Any) -> dict[str, Any]:
        return {"name": "bridge", "supportedInterfaces": "advertised-elsewhere"}

    class GrpcServer:
        def stop(self, *, grace: None) -> None:
            seen["grpc-stopped"] = grace is None

    def start_grpc(bridge: Any, **_: Any) -> tuple[GrpcServer, object]:
        return GrpcServer(), object()

    def serve(**kwargs: Any) -> None:
        bridge = kwargs["bridge"]
        seen["interfaces"] = bridge.agent_card["supportedInterfaces"]
        sent = bridge.send_message(
            {"message": {"messageId": "m-1", "role": "ROLE_USER", "parts": [{"text": "work"}]}}
        )
        seen["state"] = sent["task"]["status"]["state"]
        raise KeyboardInterrupt

    ns = cli.build_parser().parse_args(
        ["a2a-serve", "--endpoint-url", "http://127.0.0.1:8877", "--grpc-port", "50052"]
    )

    assert (
        cli_a2a._cmd_a2a_serve(
            ns,
            manifest_fetcher=manifest,
            card_builder=card_builder,
            runtime_factory=lambda agent: _StartedRuntime(agent, events),
            server_runner=serve,
            grpc_server_starter=start_grpc,
        )
        == 0
    )
    assert seen == {
        "interfaces": "advertised-elsewhere",
        "state": "TASK_STATE_WORKING",
        "grpc-stopped": True,
    }
    assert events == ["runtime-stop"]
    assert "A2A gRPC binding on grpc://127.0.0.1:50052" in capsys.readouterr().out
