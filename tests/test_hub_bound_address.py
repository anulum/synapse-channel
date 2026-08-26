# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real-socket tests for atomic ephemeral hub binding

from __future__ import annotations

import asyncio
import contextlib

import pytest
from websockets.asyncio.client import connect

from synapse_channel import SynapseHub


async def test_port_zero_exposes_the_kernel_assigned_address() -> None:
    """A real hub binds port zero atomically and exposes its live address."""
    hub = SynapseHub(hub_id="ephemeral-bind")
    assert hub.bound_address is None

    task = asyncio.create_task(hub.serve("127.0.0.1", 0))
    host, port = await hub.wait_until_serving()
    try:
        assert host == "127.0.0.1"
        assert port > 0
        assert hub.bound_address == (host, port)
        connection = await connect(f"ws://{host}:{port}")
        await connection.close()
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert hub.bound_address is None


async def test_failed_bind_unblocks_readiness_without_a_false_address() -> None:
    """A real occupied socket fails promptly and never reports readiness."""
    listener = await asyncio.start_server(lambda _r, _w: None, "127.0.0.1", 0)
    port = int(listener.sockets[0].getsockname()[1])
    hub = SynapseHub(hub_id="occupied-bind")
    task = asyncio.create_task(hub.serve("127.0.0.1", port))
    try:
        with pytest.raises(RuntimeError, match="without a bound address"):
            await hub.wait_until_serving()
        with pytest.raises(OSError):
            await task
        assert hub.bound_address is None
    finally:
        listener.close()
        await listener.wait_closed()
