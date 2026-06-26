# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A Agent Card CLI command
"""Agent2Agent card projection command for the ``synapse`` CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from synapse_channel.a2a import agent_card_from_manifest
from synapse_channel.cli_a2a_types import A2ACardRunner, AsyncRunner
from synapse_channel.cli_queries import _query_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MessageType


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
