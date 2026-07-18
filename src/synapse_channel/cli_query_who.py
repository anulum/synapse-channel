# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — WHO query command flow
"""Roster query, observed-peer join, and mailbox display controls."""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from synapse_channel.cli_query_rendering import _render_who, _render_who_me
from synapse_channel.cli_query_transport import AgentFactory, _query_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.mailbox_pending import parse_pending_counts
from synapse_channel.core.protocol import MessageType
from synapse_channel.machine_identity import who_query_identity
from synapse_channel.observed_peers import (
    ObservedPeerSpec,
    fetch_observed_peers,
    network_observed_fetcher_factory,
    resolve_observed_pins,
)


async def _who(
    *,
    uri: str,
    name: str,
    project: str | None = None,
    me: bool = False,
    all_mailbox_pending: bool = False,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
    ready_timeout: float = 5.0,
    observed_peers: tuple[ObservedPeerSpec, ...] = (),
    observed_token: str | None = None,
    observed_timeout: float = 10.0,
    observed_pins: dict[str, str] | None = None,
) -> int:
    """Connect, print the online roster and bounded mailbox summary, then exit.

    Discovery for the directory: when several agents share a project their
    identities are ``<project>/<agent>``, so ``--project`` lists exactly the
    instances live on that repo right now.

    Parameters
    ----------
    uri, name : str
        Hub URI and the requester's display name.
    project : str or None, optional
        When set, keep only agents named ``project`` or ``project/...``.
    me : bool, optional
        Inspect ``name`` and ``name-rx`` instead of printing the full roster.
    all_mailbox_pending : bool, optional
        Show every positive mailbox identity. The default full-roster view is
        bounded to the largest counts; ``--me`` always shows only ``name``.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.
    ready_timeout : float, optional
        Seconds to wait for the welcome handshake.

    Returns
    -------
    int
        ``0`` once a roster is printed, ``1`` when the hub could not be reached.
    """
    query_name = f"{name}-who" if me else name
    fallback_name = who_query_identity(query_name)

    def transform(
        data: dict[str, Any],
    ) -> tuple[
        list[str],
        dict[str, Any] | None,
        dict[str, Any] | None,
        dict[str, int] | None,
    ]:
        """Remove the internal fallback actor from the rendered roster."""
        online = [str(agent) for agent in data.get("online_agents", [])]
        if fallback_name is not None:
            online = [agent for agent in online if agent != fallback_name]
        return (
            online,
            data.get("agent_liveness") if isinstance(data.get("agent_liveness"), dict) else None,
            data.get("wake_capabilities")
            if isinstance(data.get("wake_capabilities"), dict)
            else None,
            parse_pending_counts(data.get("mailbox_pending")),
        )

    observed = await fetch_observed_peers(
        observed_peers,
        fetcher_factory=network_observed_fetcher_factory(
            local_id=f"{name}-observed",
            token=observed_token,
            timeout=observed_timeout,
            pins=observed_pins,
        ),
    )
    return await _query_hub(
        uri=uri,
        name=query_name,
        token=token,
        agent_factory=agent_factory,
        response_type=MessageType.WHO_SNAPSHOT,
        transform=transform,
        request=lambda agent: agent.request_who(),
        render=(
            (
                lambda result: _render_who_me(
                    result[0],
                    name=name,
                    mailbox_pending=result[3],
                    show_mailbox_pending=True,
                )
            )
            if me
            else (
                lambda result: _render_who(
                    result[0],
                    project=project,
                    liveness=result[1],
                    wake_capabilities=result[2],
                    mailbox_pending=result[3],
                    show_mailbox_pending=True,
                    show_all_mailbox_pending=all_mailbox_pending,
                    observed_peers=observed,
                )
            )
        ),
        ready_timeout=ready_timeout,
        identity_fallback_name=fallback_name,
    )


def _cmd_who(args: argparse.Namespace) -> int:
    """Dispatch the ``who`` subcommand."""
    observed_specs = tuple(getattr(args, "observed_peers", ()))
    try:
        observed_pins = resolve_observed_pins(getattr(args, "observed_pins", ()), observed_specs)
    except ValueError as exc:
        print(f"synapse who: {exc}", file=sys.stderr)
        return 2
    return asyncio.run(
        _who(
            uri=args.uri,
            name=args.name,
            project=args.project,
            me=args.me,
            all_mailbox_pending=bool(getattr(args, "all_mailbox_pending", False)),
            token=args.token,
            ready_timeout=args.ready_timeout,
            observed_peers=observed_specs,
            observed_token=getattr(args, "observed_token", None),
            observed_timeout=float(getattr(args, "observed_timeout", 10.0)),
            observed_pins=observed_pins,
        )
    )
