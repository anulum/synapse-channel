# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — what the hub snapshot keeps from a hub that answers in an older or odd shape
"""The hub snapshot keeps only what a hub said; its document keeps no projection inputs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

import synapse_channel.dashboard_hub_snapshot as hub_snapshot_module
from synapse_channel.core.protocol import MessageType
from synapse_channel.dashboard_hub_snapshot import DashboardSnapshot, fetch_dashboard_snapshot

Collect = Callable[[dict[str, Any]], Awaitable[None]]


class _OldHubAgent:
    """A client whose hub predates the welcome's hub id and sends one malformed manifest card."""

    def __init__(self, _name: str, collect: Collect, **_options: object) -> None:
        self._collect = collect
        self.running = True
        self.hub_id = "unknown"

    async def connect(self) -> None:
        while self.running:
            await asyncio.sleep(0.01)

    async def wait_until_ready(self, *, timeout: float) -> bool:
        return True

    async def request_who(self) -> None:
        await self._collect({"type": MessageType.WHO_SNAPSHOT, "online_agents": ["harbour/api"]})

    async def request_state(self) -> None:
        await self._collect({"type": MessageType.STATE_SNAPSHOT, "snapshot": {"claims": {}}})

    async def request_board(self) -> None:
        await self._collect({"type": MessageType.BOARD_SNAPSHOT, "board": {"tasks": {}}})

    async def request_manifest(self) -> None:
        cards: list[object] = [{"agent": "harbour/api", "skills": []}, "not a card", 7, None]
        await self._collect({"type": MessageType.MANIFEST_SNAPSHOT, "manifest": cards})


async def test_an_old_hubs_unknown_id_and_malformed_cards_are_not_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hub_snapshot_module, "SynapseAgent", _OldHubAgent)

    snapshot = await fetch_dashboard_snapshot(
        uri="ws://127.0.0.1:1",
        name="SYNAPSE-CHANNEL/dashboard",
        token=None,
        ready_timeout=0.5,
        response_timeout=0.5,
    )

    # The client's placeholder is not a hub id: the document says "not reported", not "unknown".
    assert snapshot.hub_id == ""
    # Only mapping cards are capability cards; anything else is dropped, not served.
    assert snapshot.manifest == [{"agent": "harbour/api", "skills": []}]
    assert snapshot.online_agents == ["harbour/api"]


def test_the_document_keeps_no_peer_marker_and_no_projection_inputs() -> None:
    snapshot = DashboardSnapshot(
        online_agents=[],
        state={},
        board={},
        manifest=[],
        agent_liveness={"harbour/api": {"proven": True}},
        wake_capabilities={"harbour/api": "waiter"},
        observed_from="east",
    )

    document = snapshot.to_dict()

    # A peer's document says so in its own advisory and peer blocks, not in this field.
    assert "observed_from" not in document
    assert "agent_liveness" not in document
    assert "wake_capabilities" not in document
    assert document["observed_peers"] == []
