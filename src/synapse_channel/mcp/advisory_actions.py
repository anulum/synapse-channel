# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP advisory routing, bids, and memory recall
"""Translate MCP advisory tools into hub snapshots and local projections."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.capability_directory import build_capability_directory
from synapse_channel.core.capability_observations import (
    ObservedCapabilityInputError,
    read_observed_capability_index,
)
from synapse_channel.core.memory_projection import (
    MemoryRecallInputError,
    memory_recall_to_json,
    read_memory_recall,
)
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.resource_bidding import (
    recommend_resource_bids,
    resource_bid_report_to_json,
)
from synapse_channel.core.semantic_routing import (
    find_task,
    recommend_agents_for_task,
    recommendation_to_json,
)

Matcher = Callable[[dict[str, Any]], bool]
Sender = Callable[[], Awaitable[None]]
ReplyAwaiter = Callable[[Matcher, Sender], Awaitable[dict[str, Any] | None]]

logger = logging.getLogger(__name__)


class McpAdvisoryActions:
    """Own MCP advisory route/bid tools and local memory recall.

    Parameters
    ----------
    agent : SynapseAgent
        Connected hub client used for board/manifest/state snapshots.
    await_reply : ReplyAwaiter
        Correlator owned by the bridge transport layer.
    """

    def __init__(self, agent: SynapseAgent, await_reply: ReplyAwaiter) -> None:
        self.agent = agent
        self.await_reply = await_reply

    async def route_task(
        self,
        task_id: str,
        limit: int = 5,
        include_zero: bool = False,
        event_store: str | None = None,
        event_store_key_file: str | None = None,
    ) -> str:
        """Return advisory routes or a caller-safe store-read refusal.

        Parameters
        ----------
        task_id : str
            Existing board task whose routing candidates are requested.
        limit : int, optional
            Maximum recommendations returned by the routing projector.
        include_zero : bool, optional
            Whether to retain candidates with no matching capability tokens.
        event_store : str or None, optional
            Local store supplying advisory observed capability evidence.
        event_store_key_file : str or None, optional
            Owner-only key file for that store.

        Returns
        -------
        str
            JSON recommendations, a missing-snapshot/task response, an
            explicitly authored input refusal or fixed storage-failure text.
            Unexpected storage details are logged only on the server.
        """
        board_reply = await self.await_reply(
            lambda data: data.get("type") == MessageType.BOARD_SNAPSHOT,
            self.agent.request_board,
        )
        if board_reply is None:
            return "the hub did not return semantic routing snapshots"
        manifest_reply = await self.await_reply(
            lambda data: data.get("type") == MessageType.MANIFEST_SNAPSHOT,
            self.agent.request_manifest,
        )
        if manifest_reply is None:
            return "the hub did not return semantic routing snapshots"
        state_reply = await self.await_reply(
            lambda data: data.get("type") == MessageType.STATE_SNAPSHOT,
            self.agent.request_state,
        )
        if state_reply is None:
            return "the hub did not return semantic routing snapshots"

        board = board_reply.get("board", {})
        task = find_task(board if isinstance(board, dict) else {}, task_id)
        if task is None:
            return f"task '{task_id}' is not on the board"
        manifest = manifest_reply.get("manifest", [])
        snapshot = state_reply.get("snapshot", {})
        resources = snapshot.get("resources", []) if isinstance(snapshot, dict) else []
        directory = build_capability_directory(
            manifest=manifest if isinstance(manifest, list) else [],
            resources=resources if isinstance(resources, list) else [],
        )
        observations = None
        if event_store is not None:
            try:
                observations = read_observed_capability_index(
                    event_store, key_file=event_store_key_file
                )
            except ObservedCapabilityInputError as exc:
                return str(exc)
            except Exception:
                logger.exception("cannot read observed capability event store")
                return "cannot read observed capability event store"
        recommendation = recommend_agents_for_task(
            task,
            directory,
            limit=limit,
            include_zero=include_zero,
            observations=observations,
        )
        return recommendation_to_json(recommendation)

    async def resource_bids(
        self,
        task_id: str,
        resource_kind: str | None = None,
        limit: int = 5,
        include_zero: bool = False,
    ) -> str:
        """Return advisory resource bids for a board task as JSON."""
        board_reply = await self.await_reply(
            lambda data: data.get("type") == MessageType.BOARD_SNAPSHOT,
            self.agent.request_board,
        )
        if board_reply is None:
            return "the hub did not return resource bidding snapshots"
        manifest_reply = await self.await_reply(
            lambda data: data.get("type") == MessageType.MANIFEST_SNAPSHOT,
            self.agent.request_manifest,
        )
        if manifest_reply is None:
            return "the hub did not return resource bidding snapshots"
        state_reply = await self.await_reply(
            lambda data: data.get("type") == MessageType.STATE_SNAPSHOT,
            self.agent.request_state,
        )
        if state_reply is None:
            return "the hub did not return resource bidding snapshots"

        board = board_reply.get("board", {})
        task = find_task(board if isinstance(board, dict) else {}, task_id)
        if task is None:
            return f"task '{task_id}' is not on the board"
        manifest = manifest_reply.get("manifest", [])
        snapshot = state_reply.get("snapshot", {})
        resources = snapshot.get("resources", []) if isinstance(snapshot, dict) else []
        directory = build_capability_directory(
            manifest=manifest if isinstance(manifest, list) else [],
            resources=resources if isinstance(resources, list) else [],
        )
        report = recommend_resource_bids(
            task,
            directory,
            resource_kind=resource_kind,
            limit=limit,
            include_zero=include_zero,
        )
        return resource_bid_report_to_json(report)

    async def memory_recall(
        self,
        event_store: str,
        query: str,
        limit: int = 5,
        since_seq: int = 0,
        event_store_key_file: str | None = None,
    ) -> str:
        """Return local memory hits or a caller-safe store-read refusal.

        Parameters
        ----------
        event_store : str
            Local event store containing the retained memory records.
        query : str
            Text used by the deterministic memory projector.
        limit : int, optional
            Maximum matching records returned.
        since_seq : int, optional
            Exclusive lower event sequence bound.
        event_store_key_file : str or None, optional
            Owner-only key file for the store.

        Returns
        -------
        str
            JSON hits, an explicitly authored missing-store input refusal or
            fixed storage-failure text. Unexpected details stay in server logs.
        """
        try:
            report = read_memory_recall(
                event_store,
                query,
                since_seq=since_seq,
                limit=limit,
                key_file=event_store_key_file,
            )
        except MemoryRecallInputError as exc:
            return str(exc)
        except Exception:
            logger.exception("cannot read memory recall event store")
            return "cannot read memory recall event store"
        return memory_recall_to_json(report)
