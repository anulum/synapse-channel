# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — checkpoint custody through real service cancellation
"""A real checkpoint remains queryable until its anchor task has finished."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from hub_e2e_helpers import read_until_type, running_hub
from hub_service_lifetime_examples import until
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.merkle_checkpoint import LiveCheckpoint
from synapse_channel.core.persistence import EventStore


async def test_checkpoint_outlives_actual_anchor_task_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Observe original anchor work and actual store access during its unwind.

    The observer delegates to the complete production anchor loop. It does
    not replace the checkpoint, its I/O, the task, or the runtime's teardown.
    """
    entered, drained = asyncio.Event(), asyncio.Event()
    with EventStore(tmp_path / "checkpoint-order.db") as store:
        hub = SynapseHub(hub_id="checkpoint-order", journal=store, checkpoint_interval=0.01)
        checkpoint = hub._live_checkpoint
        assert checkpoint is not None
        original = hub._checkpoint_anchor_loop

        async def observed_anchor(live: LiveCheckpoint) -> None:
            """Delegate real anchoring, then query its actual store during cancellation."""
            entered.set()
            try:
                await original(live)
            finally:
                assert live is checkpoint
                assert live.store.latest() is not None
                assert store.max_seq() > 0
                drained.set()

        monkeypatch.setattr(hub, "_checkpoint_anchor_loop", observed_anchor)
        async with running_hub(hub) as (_, uri):
            await asyncio.wait_for(entered.wait(), 5)
            async with connect(uri) as websocket:
                await read_until_type(websocket, "welcome")
                await websocket.send(json.dumps({"type": "heartbeat", "sender": "P/observer"}))
                await read_until_type(websocket, "presence_update")
                await websocket.send(
                    json.dumps({"type": "chat", "sender": "P/observer", "payload": "anchor me"})
                )
                await read_until_type(websocket, "chat")

            def anchored() -> bool:
                """Require a real periodic anchor of the newly committed chat."""
                latest = checkpoint.store.latest()
                assert latest is not None
                return latest.seq == store.max_seq()

            await until(anchored)
        assert drained.is_set(), "checkpoint closed before the anchor task drained"
        assert hub._live_checkpoint is None
        assert store.max_seq() > 0
