# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a query whose connection closes after the welcome reports why
"""A hub that welcomes and then closes a query connection yields a diagnosis."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hub_e2e_helpers import running_hub
from synapse_channel.cli_query_transport import _query_attempt
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageReplayCache,
)
from synapse_channel.core.protocol import MessageType


def _binding_hub(tmp_path: Path) -> SynapseHub:
    """A hub that welcomes first, then refuses an unproven registration and closes."""
    other = EventSignatureKey.from_private_key(
        key_id="someone-else",
        private_key=Ed25519PrivateKey.generate(),
        senders=frozenset({"P/other"}),
    )
    return SynapseHub(
        hub_id="closes-after-welcome",
        identity_trust_bundle=EventSignatureTrustBundle(
            keys={"someone-else": other},
            replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=8),
        ),
        require_identity_binding=True,
        identity_pin_path=tmp_path / "pins.json",
    )


async def test_a_request_after_the_hub_closed_reports_the_close_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async def request_after_close(agent: SynapseAgent) -> None:
        # The close can land while a request is being written. Reproduce that by
        # writing the request on the agent's own socket once the hub has closed it.
        connection = agent.connection
        assert connection is not None
        await asyncio.wait_for(connection.wait_closed(), timeout=5.0)
        frame = {"sender": "P/query", "type": MessageType.WHO_REQUEST, "target": "System"}
        await connection.send(json.dumps(frame))

    def unexpected(_value: object) -> None:
        raise AssertionError("no reply can arrive on a closed connection")

    async with running_hub(_binding_hub(tmp_path)) as (_, uri):
        code, identity_refused = await asyncio.wait_for(
            _query_attempt(
                uri=uri,
                connect_name="P/query",
                failure_name="P/query",
                token=None,
                response_type=MessageType.WHO_SNAPSHOT,
                request=request_after_close,
                render=unexpected,
                transform=lambda data: data,
                agent_factory=lambda *a, **k: SynapseAgent(*a, machine_identity=False, **k),
                attempts=3,
                ready_timeout=5.0,
                suppress_identity_refusal=False,
            ),
            timeout=15.0,
        )
    out = capsys.readouterr()
    assert (code, identity_refused) == (1, True)
    assert "4013" in out.out
    assert "Traceback" not in out.out + out.err
