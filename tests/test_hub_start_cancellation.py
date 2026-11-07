# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a hub start cancelled at any await point leaves no listener open
"""Cancelling a hub while it opens its listener must close the listening socket.

``asyncio`` yields once after it binds and starts accepting. A cancellation
delivered at that yield used to escape before the server object reached the
caller, so the bound socket stayed open until garbage collection. The hub now
starts accepting inside its own server context, which closes the socket on
every exit.
"""

from __future__ import annotations

import asyncio
import gc
import warnings

import pytest

from synapse_channel.core.hub import SynapseHub

_AWAIT_POINTS = range(9)


async def _start_then_cancel(yields: int) -> None:
    """Start a hub on a kernel-assigned port and cancel it after ``yields`` loop turns."""
    hub = SynapseHub(hub_id="start-cancellation")
    serving = asyncio.create_task(hub.serve("127.0.0.1", 0))
    for _ in range(yields):
        await asyncio.sleep(0)
    serving.cancel()
    await asyncio.gather(serving, return_exceptions=True)


@pytest.mark.parametrize("yields", _AWAIT_POINTS)
async def test_a_cancelled_start_leaves_no_listening_socket(yields: int) -> None:
    """Whichever await point the cancellation hits, no socket is left for the collector."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        await _start_then_cancel(yields)
        gc.collect()
    leaked = [str(item.message) for item in caught if issubclass(item.category, ResourceWarning)]
    assert leaked == []
