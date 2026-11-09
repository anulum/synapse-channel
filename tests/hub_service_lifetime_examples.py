# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real listener-independent service test runtime
"""Run actual hub services while a fixture separately owns client transport."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from websockets.asyncio.server import serve

from synapse_channel.core.hub import SynapseHub


@asynccontextmanager
async def running_services(hub: SynapseHub) -> AsyncIterator[asyncio.Event]:
    """Run the production service owner without binding a hub listener.

    Parameters
    ----------
    hub:
        Actual already-composed hub, with caller-owned journal and policies.

    Yields
    ------
    asyncio.Event
        Stop request set by a completed production background service.

    Notes
    -----
    This test runtime exercises the internal owner shared by public serving.
    It is neither an installed authority runtime nor a split trust profile.
    """
    services = hub._service_lifetime()
    stop = asyncio.Event()
    try:
        async with services.monitoring():
            services.start(stop)
            yield stop
            services.check_completed()
    finally:
        await services.finish(True)


@asynccontextmanager
async def client_transport(hub: SynapseHub) -> AsyncIterator[str]:
    """Supply actual loopback client sockets independently of service ownership.

    Parameters
    ----------
    hub:
        Existing handler whose admission and identity gates remain in use.

    Yields
    ------
    str
        URI of this fixture's temporary listener. The service owner creates
        no listener and never publishes this fixture as its bound address.
    """
    async with serve(hub.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://127.0.0.1:{port}"


async def until(condition: Callable[[], bool]) -> None:
    """Wait under a finite test deadline for an actual service observation.

    Parameters
    ----------
    condition:
        Read actual runtime, socket or durable state without changing it.

    Raises
    ------
    TimeoutError
        If the production service does not produce the observation in time.
    """
    async with asyncio.timeout(15):
        while not condition():
            await asyncio.sleep(0.01)
