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

from mcp_server_helpers import make_bridge


def test_constructor_wires_callback_and_name() -> None:
    bridge = make_bridge(name="adapter")
    assert bridge.name == "adapter"
    assert bridge.agent.name == "adapter"
    # The agent's callback is the bridge's response router.
    assert bridge.agent.callback == bridge.on_message


async def test_on_message_resolves_only_matching_waiter() -> None:
    bridge = make_bridge()
    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()
    bridge._waiters.append((lambda d: d.get("type") == "X", future))
    await bridge.on_message({"type": "Y"})  # no match
    assert not future.done()
    await bridge.on_message({"type": "X"})  # match resolves
    assert future.done()


async def test_on_message_no_waiters_is_noop() -> None:
    bridge = make_bridge()
    await bridge.on_message({"type": "chat"})  # nothing registered, no error
    assert bridge._waiters == []


async def test_on_message_skips_already_resolved_waiter() -> None:
    bridge = make_bridge()
    loop = asyncio.get_running_loop()
    done: asyncio.Future[dict[str, Any]] = loop.create_future()
    done.set_result({})
    bridge._waiters.append((lambda d: True, done))
    await bridge.on_message({"type": "anything"})  # matches but already done -> skipped
    assert bridge._waiters  # not removed by on_message
