# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — advisory semantic task routing CLI
"""CLI command for deterministic advisory task routing."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from collections.abc import Mapping
from typing import Any

from synapse_channel.cli_query_transport import AgentFactory
from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.connect_failures import describe_connect_failure
from synapse_channel.core.capability_directory import (
    CapabilityDirectory,
    build_capability_directory,
)
from synapse_channel.core.capability_observations import read_observed_capability_index
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.semantic_routing import (
    RoutingRecommendation,
    find_task,
    recommend_agents_for_task,
    recommendation_to_json,
)
from synapse_channel.terminal_text import terminal_text


def _cards(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return manifest cards from a hub reply, ignoring malformed entries."""
    manifest = data.get("manifest", [])
    if not isinstance(manifest, list):
        return []
    return [dict(card) for card in manifest if isinstance(card, Mapping)]


def _resources(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return resource offers from a state snapshot, ignoring malformed entries."""
    snapshot = data.get("snapshot", {})
    if not isinstance(snapshot, Mapping):
        return []
    resources = snapshot.get("resources", [])
    if not isinstance(resources, list):
        return []
    return [dict(resource) for resource in resources if isinstance(resource, Mapping)]


def _board(data: dict[str, Any]) -> dict[str, Any]:
    """Return a board snapshot from a hub reply, ignoring malformed snapshots."""
    board = data.get("board", {})
    if not isinstance(board, Mapping):
        return {}
    return dict(board)


def _render_recommendation(recommendation: RoutingRecommendation) -> None:
    """Print a compact text representation of ``recommendation``."""
    candidate_count = len(recommendation.candidates)
    print(
        f"Route recommendations for {terminal_text(recommendation.task_id)} "
        f"({candidate_count} candidates):"
    )
    for candidate in recommendation.candidates:
        reasons = ", ".join(terminal_text(reason) for reason in candidate.reasons)
        classes = ", ".join(terminal_text(item) for item in candidate.task_classes) or "none"
        skills = ", ".join(terminal_text(skill) for skill in candidate.skills) or "none"
        print(
            f"  {terminal_text(candidate.agent)} score={candidate.score} "
            f"classes={classes} skills={skills} reasons={reasons}"
        )
    if recommendation.fallback_reason:
        print(f"Fallback: {terminal_text(recommendation.fallback_reason)}")
    print(f"Advisory only: {terminal_text(recommendation.trust_boundary)}")


async def _fetch_routing_inputs(
    *,
    uri: str,
    name: str,
    token: str | None,
    agent_factory: AgentFactory,
    ready_timeout: float,
    response_timeout: float,
) -> tuple[dict[str, Any], CapabilityDirectory] | None:
    """Fetch board, manifest, and state snapshots needed for routing."""
    replies: dict[str, dict[str, Any]] = {}
    expected = {
        MessageType.BOARD_SNAPSHOT,
        MessageType.MANIFEST_SNAPSHOT,
        MessageType.STATE_SNAPSHOT,
    }

    async def collect(data: dict[str, Any]) -> None:
        message_type = str(data.get("type", ""))
        if message_type in expected:
            replies[message_type] = data

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout):
            print(
                terminal_text(
                    describe_connect_failure(
                        name,
                        uri,
                        close_code=agent.last_close_code,
                        close_reason=agent.last_close_reason,
                    )
                )
            )
            return None
        await agent.request_board()
        await agent.request_manifest()
        await agent.request_state()
        deadline = asyncio.get_running_loop().time() + max(0.0, response_timeout)
        while set(replies) != expected and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.025)
        if set(replies) != expected:
            missing = ", ".join(sorted(expected.difference(replies)))
            print(
                f"[{terminal_text(name)}] Hub did not return semantic routing snapshots: "
                f"{terminal_text(missing)}."
            )
            return None
        directory = build_capability_directory(
            manifest=_cards(replies[MessageType.MANIFEST_SNAPSHOT]),
            resources=_resources(replies[MessageType.STATE_SNAPSHOT]),
        )
        return _board(replies[MessageType.BOARD_SNAPSHOT]), directory
    finally:
        agent.running = False
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task


async def _route_task(
    *,
    uri: str,
    name: str,
    task_id: str,
    token: str | None = None,
    agent_factory: AgentFactory = SynapseAgent,
    ready_timeout: float = 5.0,
    response_timeout: float = 2.0,
    limit: int = 5,
    include_zero: bool = False,
    event_store: str | None = None,
    event_store_key_file: str | None = None,
    as_json: bool = False,
) -> int:
    """Fetch live snapshots and render advisory route recommendations.

    Parameters
    ----------
    uri, name : str
        Hub URI and temporary query identity.
    task_id : str
        Board task id to route.
    token : str or None, optional
        Shared-secret token for a secured hub.
    agent_factory : AgentFactory, optional
        Client factory, injectable for tests.
    ready_timeout, response_timeout : float, optional
        Hub readiness and snapshot response timeouts.
    limit : int, optional
        Maximum candidate count.
    include_zero : bool, optional
        Include zero-score agents for diagnostic output.
    event_store : str or None, optional
        Optional hub event-store path used to add observed release-receipt
        evidence to the advisory ranking.
    event_store_key_file : str or None, optional
        Owner-only SQLCipher key when ``event_store`` is encrypted.
    as_json : bool, optional
        Print JSON instead of compact text.

    Returns
    -------
    int
        ``0`` on rendered recommendations, ``1`` when the hub is unreachable,
        missing snapshots, or the task id is absent from the board.
    """
    inputs = await _fetch_routing_inputs(
        uri=uri,
        name=name,
        token=token,
        agent_factory=agent_factory,
        ready_timeout=ready_timeout,
        response_timeout=response_timeout,
    )
    if inputs is None:
        return 1
    board, directory = inputs
    task = find_task(board, task_id)
    if task is None:
        print(f"[{name}] Task {task_id} is not on the board.")
        return 1
    observations = None
    if event_store is not None:
        try:
            observations = read_observed_capability_index(
                event_store, key_file=event_store_key_file
            )
        except ValueError as exc:
            print(f"[{name}] {exc}")
            return 1
    recommendation = recommend_agents_for_task(
        task,
        directory,
        limit=limit,
        include_zero=include_zero,
        observations=observations,
    )
    if as_json:
        print(recommendation_to_json(recommendation))
    else:
        _render_recommendation(recommendation)
    return 0


def _cmd_route_task(args: argparse.Namespace) -> int:
    """Dispatch the ``route-task`` subcommand."""
    return asyncio.run(
        _route_task(
            uri=args.uri,
            name=args.name,
            task_id=args.task_id,
            token=args.token,
            ready_timeout=args.ready_timeout,
            response_timeout=args.response_timeout,
            limit=args.limit,
            include_zero=args.include_zero,
            event_store=args.event_store,
            event_store_key_file=getattr(args, "db_key_file", None),
            as_json=args.json,
        )
    )


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``route-task`` subcommand."""
    route = subparsers.add_parser(
        "route-task",
        help="Recommend agents for a board task using local capability signals.",
    )
    route.add_argument("task_id", help="Board task id to route.")
    route.add_argument("--uri", default=default_hub_uri())
    route.add_argument("--name", default="ROUTER")
    route.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    route.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of candidate agents to print.",
    )
    route.add_argument(
        "--include-zero",
        action="store_true",
        help="Include agents with no local signal match.",
    )
    route.add_argument(
        "--event-store",
        default=None,
        help="Optional hub event-store DB used for observed capability evidence.",
    )
    route.add_argument(
        "--db-key-file",
        default=None,
        help="Owner-only SQLCipher key when --event-store is encrypted.",
    )
    route.add_argument("--json", action="store_true", help="Print JSON instead of text.")
    route.add_argument(
        "--response-timeout",
        type=float,
        default=2.0,
        help="Seconds to wait for board, manifest, and state snapshots.",
    )
    route.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    route.set_defaults(func=_cmd_route_task)
