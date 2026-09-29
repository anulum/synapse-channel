# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — K4-WF10: why secured hubs record floors in compat, not strict
"""A restarted client numbers its frames from 1 again; strict floors refuse it.

K4-WF10 turns durable sequence floors on under ``--secure`` / ``--team-secure``.
Before choosing the mode this was tested through the public claim route: two
successive client processes of one seat, each with the shipped client, against a
real hub with a durable ledger. ``compat`` admits both; ``strict`` refuses the
second process's first frame (``sequence_mismatch``). The profiles therefore pick
``compat``; ``strict`` stays an operator choice until clients keep their sequence.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import Recorder, running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import MessageAuthKey
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


@pytest.mark.parametrize(
    ("mode", "second"),
    [("compat", MessageType.CLAIM_GRANTED), ("strict", MessageType.ERROR)],
)
async def test_a_restarted_client_passes_compat_and_is_refused_by_strict(
    tmp_path: Path, mode: str, second: str
) -> None:
    ledger = DurableMessageAuthReplayStore(
        tmp_path / "auth.db", max_entries=1000, window_seconds=300.0
    )
    hub = SynapseHub(
        require_per_message_auth=True,
        per_message_auth_keys=[
            MessageAuthKey(key_id="k1", secret=_SECRET.encode(), senders=frozenset({"P/a"}))
        ],
        per_message_auth_replay_store=ledger,
        per_message_auth_sequence_floor_mode=mode,
    )
    try:
        async with running_hub(hub) as (_hub, uri):
            first = await _claim_in_a_fresh_client(uri, "T1")
            restarted = await _claim_in_a_fresh_client(uri, "T2")
    finally:
        ledger.close()
    assert first["type"] == MessageType.CLAIM_GRANTED
    assert restarted["type"] == second
    if second == MessageType.ERROR:
        assert restarted["verification_result"] == "sequence_mismatch"
