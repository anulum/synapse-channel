# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — K4-WF10: why secured hubs record floors in compat, not strict
"""A restarted client continues its sequence, so strict durable floors admit it.

K4-WF10 found through the public claim route that the shipped client numbered
frames from 1 in every process. Two successive client processes of one seat
against a real hub with a durable ledger: ``compat`` admitted both, ``strict``
refused the second process's first frame (``sequence_mismatch``). The test pinning
that is in history up to Core ``814a6b03``.

CLIENT-SEQUENCE-PERSIST makes the sequence time-derived
(:func:`~synapse_channel.core.message_auth.next_message_auth_sequence`), so the
same two processes now pass under both modes. A frame whose sequence falls back
below the floor, as the old client's did, is still refused by ``strict``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import Recorder, running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import (
    MessageAuthKey,
    next_message_auth_sequence,
    sign_frame,
)
from synapse_channel.core.message_auth_durable import DurableMessageAuthReplayStore
from synapse_channel.core.protocol import MessageType

_SECRET = "wf10-shared-secret"


async def _claim_in_a_fresh_client(uri: str, task: str) -> dict[str, Any]:
    """Run one short-lived client process of seat P/a and return the hub's answer."""
    recorder = Recorder()
    agent = SynapseAgent(
        "P/a",
        recorder,
        uri=uri,
        verbose=False,
        per_message_auth_key_id="k1",
        per_message_auth_secret=_SECRET,
        machine_identity=False,
    )
    connection = asyncio.create_task(agent.connect())
    try:
        assert await agent.wait_until_ready(3.0)
        await agent.claim(task, worktree="/repo", paths=[task])
        return await recorder.wait_for(
            lambda m: (
                m.get("type")
                in (MessageType.CLAIM_GRANTED, MessageType.CLAIM_DENIED, MessageType.ERROR)
            )
        )
    finally:
        agent.running = False
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)


def _hub(ledger: DurableMessageAuthReplayStore, mode: str) -> SynapseHub:
    return SynapseHub(
        require_per_message_auth=True,
        per_message_auth_keys=[
            MessageAuthKey(key_id="k1", secret=_SECRET.encode(), senders=frozenset({"P/a"}))
        ],
        per_message_auth_replay_store=ledger,
        per_message_auth_sequence_floor_mode=mode,
    )


@pytest.mark.parametrize("mode", ["compat", "strict"])
async def test_a_restarted_client_passes_compat_and_strict(tmp_path: Path, mode: str) -> None:
    ledger = DurableMessageAuthReplayStore(
        tmp_path / "auth.db", max_entries=1000, window_seconds=300.0
    )
    try:
        async with running_hub(_hub(ledger, mode)) as (_hub_, uri):
            first = await _claim_in_a_fresh_client(uri, "T1")
            restarted = await _claim_in_a_fresh_client(uri, "T2")
        floor = ledger.floor("k1", "P/a")
    finally:
        ledger.close()
    assert first["type"] == MessageType.CLAIM_GRANTED
    assert restarted["type"] == MessageType.CLAIM_GRANTED
    assert floor is not None and floor > 1_000_000_000_000_000


async def test_strict_still_refuses_a_sequence_below_the_floor(tmp_path: Path) -> None:
    """The old client's behaviour: a new process signing sequence 1 after the floor moved."""
    ledger = DurableMessageAuthReplayStore(
        tmp_path / "auth.db", max_entries=1000, window_seconds=300.0
    )
    key = MessageAuthKey(key_id="k1", secret=_SECRET.encode(), senders=frozenset({"P/a"}))
    try:
        async with running_hub(_hub(ledger, "strict")) as (_hub_, uri):
            first = await _claim_in_a_fresh_client(uri, "T1")
            recorder = Recorder()
            agent = SynapseAgent(
                "P/a",
                recorder,
                uri=uri,
                verbose=False,
                per_message_auth_key_id="k1",
                per_message_auth_secret=_SECRET,
                machine_identity=False,
            )
            connection = asyncio.create_task(agent.connect())
            try:
                assert await agent.wait_until_ready(3.0)
                assert agent.connection is not None
                frame = sign_frame(
                    {
                        "sender": "P/a",
                        "type": MessageType.CLAIM,
                        "target": "System",
                        "payload": "",
                        "task_id": "T2",
                        "worktree": "/repo",
                        "paths": ["T2"],
                        "idem_key": "old-client-restart",
                    },
                    key=key,
                    nonce="old-client-nonce",
                    sequence=1,
                )
                await agent.connection.send(json.dumps(frame))
                refused = await recorder.wait_for(lambda m: m.get("type") == MessageType.ERROR)
            finally:
                agent.running = False
                connection.cancel()
                await asyncio.gather(connection, return_exceptions=True)
    finally:
        ledger.close()
    assert first["type"] == MessageType.CLAIM_GRANTED
    assert refused["verification_result"] == "sequence_mismatch"


def test_the_sequence_follows_the_clock_and_never_repeats() -> None:
    assert next_message_auth_sequence(0, now_ns=5_000_000) == 5_000
    assert next_message_auth_sequence(5_000, now_ns=5_000_000) == 5_001
    assert next_message_auth_sequence(9_000, now_ns=5_000_000) == 9_001  # clock stepped back
    assert next_message_auth_sequence(0) > 1_000_000_000_000_000
    assert next_message_auth_sequence(0) < 2**53
