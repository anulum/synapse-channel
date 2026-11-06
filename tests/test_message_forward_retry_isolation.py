# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one forward's unexpected fault cannot stop retries for every peer
"""A retry sweep isolates each forward: a fault fails that attempt, storage errors still stop."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

import pytest
from websockets.asyncio.client import connect

from hub_e2e_helpers import read_until_type, running_hub
from message_forward_peer_helpers import result_frame, scripted_peer
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_forward_origin import (
    INTERNAL_ERROR_DETAIL,
    run_forward_retries,
)
from synapse_channel.core.message_forward_transport import (
    MessageForwardPeer,
    forward_message,
)
from synapse_channel.core.message_forward_wire import (
    MessageForwardRequest,
    MessageForwardResult,
    encode_message_forward_request,
)


def _enqueue(hub: SynapseHub, forward_id: str, peer_hub: str) -> None:
    request = MessageForwardRequest(
        forward_id=forward_id,
        kind="chat",
        sender_seat="PROJ/alice",
        target_seat="PROJ/bob",
        body={"payload": "hi"},
    )
    now = time.time()
    hub.message_forward_ledger.enqueue(
        forward_id=forward_id,
        peer_hub=peer_hub,
        sender="PROJ/alice",
        target=f"PROJ/bob@{peer_hub}",
        request=encode_message_forward_request(request),
        now=now,
        expires_at=now + 3600,
    )


def _failing_for(peer_hub: str, fault: Exception) -> Any:
    """Forward normally, except that every forward to ``peer_hub`` raises ``fault``."""

    async def forwarder(
        request: MessageForwardRequest, *, peer: MessageForwardPeer, local_id: str
    ) -> MessageForwardResult:
        if peer.uri.endswith(f"/{peer_hub}"):
            raise fault
        return await forward_message(request, peer=peer, local_id=local_id)

    return forwarder


async def test_a_faulting_forward_leaves_the_other_peers_retrying_and_the_hub_serving(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The faulting entry stays pending with a fixed reason; the healthy peer's entry settles."""
    async with scripted_peer(lambda frame: result_frame(frame["forward_id"])) as good:
        hub = SynapseHub(
            hub_id="origin",
            message_peers={
                "bad": MessageForwardPeer("ws://127.0.0.1:9/bad"),
                "good": MessageForwardPeer(good.uri),
            },
        )
        hub.message_forwarder = _failing_for("bad", RuntimeError("bug in a forwarder"))
        async with running_hub(hub) as (_, uri):
            _enqueue(hub, "a" * 32, "bad")
            _enqueue(hub, "b" * 32, "good")
            assert await run_forward_retries(hub, now=time.time() + 1) == 1
            bad = hub.message_forward_ledger.outbox_entry("a" * 32)
            settled = hub.message_forward_ledger.outbox_entry("b" * 32)
            async with connect(uri) as agent:
                await read_until_type(agent, "welcome")
                await agent.send(
                    json.dumps({"sender": "PROJ/c", "type": "heartbeat", "protocol_version": 3})
                )
                await agent.send(json.dumps({"sender": "PROJ/c", "type": "who_request"}))
                assert (await read_until_type(agent, "who_snapshot"))["type"] == "who_snapshot"
    assert bad is not None and settled is not None
    assert (bad.state, bad.attempts) == ("pending", 1)
    assert bad.result == {"last_error": INTERNAL_ERROR_DETAIL}
    assert settled.state == "accepted"
    (record,) = [r for r in caplog.records if r.name == "synapse.message_forward"]
    assert record.levelname == "ERROR" and record.exc_info is not None
    assert "unexpected error (attempt 1)" in record.getMessage()


async def test_a_storage_error_still_stops_the_sweep() -> None:
    """A ledger that cannot be used is not retried around: the sweep fails stop."""
    hub = SynapseHub(
        hub_id="origin", message_peers={"bad": MessageForwardPeer("ws://127.0.0.1:9/bad")}
    )
    hub.message_forwarder = _failing_for("bad", sqlite3.OperationalError("disk I/O error"))
    _enqueue(hub, "a" * 32, "bad")
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        await run_forward_retries(hub, now=time.time() + 1)
    entry = hub.message_forward_ledger.outbox_entry("a" * 32)
    assert entry is not None and (entry.state, entry.attempts) == ("pending", 0)
