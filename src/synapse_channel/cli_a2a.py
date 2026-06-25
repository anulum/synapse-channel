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
from typing import Any

from synapse_channel.a2a import agent_card_from_manifest
from synapse_channel.cli_queries import _query_hub
from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent
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
    )


def _cmd_a2a_card(args: argparse.Namespace) -> int:
    """Dispatch the ``a2a-card`` subcommand."""
    return asyncio.run(
        _a2a_card(
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
