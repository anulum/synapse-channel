# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - tests for hub resource offers

from __future__ import annotations

from hub_helpers import FakeServerWS, _hub, _msg
from synapse_channel.core.state import MAX_OFFERS_PER_AGENT


async def test_resource_offer_is_broadcast() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(
        _msg(sender="A", type="resource", kind="llm", name="m", capacity=2), ws
    )
    offered = [m for m in ws.decoded() if m.get("type") == "resource_offered"]
    assert offered[-1]["name"] == "m"
    assert offered[-1]["key"] == "A:llm:m"


async def test_resource_offer_missing_fields_errors() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="resource", kind="llm"), ws)
    assert ws.last()["type"] == "error"
    assert "kind+name" in ws.last()["payload"]


async def test_resource_offer_quota_is_enforced() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    for index in range(MAX_OFFERS_PER_AGENT):
        await hub.handle_message(
            _msg(sender="A", type="resource", kind="llm", name=f"m{index}"), ws
        )
    await hub.handle_message(_msg(sender="A", type="resource", kind="llm", name="overflow"), ws)
    assert ws.last()["type"] == "error"
    assert "quota" in ws.last()["payload"]
