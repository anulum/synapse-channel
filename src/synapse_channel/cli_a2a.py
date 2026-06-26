# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A bridge CLI commands
"""Agent2Agent bridge command surfaces for ``synapse``.

The first bridge slice is discovery-only: ``synapse a2a-card`` reads the live
SYNAPSE capability manifest and emits an A2A Agent Card JSON document that can
be served as ``/.well-known/agent-card.json`` by a thin HTTP edge.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from synapse_channel.a2a import agent_card_from_manifest
from synapse_channel.a2a_http import serve_a2a_http
from synapse_channel.a2a_server import A2ABridge, SynapseAgentRuntime
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.cli_queries import _query_hub
from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.core.hub import is_loopback_host
from synapse_channel.core.protocol import MessageType

T = TypeVar("T")
AsyncRunner = Callable[[Coroutine[Any, Any, T]], T]
A2ACardRunner = Callable[..., Coroutine[Any, Any, int]]
ManifestFetcher = Callable[..., Coroutine[Any, Any, list[dict[str, Any]] | None]]
CardBuilder = Callable[..., dict[str, Any]]
RuntimeFactory = Callable[[Any], Any]
BridgeFactory = Callable[..., A2ABridge]
StoreFactory = Callable[..., A2ATaskStore]
ServerRunner = Callable[..., None]


def _print_agent_card(card: dict[str, Any]) -> None:
    """Print an Agent Card as deterministic, human-readable JSON."""
    print(json.dumps(card, indent=2, sort_keys=True))


async def _a2a_card(
    *,
    uri: str,
    name: str,
    endpoint_url: str,
    token: str | None = None,
    bridge_name: str = "SYNAPSE CHANNEL",
    description: str | None = None,
    documentation_url: str = "https://anulum.github.io/synapse-channel",
    bearer_auth: bool = False,
    agent_factory: Any = SynapseAgent,
    ready_timeout: float = 5.0,
) -> int:
    """Connect to the hub, read its manifest, and print an A2A Agent Card.

    Parameters
    ----------
    uri, name : str
        Hub URI and query agent name.
    endpoint_url : str
        Absolute A2A bridge endpoint URL to advertise.
    token : str or None, optional
        Shared-secret token for a secured SYNAPSE hub.
    bridge_name : str, optional
        Human-facing A2A card name.
    description : str or None, optional
        A2A card description; ``None`` uses the mapper default.
    documentation_url : str, optional
        Public documentation URL.
    bearer_auth : bool, optional
        Declare HTTP Bearer authentication on the advertised A2A endpoint.
    agent_factory : Any, optional
        Test seam for the SYNAPSE client factory.
    ready_timeout : float, optional
        Seconds to wait for connection readiness.

    Returns
    -------
    int
        ``0`` once a card is printed, ``1`` when the hub could not be reached.
    """

    def render(manifest: list[dict[str, Any]]) -> None:
        kwargs: dict[str, Any] = {
            "endpoint_url": endpoint_url,
            "name": bridge_name,
            "documentation_url": documentation_url,
            "bearer_auth": bearer_auth,
        }
        if description is not None:
            kwargs["description"] = description
        _print_agent_card(agent_card_from_manifest(manifest, **kwargs))

    return await _query_hub(
        uri=uri,
        name=name,
        token=token,
        agent_factory=agent_factory,
        response_type=MessageType.MANIFEST_SNAPSHOT,
        transform=lambda data: data.get("manifest", []),
        request=lambda agent: agent.request_manifest(),
        render=render,
        ready_timeout=ready_timeout,
    )


def _cmd_a2a_card(
    args: argparse.Namespace,
    *,
    card_runner: A2ACardRunner = _a2a_card,
    async_runner: AsyncRunner[int] = asyncio.run,
) -> int:
    """Dispatch the ``a2a-card`` subcommand."""
    return async_runner(
        card_runner(
            uri=args.uri,
            name=args.name,
            token=args.token,
            endpoint_url=args.endpoint_url,
            bridge_name=args.bridge_name,
            description=args.description,
            documentation_url=args.documentation_url,
            bearer_auth=args.bearer_auth,
        )
    )


async def _fetch_manifest(
    *,
    uri: str,
    name: str,
    token: str | None,
    agent_factory: Any = SynapseAgent,
    ready_timeout: float = 5.0,
    attempts: int = 50,
    poll_interval: float = 0.05,
) -> list[dict[str, Any]] | None:
    """Fetch one manifest snapshot for bridge startup."""
    results: list[list[dict[str, Any]]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") == MessageType.MANIFEST_SNAPSHOT:
            manifest = data.get("manifest", [])
            if isinstance(manifest, list):
                results.append([card for card in manifest if isinstance(card, dict)])

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout):
            return None
        await agent.request_manifest()
        for _ in range(attempts):
            if results:
                break
            await asyncio.sleep(poll_interval)
        return results[-1] if results else []
    finally:
        agent.running = False
        conn_task.cancel()


async def _a2a_inbound_handler(bridge_ref: dict[str, Any], data: dict[str, Any]) -> None:
    """Forward inbound SYNAPSE frames to the bridge for A2A task correlation."""
    bridge = bridge_ref.get("bridge")
    if bridge is not None:
        bridge.handle_synapse_frame(data)


def _cmd_a2a_serve(
    args: argparse.Namespace,
    *,
    async_runner: AsyncRunner[Any] = asyncio.run,
    manifest_fetcher: ManifestFetcher = _fetch_manifest,
    card_builder: CardBuilder = agent_card_from_manifest,
    agent_factory: Callable[..., SynapseAgent] = SynapseAgent,
    runtime_factory: RuntimeFactory = SynapseAgentRuntime,
    bridge_factory: BridgeFactory = A2ABridge,
    store_factory: StoreFactory = A2ATaskStore,
    server_runner: ServerRunner = serve_a2a_http,
) -> int:
    """Dispatch the ``a2a-serve`` subcommand."""
    if args.bearer_auth and not args.a2a_token:
        print(
            f"[{args.name}] --a2a-token is required when --bearer-auth is enabled.",
            file=sys.stderr,
        )
        return 2
    if not is_loopback_host(args.host) and not args.bearer_auth:
        if not args.insecure_off_loopback:
            print(
                f"[{args.name}] Refusing to bind A2A bridge to non-loopback host "
                f"{args.host!r} without --bearer-auth and --a2a-token. Pass "
                "--insecure-off-loopback to bind anyway.",
                file=sys.stderr,
            )
            return 2
        print(
            f"[{args.name}] WARNING: binding A2A bridge to non-loopback host "
            f"{args.host!r} without bearer authentication.",
            file=sys.stderr,
        )
    manifest = async_runner(
        manifest_fetcher(uri=args.uri, name=f"{args.name}-manifest", token=args.token)
    )
    if manifest is None:
        print(f"[{args.name}] Could not reach hub at {args.uri}.", file=sys.stderr)
        return 1
    agent_card = card_builder(
        manifest,
        endpoint_url=args.endpoint_url,
        name=args.bridge_name,
        description=args.description
        or "Local-first A2A bridge for SYNAPSE coordination and capability discovery.",
        documentation_url=args.documentation_url,
        bearer_auth=args.bearer_auth,
    )
    capabilities = agent_card.setdefault("capabilities", {})
    if isinstance(capabilities, dict):
        capabilities["streaming"] = True
        capabilities["pushNotifications"] = True
        capabilities["extendedAgentCard"] = bool(args.bearer_auth)
    bridge_ref: dict[str, Any] = {"bridge": None}

    async def _handler(data: dict[str, Any]) -> None:
        await _a2a_inbound_handler(bridge_ref, data)

    agent = agent_factory(args.name, _handler, uri=args.uri, verbose=False, token=args.token)
    runtime = runtime_factory(agent)
    if not runtime.start():
        print(f"[{args.name}] Could not establish persistent hub connection.", file=sys.stderr)
        runtime.stop()
        return 1
    bridge = bridge_factory(
        agent=agent,
        agent_card=agent_card,
        target=args.target,
        store=store_factory(storage_path=args.state_file),
        submit=runtime.run,
        auth_token=args.a2a_token if args.bearer_auth else None,
        task_timeout_seconds=args.task_timeout,
        subscribe_wait_seconds=args.subscribe_timeout,
    )
    bridge_ref["bridge"] = bridge
    try:
        print(f"[{args.name}] A2A bridge listening on http://{args.host}:{args.port}")
        server_runner(bridge=bridge, host=args.host, port=args.port)
    except KeyboardInterrupt:
        print(f"\n[{args.name}] A2A bridge stopped by user.")
    finally:
        runtime.stop()
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register A2A bridge subcommands."""
    card = subparsers.add_parser(
        "a2a-card",
        help="Print an A2A Agent Card projected from the live SYNAPSE capability manifest.",
    )
    card.add_argument("--uri", default=DEFAULT_HUB_URI)
    card.add_argument("--name", default="A2A-BRIDGE")
    card.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    card.add_argument(
        "--endpoint-url",
        required=True,
        help="Absolute URL of the A2A bridge endpoint advertised in the Agent Card.",
    )
    card.add_argument("--bridge-name", default="SYNAPSE CHANNEL")
    card.add_argument("--description", default=None)
    card.add_argument(
        "--documentation-url",
        default="https://anulum.github.io/synapse-channel",
    )
    card.add_argument(
        "--bearer-auth",
        action="store_true",
        help="Declare HTTP Bearer authentication for the advertised A2A endpoint.",
    )
    card.set_defaults(func=_cmd_a2a_card)

    serve = subparsers.add_parser(
        "a2a-serve",
        help="Run the stdlib HTTP+JSON A2A bridge for discovery, messages, and tasks.",
    )
    serve.add_argument("--uri", default=DEFAULT_HUB_URI)
    serve.add_argument("--name", default="A2A-BRIDGE")
    serve.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8877)
    serve.add_argument(
        "--endpoint-url",
        required=True,
        help="Absolute URL of this A2A bridge endpoint as clients will reach it.",
    )
    serve.add_argument(
        "--target",
        default="all",
        help="Default SYNAPSE target for A2A messages without metadata.target.",
    )
    serve.add_argument("--bridge-name", default="SYNAPSE CHANNEL")
    serve.add_argument("--description", default=None)
    serve.add_argument(
        "--documentation-url",
        default="https://anulum.github.io/synapse-channel",
    )
    serve.add_argument(
        "--bearer-auth",
        action="store_true",
        help="Declare HTTP Bearer authentication for the advertised A2A endpoint.",
    )
    serve.add_argument(
        "--a2a-token",
        default=None,
        help="Bearer token required by protected A2A bridge routes.",
    )
    serve.add_argument(
        "--insecure-off-loopback",
        action="store_true",
        help="Allow a non-loopback A2A bind without bearer authentication.",
    )
    serve.add_argument(
        "--state-file",
        default=None,
        help="Optional JSON state file for persisted A2A tasks and push configs.",
    )
    serve.add_argument(
        "--task-timeout",
        type=float,
        default=300.0,
        help="Seconds before an open A2A task is marked failed while awaiting a SYNAPSE reply.",
    )
    serve.add_argument(
        "--subscribe-timeout",
        type=float,
        default=0.0,
        help="Seconds a task subscription waits for one queued lifecycle update.",
    )
    serve.set_defaults(func=_cmd_a2a_serve)
