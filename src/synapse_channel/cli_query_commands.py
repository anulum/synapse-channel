# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only hub query CLI command handlers
"""Async command flows for read-only hub query CLI subcommands."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys

from synapse_channel.cli_query_rendering import (
    _print_board,
    _print_manifest,
    _render_approvals,
    _render_dead_letters,
    _render_state,
)
from synapse_channel.cli_query_transport import AgentFactory, _drop_message, _query_hub
from synapse_channel.cli_query_who import _cmd_who as _cmd_who
from synapse_channel.cli_query_who import _who as _who
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MessageType
from synapse_channel.observed_peers import (
    ObservedPeerSpec,
    fetch_observed_peers,
    network_observed_fetcher_factory,
    resolve_observed_pins,
)


async def _health(
    *,
    uri: str,
    name: str = "HEALTH",
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
    ready_timeout: float = 5.0,
) -> int:
    """Connect and report whether the hub is reachable: ``0`` if so, ``1`` if not.

    A quiet liveness probe for container healthchecks — it opens a connection, waits
    for the welcome handshake, and exits without printing on success.

    Parameters
    ----------
    uri, name : str
        Hub URI and the probe's display name.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.
    ready_timeout : float, optional
        Seconds to wait for the welcome handshake before treating the hub as
        unreachable. Defaults to ``5.0``.

    Returns
    -------
    int
        ``0`` when the hub answered, ``1`` otherwise.
    """
    agent = agent_factory(name, _drop_message, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        return 0 if await agent.wait_until_ready(timeout=ready_timeout) else 1
    finally:
        agent.running = False
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task


def _cmd_health(args: argparse.Namespace) -> int:
    """Probe the hub and return its reachability as the process exit code."""
    return asyncio.run(
        _health(uri=args.uri, name=args.name, token=args.token, ready_timeout=args.ready_timeout)
    )


async def _state(
    *,
    uri: str,
    name: str,
    owner: str | None = None,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
    ready_timeout: float = 5.0,
    observed_peers: tuple[ObservedPeerSpec, ...] = (),
    observed_token: str | None = None,
    observed_timeout: float = 10.0,
    observed_pins: dict[str, str] | None = None,
) -> int:
    """Print the live claims and their checkpoints — the "where was I" recovery view.

    A returning agent reads this to see what is leased and which tasks carry a
    resume checkpoint, optionally filtered to its own name or project.

    Parameters
    ----------
    uri, name : str
        Hub URI and the requester's display name.
    owner : str or None, optional
        Keep only claims owned by this name or project (``owner`` or ``owner/...``).
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.
    ready_timeout : float, optional
        Seconds to wait for the welcome handshake before treating the hub as
        unreachable. Defaults to ``5.0``.

    Returns
    -------
    int
        ``0`` once the claims are printed, ``1`` when the hub could not be reached.
    """
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
        name=name,
        token=token,
        agent_factory=agent_factory,
        response_type=MessageType.STATE_SNAPSHOT,
        transform=lambda data: data.get("snapshot", {}),
        request=lambda agent: agent.request_state(),
        render=lambda snapshot: _render_state(snapshot, owner=owner, observed_peers=observed),
        ready_timeout=ready_timeout,
    )


def _cmd_state(args: argparse.Namespace) -> int:
    """Dispatch the ``state`` subcommand."""
    observed_specs = tuple(getattr(args, "observed_peers", ()))
    try:
        observed_pins = resolve_observed_pins(getattr(args, "observed_pins", ()), observed_specs)
    except ValueError as exc:
        print(f"synapse state: {exc}", file=sys.stderr)
        return 2
    return asyncio.run(
        _state(
            uri=args.uri,
            name=args.name,
            owner=args.owner,
            token=args.token,
            ready_timeout=args.ready_timeout,
            observed_peers=observed_specs,
            observed_token=getattr(args, "observed_token", None),
            observed_timeout=float(getattr(args, "observed_timeout", 10.0)),
            observed_pins=observed_pins,
        )
    )


async def _dead_letters(
    *,
    uri: str,
    name: str,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
    ready_timeout: float = 5.0,
) -> int:
    """Print the hub's dead-letter ledger — directed chats that reached nobody.

    The ledger rides in the state snapshot (the same one the dashboard and
    cockpit read), so this reuses the state request and renders only the
    ``dead_letters`` section — bringing the blackhole list to a terminal
    operator with the drain remedy, instead of it being visible only in the UI.

    Parameters
    ----------
    uri, name : str
        Hub URI and the requester's display name.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.
    ready_timeout : float, optional
        Seconds to wait for the welcome handshake before treating the hub as
        unreachable. Defaults to ``5.0``.

    Returns
    -------
    int
        ``0`` once the ledger is printed, ``1`` when the hub could not be reached.
    """
    return await _query_hub(
        uri=uri,
        name=name,
        token=token,
        agent_factory=agent_factory,
        response_type=MessageType.STATE_SNAPSHOT,
        transform=lambda data: data.get("snapshot", {}),
        request=lambda agent: agent.request_state(),
        render=_render_dead_letters,
        ready_timeout=ready_timeout,
    )


def _cmd_dead_letters(args: argparse.Namespace) -> int:
    """Dispatch the ``dead-letters`` subcommand."""
    return asyncio.run(
        _dead_letters(
            uri=args.uri,
            name=args.name,
            token=args.token,
            ready_timeout=args.ready_timeout,
        )
    )


async def _approvals(
    *,
    uri: str,
    name: str,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
    ready_timeout: float = 5.0,
) -> int:
    """Print the relays awaiting a second operator — the two-person approval quorum.

    The pending set rides in the state snapshot (the same one the dashboard and
    cockpit read), so this reuses the state request and renders only the
    ``pending_relay_approvals`` section — making the per-hub quorum operable from a
    terminal instead of enforced-but-invisible.

    Parameters
    ----------
    uri, name : str
        Hub URI and the requester's display name.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.
    ready_timeout : float, optional
        Seconds to wait for the welcome handshake before treating the hub as
        unreachable. Defaults to ``5.0``.

    Returns
    -------
    int
        ``0`` once the pending set is printed, ``1`` when the hub could not be reached.
    """
    return await _query_hub(
        uri=uri,
        name=name,
        token=token,
        agent_factory=agent_factory,
        response_type=MessageType.STATE_SNAPSHOT,
        transform=lambda data: data.get("snapshot", {}),
        request=lambda agent: agent.request_state(),
        render=_render_approvals,
        ready_timeout=ready_timeout,
    )


def _cmd_approvals(args: argparse.Namespace) -> int:
    """Dispatch the ``approvals`` subcommand."""
    return asyncio.run(
        _approvals(
            uri=args.uri,
            name=args.name,
            token=args.token,
            ready_timeout=args.ready_timeout,
        )
    )


async def _board(
    *,
    uri: str,
    name: str,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
    ready_timeout: float = 5.0,
) -> int:
    """Connect, request the shared blackboard, print it, and exit.

    Parameters
    ----------
    uri, name : str
        Hub URI and the requester's display name.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.
    ready_timeout : float, optional
        Seconds to wait for the welcome handshake before treating the hub as
        unreachable. Defaults to ``5.0``.

    Returns
    -------
    int
        ``0`` once a snapshot is printed, ``1`` when the hub could not be reached.
    """
    return await _query_hub(
        uri=uri,
        name=name,
        token=token,
        agent_factory=agent_factory,
        response_type=MessageType.BOARD_SNAPSHOT,
        transform=lambda data: data.get("board", {}),
        request=lambda agent: agent.request_board(),
        render=_print_board,
        ready_timeout=ready_timeout,
    )


def _cmd_board(args: argparse.Namespace) -> int:
    """Dispatch the ``board`` subcommand."""
    return asyncio.run(
        _board(uri=args.uri, name=args.name, token=args.token, ready_timeout=args.ready_timeout)
    )


async def _manifest(
    *,
    uri: str,
    name: str,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
    ready_timeout: float = 5.0,
) -> int:
    """Connect, request the capability manifest, print it, and exit.

    Parameters
    ----------
    uri, name : str
        Hub URI and the requester's display name.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.
    ready_timeout : float, optional
        Seconds to wait for the connection readiness event.

    Returns
    -------
    int
        ``0`` once a manifest is printed, ``1`` when the hub could not be reached.
    """
    return await _query_hub(
        uri=uri,
        name=name,
        token=token,
        agent_factory=agent_factory,
        response_type=MessageType.MANIFEST_SNAPSHOT,
        transform=lambda data: data.get("manifest", []),
        request=lambda agent: agent.request_manifest(),
        render=_print_manifest,
        ready_timeout=ready_timeout,
    )


def _cmd_manifest(args: argparse.Namespace) -> int:
    """Dispatch the ``manifest`` subcommand."""
    return asyncio.run(
        _manifest(uri=args.uri, name=args.name, token=args.token, ready_timeout=args.ready_timeout)
    )
