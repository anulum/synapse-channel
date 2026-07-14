# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — advisory resource bidding CLI
"""CLI command for read-only advisory resource bids."""

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
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.resource_bidding import (
    ResourceBidReport,
    recommend_resource_bids,
    resource_bid_report_to_json,
)
from synapse_channel.core.semantic_routing import find_task
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


def _render_resource_bids(report: ResourceBidReport) -> None:
    """Print a compact text representation of ``report``."""
    candidate_count = len(report.candidates)
    print(f"Resource bids for {terminal_text(report.task_id)} ({candidate_count} candidates):")
    for candidate in report.candidates:
        reasons = ", ".join(terminal_text(reason) for reason in candidate.reasons)
        print(
            f"  {terminal_text(candidate.agent)} "
            f"{terminal_text(candidate.resource_kind)}/"
            f"{terminal_text(candidate.resource_name)} score={candidate.score} "
            f"capacity={candidate.capacity} reasons={reasons}"
        )
    if report.fallback_reason:
        print(f"Fallback: {terminal_text(report.fallback_reason)}")
    print(f"Advisory only: {terminal_text(report.trust_boundary)}")


async def _fetch_bidding_inputs(
    *,
    uri: str,
    name: str,
    token: str | None,
    agent_factory: AgentFactory,
    ready_timeout: float,
    response_timeout: float,
) -> tuple[dict[str, Any], CapabilityDirectory] | None:
    """Fetch board, manifest, and state snapshots needed for resource bids."""
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
                f"[{terminal_text(name)}] Hub did not return resource bidding snapshots: "
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


async def _resource_bids(
    *,
    uri: str,
    name: str,
    task_id: str,
    token: str | None = None,
    agent_factory: AgentFactory = SynapseAgent,
    ready_timeout: float = 5.0,
    response_timeout: float = 2.0,
    resource_kind: str | None = None,
    limit: int = 5,
    include_zero: bool = False,
    as_json: bool = False,
) -> int:
    """Fetch live snapshots and render advisory resource bids.

    Parameters
    ----------
    uri, name : str
        Hub URI and temporary query identity.
    task_id : str
        Board task id to evaluate.
    token : str or None, optional
        Shared-secret token for a secured hub.
    agent_factory : AgentFactory, optional
        Client factory, injectable for tests.
    ready_timeout, response_timeout : float, optional
        Hub readiness and snapshot response timeouts.
    resource_kind : str or None, optional
        Optional exact resource-kind filter.
    limit : int, optional
        Maximum candidate count.
    include_zero : bool, optional
        Include zero-score resource offers for diagnostics.
    as_json : bool, optional
        Print JSON instead of compact text.

    Returns
    -------
    int
        ``0`` on rendered bids, ``1`` when the hub is unreachable, missing
        snapshots, or the task id is absent from the board.
    """
    inputs = await _fetch_bidding_inputs(
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
    report = recommend_resource_bids(
        task,
        directory,
        resource_kind=resource_kind,
        limit=limit,
        include_zero=include_zero,
    )
    if as_json:
        print(resource_bid_report_to_json(report))
    else:
        _render_resource_bids(report)
    return 0


def _cmd_resource_bids(args: argparse.Namespace) -> int:
    """Dispatch the ``resource-bids`` subcommand."""
    return asyncio.run(
        _resource_bids(
            uri=args.uri,
            name=args.name,
            task_id=args.task_id,
            token=args.token,
            ready_timeout=args.ready_timeout,
            response_timeout=args.response_timeout,
            resource_kind=args.resource_kind,
            limit=args.limit,
            include_zero=args.include_zero,
            as_json=args.json,
        )
    )


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``resource-bids`` subcommand."""
    parser = subparsers.add_parser(
        "resource-bids",
        help="Rank live resource offers for a board task without reserving capacity.",
    )
    parser.add_argument("task_id", help="Board task id to evaluate.")
    parser.add_argument("--uri", default=default_hub_uri())
    parser.add_argument("--name", default="RESOURCE-BIDDER")
    parser.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    parser.add_argument("--resource-kind", default=None, help="Only consider this resource kind.")
    parser.add_argument("--limit", type=int, default=5, help="Maximum candidates to print.")
    parser.add_argument(
        "--include-zero",
        action="store_true",
        help="Include zero-score resource offers for diagnostics.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text.")
    parser.add_argument(
        "--response-timeout",
        type=float,
        default=2.0,
        help="Seconds to wait for board, manifest, and state snapshots.",
    )
    parser.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    parser.set_defaults(func=_cmd_resource_bids)
