# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — inbound frame decoding: non-objects, bad incarnations, empty leases
"""The agent drops frames it cannot trust before they change state or reach the callback."""

from __future__ import annotations

import json
from typing import Any

import pytest

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MessageType


def _agent(*, verbose: bool) -> tuple[SynapseAgent, list[dict[str, Any]], list[str]]:
    seen: list[dict[str, Any]] = []
    leases: list[str] = []

    async def record(frame: dict[str, Any]) -> None:
        seen.append(frame)

    agent = SynapseAgent(
        "P/receiver",
        record,
        verbose=verbose,
        machine_identity=False,
        on_lease_granted=leases.append,
    )
    return agent, seen, leases


@pytest.mark.parametrize("raw", ["[]", '"text"', "7", "null"])
async def test_a_non_object_frame_is_dropped_and_reported_only_when_verbose(
    raw: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Valid JSON that is not an object never reaches the callback."""
    loud, loud_seen, _ = _agent(verbose=True)
    await loud._dispatch(raw)
    assert loud_seen == []
    assert capsys.readouterr().out == "[P/receiver] Received a non-object frame from hub.\n"
    quiet, quiet_seen, _ = _agent(verbose=False)
    await quiet._dispatch(raw)
    assert quiet_seen == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("incarnation", [None, "abc", "A" * 64, "g" * 64, 7])
async def test_a_delivery_session_with_a_malformed_incarnation_is_ignored(
    incarnation: object,
) -> None:
    """Only a 64-character lower-case hex incarnation arms delivery readiness."""
    agent, seen, _ = _agent(verbose=False)
    before = agent.delivery_incarnation
    frame = {"type": MessageType.DELIVERY_SESSION, "incarnation": incarnation}
    await agent._dispatch(json.dumps(frame))
    assert agent.delivery_incarnation == before
    assert not agent.delivery_ready_event.is_set()
    assert seen == [frame]


@pytest.mark.parametrize("owner_lease", [None, ""])
async def test_a_lease_grant_without_a_token_records_nothing(owner_lease: object) -> None:
    """An empty grant neither replaces the held lease nor reaches the persistence hook."""
    agent, seen, leases = _agent(verbose=False)
    agent.owner_lease = "held-token"
    frame = {"type": MessageType.LEASE_GRANTED, "owner_lease": owner_lease}
    await agent._dispatch(json.dumps(frame))
    assert agent.owner_lease == "held-token"
    assert leases == []
    assert seen == [frame]
