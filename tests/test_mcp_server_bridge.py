# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Model Context Protocol bridge

from __future__ import annotations

import asyncio
from typing import Any

from synapse_channel.mcp.server import SynapseHubBridge


def test_constructor_wires_callback_and_name() -> None:
    bridge = SynapseHubBridge(name="adapter")
    assert bridge.name == "adapter"
    assert bridge.agent.name == "adapter"
    assert bridge.agent.callback == bridge.on_message


async def test_on_message_resolves_only_matching_waiter() -> None:
    bridge = SynapseHubBridge(request_timeout=0.05)
    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()
    bridge._waiters.append((lambda data: data.get("type") == "X", future))
    await bridge.on_message({"type": "Y"})
    assert not future.done()
    await bridge.on_message({"type": "X"})
    assert future.done()


async def test_on_message_no_waiters_is_noop() -> None:
    bridge = SynapseHubBridge(request_timeout=0.05)
    await bridge.on_message({"type": "chat"})
    assert bridge._waiters == []


async def test_on_message_skips_already_resolved_waiter() -> None:
    bridge = SynapseHubBridge(request_timeout=0.05)
    loop = asyncio.get_running_loop()
    done: asyncio.Future[dict[str, Any]] = loop.create_future()
    done.set_result({})
    bridge._waiters.append((lambda data: True, done))
    await bridge.on_message({"type": "anything"})
    assert bridge._waiters
