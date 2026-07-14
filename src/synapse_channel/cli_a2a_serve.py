# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A bridge serve CLI command
"""Agent2Agent bridge startup command for the ``synapse`` CLI."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable
from typing import Any

from synapse_channel.a2a import agent_card_from_manifest
from synapse_channel.a2a_http import serve_a2a_http
from synapse_channel.a2a_http_protocol import endpoint_authorities, normalise_origin
from synapse_channel.a2a_server import A2ABridge, SynapseAgentRuntime
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.cli_a2a_types import (
    AsyncRunner,
    BridgeFactory,
    CardBuilder,
    ManifestFetcher,
    RuntimeFactory,
    ServerRunner,
    StoreFactory,
)
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import is_loopback_host
from synapse_channel.core.protocol import MessageType


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
    try:
        allowed_origins = tuple(
            normalise_origin(origin) for origin in (getattr(args, "allow_origin", None) or ())
        )
        allowed_authorities = endpoint_authorities(args.endpoint_url) if allowed_origins else ()
    except ValueError as exc:
        print(f"[{args.name}] Invalid A2A browser boundary: {exc}.", file=sys.stderr)
        return 2
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
        allowed_origins=allowed_origins,
        allowed_authorities=allowed_authorities,
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
